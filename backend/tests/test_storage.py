import os
import sqlite3
from pathlib import Path

import pytest
from pydantic import SecretStr

from storage.crypto import DecryptionError, FieldCipher
from storage.db import Database
from storage.models import (
    AccountCleanupState,
    AccountConfigurationUpdate,
    AccountCreate,
    AutomaticConfigurationSettings,
    PendingAccountCreate,
)
from storage.repositories import (
    AccountAlreadyExistsError,
    AccountRepository,
)
from storage.settings_repository import ConfigurationSettingsError, EncryptionSettingsError, SettingsRepository


def create_repository(database_path: Path, password: str = "correct horse battery staple") -> AccountRepository:
    """
    创建使用临时数据库的账号仓储

    :param database_path (Path): 测试 SQLite 文件路径
    :param password (str): 测试主密码

    :return AccountRepository: 已初始化账号仓储
    """

    database = Database(database_path)
    database.initialize()
    cipher = FieldCipher(SecretStr(password), b"0123456789abcdef")
    return AccountRepository(database, cipher)


def create_account(provider_name: str = "opencode-go") -> AccountCreate:
    """
    创建使用明显虚构凭据的账号输入

    :param provider_name (str): OpenCode provider 名称

    :return AccountCreate: 测试账号输入
    """

    return AccountCreate(
        uuid="00000000-0000-4000-8000-000000000001",
        github_username="learner-test123",
        github_email="learner@example.test",
        github_password=SecretStr("Fake-GitHub-Password-123!"),
        opencode_provider_name=provider_name,
        opencode_workspace_id="wrk_test123",
        opencode_api_key=SecretStr("sk-" + "x" * 64),
        email_provider="duckmail",
        temp_email="learner@example.test",
    )


def test_database_applies_migration_once_and_restricts_permissions(tmp_path: Path) -> None:
    """
    验证迁移可重复执行且数据库仅允许当前用户访问
    """

    database_path = tmp_path / "accounts.db"
    database = Database(database_path)

    database.initialize()
    database.initialize()

    with sqlite3.connect(database_path) as connection:
        versions = connection.execute("SELECT version FROM schema_migrations").fetchall()
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert versions == [(1,), (2,), (3,), (4,), (5,)]
    assert {
        "accounts",
        "pool_state",
        "operation_logs",
        "settings",
        "account_cleanup_operations",
        "pending_accounts",
        "pending_account_cleanup_operations",
    }.issubset(tables)
    assert os.stat(database_path).st_mode & 0o077 == 0


