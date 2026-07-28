import json
from pathlib import Path
from typing import List, Optional, Tuple

import httpx
import pytest
from pydantic import SecretStr

from browser.base import GitHubCleanupClient
from browser.models import GitHubCleanupPageResult, GitHubCleanupPageStatus
from config.model_catalog import OpenCodeGoModelClient
from config.models import OpenCodeConfigPaths
from config.omo_writer import OmoConfigWriter
from config.opencode_writer import OpenCodeConfigWriter
from config.pool_service import OpenCodePoolConfigService
from engine.cleanup_models import AccountCleanupStatus
from engine.cleanup_service import AccountCleanupService, CleanupIdentityMismatchError
from engine.models import ManualInterventionReason
from main import create_app
from storage.models import (
    AccountCleanupState,
    AccountCreate,
    AccountStatus,
    BrowserAuthState,
    BrowserCookieState,
    PendingAccountCreate,
)
from storage.service import AccountVaultService


class FakeGitHubCleanupClient(GitHubCleanupClient):
    """
    GitHub 账号清理流程测试替身
    """

    def __init__(self, results: List[GitHubCleanupPageResult]) -> None:
        """
        初始化按顺序返回的浏览器结果

        :param results (List): 预设浏览器结果
        """

        self._results = results
        self.start_calls = 0
        self.closed = False
        self.received_auth_state: Optional[BrowserAuthState] = None

    async def start_cleanup(
        self,
        username: str,
        password: SecretStr,
        github_auth_state: BrowserAuthState,
    ) -> GitHubCleanupPageResult:
        """
        返回首次浏览器清理结果

        :param username (str): 测试 GitHub 用户名
        :param password (SecretStr): 测试 GitHub 密码
        :param github_auth_state (BrowserAuthState): 测试 GitHub 认证状态

        :return GitHubCleanupPageResult: 预设结果
        """

        assert username
        assert password.get_secret_value()
        self.received_auth_state = github_auth_state
        self.start_calls += 1
        return self._results.pop(0)

    async def inspect_after_manual(self) -> GitHubCleanupPageResult:
        """
        返回人工操作后的浏览器清理结果

        :return GitHubCleanupPageResult: 预设结果
        """

        return self._results.pop(0)

    async def close(self) -> None:
        """
        记录浏览器清理替身关闭

        :return None: 无返回值
        """

        self.closed = True


def _github_auth_state(username: str) -> BrowserAuthState:
    return BrowserAuthState(
        cookies=[
            BrowserCookieState(
                name="user_session",
                value=SecretStr(f"fake-{username}-cookie"),
                domain=".github.com",
                path="/",
                expires=2_000_000_000,
                http_only=True,
                secure=True,
                same_site="Lax",
            )
        ]
    )


