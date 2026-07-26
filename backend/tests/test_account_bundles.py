import hashlib
import io
import json
import os
import zipfile
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from pydantic import SecretStr

from storage.bundles import AccountBundleCodec, BundleValidationError
from storage.crypto import FieldCipher
from storage.db import Database
from storage.models import Account, AccountCreate, PendingAccount, PendingAccountCreate
from storage.repositories import AccountAlreadyExistsError, AccountRepository
from storage.service import AccountVaultService
from storage.settings_repository import SettingsRepository


def _create_account(database_path: Path, master_password: str, suffix: int) -> Account:
    database = Database(database_path)
    database.initialize()
    salt = SettingsRepository(database).get_or_create_encryption_salt()
    repository = AccountRepository(database, FieldCipher(SecretStr(master_password), salt))
    return repository.add(
        AccountCreate(
            uuid=f"00000000-0000-4000-8000-{suffix:012d}",
            github_username=f"bundle-user-{suffix}",
            github_email=f"bundle-{suffix}@example.test",
            github_password=SecretStr(f"Fake-Bundle-Password-{suffix}!"),
            opencode_provider_name="opencode-go" if suffix == 1 else f"opencode-go{suffix}",
            opencode_workspace_id=f"wrk_bundle{suffix}",
            opencode_api_key=SecretStr("sk-" + str(suffix) * 64),
            email_provider="duckmail",
            temp_email=f"bundle-{suffix}@example.test",
        )
    )


def test_bundle_round_trip_uses_authenticated_encryption(tmp_path: Path) -> None:
    """
    验证账号包可完整往返且加密数据不包含敏感明文
    """

    account = _create_account(tmp_path / "source.db", "source master password", 1)
    codec = AccountBundleCodec()
    bundle_password = SecretStr("independent bundle password")

    bundle = codec.export([account], bundle_password)
    loaded = codec.load(bundle, bundle_password)

    assert bundle.startswith(b"OCRB1")
    assert b"Fake-Bundle-Password" not in bundle
    assert b"sk-" not in bundle
    assert loaded[0].uuid == account.uuid
    assert loaded[0].github_password.get_secret_value() == "Fake-Bundle-Password-1!"
    assert isinstance(loaded[0], Account)
    assert loaded[0].opencode_api_key.get_secret_value() == "sk-" + "1" * 64


def test_bundle_round_trip_preserves_pending_account_without_opencode_fields(tmp_path: Path) -> None:
    """
    验证未完成账号可加密导出并保持无 OpenCode 配置的结构
    """

    vault = AccountVaultService(tmp_path / "pending.db")
    password = SecretStr("pending vault master password")
    vault.unlock(password, password)
    pending = vault.add_pending_account(
        PendingAccountCreate(
            github_username="pending-bundle-user",
            github_email="pending-bundle@example.test",
            github_password=SecretStr("Fake-Pending-Bundle-Password!"),
            email_provider="duckmail",
            temp_email="pending-bundle@example.test",
        )
    )

    bundle = vault.export_accounts(SecretStr("pending bundle password"))
    loaded = AccountBundleCodec().load(bundle, SecretStr("pending bundle password"))

    assert len(loaded) == 1
    assert isinstance(loaded[0], PendingAccount)
    assert loaded[0].uuid == pending.uuid
    assert b"Fake-Pending-Bundle-Password" not in bundle


def test_bundle_loads_version_one_manifest(tmp_path: Path) -> None:
    """
    验证版本一清单的完整账号包保持向后兼容
    """

    account = _create_account(tmp_path / "source.db", "source master password", 1)
    account_payload = json.dumps(
        [
            {
                "uuid": account.uuid,
                "github_username": account.github_username,
                "github_email": account.github_email,
                "github_password": account.github_password.get_secret_value(),
                "github_created_at": account.github_created_at.isoformat(),
                "opencode_provider_name": account.opencode_provider_name,
                "opencode_workspace_id": account.opencode_workspace_id,
                "opencode_api_key": account.opencode_api_key.get_secret_value(),
                "opencode_user_id": account.opencode_user_id,
                "email_provider": account.email_provider,
                "temp_email": account.temp_email,
                "status": account.status.value,
                "quota_total": account.quota_total,
                "quota_used": account.quota_used,
                "quota_updated_at": None,
                "created_at": account.created_at.isoformat(),
                "updated_at": account.updated_at.isoformat(),
                "notes": account.notes,
            }
        ],
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")
    manifest = json.dumps(
        {
            "format_version": 1,
            "created_at": account.created_at.isoformat(),
            "account_count": 1,
            "payload_sha256": hashlib.sha256(account_payload).hexdigest(),
        }
    ).encode("utf-8")
    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", manifest)
        archive.writestr("accounts.json", account_payload)
    password = SecretStr("version one bundle password")

    loaded = AccountBundleCodec().load(_encrypt_test_archive(archive_buffer.getvalue(), password), password)

    assert len(loaded) == 1
    assert isinstance(loaded[0], Account)
    assert loaded[0].uuid == account.uuid


def test_bundle_rejects_wrong_password_tampering_and_oversize(tmp_path: Path) -> None:
    """
    验证错误密码、密文篡改和超大输入均在解析前失败
    """

    account = _create_account(tmp_path / "source.db", "source master password", 1)
    codec = AccountBundleCodec()
    bundle = codec.export([account], SecretStr("correct bundle password"))
    tampered = bundle[:-1] + bytes([bundle[-1] ^ 1])

    with pytest.raises(BundleValidationError):
        codec.load(bundle, SecretStr("wrong bundle password"))
    with pytest.raises(BundleValidationError):
        codec.load(tampered, SecretStr("correct bundle password"))
    with pytest.raises(BundleValidationError):
        codec.load(b"x" * (10 * 1024 * 1024 + 1), SecretStr("correct bundle password"))


def test_bundle_rejects_path_traversal_entry() -> None:
    """
    验证通过认证但含路径穿越条目的 ZIP 仍被拒绝
    """

    archive_buffer = io.BytesIO()
    with zipfile.ZipFile(archive_buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", b"{}")
        archive.writestr("../accounts.json", b"[]")
    password = SecretStr("path traversal bundle password")
    bundle = _encrypt_test_archive(archive_buffer.getvalue(), password)

    with pytest.raises(BundleValidationError, match="文件清单"):
        AccountBundleCodec().load(bundle, password)


def test_import_conflict_rolls_back_entire_batch(tmp_path: Path) -> None:
    """
    验证导入批次任一账号冲突时不会留下其他账号
    """

    source_path = tmp_path / "source.db"
    source_password = "source master password"
    first = _create_account(source_path, source_password, 1)
    second = _create_account(source_path, source_password, 2)
    bundle = AccountBundleCodec().export([first, second], SecretStr("transaction bundle password"))

    target_path = tmp_path / "target.db"
    _create_account(target_path, "target master password", 1)
    vault = AccountVaultService(target_path)
    target_password = SecretStr("target master password")
    vault.unlock(target_password, target_password)

    with pytest.raises(AccountAlreadyExistsError):
        vault.import_accounts(bundle, SecretStr("transaction bundle password"))

    accounts = vault.list_accounts()
    assert len(accounts) == 1
    assert accounts[0].uuid == first.uuid


def _encrypt_test_archive(archive: bytes, password: SecretStr) -> bytes:
    prefix = b"OCRB1"
    salt = os.urandom(16)
    nonce = os.urandom(12)
    key = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=600_000,
    ).derive(password.get_secret_value().encode("utf-8"))
    return prefix + salt + nonce + AESGCM(key).encrypt(nonce, archive, prefix)