def test_failed_migration_rolls_back_schema_and_version(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证失败迁移不会留下部分表结构或已应用版本
    """

    database_path = tmp_path / "accounts.db"
    database = Database(database_path)
    database.initialize()
    monkeypatch.setattr(
        "storage.db.MIGRATIONS",
        [
            (
                6,
                "CREATE TABLE partial_migration (id INTEGER PRIMARY KEY); INVALID SQL;",
            )
        ],
    )

    with pytest.raises(sqlite3.Error):
        database.initialize()

    with sqlite3.connect(database_path) as connection:
        version = connection.execute("SELECT version FROM schema_migrations WHERE version = 6").fetchone()
        partial_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'partial_migration'"
        ).fetchone()
    assert version is None
    assert partial_table is None


def test_account_repository_encrypts_sensitive_fields_before_sqlite(tmp_path: Path) -> None:
    """
    验证账号仓储往返数据且 SQLite 不包含敏感明文
    """

    database_path = tmp_path / "accounts.db"
    repository = create_repository(database_path)
    account_input = create_account()

    account = repository.add(account_input)

    assert account.github_password.get_secret_value() == "Fake-GitHub-Password-123!"
    assert account.opencode_api_key.get_secret_value() == "sk-" + "x" * 64
    assert "Fake-GitHub-Password" not in repr(account)
    assert "sk-" not in repr(account)
    with sqlite3.connect(database_path) as connection:
        stored_password, stored_api_key = connection.execute(
            "SELECT github_password, opencode_api_key FROM accounts"
        ).fetchone()
    assert b"Fake-GitHub-Password" not in stored_password
    assert b"sk-" not in stored_api_key
    assert stored_password.startswith(b"OCR1")
    assert stored_api_key.startswith(b"OCR1")


def test_account_repository_rejects_duplicate_identity(tmp_path: Path) -> None:
    """
    验证账号 UUID 和 provider 唯一约束映射为仓储异常
    """

    repository = create_repository(tmp_path / "accounts.db")
    repository.add(create_account())

    with pytest.raises(AccountAlreadyExistsError):
        repository.add(create_account())


def test_configuration_settings_default_enabled_and_persist_dependency(tmp_path: Path) -> None:
    """
    验证自动配置默认开启、可持久化且拒绝缺失 OpenCode 依赖
    """

    database = Database(tmp_path / "accounts.db")
    database.initialize()
    settings = SettingsRepository(database)

    assert settings.get_automatic_configuration() == AutomaticConfigurationSettings()
    saved = settings.update_automatic_configuration(
        AutomaticConfigurationSettings(auto_configure_opencode=False, auto_configure_omo=False)
    )

    assert saved.auto_configure_opencode is False
    assert settings.get_automatic_configuration() == saved
    with pytest.raises(ConfigurationSettingsError, match="依赖 OpenCode"):
        settings.update_automatic_configuration(
            AutomaticConfigurationSettings(auto_configure_opencode=False, auto_configure_omo=True)
        )


def test_account_repository_updates_configuration_status_atomically(tmp_path: Path) -> None:
    """
    验证账号配置状态在 SQLite 中原子更新
    """

    repository = create_repository(tmp_path / "accounts.db")
    stored = repository.add(create_account().model_copy(update={"opencode_configured": False, "omo_configured": False}))

    repository.update_configuration(
        [
            AccountConfigurationUpdate(
                account_id=stored.uuid,
                opencode_configured=True,
                omo_configured=False,
            )
        ]
    )

    updated = repository.get(stored.uuid)
    assert updated is not None
    assert updated.opencode_configured is True
    assert updated.omo_configured is False


def test_pending_account_is_encrypted_and_atomically_promoted(tmp_path: Path) -> None:
    """
    验证 GitHub 凭据先加密写入未完成表并在完成时原子提升
    """

    database_path = tmp_path / "accounts.db"
    repository = create_repository(database_path)
    pending = repository.add_pending(
        PendingAccountCreate(
            uuid="00000000-0000-4000-8000-000000000009",
            github_username="pending-user",
            github_email="pending@example.test",
            github_password=SecretStr("Fake-Pending-GitHub-Password!"),
            email_provider="duckmail",
            temp_email="pending@example.test",
        )
    )

    with sqlite3.connect(database_path) as connection:
        encrypted_password = connection.execute(
            "SELECT github_password FROM pending_accounts WHERE uuid = ?",
            (pending.uuid,),
        ).fetchone()[0]
    assert b"Fake-Pending-GitHub-Password" not in encrypted_password

    completed_input = create_account().model_copy(
        update={
            "uuid": pending.uuid,
            "github_username": pending.github_username,
            "github_email": pending.github_email,
            "github_password": pending.github_password,
            "email_provider": pending.email_provider,
            "temp_email": pending.temp_email,
        }
    )
    completed = repository.complete_pending(completed_input)

    assert completed.uuid == pending.uuid
    assert repository.get_pending(pending.uuid) is None
    assert repository.get(pending.uuid) is not None


def test_cleanup_intent_survives_until_atomic_delete_and_promotion(tmp_path: Path) -> None:
    """
    验证远端删除状态持久化并随本地账号删除原子清理
    """

    database_path = tmp_path / "accounts.db"
    repository = create_repository(database_path)
    repository.add(create_account())
    repository.add(
        create_account("opencode-go2").model_copy(
            update={
                "uuid": "00000000-0000-4000-8000-000000000002",
                "github_username": "learner-test456",
            }
        )
    )

    state = repository.begin_cleanup("00000000-0000-4000-8000-000000000001")
    repository.mark_remote_deleted("00000000-0000-4000-8000-000000000001")

    assert state == AccountCleanupState.REQUESTED
    assert repository.cleanup_state("00000000-0000-4000-8000-000000000001") == AccountCleanupState.REMOTE_DELETED

    repository.delete_and_promote(
        "00000000-0000-4000-8000-000000000001",
        "00000000-0000-4000-8000-000000000002",
    )

    remaining = repository.list_all()
    assert len(remaining) == 1
    assert remaining[0].opencode_provider_name == "opencode-go"
    assert repository.cleanup_state("00000000-0000-4000-8000-000000000001") is None


def test_field_cipher_rejects_wrong_password_and_tampering() -> None:
    """
    验证错误主密码和密文篡改均无法通过认证
    """

    salt = FieldCipher.generate_salt()
    cipher = FieldCipher(SecretStr("test master password"), salt)
    ciphertext = cipher.encrypt(SecretStr("fake secret"))
    tampered = ciphertext[:-1] + bytes([ciphertext[-1] ^ 1])

    with pytest.raises(DecryptionError):
        FieldCipher(SecretStr("wrong master password"), salt).decrypt(ciphertext)
    with pytest.raises(DecryptionError):
        cipher.decrypt(tampered)


def test_encryption_salt_is_randomly_created_and_reused(tmp_path: Path) -> None:
    """
    验证数据库加密盐只创建一次且后续启动稳定读取
    """

    database = Database(tmp_path / "accounts.db")
    database.initialize()
    repository = SettingsRepository(database)

    first_salt = repository.get_or_create_encryption_salt()
    second_salt = repository.get_or_create_encryption_salt()

    assert first_salt == second_salt
    assert len(first_salt) == 16
    assert first_salt != b"0123456789abcdef"


def test_invalid_persisted_encryption_salt_fails_safely(tmp_path: Path) -> None:
    """
    验证损坏的加密盐不会被静默替换导致已有密文丢失
    """

    database = Database(tmp_path / "accounts.db")
    database.initialize()
    with database.connection() as connection:
        connection.execute("INSERT INTO settings (key, value) VALUES ('encryption_salt', 'not-valid-base64!')")

    with pytest.raises(EncryptionSettingsError):
        SettingsRepository(database).get_or_create_encryption_salt()