def _vault(tmp_path: Path) -> AccountVaultService:
    vault = AccountVaultService(tmp_path / "accounts.db")
    password = SecretStr("account cleanup master password")
    vault.unlock(password, password)
    vault.add_account(
        AccountCreate(
            uuid="cleanup-primary",
            github_username="cleanup-primary-user",
            github_email="cleanup-primary@example.test",
            github_password=SecretStr("Fake-Cleanup-Primary-Password!"),
            github_auth_state=_github_auth_state("cleanup-primary-user"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_cleanupprimary",
            opencode_api_key=SecretStr("sk-" + "p" * 64),
            email_provider="temp_mail",
            temp_email="cleanup-primary@example.test",
        )
    )
    vault.add_account(
        AccountCreate(
            uuid="cleanup-secondary",
            github_username="cleanup-secondary-user",
            github_email="cleanup-secondary@example.test",
            github_password=SecretStr("Fake-Cleanup-Secondary-Password!"),
            github_auth_state=_github_auth_state("cleanup-secondary-user"),
            opencode_provider_name="opencode-go2",
            opencode_workspace_id="wrk_cleanupsecondary",
            opencode_api_key=SecretStr("sk-" + "s" * 64),
            email_provider="temp_mail",
            temp_email="cleanup-secondary@example.test",
        )
    )
    return vault


def _pool(tmp_path: Path) -> Tuple[OpenCodePoolConfigService, httpx.AsyncClient]:
    paths = OpenCodeConfigPaths(
        auth_path=tmp_path / "auth.json",
        opencode_path=tmp_path / "opencode.json",
        omo_path=tmp_path / "oh-my-openagent.json",
    )
    paths.auth_path.write_text(
        json.dumps({"opencode-go": {"type": "api", "key": "sk-" + "p" * 64}}),
        encoding="utf-8",
    )
    paths.opencode_path.write_text(
        json.dumps({"provider": {"opencode-go2": {"models": {}}}}),
        encoding="utf-8",
    )
    paths.omo_path.write_text(
        json.dumps(
            {
                "agents": {
                    "build": {
                        "model": "opencode-go/kimi-k2.7-code",
                        "fallback_models": ["opencode-go2/kimi-k2.7-code"],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    http_client = httpx.AsyncClient()
    service = OpenCodePoolConfigService(
        OpenCodeGoModelClient(http_client),
        OpenCodeConfigWriter(paths),
        OmoConfigWriter(paths),
    )
    return service, http_client


@pytest.mark.anyio
async def test_cleanup_automates_remote_delete_then_promotes_secondary(tmp_path: Path) -> None:
    """
    验证精确用户名确认后自动删除远端账号并递补二级账号
    """

    vault = _vault(tmp_path)
    pool, http_client = _pool(tmp_path)
    client = FakeGitHubCleanupClient([GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)])
    service = AccountCleanupService(vault, pool, lambda: client)
    try:
        completed = await service.start("cleanup-primary", "cleanup-primary-user")
    finally:
        await service.close()
        await http_client.aclose()

    assert completed.status == AccountCleanupStatus.DONE
    assert completed.promoted_account_id == "cleanup-secondary"
    assert client.received_auth_state is not None
    assert [account.opencode_provider_name for account in vault.list_accounts()] == ["opencode-go"]
    assert "Fake-Cleanup" not in completed.model_dump_json()
    assert "sk-" not in completed.model_dump_json()


@pytest.mark.anyio
async def test_cleanup_deletes_pending_account_without_touching_pool_config(tmp_path: Path) -> None:
    """
    验证未完成账号远端删除后只清理 pending 记录而不修改号池配置
    """

    vault = AccountVaultService(tmp_path / "pending.db")
    password = SecretStr("pending cleanup master password")
    vault.unlock(password, password)
    pending = vault.add_pending_account(
        PendingAccountCreate(
            github_username="pending-cleanup-user",
            github_email="pending-cleanup@example.test",
            github_password=SecretStr("Fake-Pending-Cleanup-Password!"),
            github_auth_state=_github_auth_state("pending-cleanup-user"),
            email_provider="temp_mail",
            temp_email="pending-cleanup@example.test",
        )
    )
    pool, http_client = _pool(tmp_path)
    original_auth = (tmp_path / "auth.json").read_text(encoding="utf-8")
    client = FakeGitHubCleanupClient([GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)])
    service = AccountCleanupService(vault, pool, lambda: client)
    try:
        completed = await service.start(pending.uuid, pending.github_username)
    finally:
        await service.close()
        await http_client.aclose()

    assert completed.status == AccountCleanupStatus.DONE
    assert vault.list_accounts() == []
    assert (tmp_path / "auth.json").read_text(encoding="utf-8") == original_auth


@pytest.mark.anyio
async def test_cleanup_resumes_after_manual_security_verification(tmp_path: Path) -> None:
    """
    验证安全挑战暂停后可继续自动删除且删除意图持续保存
    """

    vault = _vault(tmp_path)
    pool, http_client = _pool(tmp_path)
    client = FakeGitHubCleanupClient(
        [
            GitHubCleanupPageResult(
                status=GitHubCleanupPageStatus.MANUAL_REQUIRED,
                manual_reason=ManualInterventionReason.CAPTCHA,
            ),
            GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED),
        ]
    )
    closed_account_sessions: List[str] = []

    async def close_account_session(account_id: str) -> None:
        closed_account_sessions.append(account_id)

    service = AccountCleanupService(vault, pool, lambda: client, close_account_session)
    try:
        manual = await service.start("cleanup-primary", "cleanup-primary-user")
        assert vault.cleanup_state("cleanup-primary") == AccountCleanupState.REQUESTED
        completed = await service.resume("cleanup-primary")
    finally:
        await service.close()
        await http_client.aclose()

    assert manual.status == AccountCleanupStatus.MANUAL_REQUIRED
    assert manual.manual_intervention is not None
    assert manual.manual_intervention.reason == ManualInterventionReason.CAPTCHA
    assert completed.status == AccountCleanupStatus.DONE
    assert closed_account_sessions == ["cleanup-primary", "cleanup-primary"]


@pytest.mark.anyio
async def test_remote_deleted_state_retries_local_cleanup_without_login(tmp_path: Path) -> None:
    """
    验证配置失败后使用持久化远端删除状态重试且不再次登录 GitHub
    """

    vault = _vault(tmp_path)
    pool, http_client = _pool(tmp_path)
    (tmp_path / "oh-my-openagent.json").write_text('{"agents":{"build":"invalid"}}', encoding="utf-8")
    first_client = FakeGitHubCleanupClient([GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)])
    retry_client = FakeGitHubCleanupClient([])
    clients = [first_client, retry_client]
    service = AccountCleanupService(vault, pool, lambda: clients.pop(0))
    try:
        failed = await service.start("cleanup-primary", "cleanup-primary-user")
        assert failed.status == AccountCleanupStatus.ERROR
        assert vault.cleanup_state("cleanup-primary") == AccountCleanupState.REMOTE_DELETED

        (tmp_path / "oh-my-openagent.json").write_text(
            json.dumps(
                {
                    "agents": {
                        "build": {
                            "model": "opencode-go/kimi-k2.7-code",
                            "fallback_models": ["opencode-go2/kimi-k2.7-code"],
                        }
                    }
                }
            ),
            encoding="utf-8",
        )
        completed = await service.start("cleanup-primary", "cleanup-primary-user")
    finally:
        await service.close()
        await http_client.aclose()

    assert completed.status == AccountCleanupStatus.DONE
    assert retry_client.start_calls == 0


@pytest.mark.anyio
async def test_cleanup_rejects_unconfirmed_identity_before_persisting_intent(tmp_path: Path) -> None:
    """
    验证用户名确认不一致时不打开浏览器也不持久化删除意图
    """

    vault = _vault(tmp_path)
    pool, http_client = _pool(tmp_path)
    client = FakeGitHubCleanupClient([])
    service = AccountCleanupService(vault, pool, lambda: client)
    try:
        with pytest.raises(CleanupIdentityMismatchError):
            await service.start("cleanup-primary", "different-user")
    finally:
        await service.close()
        await http_client.aclose()

    assert vault.cleanup_state("cleanup-primary") is None
    assert client.start_calls == 0


@pytest.mark.anyio
async def test_cleanup_without_saved_session_requires_reauthorization_without_browser(tmp_path: Path) -> None:
    """
    验证历史账号缺少 GitHub 会话时不启动浏览器且不改账号状态
    """

    vault = AccountVaultService(tmp_path / "legacy.db")
    password = SecretStr("legacy cleanup master password")
    vault.unlock(password, password)
    account = vault.add_account(
        AccountCreate(
            uuid="legacy-cleanup",
            github_username="legacy-cleanup-user",
            github_email="legacy-cleanup@example.test",
            github_password=SecretStr("Fake-Legacy-Cleanup-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_legacycleanup",
            opencode_api_key=SecretStr("sk-" + "l" * 64),
            email_provider="temp_mail",
            temp_email="legacy-cleanup@example.test",
        )
    )
    pool, http_client = _pool(tmp_path)
    client = FakeGitHubCleanupClient([])
    service = AccountCleanupService(vault, pool, lambda: client)
    try:
        result = await service.start(account.uuid, account.github_username)
    finally:
        await service.close()
        await http_client.aclose()

    stored = vault.get_account(account.uuid)
    assert result.status == AccountCleanupStatus.ERROR
    assert result.error_code == "github_cleanup_auth_required"
    assert client.start_calls == 0
    assert stored is not None
    assert stored.status == AccountStatus.ACTIVE


@pytest.mark.anyio
async def test_cleanup_api_requires_exact_identity_and_never_returns_credentials(tmp_path: Path) -> None:
    """
    验证删除 API 需要精确用户名确认并保持公开快照脱敏
    """

    vault = _vault(tmp_path)
    pool, pool_http_client = _pool(tmp_path)
    browser_client = FakeGitHubCleanupClient([GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)])
    cleanup_service = AccountCleanupService(vault, pool, lambda: browser_client)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=create_app(vault, cleanup_service=cleanup_service)),
            base_url="http://test",
        ) as api_client:
            mismatch = await api_client.request(
                "DELETE",
                "/api/accounts/cleanup-primary",
                json={"confirmed_username": "different-user"},
            )
            started = await api_client.request(
                "DELETE",
                "/api/accounts/cleanup-primary",
                json={"confirmed_username": "cleanup-primary-user"},
            )
    finally:
        await cleanup_service.close()
        await pool_http_client.aclose()

    assert mismatch.status_code == 409
    assert started.status_code == 202
    assert started.json()["status"] == "done"
    assert "Fake-Cleanup" not in started.text
    assert "sk-" not in started.text
