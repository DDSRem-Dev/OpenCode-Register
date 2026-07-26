import asyncio
import re
from typing import List, Optional

from pydantic import SecretStr

from config._json_file import rollback_path, rollback_write
from config.errors import ConfigFileError
from config.model_catalog import OpenCodeGoModelClient
from config.models import (
    ConfigWriteResult,
    OpenCodeModel,
    PoolAccountRemovalResult,
    PoolConfigWriteResult,
    PoolModelSyncResult,
)
from config.omo_writer import OmoConfigWriter
from config.opencode_writer import OpenCodeConfigWriter
from storage.models import Account

_SECONDARY_PROVIDER_PATTERN = re.compile(r"^opencode-go([2-9][0-9]*)$")


class OpenCodePoolConfigService:
    """
    OpenCode Go 官方模型与本地号池配置协调服务
    """

    def __init__(
        self,
        model_client: OpenCodeGoModelClient,
        opencode_writer: OpenCodeConfigWriter,
        omo_writer: OmoConfigWriter,
    ) -> None:
        """
        初始化号池配置协调服务

        :param model_client (OpenCodeGoModelClient): 官方模型目录客户端
        :param opencode_writer (OpenCodeConfigWriter): OpenCode 配置写入器
        :param omo_writer (OmoConfigWriter): OMO 配置写入器
        """

        self._model_client = model_client
        self._opencode_writer = opencode_writer
        self._omo_writer = omo_writer

    async def refresh_models(self) -> PoolModelSyncResult:
        """
        从官网获取模型目录并同步全部二级账号

        :return PoolModelSyncResult: OpenCode 与 OMO 联合模型同步结果
        """

        models = await self._model_client.fetch_models()
        opencode_result = await asyncio.to_thread(self._opencode_writer.sync_secondary_models, models)
        try:
            omo_result = await asyncio.to_thread(self._omo_writer.sync_official_models, models)
        except ConfigFileError:
            if opencode_result.updated_providers:
                await asyncio.to_thread(
                    rollback_path,
                    opencode_result.target_path,
                    opencode_result.backup_path,
                )
            raise
        return PoolModelSyncResult(
            model_count=len(models),
            opencode_result=opencode_result,
            omo_result=omo_result,
        )

    async def add_secondary_account(
        self,
        api_key: SecretStr,
        configure_omo: bool = True,
        expected_provider_name: Optional[str] = None,
    ) -> PoolConfigWriteResult:
        """
        自动分配序号并使用最新官方模型原子协调新增二级账号配置

        OMO 写入失败时恢复本次 opencode.json 变更

        :param api_key (SecretStr): 已验证 OpenCode API Key
        :param configure_omo (bool): 是否同步追加 Oh My OpenCode fallback
        :param expected_provider_name (str): 可选的账号库预分配 provider 名称

        :return PoolConfigWriteResult: 两个配置文件的写入结果

        :raises ConfigFileError: 配置写入失败且已执行跨文件回滚
        """

        models: List[OpenCodeModel] = await self._model_client.fetch_models()
        opencode_result = await asyncio.to_thread(
            self._opencode_writer.add_secondary_account,
            api_key,
            models,
            expected_provider_name,
        )
        if not configure_omo:
            return PoolConfigWriteResult(
                provider_name=opencode_result.provider_name,
                model_count=len(models),
                opencode_result=opencode_result,
            )
        try:
            omo_result = await asyncio.to_thread(
                self._omo_writer.append_account_fallback,
                opencode_result.provider_name,
                models,
            )
        except ConfigFileError:
            await asyncio.to_thread(rollback_write, opencode_result)
            raise
        return PoolConfigWriteResult(
            provider_name=opencode_result.provider_name,
            model_count=len(models),
            opencode_result=opencode_result,
            omo_result=omo_result,
        )

    async def append_account_fallback(self, provider_name: str) -> ConfigWriteResult:
        """
        将已配置的二级 provider 追加到 Oh My OpenCode fallback

        :param provider_name (str): 已存在的二级 OpenCode provider 名称

        :return ConfigWriteResult: Oh My OpenCode 配置写入结果
        """

        models: List[OpenCodeModel] = await self._model_client.fetch_models()
        return await asyncio.to_thread(self._omo_writer.append_account_fallback, provider_name, models)

    async def remove_account(
        self,
        removed_account: Account,
        remaining_accounts: List[Account],
    ) -> PoolAccountRemovalResult:
        """
        从三份配置移除账号并在删除首账号时递补最低编号账号

        任一文件写入失败时按相反顺序恢复本次已完成写入

        :param removed_account (Account): 已完成远端删除的账号
        :param remaining_accounts (List): 当前账号库中的其他账号

        :return PoolAccountRemovalResult: 配置清理和递补结果

        :raises ConfigFileError: 配置结构无效或跨文件回滚失败
        """

        if removed_account.opencode_provider_name == "opencode-go":
            return await self._remove_primary_account(removed_account, remaining_accounts)
        writes: List[ConfigWriteResult] = []
        try:
            if removed_account.opencode_configured:
                writes.append(
                    await asyncio.to_thread(
                        self._opencode_writer.remove_secondary_account,
                        removed_account.opencode_provider_name,
                    )
                )
            if removed_account.omo_configured:
                writes.append(
                    await asyncio.to_thread(
                        self._omo_writer.remove_account,
                        removed_account.opencode_provider_name,
                    )
                )
        except ConfigFileError:
            await self._rollback_writes(writes)
            raise
        return PoolAccountRemovalResult(
            removed_provider_name=removed_account.opencode_provider_name,
            writes=writes,
        )

    async def rollback_account_removal(self, result: PoolAccountRemovalResult) -> None:
        """
        恢复已完成但尚未提交数据库删除的号池配置清理

        :param result (PoolAccountRemovalResult): 先前配置清理结果

        :return None: 无返回值

        :raises ConfigFileError: 任一配置文件无法从本次备份恢复
        """

        await self._rollback_writes(result.writes)

    async def _remove_primary_account(
        self,
        removed_account: Account,
        remaining_accounts: List[Account],
    ) -> PoolAccountRemovalResult:
        promoted = _promotion_candidate(remaining_accounts)
        promoted_key = (
            promoted.opencode_api_key
            if removed_account.opencode_configured and promoted is not None and promoted.opencode_configured
            else None
        )
        writes: List[ConfigWriteResult] = []
        try:
            if removed_account.opencode_configured:
                writes.append(
                    await asyncio.to_thread(
                        self._opencode_writer.replace_or_remove_primary_account,
                        promoted_key,
                    )
                )
            if promoted is None and removed_account.omo_configured:
                writes.append(await asyncio.to_thread(self._omo_writer.remove_account, "opencode-go"))
            elif promoted is not None and promoted_key is not None:
                writes.append(
                    await asyncio.to_thread(
                        self._opencode_writer.remove_secondary_account,
                        promoted.opencode_provider_name,
                    )
                )
                if promoted.omo_configured:
                    writes.append(
                        await asyncio.to_thread(
                            self._omo_writer.remove_account,
                            promoted.opencode_provider_name,
                        )
                    )
        except ConfigFileError:
            await self._rollback_writes(writes)
            raise
        return PoolAccountRemovalResult(
            removed_provider_name="opencode-go",
            promoted_account_id=promoted.uuid if promoted is not None else None,
            promoted_provider_name=promoted.opencode_provider_name if promoted is not None else None,
            writes=writes,
        )

    async def _rollback_writes(self, writes: List[ConfigWriteResult]) -> None:
        for write in reversed(writes):
            if write.changed:
                await asyncio.to_thread(rollback_write, write)


def _promotion_candidate(accounts: List[Account]) -> Optional[Account]:
    numbered_accounts = []
    for account in accounts:
        match = _SECONDARY_PROVIDER_PATTERN.fullmatch(account.opencode_provider_name)
        if match is not None:
            numbered_accounts.append((int(match.group(1)), account))
    if not numbered_accounts:
        return None
    numbered_accounts.sort(key=lambda candidate: candidate[0])
    return numbered_accounts[0][1]
