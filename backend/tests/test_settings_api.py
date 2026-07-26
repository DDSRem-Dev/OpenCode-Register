import json
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from config.model_catalog import OpenCodeGoModelClient
from config.models import OpenCodeModel
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


@pytest.mark.anyio
async def test_repair_configuration_fills_missing_category_fallbacks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证一键修复按实际 provider 补齐 category fallback
    """

    sandbox_root = tmp_path / "sandbox"
    config_root = sandbox_root / "opencode-config"
    data_root = sandbox_root / "opencode-data"
    config_root.mkdir(parents=True)
    data_root.mkdir(parents=True)
    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(sandbox_root))
    monkeypatch.setattr(
        OpenCodeGoModelClient,
        "fetch_models",
        AsyncMock(return_value=[OpenCodeModel(model_id="kimi-k2.7-code", name="Kimi K2.7 Code")]),
    )
    (data_root / "auth.json").write_text(
        '{"opencode-go":{"type":"api","key":"sk-pppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppppp"}}',
        encoding="utf-8",
    )
    (config_root / "opencode.json").write_text(
        '{"provider":{"opencode-go2":{},"opencode-go3":{}}}',
        encoding="utf-8",
    )
    (config_root / "oh-my-openagent.json").write_text(
        '{"agents":{"build":{"model":"opencode-go2/kimi-k2.7-code","fallback_models":["opencode-go/kimi-k2.7-code","opencode-go3/kimi-k2.7-code"]}},"categories":{"quick":{"model":"opencode-go2/kimi-k2.7-code","fallback_models":["opencode-go/kimi-k2.7-code"]}}}',
        encoding="utf-8",
    )
    app = create_app(vault_service=AccountVaultService(tmp_path / "accounts.db"))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.post("/api/settings/repair")

    repaired = json.loads((config_root / "oh-my-openagent.json").read_text(encoding="utf-8"))
    assert response.status_code == 200
    assert response.json() == {
        "updated_target_count": 1,
        "added_fallback_count": 1,
        "removed_fallback_count": 0,
    }
    assert repaired["categories"]["quick"]["fallback_models"] == [
        "opencode-go/kimi-k2.7-code",
        "opencode-go3/kimi-k2.7-code",
    ]
