import base64
import sqlite3
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from main import create_app
from storage.crypto import FieldCipher
from storage.db import Database
from storage.models import AccountCreate, PendingAccountCreate
from storage.repositories import AccountRepository
from storage.service import AccountVaultService, MasterPasswordConfirmationError
from storage.settings_repository import SettingsRepository


def _seed_account(database_path: Path, master_password: str) -> None:
    database = Database(database_path)
    database.initialize()
    salt = SettingsRepository(database).get_or_create_encryption_salt()
    repository = AccountRepository(database, FieldCipher(SecretStr(master_password), salt))
    repository.add(
        AccountCreate(
            uuid="00000000-0000-4000-8000-000000000006",
            github_username="phase-six-user",
            github_email="phase-six@example.test",
            github_password=SecretStr("Fake-GitHub-Password-Phase-6!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_phase6",
            opencode_api_key=SecretStr("sk-" + "q" * 64),
            email_provider="duckmail",
            temp_email="phase-six@example.test",
        )
    )


@pytest.mark.anyio
async def test_account_list_requires_unlock(tmp_path: Path) -> None:
    """
    验证账号列表在主密码解锁前拒绝访问
    """

    vault_service = AccountVaultService(tmp_path / "accounts.db")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/accounts")

    assert response.status_code == 423
    assert response.json()["code"] == "vault_locked"


@pytest.mark.anyio
async def test_account_creation_requires_unlocked_vault(tmp_path: Path) -> None:
    """
    验证账号创建流程在账号库解锁前拒绝启动
    """

    vault_service = AccountVaultService(tmp_path / "accounts.db")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/accounts")

    assert response.status_code == 423
    assert response.json() == {
        "code": "vault_locked",
        "message": "请先使用主密码解锁本地账号库",
        "details": None,
    }


@pytest.mark.anyio
async def test_new_vault_requires_matching_master_password_confirmation(tmp_path: Path) -> None:
    """
    验证首次初始化账号库必须提供一致的主密码确认
    """

    database_path = tmp_path / "accounts.db"
    vault_service = AccountVaultService(database_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        missing_response = await client.post(
            "/api/vault/unlock",
            json={"master_password": "new phase six master password"},
        )
        mismatch_response = await client.post(
            "/api/vault/unlock",
            json={
                "master_password": "new phase six master password",
                "master_password_confirmation": "different phase six password",
            },
        )
        success_response = await client.post(
            "/api/vault/unlock",
            json={
                "master_password": "new phase six master password",
                "master_password_confirmation": "new phase six master password",
            },
        )

    assert missing_response.status_code == 400
    assert mismatch_response.status_code == 400
    assert success_response.json() == {"unlocked": True, "initialized": True}
    assert database_path.is_file()


@pytest.mark.anyio
async def test_empty_vault_rejects_wrong_master_password_after_restart(tmp_path: Path) -> None:
    """
    验证空账号库重启后仍使用认证密文拒绝错误主密码
    """

    database_path = tmp_path / "accounts.db"
    master_password = "empty vault correct master password"
    first_vault = AccountVaultService(database_path)
    first_vault.unlock(SecretStr(master_password), SecretStr(master_password))
    restarted_vault = AccountVaultService(database_path)

    async with AsyncClient(
        transport=ASGITransport(app=create_app(restarted_vault)),
        base_url="http://test",
    ) as client:
        wrong_response = await client.post(
            "/api/vault/unlock",
            json={"master_password": "empty vault wrong master password"},
        )
        correct_response = await client.post(
            "/api/vault/unlock",
            json={"master_password": master_password},
        )

    assert wrong_response.status_code == 401
    assert correct_response.status_code == 200
    with sqlite3.connect(database_path) as connection:
        verifier = connection.execute("SELECT value FROM settings WHERE key = 'encryption_verifier'").fetchone()[0]
    assert master_password not in verifier


@pytest.mark.anyio
async def test_legacy_account_vault_adds_master_password_verifier(tmp_path: Path) -> None:
    """
    验证旧版已有账号库在成功认证后补写主密码验证密文
    """

    database_path = tmp_path / "accounts.db"
    master_password = "legacy vault master password"
    _seed_account(database_path, master_password)
    vault_service = AccountVaultService(database_path)

    vault_service.unlock(SecretStr(master_password))

    with sqlite3.connect(database_path) as connection:
        verifier = connection.execute("SELECT value FROM settings WHERE key = 'encryption_verifier'").fetchone()
    assert verifier is not None
    assert master_password not in verifier[0]


def test_legacy_empty_vault_requires_reinitialization_confirmation(tmp_path: Path) -> None:
    """
    验证旧版空账号库没有认证材料时重新要求主密码确认
    """

    database_path = tmp_path / "accounts.db"
    database = Database(database_path)
    database.initialize()
    SettingsRepository(database).get_or_create_encryption_salt()
    vault_service = AccountVaultService(database_path)
    password = SecretStr("legacy empty vault new master password")

    assert vault_service.is_initialized is False
    with pytest.raises(MasterPasswordConfirmationError):
        vault_service.unlock(password)

    vault_service.unlock(password, password)

    assert vault_service.is_initialized is True


def test_preexisting_empty_database_file_requires_confirmation(tmp_path: Path) -> None:
    """
    验证空文件不会绕过首次设置主密码确认
    """

    database_path = tmp_path / "accounts.db"
    database_path.touch()
    vault_service = AccountVaultService(database_path)
    password = SecretStr("empty file vault master password")

    assert vault_service.is_initialized is False
    with pytest.raises(MasterPasswordConfirmationError):
        vault_service.unlock(password)

    vault_service.unlock(password, password)

    assert vault_service.is_initialized is True


@pytest.mark.anyio
async def test_unlock_lists_only_account_summary(tmp_path: Path) -> None:
    """
    验证解锁后列表只返回脱敏账号摘要
    """

    database_path = tmp_path / "accounts.db"
    master_password = "correct phase six master password"
    _seed_account(database_path, master_password)
    vault_service = AccountVaultService(database_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        unlock_response = await client.post(
            "/api/vault/unlock",
            json={"master_password": master_password},
        )
        list_response = await client.get("/api/accounts")

    assert unlock_response.json() == {"unlocked": True, "initialized": True}
    assert list_response.status_code == 200
    account = list_response.json()["accounts"][0]
    assert account["github_username"] == "phase-six-user"
    assert account["github_email_masked"] == "p***@example.test"
    assert account["opencode_provider_name"] == "opencode-go"
    assert "github_password" not in account
    assert "opencode_api_key" not in account
    assert "Fake-GitHub-Password" not in list_response.text
    assert "phase-six@example.test" not in list_response.text
    assert "sk-" not in list_response.text


@pytest.mark.anyio
async def test_account_api_key_requires_explicit_target_and_keeps_list_masked(tmp_path: Path) -> None:
    """
    验证 API Key 只通过单账号专用接口按需返回
    """

    database_path = tmp_path / "accounts.db"
    master_password = "correct phase six master password"
    _seed_account(database_path, master_password)
    vault_service = AccountVaultService(database_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        await client.post("/api/vault/unlock", json={"master_password": master_password})
        list_response = await client.get("/api/accounts")
        key_response = await client.get("/api/accounts/00000000-0000-4000-8000-000000000006/api-key")
        missing_response = await client.get("/api/accounts/missing/api-key")

    assert "sk-" not in list_response.text
    assert key_response.status_code == 200
    assert key_response.headers["cache-control"] == "no-store"
    assert key_response.json() == {
        "account_id": "00000000-0000-4000-8000-000000000006",
        "api_key": "sk-" + "q" * 64,
    }
    assert missing_response.status_code == 404


@pytest.mark.anyio
async def test_pending_account_list_is_masked_and_has_no_opencode_credentials(tmp_path: Path) -> None:
    """
    验证未完成账号列表只返回脱敏身份和可空 OpenCode 字段
    """

    vault_service = AccountVaultService(tmp_path / "accounts.db")
    master_password = SecretStr("pending account master password")
    vault_service.unlock(master_password, master_password)
    vault_service.add_pending_account(
        PendingAccountCreate(
            github_username="pending-api-user",
            github_email="pending-api@example.test",
            github_password=SecretStr("Fake-Pending-Api-Password!"),
            email_provider="duckmail",
            temp_email="pending-api@example.test",
        )
    )

    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/accounts")

    assert response.status_code == 200
    account = response.json()["accounts"][0]
    assert account["github_username"] == "pending-api-user"
    assert account["github_email_masked"] == "p***@example.test"
    assert account["status"] == "pending_setup"
    assert account["opencode_provider_name"] is None
    assert account["opencode_workspace_id"] is None
    assert "github_password" not in account
    assert "opencode_api_key" not in account
    assert "Fake-Pending-Api-Password" not in response.text
    assert "pending-api@example.test" not in response.text


@pytest.mark.anyio
async def test_unlock_rejects_wrong_password_without_exposing_it(tmp_path: Path) -> None:
    """
    验证错误主密码返回稳定且不包含密码的错误响应
    """

    database_path = tmp_path / "accounts.db"
    _seed_account(database_path, "correct phase six master password")
    vault_service = AccountVaultService(database_path)
    wrong_password = "wrong phase six master password"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault_service)),
        base_url="http://test",
    ) as client:
        response = await client.post(
            "/api/vault/unlock",
            json={"master_password": wrong_password},
        )

    assert response.status_code == 401
    assert response.json()["code"] == "invalid_master_password"
    assert wrong_password not in response.text


@pytest.mark.anyio
async def test_export_then_import_uses_binary_bundle_contract(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证 HTTP 导出包可用独立密码导入另一已解锁账号库
    """

    sandbox_path = tmp_path / "sandbox"
    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(sandbox_path))
    source_path = tmp_path / "source.db"
    source_password = "source phase six master password"
    _seed_account(source_path, source_password)
    source_vault = AccountVaultService(source_path)
    source_vault.unlock(SecretStr(source_password))
    bundle_password = "independent phase six bundle password"
    async with AsyncClient(
        transport=ASGITransport(app=create_app(source_vault)),
        base_url="http://test",
    ) as source_client:
        export_response = await source_client.post(
            "/api/export",
            json={"bundle_password": bundle_password},
        )

    target_vault = AccountVaultService(tmp_path / "target.db")
    target_password = SecretStr("target phase six master password")
    target_vault.unlock(target_password, target_password)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(target_vault)),
        base_url="http://test",
    ) as target_client:
        import_response = await target_client.post(
            "/api/import",
            json={
                "bundle_password": bundle_password,
                "bundle_base64": base64.b64encode(export_response.content).decode("ascii"),
            },
        )

    assert export_response.status_code == 200
    assert export_response.headers["content-type"] == "application/vnd.opencode-register.bundle"
    assert export_response.content.startswith(b"OCRB1")
    assert import_response.json() == {"imported_count": 1}
    assert len(target_vault.list_accounts()) == 1
    assert (sandbox_path / "opencode-data" / "auth.json").is_file()
