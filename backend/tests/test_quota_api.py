from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from browser.base import OpenCodeQuotaBrowserClient
from browser.models import OpenCodeQuotaPageResult, OpenCodeQuotaPageStatus
from engine.quota_service import QuotaCheckService
from main import create_app
from storage.models import AccountCreate
from storage.service import AccountVaultService


def _vault(tmp_path: Path, *, unlock: bool = True) -> AccountVaultService:
    vault = AccountVaultService(tmp_path / "accounts.db")
    if not unlock:
        return vault
    password = SecretStr("phase seven API master password")
    vault.unlock(password, password)
    vault.add_account(
        AccountCreate(
            uuid="quota-api-account",
            github_username="quota-api-user",
            github_email="quota-api@example.test",
            github_password=SecretStr("Fake-Quota-Api-GitHub-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_quotaapi",
            opencode_api_key=SecretStr("sk-" + "z" * 64),
            email_provider="duckmail",
            temp_email="quota-api@example.test",
        )
    )
    return vault


class FakeQuotaBrowserClient(OpenCodeQuotaBrowserClient):
    """
    后台额度浏览器测试替身
    """

    async def start_check(
        self,
        github_username: str,
        github_password: SecretStr,
        workspace_id: str,
    ) -> OpenCodeQuotaPageResult:
        """
        返回固定的仪表盘额度结果

        :param github_username (str): 测试 GitHub 用户名
        :param github_password (SecretStr): 测试 GitHub 密码
        :param workspace_id (str): 测试 workspace 标识

        :return OpenCodeQuotaPageResult: 固定额度结果
        """

        assert github_username
        assert github_password.get_secret_value()
        assert workspace_id
        return OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.UPDATED, usage_percent=64)

    async def close(self) -> None:
        """
        关闭测试替身

        :return None: 无返回值
        """


def _quota_service(vault: AccountVaultService) -> QuotaCheckService:
    return QuotaCheckService(vault, FakeQuotaBrowserClient)


@pytest.mark.anyio
async def test_quota_refresh_endpoint_updates_account_summary(tmp_path: Path) -> None:
    """
    验证显式额度刷新接口更新脱敏账号摘要
    """

    vault = _vault(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault, _quota_service(vault))),
        base_url="http://test",
    ) as client:
        refresh_response = await client.post("/api/accounts/quota-api-account/quota/refresh")
        accounts_response = await client.get("/api/accounts")

    assert refresh_response.status_code == 200
    assert refresh_response.json()["status"] == "updated"
    assert refresh_response.json()["quota_used"] == 64
    assert accounts_response.json()["accounts"][0]["quota_used"] == 64
    assert "sk-" not in refresh_response.text


@pytest.mark.anyio
async def test_quota_routes_map_locked_and_missing_accounts(tmp_path: Path) -> None:
    """
    验证额度接口稳定映射锁库和账号不存在错误
    """

    locked_vault = _vault(tmp_path, unlock=False)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(locked_vault, _quota_service(locked_vault))),
        base_url="http://test",
    ) as client:
        locked_response = await client.post("/api/accounts/missing/quota/refresh")
    assert locked_response.status_code == 423
    assert locked_response.json()["code"] == "vault_locked"

    unlocked_vault = _vault(tmp_path / "unlocked")
    async with AsyncClient(
        transport=ASGITransport(app=create_app(unlocked_vault, _quota_service(unlocked_vault))),
        base_url="http://test",
    ) as client:
        missing_response = await client.post("/api/accounts/missing/quota/refresh")
    assert missing_response.status_code == 404
    assert missing_response.json()["code"] == "account_not_found"


@pytest.mark.anyio
async def test_mark_exhausted_endpoint_uses_explicit_account_target(tmp_path: Path) -> None:
    """
    验证人工标记耗尽只更新路径指定账号
    """

    vault = _vault(tmp_path)
    async with AsyncClient(
        transport=ASGITransport(app=create_app(vault, _quota_service(vault))),
        base_url="http://test",
    ) as client:
        response = await client.post("/api/accounts/quota-api-account/mark-exhausted")

    assert response.status_code == 200
    assert response.json() == {"account_id": "quota-api-account", "status": "exhausted"}
