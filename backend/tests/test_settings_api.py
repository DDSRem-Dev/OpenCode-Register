from pathlib import Path

import httpx
import pytest
from pydantic import SecretStr

from main import create_app
from storage.service import AccountVaultService


@pytest.mark.anyio
async def test_settings_api_persists_switches_and_enforces_dependency(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证设置接口持久化开关并拒绝脱离 OpenCode 的 Oh My OpenCode 配置
    """

    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(tmp_path / "sandbox"))
    vault = AccountVaultService(tmp_path / "accounts.db")
    app = create_app(vault_service=vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        initial = await client.get("/api/settings")
        updated = await client.put(
            "/api/settings",
            json={"auto_configure_opencode": False, "auto_configure_omo": False},
        )
        invalid = await client.put(
            "/api/settings",
            json={"auto_configure_opencode": False, "auto_configure_omo": True},
        )

    assert initial.json()["auto_configure_opencode"] is True
    assert initial.json()["auto_configure_omo"] is True
    assert updated.json()["auto_configure_opencode"] is False
    assert updated.json()["auto_configure_omo"] is False
    assert invalid.status_code == 409
    assert invalid.json()["code"] == "omo_configuration_requires_opencode"


@pytest.mark.anyio
async def test_apply_settings_requires_unlock_and_returns_pending_counts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证应用现有账号配置要求账号库解锁并返回稳定结果
    """

    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(tmp_path / "sandbox"))
    vault = AccountVaultService(tmp_path / "accounts.db")
    app = create_app(vault_service=vault)
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        locked = await client.post("/api/settings/apply")
        password = SecretStr("settings api master password")
        vault.unlock(password, password)
        applied = await client.post("/api/settings/apply")

    assert locked.status_code == 423
    assert locked.json()["code"] == "vault_locked"
    assert applied.status_code == 200
    assert applied.json()["applied_count"] == 0
    assert applied.json()["opencode_pending_count"] == 0
    assert applied.json()["omo_pending_count"] == 0
