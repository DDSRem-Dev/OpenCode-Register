import sqlite3
from datetime import datetime
from pathlib import Path
from threading import RLock
from typing import List, Optional, Tuple, Union

from pydantic import SecretStr

from storage.bundles import AccountBundleCodec
from storage.crypto import DecryptionError, FieldCipher
from storage.db import Database
from storage.models import (
    Account,
    AccountCleanupState,
    AccountConfigurationUpdate,
    AccountCreate,
    AccountRecord,
    AccountStatus,
    AccountSummary,
    AutomaticConfigurationSettings,
    BrowserAuthState,
    PendingAccount,
    PendingAccountCreate,
    QuotaInvalidReason,
)
from storage.repositories import AccountRepository
from storage.settings_repository import (
    ConfigurationSettingsError,
    EncryptionSettingsError,
    SettingsRepository,
)


class VaultLockedError(Exception):
    """
    本地账号库尚未解锁异常
    """


class InvalidMasterPasswordError(Exception):
    """
    主密码无法认证已有账号密文异常
    """


class MasterPasswordConfirmationError(Exception):
    """
    首次设置主密码时确认值缺失或不一致异常
    """


class InvalidConfigurationSettingsError(Exception):
    """
    自动配置设置无法读取或保存异常
    """


class AccountVaultService:
    """
    本地账号库解锁与只读查询服务

    主密码和派生密钥只保留在当前进程内存中
    """

    def __init__(self, database_path: Path) -> None:
        """
        初始化本地账号库服务

        :param database_path (Path): SQLite 数据库文件路径
        """

        self._database = Database(database_path)
        self._database_path = database_path
        self._repository: Optional[AccountRepository] = None
        self._bundle_codec = AccountBundleCodec()
        self._lock = RLock()

    @property
    def is_unlocked(self) -> bool:
        """
        判断本地账号库是否已在当前进程解锁

        :return bool: 已解锁时返回真
        """

        with self._lock:
            return self._repository is not None

    @property
    def is_initialized(self) -> bool:
        """
        判断本地账号库是否已经完成首次初始化

        :return bool: 已有主密码验证密文或账号密文时返回真
        """

        if not self._database_path.is_file():
            return False
        try:
            settings = SettingsRepository(self._database)
            if not settings.has_vault_schema():
                return False
            return settings.get_encryption_verifier() is not None or settings.has_encrypted_accounts()
        except (EncryptionSettingsError, sqlite3.Error):
            return True

    def unlock(
        self,
        master_password: SecretStr,
        master_password_confirmation: Optional[SecretStr] = None,
    ) -> None:
        """
        使用主密码初始化或解锁本地账号库

        :param master_password (SecretStr): 不落盘的用户主密码
        :param master_password_confirmation (SecretStr): 首次设置时的主密码确认

        :return None: 无返回值

        :raises InvalidMasterPasswordError: 主密码无法认证已有密文
        :raises MasterPasswordConfirmationError: 首次设置的主密码确认缺失或不一致
        """

        with self._lock:
            if not self.is_initialized:
                confirmation = (
                    master_password_confirmation.get_secret_value()
                    if master_password_confirmation is not None
                    else None
                )
                if confirmation != master_password.get_secret_value():
                    raise MasterPasswordConfirmationError("首次设置的主密码确认不一致")
            self._database.initialize()
            settings = SettingsRepository(self._database)
            try:
                salt = settings.get_or_create_encryption_salt()
                cipher = FieldCipher(master_password, salt)
                repository = AccountRepository(self._database, cipher)
                verifier = settings.get_encryption_verifier()
                if verifier is None:
                    repository.validate_cipher()
                    settings.create_encryption_verifier(cipher.create_verifier())
                else:
                    cipher.verify(verifier)
            except (DecryptionError, EncryptionSettingsError) as error:
                raise InvalidMasterPasswordError("主密码无法解锁本地账号库") from error
            self._repository = repository

    def list_accounts(self) -> List[AccountSummary]:
        """
        返回不包含密码或 API Key 的账号摘要

        :return List: 账号摘要列表

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            accounts = self._repository.list_summaries() + self._repository.list_pending_summaries()
            return sorted(accounts, key=lambda account: account.created_at)

    def get_automatic_configuration(self) -> AutomaticConfigurationSettings:
        """
        读取持久化自动配置开关

        :return AutomaticConfigurationSettings: 当前自动配置设置

        :raises InvalidConfigurationSettingsError: 设置存储格式无效
        """

        with self._lock:
            self._database.initialize()
            try:
                return SettingsRepository(self._database).get_automatic_configuration()
            except ConfigurationSettingsError as error:
                raise InvalidConfigurationSettingsError("自动配置设置无效") from error

    def update_automatic_configuration(
        self,
        settings: AutomaticConfigurationSettings,
    ) -> AutomaticConfigurationSettings:
        """
        原子保存自动配置开关

        :param settings (AutomaticConfigurationSettings): 目标自动配置设置

        :return AutomaticConfigurationSettings: 已保存的设置

        :raises InvalidConfigurationSettingsError: 设置依赖无效或无法保存
        """

        with self._lock:
            self._database.initialize()
            try:
                return SettingsRepository(self._database).update_automatic_configuration(settings)
            except ConfigurationSettingsError as error:
                raise InvalidConfigurationSettingsError("自动配置设置无效") from error

    def count_pending_configuration(self) -> Tuple[int, int]:
        """
        统计两类待应用配置的完整账号

        :return Tuple: OpenCode 与 Oh My OpenCode 待配置数量
        """

        with self._lock:
            self._database.initialize()
            return SettingsRepository(self._database).count_pending_configuration()

    def update_account_configuration(self, updates: List[AccountConfigurationUpdate]) -> None:
        """
        原子更新一组账号的本地配置应用状态

        :param updates (List): 每个账号的目标配置状态

        :return None: 无返回值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            self._repository.update_configuration(updates)

    def account_count(self) -> int:
        """
        返回当前持久化账号数量

        :return int: 账号记录数量

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return len(self._repository.list_summaries())

    def get_account(self, account_id: str) -> Optional[Union[Account, PendingAccount]]:
        """
        读取仅供后端受信服务使用的完整账号记录

        返回模型中的凭据保持 SecretStr，禁止传入 HTTP 响应、事件或日志

        :param account_id (str): 账号稳定 UUID

        :return Union: 完整或未完成账号记录，不存在时返回空值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.get(account_id) or self._repository.get_pending(account_id)

    def list_complete_accounts(self) -> List[Account]:
        """
        返回仅供后端受信协调服务使用的完整账号记录

        凭据保持 SecretStr，禁止传入 HTTP 响应、事件或日志

        :return List: 按创建顺序排列的完整账号记录

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.list_all()

    def add_account(self, account: AccountCreate) -> Account:
        """
        加密并持久化一个完成账号

        :param account (AccountCreate): 已验证账号数据

        :return Account: 已持久化账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.add(account)

    def add_pending_account(self, account: PendingAccountCreate) -> PendingAccount:
        """
        加密并持久化 GitHub 已创建的未完成账号

        :param account (PendingAccountCreate): 未完成账号数据

        :return PendingAccount: 已持久化未完成账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.add_pending(account)

    def complete_pending_account(self, account: AccountCreate) -> Account:
        """
        原子提升未完成账号为完整账号

        :param account (AccountCreate): 使用未完成账号 UUID 的完整数据

        :return Account: 已持久化完整账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.complete_pending(account)

    def update_pending_status(self, account_id: str, status: AccountStatus) -> PendingAccount:
        """
        更新未完成账号的生命周期状态

        :param account_id (str): 未完成账号稳定 UUID
        :param status (AccountStatus): 目标状态

        :return PendingAccount: 更新后的未完成账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.update_pending_status(account_id, status)

    def update_pending_auth_states(
        self,
        account_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> PendingAccount:
        """
        加密更新未完成账号的浏览器认证状态

        :param account_id (str): 未完成账号稳定 UUID
        :param github_auth_state (BrowserAuthState): GitHub 浏览器认证状态
        :param opencode_auth_state (BrowserAuthState): OpenCode 浏览器认证状态

        :return PendingAccount: 更新后的未完成账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.update_pending_auth_states(
                account_id,
                github_auth_state,
                opencode_auth_state,
            )

    def update_auth_states(
        self,
        account_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> Account:
        """
        加密滚动更新完整账号的浏览器认证状态

        :param account_id (str): 账号稳定 UUID
        :param github_auth_state (BrowserAuthState): GitHub 浏览器认证状态
        :param opencode_auth_state (BrowserAuthState): OpenCode 浏览器认证状态

        :return Account: 更新后的完整账号

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.update_auth_states(account_id, github_auth_state, opencode_auth_state)

    def update_quota(
        self,
        account_id: str,
        quota_total: int,
        quota_used: int,
        quota_updated_at: datetime,
        status: AccountStatus,
        github_auth_state: Optional[BrowserAuthState] = None,
        opencode_auth_state: Optional[BrowserAuthState] = None,
    ) -> Account:
        """
        更新账号额度快照和状态

        :param account_id (str): 账号稳定 UUID
        :param quota_total (int): 当前额度总量
        :param quota_used (int): 当前已用额度
        :param quota_updated_at (datetime): 额度更新时间
        :param status (AccountStatus): 派生账号状态
        :param github_auth_state (BrowserAuthState): 可选滚动 GitHub 认证状态
        :param opencode_auth_state (BrowserAuthState): 可选滚动 OpenCode 认证状态

        :return Account: 更新后的账号记录

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.update_quota(
                account_id,
                quota_total,
                quota_used,
                quota_updated_at,
                status,
                github_auth_state,
                opencode_auth_state,
            )

    def update_status(self, account_id: str, status: AccountStatus) -> Account:
        """
        更新账号状态

        :param account_id (str): 账号稳定 UUID
        :param status (AccountStatus): 目标账号状态

        :return Account: 更新后的账号记录

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.update_status(account_id, status)

    def clear_quota(
        self,
        account_id: str,
        status: AccountStatus,
        checked_at: datetime,
        invalid_reason: QuotaInvalidReason,
    ) -> Account:
        """
        清除失效额度快照并更新账号状态

        :param account_id (str): 账号稳定 UUID
        :param status (AccountStatus): 目标账号状态
        :param checked_at (datetime): 确认失效的检查时间
        :param invalid_reason (QuotaInvalidReason): 确认的失效原因

        :return Account: 更新后的账号记录

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.clear_quota(account_id, status, checked_at, invalid_reason)

    def begin_cleanup(self, account_id: str) -> AccountCleanupState:
        """
        持久化精确账号远端删除意图

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupState: 当前清理状态

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.begin_cleanup(account_id)

    def mark_remote_deleted(self, account_id: str) -> None:
        """
        持久化 GitHub 远端删除已经验证完成

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            self._repository.mark_remote_deleted(account_id)

    def cleanup_state(self, account_id: str) -> Optional[AccountCleanupState]:
        """
        读取账号远端清理持久化状态

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupState: 已存在状态；尚未发起时返回空值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.cleanup_state(account_id)

    def cancel_cleanup(self, account_id: str) -> None:
        """
        取消尚未验证远端删除的清理意图

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            self._repository.cancel_cleanup(account_id)

    def delete_and_promote(self, account_id: str, promoted_account_id: Optional[str]) -> None:
        """
        原子删除本地账号并更新可选递补账号 provider

        :param account_id (str): 待删除账号稳定 UUID
        :param promoted_account_id (str): 可选递补账号稳定 UUID

        :return None: 无返回值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            self._repository.delete_and_promote(account_id, promoted_account_id)

    def delete_pending_account(self, account_id: str) -> None:
        """
        删除已完成远端清理的未完成账号

        :param account_id (str): 未完成账号稳定 UUID

        :return None: 无返回值

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            self._repository.delete_pending(account_id)

    def export_accounts(self, bundle_password: SecretStr) -> bytes:
        """
        导出全部账号为认证加密包

        :param bundle_password (SecretStr): 导出包独立密码

        :return bytes: 版本化加密包

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            records: List[AccountRecord] = self._repository.list_all() + self._repository.list_pending()
            return self._bundle_codec.export(records, bundle_password)

    def import_accounts(self, bundle: bytes, bundle_password: SecretStr) -> int:
        """
        校验完整导入包后在单个事务中写入账号

        :param bundle (bytes): 版本化加密包
        :param bundle_password (SecretStr): 导出包密码

        :return int: 成功导入的账号数量

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            accounts = self._bundle_codec.load(bundle, bundle_password)
            return self._repository.import_accounts(accounts)

    def decode_accounts(self, bundle: bytes, bundle_password: SecretStr) -> List[AccountRecord]:
        """
        认证并解析导入包但不修改账号库

        :param bundle (bytes): 版本化加密包
        :param bundle_password (SecretStr): 导出包密码

        :return List: 已完整验证的账号记录

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._bundle_codec.load(bundle, bundle_password)

    def import_account_records(self, accounts: List[AccountRecord]) -> int:
        """
        在单个 SQLite 事务中导入已验证账号记录

        :param accounts (List): 已验证并完成目标 provider 分配的记录

        :return int: 成功导入数量

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        with self._lock:
            if self._repository is None:
                raise VaultLockedError("本地账号库尚未解锁")
            return self._repository.import_accounts(accounts)
