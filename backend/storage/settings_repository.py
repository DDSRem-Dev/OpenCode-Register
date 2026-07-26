import base64
import sqlite3
from typing import Optional, Tuple

from storage.crypto import FieldCipher
from storage.db import Database
from storage.models import AutomaticConfigurationSettings

_AUTO_CONFIGURE_OPENCODE_KEY = "auto_configure_opencode"
_AUTO_CONFIGURE_OMO_KEY = "auto_configure_omo"


class EncryptionSettingsError(Exception):
    """
    数据库加密设置缺失或格式无效异常
    """


class ConfigurationSettingsError(Exception):
    """
    自动配置设置缺失或格式无效异常
    """


class SettingsRepository:
    """
    应用设置 SQLite 持久化边界
    """

    def __init__(self, database: Database) -> None:
        """
        初始化设置仓储

        :param database (Database): SQLite 连接所有者
        """

        self._database = database

    def get_or_create_encryption_salt(self) -> bytes:
        """
        原子读取或创建数据库专属加密盐

        :return bytes: 字段密钥派生盐

        :raises EncryptionSettingsError: 已存加密盐格式无效
        """

        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT value FROM settings WHERE key = 'encryption_salt'").fetchone()
            if row is None:
                salt = FieldCipher.generate_salt()
                encoded_salt = base64.urlsafe_b64encode(salt).decode("ascii")
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES ('encryption_salt', ?)",
                    (encoded_salt,),
                )
                return salt
            try:
                salt = base64.b64decode(row["value"], altchars=b"-_", validate=True)
            except (ValueError, UnicodeEncodeError) as error:
                raise EncryptionSettingsError("数据库加密盐格式无效") from error
            if len(salt) != 16:
                raise EncryptionSettingsError("数据库加密盐长度无效")
            return salt

    def has_vault_schema(self) -> bool:
        """
        判断账号库核心表是否已经存在

        :return bool: settings 与 accounts 表均存在时返回真
        """

        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name IN ('settings', 'accounts')"
            ).fetchall()
        return {row["name"] for row in rows} == {"settings", "accounts"}

    def get_encryption_verifier(self) -> Optional[bytes]:
        """
        读取用于空账号库主密码认证的密文

        :return bytes: 已存在的验证密文，不存在时返回空值

        :raises EncryptionSettingsError: 已存验证密文编码无效
        """

        with self._database.connection() as connection:
            row = connection.execute("SELECT value FROM settings WHERE key = 'encryption_verifier'").fetchone()
        if row is None:
            return None
        try:
            return base64.b64decode(row["value"], altchars=b"-_", validate=True)
        except (ValueError, UnicodeEncodeError) as error:
            raise EncryptionSettingsError("数据库加密验证值格式无效") from error

    def create_encryption_verifier(self, verifier: bytes) -> None:
        """
        首次写入用于主密码认证的密文

        :param verifier (bytes): 当前派生密钥生成的验证密文

        :return None: 无返回值

        :raises EncryptionSettingsError: 验证密文已存在或无法写入
        """

        encoded_verifier = base64.urlsafe_b64encode(verifier).decode("ascii")
        try:
            with self._database.connection() as connection:
                connection.execute(
                    "INSERT INTO settings (key, value) VALUES ('encryption_verifier', ?)",
                    (encoded_verifier,),
                )
        except sqlite3.IntegrityError as error:
            raise EncryptionSettingsError("数据库加密验证值已存在") from error

    def has_encrypted_accounts(self) -> bool:
        """
        判断账号库是否已有可用于兼容验证的账号密文

        :return bool: 至少存在一条账号记录时返回真
        """

        with self._database.connection() as connection:
            row = connection.execute("SELECT 1 FROM accounts LIMIT 1").fetchone()
        return row is not None

    def get_automatic_configuration(self) -> AutomaticConfigurationSettings:
        """
        读取自动配置开关并为未设置项使用开启默认值

        :return AutomaticConfigurationSettings: 已验证的自动配置设置

        :raises ConfigurationSettingsError: 已存设置值格式无效
        """

        with self._database.connection() as connection:
            rows = connection.execute(
                "SELECT key, value FROM settings WHERE key IN (?, ?)",
                (_AUTO_CONFIGURE_OPENCODE_KEY, _AUTO_CONFIGURE_OMO_KEY),
            ).fetchall()
        values = {row["key"]: _parse_boolean(row["value"]) for row in rows}
        settings = AutomaticConfigurationSettings(
            auto_configure_opencode=values.get(_AUTO_CONFIGURE_OPENCODE_KEY, True),
            auto_configure_omo=values.get(_AUTO_CONFIGURE_OMO_KEY, True),
        )
        if settings.auto_configure_omo and not settings.auto_configure_opencode:
            raise ConfigurationSettingsError("Oh My OpenCode 自动配置依赖 OpenCode 自动配置")
        return settings

    def update_automatic_configuration(
        self,
        settings: AutomaticConfigurationSettings,
    ) -> AutomaticConfigurationSettings:
        """
        原子保存自动配置开关

        :param settings (AutomaticConfigurationSettings): 已验证的目标设置

        :return AutomaticConfigurationSettings: 已保存的设置

        :raises ConfigurationSettingsError: Oh My OpenCode 开关缺少 OpenCode 依赖
        """

        if settings.auto_configure_omo and not settings.auto_configure_opencode:
            raise ConfigurationSettingsError("Oh My OpenCode 自动配置依赖 OpenCode 自动配置")
        with self._database.connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.executemany(
                "INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                [
                    (_AUTO_CONFIGURE_OPENCODE_KEY, _serialize_boolean(settings.auto_configure_opencode)),
                    (_AUTO_CONFIGURE_OMO_KEY, _serialize_boolean(settings.auto_configure_omo)),
                ],
            )
        return settings

    def count_pending_configuration(self) -> Tuple[int, int]:
        """
        统计尚未写入两类本地配置的完整账号

        :return tuple: OpenCode 与 Oh My OpenCode 待配置账号数量
        """

        with self._database.connection() as connection:
            row = connection.execute(
                """
                SELECT
                    SUM(CASE WHEN opencode_configured = 0 THEN 1 ELSE 0 END) AS opencode_pending,
                    SUM(CASE WHEN omo_configured = 0 THEN 1 ELSE 0 END) AS omo_pending
                FROM accounts
                """
            ).fetchone()
        return int(row["opencode_pending"] or 0), int(row["omo_pending"] or 0)


def _parse_boolean(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ConfigurationSettingsError("自动配置设置值格式无效")


def _serialize_boolean(value: bool) -> str:
    return "true" if value else "false"
