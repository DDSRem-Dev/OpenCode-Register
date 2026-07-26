import sqlite3
from datetime import datetime
from typing import List, Optional, Tuple

from storage.crypto import FieldCipher
from storage.db import Database
from storage.models import (
    Account,
    AccountCleanupState,
    AccountConfigurationUpdate,
    AccountCreate,
    AccountRecord,
    AccountStatus,
    AccountSummary,
    PendingAccount,
    PendingAccountCreate,
    QuotaInvalidReason,
    utc_now,
)


class AccountAlreadyExistsError(Exception):
    """
    账号唯一标识或 provider 名称已存在异常
    """


class AccountNotFoundError(Exception):
    """
    账号记录不存在异常
    """


class AccountRepository:
    """
    账号 SQLite 持久化边界
    """

    def __init__(self, database: Database, cipher: FieldCipher) -> None:
        """
        初始化账号仓储

        :param database (Database): SQLite 连接所有者
        :param cipher (FieldCipher): 敏感字段加密器
        """

        self._database = database
        self._cipher = cipher

    def add(self, account: AccountCreate) -> Account:
        """
        在单个事务中加密并新增账号

        :param account (AccountCreate): 已验证账号输入

        :return Account: 新增后的账号记录

        :raises AccountAlreadyExistsError: UUID 或 provider 名称已存在
        """

        now = utc_now()
        values = (
            account.uuid,
            account.github_username,
            account.github_email,
            self._cipher.encrypt(account.github_password),
            _serialize_datetime(account.github_created_at),
            account.opencode_provider_name,
            account.opencode_workspace_id,
            self._cipher.encrypt(account.opencode_api_key),
            account.opencode_user_id,
            account.email_provider,
            account.temp_email,
            account.status.value,
            int(account.opencode_configured),
            int(account.omo_configured),
            _serialize_datetime(now),
            _serialize_datetime(now),
            account.notes,
        )
        try:
            with self._database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO accounts (
                        uuid, github_username, github_email, github_password, github_created_at,
                        opencode_provider_name, opencode_workspace_id, opencode_api_key, opencode_user_id,
                        email_provider, temp_email, status, opencode_configured, omo_configured,
                        created_at, updated_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    values,
                )
        except sqlite3.IntegrityError as error:
            raise AccountAlreadyExistsError("账号或 provider 已存在") from error
        stored = self.get(account.uuid)
        if stored is None:
            raise RuntimeError("新增账号后无法读取记录")
        return stored

    def add_pending(self, account: PendingAccountCreate) -> PendingAccount:
        """
        加密并持久化一个已创建 GitHub 的未完成账号

        :param account (PendingAccountCreate): 未完成账号输入

        :return PendingAccount: 新增后的未完成账号

        :raises AccountAlreadyExistsError: UUID 或 GitHub 用户名已存在
        """

        now = utc_now()
        try:
            with self._database.connection() as connection:
                connection.execute(
                    """
                    INSERT INTO pending_accounts (
                        uuid, github_username, github_email, github_password, github_created_at,
                        email_provider, temp_email, status, created_at, updated_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account.uuid,
                        account.github_username,
                        account.github_email,
                        self._cipher.encrypt(account.github_password),
                        _serialize_datetime(account.github_created_at),
                        account.email_provider,
                        account.temp_email,
                        account.status.value,
                        _serialize_datetime(now),
                        _serialize_datetime(now),
                        account.notes,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise AccountAlreadyExistsError("未完成账号已存在") from error
        stored = self.get_pending(account.uuid)
        if stored is None:
            raise RuntimeError("新增未完成账号后无法读取记录")
        return stored

    def complete_pending(self, account: AccountCreate) -> Account:
        """
        在单个事务中把未完成账号提升为完整账号

        :param account (AccountCreate): 使用相同 UUID 的完整账号输入

        :return Account: 提升后的完整账号

        :raises AccountNotFoundError: 未完成账号不存在或身份不一致
        :raises AccountAlreadyExistsError: 完整账号或 provider 冲突
        """

        now = utc_now()
        try:
            with self._database.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                pending = connection.execute(
                    "SELECT github_username, github_email FROM pending_accounts WHERE uuid = ?",
                    (account.uuid,),
                ).fetchone()
                if pending is None:
                    raise AccountNotFoundError("未完成账号记录不存在")
                if (
                    pending["github_username"] != account.github_username
                    or pending["github_email"] != account.github_email
                ):
                    raise AccountNotFoundError("未完成账号身份不一致")
                connection.execute(
                    """
                    INSERT INTO accounts (
                        uuid, github_username, github_email, github_password, github_created_at,
                        opencode_provider_name, opencode_workspace_id, opencode_api_key, opencode_user_id,
                        email_provider, temp_email, status, opencode_configured, omo_configured,
                        created_at, updated_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        account.uuid,
                        account.github_username,
                        account.github_email,
                        self._cipher.encrypt(account.github_password),
                        _serialize_datetime(account.github_created_at),
                        account.opencode_provider_name,
                        account.opencode_workspace_id,
                        self._cipher.encrypt(account.opencode_api_key),
                        account.opencode_user_id,
                        account.email_provider,
                        account.temp_email,
                        account.status.value,
                        int(account.opencode_configured),
                        int(account.omo_configured),
                        _serialize_datetime(now),
                        _serialize_datetime(now),
                        account.notes,
                    ),
                )
                connection.execute("DELETE FROM pending_accounts WHERE uuid = ?", (account.uuid,))
        except sqlite3.IntegrityError as error:
            raise AccountAlreadyExistsError("账号或 provider 已存在") from error
        stored = self.get(account.uuid)
        if stored is None:
            raise RuntimeError("提升未完成账号后无法读取记录")
        return stored

    def get(self, account_id: str) -> Optional[Account]:
        """
        按稳定 UUID 读取账号

        :param account_id (str): 账号稳定 UUID

        :return Account: 账号记录，不存在时返回空值
        """

        with self._database.connection() as connection:
            row = connection.execute("SELECT * FROM accounts WHERE uuid = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return self._account_from_row(row)

    def get_pending(self, account_id: str) -> Optional[PendingAccount]:
        """
        按稳定 UUID 读取未完成账号

        :param account_id (str): 账号稳定 UUID

        :return PendingAccount: 未完成账号，不存在时返回空值
        """

        with self._database.connection() as connection:
            row = connection.execute("SELECT * FROM pending_accounts WHERE uuid = ?", (account_id,)).fetchone()
        if row is None:
            return None
        return self._pending_from_row(row)

    def list_all(self) -> List[Account]:
        """
        按创建时间列出全部账号

        :return List: 类型化账号记录列表
        """

        with self._database.connection() as connection:
            rows = connection.execute("SELECT * FROM accounts ORDER BY id").fetchall()
        return [self._account_from_row(row) for row in rows]

    def validate_cipher(self) -> None:
        """
        使用已有敏感字段验证当前主密码

        空数据库没有可验证密文，此时允许首次解锁

        :return None: 无返回值

        :raises DecryptionError: 当前主密码无法认证已有密文
        """

        with self._database.connection() as connection:
            row = connection.execute("SELECT github_password FROM accounts ORDER BY id LIMIT 1").fetchone()
            if row is None:
                row = connection.execute("SELECT github_password FROM pending_accounts ORDER BY id LIMIT 1").fetchone()
        if row is not None:
            self._cipher.decrypt(row["github_password"])

    def list_summaries(self) -> List[AccountSummary]:
        """
        列出不读取或解密敏感字段的账号摘要

        :return List: 按创建顺序排列的账号摘要
        """

        with self._database.connection() as connection:
            rows = connection.execute(
                """
                SELECT uuid, github_username, github_email, opencode_provider_name,
                       opencode_workspace_id, status, quota_total, quota_used,
                       quota_updated_at, quota_checked_at, quota_invalid_reason,
                       opencode_configured, omo_configured,
                       created_at, updated_at, notes
                FROM accounts
                ORDER BY id
                """
            ).fetchall()
        return [self._summary_from_row(row) for row in rows]

    def update_configuration(self, updates: List[AccountConfigurationUpdate]) -> None:
        """
        原子更新多个账号的本地配置状态

        :param updates (List): 账号配置状态更新集合

        :return None: 无返回值

        :raises AccountNotFoundError: 任一目标账号不存在
        """

        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            for update in updates:
                cursor = connection.execute(
                    """
                    UPDATE accounts
                    SET opencode_configured = ?, omo_configured = ?, updated_at = ?
                    WHERE uuid = ?
                    """,
                    (
                        int(update.opencode_configured),
                        int(update.omo_configured),
                        _serialize_datetime(utc_now()),
                        update.account_id,
                    ),
                )
                if cursor.rowcount != 1:
                    raise AccountNotFoundError("待更新配置状态的账号不存在")

    def list_pending_summaries(self) -> List[AccountSummary]:
        """
        列出不解密凭据的未完成账号摘要

        :return List: 按创建顺序排列的未完成账号摘要
        """

        with self._database.connection() as connection:
            rows = connection.execute("SELECT * FROM pending_accounts ORDER BY id").fetchall()
        return [self._pending_summary_from_row(row) for row in rows]

    def list_pending(self) -> List[PendingAccount]:
        """
        返回全部未完成账号记录

        :return List: 按创建顺序排列的未完成账号
        """

        with self._database.connection() as connection:
            rows = connection.execute("SELECT * FROM pending_accounts ORDER BY id").fetchall()
        return [self._pending_from_row(row) for row in rows]

    def update_pending_status(self, account_id: str, status: AccountStatus) -> PendingAccount:
        """
        更新未完成账号状态

        :param account_id (str): 账号稳定 UUID
        :param status (AccountStatus): 未完成账号目标状态

        :return PendingAccount: 更新后的记录

        :raises AccountNotFoundError: 未完成账号不存在
        """

        if status not in {
            AccountStatus.PENDING_SETUP,
            AccountStatus.PENDING_PAYMENT,
            AccountStatus.CANCELLED,
            AccountStatus.INVALID,
        }:
            raise ValueError("未完成账号状态无效")
        with self._database.connection() as connection:
            cursor = connection.execute(
                "UPDATE pending_accounts SET status = ?, updated_at = ? WHERE uuid = ?",
                (status.value, _serialize_datetime(utc_now()), account_id),
            )
            if cursor.rowcount != 1:
                raise AccountNotFoundError("未完成账号记录不存在")
        account = self.get_pending(account_id)
        if account is None:
            raise AccountNotFoundError("未完成账号记录不存在")
        return account

    def update_quota(
        self,
        account_id: str,
        quota_total: int,
        quota_used: int,
        quota_updated_at: datetime,
        status: AccountStatus,
    ) -> Account:
        """
        原子更新账号额度快照与派生状态

        :param account_id (str): 账号稳定 UUID
        :param quota_total (int): 当前额度总量
        :param quota_used (int): 当前已用额度
        :param quota_updated_at (datetime): 额度快照时间
        :param status (AccountStatus): 与额度结果一致的账号状态

        :return Account: 更新后的账号记录

        :raises AccountNotFoundError: 账号记录不存在
        """

        now = utc_now()
        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET quota_total = ?, quota_used = ?, quota_updated_at = ?, quota_checked_at = ?,
                    quota_invalid_reason = NULL, status = ?, updated_at = ?
                WHERE uuid = ?
                """,
                (
                    quota_total,
                    quota_used,
                    _serialize_datetime(quota_updated_at),
                    _serialize_datetime(quota_updated_at),
                    status.value,
                    _serialize_datetime(now),
                    account_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AccountNotFoundError("账号记录不存在")
        account = self.get(account_id)
        if account is None:
            raise AccountNotFoundError("账号记录不存在")
        return account

    def update_status(self, account_id: str, status: AccountStatus) -> Account:
        """
        原子更新账号状态

        :param account_id (str): 账号稳定 UUID
        :param status (AccountStatus): 目标账号状态

        :return Account: 更新后的账号记录

        :raises AccountNotFoundError: 账号记录不存在
        """

        with self._database.connection() as connection:
            cursor = connection.execute(
                "UPDATE accounts SET status = ?, updated_at = ? WHERE uuid = ?",
                (status.value, _serialize_datetime(utc_now()), account_id),
            )
            if cursor.rowcount != 1:
                raise AccountNotFoundError("账号记录不存在")
        account = self.get(account_id)
        if account is None:
            raise AccountNotFoundError("账号记录不存在")
        return account

    def clear_quota(
        self,
        account_id: str,
        status: AccountStatus,
        checked_at: datetime,
        invalid_reason: QuotaInvalidReason,
    ) -> Account:
        """
        清除失效额度快照并原子更新账号状态

        :param account_id (str): 账号稳定 UUID
        :param status (AccountStatus): 目标账号状态
        :param checked_at (datetime): 确认失效的检查时间
        :param invalid_reason (QuotaInvalidReason): 确认的失效原因

        :return Account: 更新后的账号记录

        :raises AccountNotFoundError: 账号记录不存在
        """

        with self._database.connection() as connection:
            cursor = connection.execute(
                """
                UPDATE accounts
                SET quota_total = NULL, quota_used = NULL, quota_updated_at = NULL,
                    quota_checked_at = ?, quota_invalid_reason = ?, status = ?, updated_at = ?
                WHERE uuid = ?
                """,
                (
                    _serialize_datetime(checked_at),
                    invalid_reason.value,
                    status.value,
                    _serialize_datetime(utc_now()),
                    account_id,
                ),
            )
            if cursor.rowcount != 1:
                raise AccountNotFoundError("账号记录不存在")
        account = self.get(account_id)
        if account is None:
            raise AccountNotFoundError("账号记录不存在")
        return account

    def begin_cleanup(self, account_id: str) -> AccountCleanupState:
        """
        在远端副作用前持久化精确账号清理意图

        已验证远端删除的记录不会被重置为 requested

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupState: 当前持久化清理状态

        :raises AccountNotFoundError: 账号记录不存在
        """

        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            account_row = connection.execute("SELECT 1 FROM accounts WHERE uuid = ?", (account_id,)).fetchone()
            pending_row = connection.execute(
                "SELECT 1 FROM pending_accounts WHERE uuid = ?",
                (account_id,),
            ).fetchone()
            if account_row is None and pending_row is None:
                raise AccountNotFoundError("账号记录不存在")
            cleanup_table = (
                "account_cleanup_operations" if account_row is not None else "pending_account_cleanup_operations"
            )
            row = connection.execute(
                f"SELECT state FROM {cleanup_table} WHERE account_id = ?",  # noqa: S608 - table is internally selected
                (account_id,),
            ).fetchone()
            if row is None:
                connection.execute(
                    f"INSERT INTO {cleanup_table} (account_id, state, updated_at) VALUES (?, ?, ?)",  # noqa: S608
                    (account_id, AccountCleanupState.REQUESTED.value, _serialize_datetime(utc_now())),
                )
                return AccountCleanupState.REQUESTED
            return AccountCleanupState(row["state"])

    def mark_remote_deleted(self, account_id: str) -> None:
        """
        持久化 GitHub 远端删除已经验证完成

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值

        :raises AccountNotFoundError: 清理意图或账号记录不存在
        """

        with self._database.connection() as connection:
            for cleanup_table in ("account_cleanup_operations", "pending_account_cleanup_operations"):
                cursor = connection.execute(
                    f"UPDATE {cleanup_table} SET state = ?, updated_at = ? WHERE account_id = ?",  # noqa: S608
                    (AccountCleanupState.REMOTE_DELETED.value, _serialize_datetime(utc_now()), account_id),
                )
                if cursor.rowcount == 1:
                    return
        raise AccountNotFoundError("账号清理意图不存在")

    def cleanup_state(self, account_id: str) -> Optional[AccountCleanupState]:
        """
        读取账号远端清理持久化状态

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupState: 已存在状态；尚未发起时返回空值
        """

        with self._database.connection() as connection:
            for cleanup_table in ("account_cleanup_operations", "pending_account_cleanup_operations"):
                row = connection.execute(
                    f"SELECT state FROM {cleanup_table} WHERE account_id = ?",  # noqa: S608
                    (account_id,),
                ).fetchone()
                if row is not None:
                    return AccountCleanupState(row["state"])
        return None

    def cancel_cleanup(self, account_id: str) -> None:
        """
        删除尚未完成远端删除的清理意图

        已进入 remote_deleted 的状态不会被取消

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值
        """

        with self._database.connection() as connection:
            for cleanup_table in ("account_cleanup_operations", "pending_account_cleanup_operations"):
                connection.execute(
                    f"DELETE FROM {cleanup_table} WHERE account_id = ? AND state = ?",  # noqa: S608
                    (account_id, AccountCleanupState.REQUESTED.value),
                )

    def delete_pending(self, account_id: str) -> None:
        """
        原子删除已完成远端清理的未完成账号

        :param account_id (str): 未完成账号稳定 UUID

        :return None: 无返回值

        :raises AccountNotFoundError: 未完成账号不存在
        """

        with self._database.connection() as connection:
            cursor = connection.execute("DELETE FROM pending_accounts WHERE uuid = ?", (account_id,))
            if cursor.rowcount != 1:
                raise AccountNotFoundError("未完成账号记录不存在")

    def delete_and_promote(self, account_id: str, promoted_account_id: Optional[str]) -> None:
        """
        原子删除本地账号并把递补账号 provider 更新为首账号名称

        :param account_id (str): 待删除账号稳定 UUID
        :param promoted_account_id (str): 可选递补账号稳定 UUID

        :return None: 无返回值

        :raises AccountNotFoundError: 待删除或递补账号不存在
        """

        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            target = connection.execute("SELECT 1 FROM accounts WHERE uuid = ?", (account_id,)).fetchone()
            if target is None:
                raise AccountNotFoundError("账号记录不存在")
            if promoted_account_id is not None:
                promoted = connection.execute(
                    "SELECT 1 FROM accounts WHERE uuid = ? AND uuid != ?",
                    (promoted_account_id, account_id),
                ).fetchone()
                if promoted is None:
                    raise AccountNotFoundError("递补账号记录不存在")
            connection.execute("DELETE FROM accounts WHERE uuid = ?", (account_id,))
            if promoted_account_id is not None:
                connection.execute(
                    "UPDATE accounts SET opencode_provider_name = ?, updated_at = ? WHERE uuid = ?",
                    ("opencode-go", _serialize_datetime(utc_now()), promoted_account_id),
                )

    def import_accounts(self, accounts: List[AccountRecord]) -> int:
        """
        在单个事务中导入全部已验证账号

        任一 UUID 或 provider 冲突都会回滚整批记录

        :param accounts (List): 已完成认证和结构校验的导入账号

        :return int: 成功导入的账号数量

        :raises AccountAlreadyExistsError: 任一账号 UUID 或 provider 已存在
        """

        complete_accounts = [account for account in accounts if isinstance(account, Account)]
        pending_accounts = [account for account in accounts if isinstance(account, PendingAccount)]
        encrypted_values = [self._import_values(account) for account in complete_accounts]
        encrypted_pending_values = [self._import_pending_values(account) for account in pending_accounts]
        try:
            with self._database.connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                imported_ids = [account.uuid for account in accounts]
                imported_usernames = [account.github_username for account in accounts]
                placeholders = ",".join("?" for _ in imported_ids)
                if imported_ids:
                    existing = connection.execute(
                        f"""
                        SELECT uuid FROM accounts WHERE uuid IN ({placeholders})
                        UNION ALL
                        SELECT uuid FROM pending_accounts WHERE uuid IN ({placeholders})
                        """,  # noqa: S608 - placeholders are generated, values remain bound
                        (*imported_ids, *imported_ids),
                    ).fetchone()
                    username_placeholders = ",".join("?" for _ in imported_usernames)
                    existing_username = connection.execute(
                        f"""
                        SELECT github_username FROM accounts WHERE github_username IN ({username_placeholders})
                        UNION ALL
                        SELECT github_username FROM pending_accounts WHERE github_username IN ({username_placeholders})
                        """,  # noqa: S608 - placeholders are generated, values remain bound
                        (*imported_usernames, *imported_usernames),
                    ).fetchone()
                    if existing is not None or existing_username is not None:
                        raise AccountAlreadyExistsError("导入包包含已存在的账号")
                connection.executemany(
                    """
                    INSERT INTO accounts (
                        uuid, github_username, github_email, github_password, github_created_at,
                        opencode_provider_name, opencode_workspace_id, opencode_api_key, opencode_user_id,
                        email_provider, temp_email, status, quota_total, quota_used, quota_updated_at,
                        quota_checked_at, quota_invalid_reason, opencode_configured, omo_configured,
                        created_at, updated_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    encrypted_values,
                )
                connection.executemany(
                    """
                    INSERT INTO pending_accounts (
                        uuid, github_username, github_email, github_password, github_created_at,
                        email_provider, temp_email, status, created_at, updated_at, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    encrypted_pending_values,
                )
        except sqlite3.IntegrityError as error:
            raise AccountAlreadyExistsError("导入包包含已存在的账号或 provider") from error
        return len(accounts)

    def _account_from_row(self, row: sqlite3.Row) -> Account:
        return Account(
            uuid=row["uuid"],
            github_username=row["github_username"],
            github_email=row["github_email"],
            github_password=self._cipher.decrypt(row["github_password"]),
            github_created_at=_parse_datetime(row["github_created_at"]),
            opencode_provider_name=row["opencode_provider_name"],
            opencode_workspace_id=row["opencode_workspace_id"],
            opencode_api_key=self._cipher.decrypt(row["opencode_api_key"]),
            opencode_user_id=row["opencode_user_id"],
            email_provider=row["email_provider"],
            temp_email=row["temp_email"],
            status=AccountStatus(row["status"]),
            notes=row["notes"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
            quota_total=row["quota_total"],
            quota_used=row["quota_used"],
            quota_updated_at=_parse_optional_datetime(row["quota_updated_at"]),
            quota_checked_at=_parse_optional_datetime(row["quota_checked_at"]),
            quota_invalid_reason=_parse_optional_invalid_reason(row["quota_invalid_reason"]),
            opencode_configured=bool(row["opencode_configured"]),
            omo_configured=bool(row["omo_configured"]),
        )

    def _summary_from_row(self, row: sqlite3.Row) -> AccountSummary:
        return AccountSummary(
            uuid=row["uuid"],
            github_username=row["github_username"],
            github_email=row["github_email"],
            opencode_provider_name=row["opencode_provider_name"],
            opencode_workspace_id=row["opencode_workspace_id"],
            status=AccountStatus(row["status"]),
            quota_total=row["quota_total"],
            quota_used=row["quota_used"],
            quota_updated_at=_parse_optional_datetime(row["quota_updated_at"]),
            quota_checked_at=_parse_optional_datetime(row["quota_checked_at"]),
            quota_invalid_reason=_parse_optional_invalid_reason(row["quota_invalid_reason"]),
            opencode_configured=bool(row["opencode_configured"]),
            omo_configured=bool(row["omo_configured"]),
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
            notes=row["notes"],
        )

    def _pending_from_row(self, row: sqlite3.Row) -> PendingAccount:
        return PendingAccount(
            uuid=row["uuid"],
            github_username=row["github_username"],
            github_email=row["github_email"],
            github_password=self._cipher.decrypt(row["github_password"]),
            github_created_at=_parse_datetime(row["github_created_at"]),
            email_provider=row["email_provider"],
            temp_email=row["temp_email"],
            status=AccountStatus(row["status"]),
            notes=row["notes"],
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
        )

    def _pending_summary_from_row(self, row: sqlite3.Row) -> AccountSummary:
        return AccountSummary(
            uuid=row["uuid"],
            github_username=row["github_username"],
            github_email=row["github_email"],
            status=AccountStatus(row["status"]),
            opencode_configured=False,
            omo_configured=False,
            created_at=_parse_datetime(row["created_at"]),
            updated_at=_parse_datetime(row["updated_at"]),
            notes=row["notes"],
        )

    def _import_values(self, account: Account) -> Tuple[object, ...]:
        return (
            account.uuid,
            account.github_username,
            account.github_email,
            self._cipher.encrypt(account.github_password),
            _serialize_datetime(account.github_created_at),
            account.opencode_provider_name,
            account.opencode_workspace_id,
            self._cipher.encrypt(account.opencode_api_key),
            account.opencode_user_id,
            account.email_provider,
            account.temp_email,
            account.status.value,
            account.quota_total,
            account.quota_used,
            _serialize_optional_datetime(account.quota_updated_at),
            _serialize_optional_datetime(account.quota_checked_at),
            account.quota_invalid_reason.value if account.quota_invalid_reason is not None else None,
            int(account.opencode_configured),
            int(account.omo_configured),
            _serialize_datetime(account.created_at),
            _serialize_datetime(account.updated_at),
            account.notes,
        )

    def _import_pending_values(self, account: PendingAccount) -> Tuple[object, ...]:
        return (
            account.uuid,
            account.github_username,
            account.github_email,
            self._cipher.encrypt(account.github_password),
            _serialize_datetime(account.github_created_at),
            account.email_provider,
            account.temp_email,
            account.status.value,
            _serialize_datetime(account.created_at),
            _serialize_datetime(account.updated_at),
            account.notes,
        )


def _serialize_datetime(value: datetime) -> str:
    return value.isoformat()


def _parse_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _parse_optional_datetime(value: Optional[str]) -> Optional[datetime]:
    if value is None:
        return None
    return _parse_datetime(value)


def _serialize_optional_datetime(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return _serialize_datetime(value)


def _parse_optional_invalid_reason(value: Optional[str]) -> Optional[QuotaInvalidReason]:
    if value is None:
        return None
    return QuotaInvalidReason(value)
