import asyncio
from typing import List, Optional

from pydantic import SecretStr

from config._json_file import rollback_write
from config.errors import ConfigFileError, ModelCatalogError
from config.models import ConfigWriteResult, PoolConfigWriteResult
from config.opencode_writer import OpenCodeConfigWriter
from config.pool_service import OpenCodePoolConfigService
from engine.models import AccountCompletionData, PendingAccountData
from storage.models import (
    Account,
    AccountConfigurationUpdate,
    AccountCreate,
    AccountRecord,
    AccountStatus,
    AutomaticConfigurationSettings,
    BrowserAuthState,
    PendingAccountCreate,
)
from storage.repositories import AccountAlreadyExistsError, AccountNotFoundError
from storage.service import AccountVaultService, VaultLockedError


class AccountCompletionError(Exception):
    """
    账号配置或加密持久化未能完整完成异常
    """


class AccountCompletionService:
    """
    协调号池配置和 SQLite 加密持久化的账号完成服务

    文件配置先写入，SQLite 失败时使用配置备份执行补偿回滚。并发完成操作在服务内串行化。
    """

    def __init__(
        self,
        vault_service: AccountVaultService,
        opencode_writer: OpenCodeConfigWriter,
        pool_service: OpenCodePoolConfigService,
    ) -> None:
        """
        初始化账号完成服务

        :param vault_service (AccountVaultService): 已解锁账号库服务
        :param opencode_writer (OpenCodeConfigWriter): 首账号配置写入器
        :param pool_service (OpenCodePoolConfigService): 二级账号号池配置服务
        """

        self._vault_service = vault_service
        self._opencode_writer = opencode_writer
        self._pool_service = pool_service
        self._lock = asyncio.Lock()

    async def complete(self, data: AccountCompletionData) -> str:
        """
        写入号池配置并加密持久化完成账号

        :param data (AccountCompletionData): 流程取得的完整账号数据

        :return str: 分配完成的 OpenCode provider 名称

        :raises AccountCompletionError: 账号库锁定、配置失败或持久化失败
        """

        async with self._lock:
            try:
                account_count = await asyncio.to_thread(self._vault_service.account_count)
                settings = await asyncio.to_thread(self._vault_service.get_automatic_configuration)
            except VaultLockedError as error:
                raise AccountCompletionError("账号库尚未解锁") from error
            try:
                has_primary_account = await asyncio.to_thread(self._opencode_writer.has_primary_account)
            except ConfigFileError as error:
                raise AccountCompletionError("首账号配置检查失败") from error
            if account_count == 0 and not has_primary_account:
                return await self._complete_primary(data, settings)
            return await self._complete_secondary(data, settings)

    async def persist_pending(self, data: PendingAccountData) -> str:
        """
        GitHub 注册完成后立即加密持久化可清理凭据

        :param data (PendingAccountData): GitHub 注册完成数据

        :return str: 未完成账号稳定 UUID

        :raises AccountCompletionError: 账号库锁定、冲突或写入失败
        """

        try:
            account = await asyncio.to_thread(
                self._vault_service.add_pending_account,
                PendingAccountCreate(
                    github_username=data.github_username,
                    github_email=data.github_email,
                    github_password=data.github_password,
                    github_auth_state=data.github_auth_state,
                    email_provider=data.email_provider,
                    temp_email=data.temp_email,
                ),
            )
        except (AccountAlreadyExistsError, VaultLockedError, RuntimeError) as error:
            raise AccountCompletionError("GitHub 账号凭据持久化失败") from error
        return account.uuid

    async def update_pending_auth_states(
        self,
        account_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> None:
        """
        加密保存首次 GitHub 与 OpenCode 登录认证状态

        :param account_id (str): 未完成账号稳定 UUID
        :param github_auth_state (BrowserAuthState): GitHub 浏览器认证状态
        :param opencode_auth_state (BrowserAuthState): OpenCode 浏览器认证状态

        :return None: 无返回值

        :raises AccountCompletionError: 认证状态无法持久化
        """

        try:
            await asyncio.to_thread(
                self._vault_service.update_pending_auth_states,
                account_id,
                github_auth_state,
                opencode_auth_state,
            )
        except (AccountNotFoundError, VaultLockedError, ValueError) as error:
            raise AccountCompletionError("浏览器认证状态持久化失败") from error

    async def mark_pending_status(self, account_id: str, status: AccountStatus) -> None:
        """
        更新 GitHub 已创建账号的未完成状态

        :param account_id (str): 未完成账号稳定 UUID
        :param status (AccountStatus): 目标未完成状态

        :return None: 无返回值

        :raises AccountCompletionError: 状态无法持久化
        """

        try:
            await asyncio.to_thread(self._vault_service.update_pending_status, account_id, status)
        except (AccountNotFoundError, VaultLockedError, ValueError) as error:
            raise AccountCompletionError("未完成账号状态持久化失败") from error

    async def import_bundle(self, bundle: bytes, bundle_password: SecretStr) -> int:
        """
        重建目标号池配置并事务导入加密账号包

        :param bundle (bytes): 版本化加密账号包
        :param bundle_password (SecretStr): 导出包独立密码

        :return int: 成功导入账号数量

        :raises AccountCompletionError: 配置写入或跨边界回滚失败
        """

        async with self._lock:
            records = await asyncio.to_thread(self._vault_service.decode_accounts, bundle, bundle_password)
            try:
                complete_count = await asyncio.to_thread(self._vault_service.account_count)
                settings = await asyncio.to_thread(self._vault_service.get_automatic_configuration)
                has_primary_account = await asyncio.to_thread(self._opencode_writer.has_primary_account)
            except (VaultLockedError, ConfigFileError) as error:
                raise AccountCompletionError("导入前无法检查目标号池状态") from error

            writes: List[ConfigWriteResult] = []
            remapped_records: List[AccountRecord] = []
            reserved_provider_names = [
                account.opencode_provider_name
                for account in await asyncio.to_thread(self._vault_service.list_complete_accounts)
            ]
            try:
                for record in records:
                    if not isinstance(record, Account):
                        remapped_records.append(record)
                        continue
                    if complete_count == 0 and not has_primary_account:
                        provider_name = "opencode-go"
                        if settings.auto_configure_opencode:
                            config_result = await asyncio.to_thread(
                                self._opencode_writer.add_primary_account,
                                record.opencode_api_key,
                            )
                            writes.append(config_result)
                        has_primary_account = True
                    else:
                        provider_name = await self._secondary_provider_name(
                            record.opencode_api_key,
                            reserved_provider_names,
                            settings,
                            writes,
                        )
                    remapped_records.append(
                        record.model_copy(
                            update={
                                "opencode_provider_name": provider_name,
                                "opencode_configured": settings.auto_configure_opencode,
                                "omo_configured": settings.auto_configure_opencode and settings.auto_configure_omo,
                            }
                        )
                    )
                    reserved_provider_names.append(provider_name)
                    complete_count += 1
                imported_count = await asyncio.to_thread(
                    self._vault_service.import_account_records,
                    remapped_records,
                )
            except (AccountAlreadyExistsError, VaultLockedError, RuntimeError):
                rollback_error = await self._rollback_writes(writes)
                if rollback_error is not None:
                    raise AccountCompletionError("账号导入失败且配置回滚不完整") from rollback_error
                raise
            except (ConfigFileError, ModelCatalogError) as error:
                rollback_error = await self._rollback_writes(writes)
                if rollback_error is not None:
                    raise AccountCompletionError("账号导入配置失败且回滚不完整") from rollback_error
                raise AccountCompletionError("账号导入配置重建失败") from error
            return imported_count

    async def apply_pending_configuration(self) -> int:
        """
        按当前开关为已有账号补写尚未应用的本地配置

        :return int: 本次完成配置状态更新的账号数量

        :raises AccountCompletionError: 账号库锁定、配置冲突或回滚失败
        """

        async with self._lock:
            try:
                settings = await asyncio.to_thread(self._vault_service.get_automatic_configuration)
                accounts = await asyncio.to_thread(self._vault_service.list_complete_accounts)
            except VaultLockedError as error:
                raise AccountCompletionError("账号库尚未解锁") from error
            writes: List[ConfigWriteResult] = []
            updates: List[AccountConfigurationUpdate] = []
            try:
                for account in accounts:
                    update = await self._apply_account_configuration(account, settings, writes)
                    if update is not None:
                        updates.append(update)
                await asyncio.to_thread(self._vault_service.update_account_configuration, updates)
            except (ConfigFileError, ModelCatalogError, AccountNotFoundError, VaultLockedError, RuntimeError) as error:
                rollback_error = await self._rollback_writes(writes)
                if rollback_error is not None:
                    raise AccountCompletionError("应用现有账号配置失败且回滚不完整") from rollback_error
                raise AccountCompletionError("无法应用现有账号配置，请检查本地配置文件") from error
            return len(updates)

    async def _complete_primary(
        self,
        data: AccountCompletionData,
        settings: AutomaticConfigurationSettings,
    ) -> str:
        if not settings.auto_configure_opencode:
            try:
                await asyncio.to_thread(self._store_completed_account, data, "opencode-go", False, False)
            except (AccountAlreadyExistsError, AccountNotFoundError, VaultLockedError, RuntimeError) as error:
                raise AccountCompletionError("首账号加密持久化失败") from error
            return "opencode-go"
        try:
            config_result = await asyncio.to_thread(
                self._opencode_writer.add_primary_account,
                data.opencode_api_key,
            )
        except ConfigFileError as error:
            raise AccountCompletionError("首账号配置写入失败") from error
        try:
            await asyncio.to_thread(
                self._store_completed_account,
                data,
                config_result.provider_name,
                True,
                settings.auto_configure_omo,
            )
        except (AccountAlreadyExistsError, AccountNotFoundError, VaultLockedError, RuntimeError) as error:
            try:
                await asyncio.to_thread(rollback_write, config_result)
            except ConfigFileError as rollback_error:
                raise AccountCompletionError("首账号加密持久化失败且配置回滚不完整") from rollback_error
            raise AccountCompletionError("首账号加密持久化失败") from error
        return config_result.provider_name

    async def _complete_secondary(
        self,
        data: AccountCompletionData,
        settings: AutomaticConfigurationSettings,
    ) -> str:
        if not settings.auto_configure_opencode:
            try:
                accounts = await asyncio.to_thread(self._vault_service.list_complete_accounts)
                provider_name = await asyncio.to_thread(
                    self._opencode_writer.next_secondary_provider_name,
                    [account.opencode_provider_name for account in accounts],
                )
                await asyncio.to_thread(self._store_completed_account, data, provider_name, False, False)
            except ConfigFileError as error:
                raise AccountCompletionError("二级账号 provider 分配失败") from error
            except (AccountAlreadyExistsError, AccountNotFoundError, VaultLockedError, RuntimeError) as error:
                raise AccountCompletionError("二级账号加密持久化失败") from error
            return provider_name
        try:
            config_result = await self._pool_service.add_secondary_account(
                data.opencode_api_key,
                configure_omo=settings.auto_configure_omo,
            )
        except ModelCatalogError as error:
            raise AccountCompletionError("OpenCode Go 模型目录暂时不可用，请稍后重试") from error
        except ConfigFileError as error:
            raise AccountCompletionError(f"二级账号号池配置写入失败：{error}") from error
        try:
            await asyncio.to_thread(
                self._store_completed_account,
                data,
                config_result.provider_name,
                True,
                settings.auto_configure_omo,
            )
        except (AccountAlreadyExistsError, AccountNotFoundError, VaultLockedError, RuntimeError) as error:
            rollback_error = await self._rollback_secondary_config(config_result)
            if rollback_error is not None:
                raise AccountCompletionError("二级账号加密持久化失败且配置回滚不完整") from rollback_error
            raise AccountCompletionError("二级账号加密持久化失败") from error
        return config_result.provider_name

    def _store_completed_account(
        self,
        data: AccountCompletionData,
        provider_name: str,
        opencode_configured: bool,
        omo_configured: bool,
    ) -> None:
        account = _account_create(data, provider_name, opencode_configured, omo_configured)
        if data.account_id is None:
            self._vault_service.add_account(account)
            return
        self._vault_service.complete_pending_account(account)

    async def _rollback_secondary_config(self, config_result: PoolConfigWriteResult) -> Optional[ConfigFileError]:
        writes = [config_result.opencode_result]
        if config_result.omo_result is not None:
            writes.append(config_result.omo_result)
        return await self._rollback_writes(writes)

    async def _secondary_provider_name(
        self,
        api_key: SecretStr,
        reserved_provider_names: List[str],
        settings: AutomaticConfigurationSettings,
        writes: List[ConfigWriteResult],
    ) -> str:
        if not settings.auto_configure_opencode:
            return await asyncio.to_thread(
                self._opencode_writer.next_secondary_provider_name,
                reserved_provider_names,
            )
        expected_provider_name = await asyncio.to_thread(
            self._opencode_writer.next_secondary_provider_name,
            reserved_provider_names,
        )
        pool_result = await self._pool_service.add_secondary_account(
            api_key,
            configure_omo=settings.auto_configure_omo,
            expected_provider_name=expected_provider_name,
        )
        writes.append(pool_result.opencode_result)
        if pool_result.omo_result is not None:
            writes.append(pool_result.omo_result)
        return pool_result.provider_name

    async def _apply_account_configuration(
        self,
        account: Account,
        settings: AutomaticConfigurationSettings,
        writes: List[ConfigWriteResult],
    ) -> Optional[AccountConfigurationUpdate]:
        configure_opencode = settings.auto_configure_opencode and not account.opencode_configured
        configure_omo = settings.auto_configure_omo and not account.omo_configured
        if not configure_opencode and not configure_omo:
            return None
        if configure_opencode and account.opencode_provider_name == "opencode-go":
            writes.append(await asyncio.to_thread(self._opencode_writer.add_primary_account, account.opencode_api_key))
        elif configure_opencode:
            pool_result = await self._pool_service.add_secondary_account(
                account.opencode_api_key,
                configure_omo=configure_omo,
                expected_provider_name=account.opencode_provider_name,
            )
            writes.append(pool_result.opencode_result)
            if pool_result.omo_result is not None:
                writes.append(pool_result.omo_result)
        elif configure_omo and account.opencode_provider_name != "opencode-go":
            writes.append(await self._pool_service.append_account_fallback(account.opencode_provider_name))
        return AccountConfigurationUpdate(
            account_id=account.uuid,
            opencode_configured=account.opencode_configured or configure_opencode,
            omo_configured=account.omo_configured or configure_omo,
        )

    async def _rollback_writes(self, writes: List[ConfigWriteResult]) -> Optional[ConfigFileError]:
        rollback_error: Optional[ConfigFileError] = None
        for write_result in reversed(writes):
            if not write_result.changed:
                continue
            try:
                await asyncio.to_thread(rollback_write, write_result)
            except ConfigFileError as error:
                rollback_error = error
        return rollback_error


def _account_create(
    data: AccountCompletionData,
    provider_name: str,
    opencode_configured: bool,
    omo_configured: bool,
) -> AccountCreate:
    account = AccountCreate(
        github_username=data.github_username,
        github_email=data.github_email,
        github_password=data.github_password,
        opencode_provider_name=provider_name,
        opencode_workspace_id=data.opencode_workspace_id,
        opencode_api_key=data.opencode_api_key,
        github_auth_state=data.github_auth_state,
        opencode_auth_state=data.opencode_auth_state,
        email_provider=data.email_provider,
        temp_email=data.temp_email,
        opencode_configured=opencode_configured,
        omo_configured=omo_configured,
    )
    if data.account_id is not None:
        return account.model_copy(update={"uuid": data.account_id})
    return account
