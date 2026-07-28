import asyncio
from pathlib import Path
from typing import Callable, List, Optional

import pytest
from pydantic import SecretStr

from browser.base import OpenCodeQuotaBrowserClient
from browser.models import OpenCodeQuotaPageResult, OpenCodeQuotaPageStatus
from engine.models import ManualInterventionReason
from engine.quota_service import QuotaCheckService
from scheduler.models import QuotaRefreshResult, QuotaRefreshStatus
from scheduler.quota_scheduler import QuotaScheduler
from storage.models import (
    Account,
    AccountCreate,
    AccountStatus,
    BrowserAuthState,
    BrowserCookieState,
    QuotaInvalidReason,
)
from storage.service import AccountVaultService, VaultLockedError


def _vault(tmp_path: Path) -> AccountVaultService:
    vault = AccountVaultService(tmp_path / "accounts.db")
    password = SecretStr("quota service master password")
    vault.unlock(password, password)
    vault.add_account(
        AccountCreate(
            uuid="quota-account",
            github_username="quota-user",
            github_email="quota@example.test",
            github_password=SecretStr("Fake-Quota-GitHub-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_quota",
            opencode_api_key=SecretStr("sk-" + "q" * 64),
            github_auth_state=BrowserAuthState(),
            opencode_auth_state=BrowserAuthState(),
            email_provider="temp_mail",
            temp_email="quota@example.test",
        )
    )
    return vault


def _quota_service(
    vault: AccountVaultService,
    browser_client_factory: Optional[Callable[[], OpenCodeQuotaBrowserClient]] = None,
) -> QuotaCheckService:
    return QuotaCheckService(vault, browser_client_factory)


def _auth_state(name: str, value: str, domain: str) -> BrowserAuthState:
    return BrowserAuthState(
        cookies=[
            BrowserCookieState(
                name=name,
                value=SecretStr(value),
                domain=domain,
                path="/",
                expires=2_000_000_000,
                http_only=True,
                secure=True,
                same_site="Lax",
            )
        ]
    )


class FakeQuotaBrowserClient(OpenCodeQuotaBrowserClient):
    """
    后台浏览器额度检查测试替身
    """

    def __init__(self, results: List[OpenCodeQuotaPageResult]) -> None:
        """
        初始化按顺序返回的额度结果

        :param results (List): 预设浏览器额度结果
        """

        self._results = results
        self.start_calls = 0
        self.closed = False

    async def start_check(
        self,
        github_username: str,
        workspace_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> OpenCodeQuotaPageResult:
        """
        返回首次浏览器额度结果

        :param github_username (str): 测试 GitHub 用户名
        :param workspace_id (str): 测试 workspace 标识
        :param github_auth_state (BrowserAuthState): 测试 GitHub 认证状态
        :param opencode_auth_state (BrowserAuthState): 测试 OpenCode 认证状态

        :return OpenCodeQuotaPageResult: 预设浏览器结果
        """

        assert github_username
        assert workspace_id
        assert github_auth_state is not None
        assert opencode_auth_state is not None
        self.start_calls += 1
        return self._results.pop(0)

    async def close(self) -> None:
        """
        记录浏览器额度替身关闭

        :return None: 无返回值
        """

        self.closed = True


@pytest.mark.anyio
async def test_quota_service_persists_monthly_percentage(tmp_path: Path) -> None:
    """
    验证额度服务持久化月度用量并保持账号可用
    """

    vault = _vault(tmp_path)
    browser_client = FakeQuotaBrowserClient(
        [OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.UPDATED, usage_percent=80)]
    )
    service = _quota_service(vault, lambda: browser_client)
    try:
        result = await service.refresh_account("quota-account")
    finally:
        await service.close()

    account = vault.list_accounts()[0]
    assert result.status == QuotaRefreshStatus.UPDATED
    assert account.quota_total == 100
    assert account.quota_used == 80
    assert account.quota_updated_at is not None
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.anyio
async def test_quota_service_marks_exhausted_and_invalid(tmp_path: Path) -> None:
    """
    验证可信上游状态会更新账号状态
    """

    vault = _vault(tmp_path)
    exhausted_browser = FakeQuotaBrowserClient(
        [OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.UPDATED, usage_percent=100)]
    )
    exhausted_service = _quota_service(vault, lambda: exhausted_browser)
    try:
        exhausted = await exhausted_service.refresh_account("quota-account")
    finally:
        await exhausted_service.close()
    assert exhausted.status == QuotaRefreshStatus.EXHAUSTED
    assert vault.list_accounts()[0].status == AccountStatus.EXHAUSTED

    invalid_browser = FakeQuotaBrowserClient([OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.INVALID)])
    invalid_service = _quota_service(vault, lambda: invalid_browser)
    try:
        invalid = await invalid_service.refresh_account("quota-account")
    finally:
        await invalid_service.close()
    assert invalid.status == QuotaRefreshStatus.INVALID
    invalid_account = vault.list_accounts()[0]
    assert invalid_account.status == AccountStatus.INVALID
    assert invalid_account.quota_checked_at is not None
    assert invalid_account.quota_invalid_reason == QuotaInvalidReason.GITHUB_CREDENTIALS_INVALID


@pytest.mark.anyio
async def test_background_browser_block_preserves_existing_snapshot(tmp_path: Path) -> None:
    """
    验证后台浏览器遇到安全验证时保留已有额度
    """

    vault = _vault(tmp_path)
    vault.update_quota("quota-account", 100, 42, vault.list_accounts()[0].created_at, AccountStatus.ACTIVE)
    browser_client = FakeQuotaBrowserClient(
        [
            OpenCodeQuotaPageResult(
                status=OpenCodeQuotaPageStatus.MANUAL_REQUIRED,
                manual_reason=ManualInterventionReason.CAPTCHA,
            )
        ]
    )
    service = _quota_service(vault, lambda: browser_client)
    try:
        result = await service.refresh_account("quota-account")
    finally:
        await service.close()

    account = vault.list_accounts()[0]
    assert result.status == QuotaRefreshStatus.UNAVAILABLE
    assert "CAPTCHA" in result.message
    assert account.quota_used == 42
    assert account.status == AccountStatus.ACTIVE


@pytest.mark.anyio
async def test_background_browser_closes_after_success(tmp_path: Path) -> None:
    """
    验证后台浏览器成功抓取后立即关闭隔离会话
    """

    vault = _vault(tmp_path)
    browser_client = FakeQuotaBrowserClient(
        [OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.UPDATED, usage_percent=87)]
    )
    service = _quota_service(vault, lambda: browser_client)
    try:
        updated = await service.refresh_account("quota-account")
    finally:
        await service.close()

    account = vault.list_accounts()[0]
    assert updated.status == QuotaRefreshStatus.UPDATED
    assert updated.quota_used == 87
    assert account.quota_used == 87
    assert browser_client.start_calls == 1
    assert browser_client.closed is True


@pytest.mark.anyio
async def test_quota_service_does_not_launch_browser_without_saved_auth_state(tmp_path: Path) -> None:
    """
    验证历史账号缺少认证状态时直接要求重新授权
    """

    vault = AccountVaultService(tmp_path / "legacy-quota.db")
    password = SecretStr("legacy quota master password")
    vault.unlock(password, password)
    vault.add_account(
        AccountCreate(
            uuid="legacy-quota-account",
            github_username="legacy-quota-user",
            github_email="legacy-quota@example.test",
            github_password=SecretStr("Fake-Legacy-Quota-Password!"),
            opencode_provider_name="opencode-go",
            opencode_workspace_id="wrk_legacyquota",
            opencode_api_key=SecretStr("sk-" + "l" * 64),
            email_provider="temp_mail",
            temp_email="legacy-quota@example.test",
        )
    )
    browser_launched = False

    def create_browser() -> OpenCodeQuotaBrowserClient:
        nonlocal browser_launched
        browser_launched = True
        return FakeQuotaBrowserClient([])

    service = _quota_service(vault, create_browser)
    result = await service.refresh_account("legacy-quota-account")

    assert result.status == QuotaRefreshStatus.UNAVAILABLE
    assert "重新完成一次登录授权" in result.message
    assert browser_launched is False


@pytest.mark.anyio
async def test_expired_auth_state_preserves_existing_quota_and_status(tmp_path: Path) -> None:
    """
    验证会话过期只要求重新授权且保留可信额度快照
    """

    vault = _vault(tmp_path)
    checked_at = vault.list_accounts()[0].created_at
    vault.update_quota("quota-account", 100, 42, checked_at, AccountStatus.ACTIVE)
    browser_client = FakeQuotaBrowserClient(
        [
            OpenCodeQuotaPageResult(
                status=OpenCodeQuotaPageStatus.AUTH_REQUIRED,
                error_message="保存的 OpenCode 登录状态已失效，需要重新授权",
            )
        ]
    )
    service = _quota_service(vault, lambda: browser_client)

    result = await service.refresh_account("quota-account")

    stored = vault.list_accounts()[0]
    assert result.status == QuotaRefreshStatus.UNAVAILABLE
    assert stored.status == AccountStatus.ACTIVE
    assert stored.quota_used == 42
    assert stored.quota_checked_at == checked_at


@pytest.mark.anyio
async def test_successful_quota_refresh_rolls_both_auth_states(tmp_path: Path) -> None:
    """
    验证成功保活会把两类更新后认证状态与额度一起保存
    """

    github_state = _auth_state("user_session", "fake-rolled-github-cookie", ".github.com")
    opencode_state = _auth_state("opencode_session", "fake-rolled-opencode-cookie", ".opencode.ai")
    vault = _vault(tmp_path)
    browser_client = FakeQuotaBrowserClient(
        [
            OpenCodeQuotaPageResult(
                status=OpenCodeQuotaPageStatus.UPDATED,
                usage_percent=33,
                github_auth_state=github_state,
                opencode_auth_state=opencode_state,
            )
        ]
    )
    service = _quota_service(vault, lambda: browser_client)

    result = await service.refresh_account("quota-account")

    stored = vault.get_account("quota-account")
    assert result.status == QuotaRefreshStatus.UPDATED
    assert isinstance(stored, Account)
    assert stored.github_auth_state == github_state
    assert stored.opencode_auth_state == opencode_state


@pytest.mark.anyio
async def test_authenticated_dashboard_failure_still_rolls_auth_states(tmp_path: Path) -> None:
    """
    验证额度 DOM 暂时不可用时仍保存服务端更新后的认证状态
    """

    github_state = _auth_state("user_session", "fake-unavailable-github-cookie", ".github.com")
    opencode_state = _auth_state("opencode_session", "fake-unavailable-opencode-cookie", ".opencode.ai")
    vault = _vault(tmp_path)
    browser_client = FakeQuotaBrowserClient(
        [
            OpenCodeQuotaPageResult(
                status=OpenCodeQuotaPageStatus.UNAVAILABLE,
                github_auth_state=github_state,
                opencode_auth_state=opencode_state,
                error_message="OpenCode Go 仪表盘暂时不可用",
            )
        ]
    )
    service = _quota_service(vault, lambda: browser_client)

    result = await service.refresh_account("quota-account")

    stored = vault.get_account("quota-account")
    assert result.status == QuotaRefreshStatus.UNAVAILABLE
    assert isinstance(stored, Account)
    assert stored.github_auth_state == github_state
    assert stored.opencode_auth_state == opencode_state


@pytest.mark.anyio
async def test_browser_quota_marks_missing_subscription_invalid(tmp_path: Path) -> None:
    """
    验证未订阅或订阅到期会清除旧额度并把账号标记为失效
    """

    vault = _vault(tmp_path)
    vault.update_quota("quota-account", 100, 42, vault.list_accounts()[0].created_at, AccountStatus.ACTIVE)
    browser_client = FakeQuotaBrowserClient(
        [OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.SUBSCRIPTION_REQUIRED)]
    )
    service = _quota_service(vault, lambda: browser_client)
    try:
        result = await service.refresh_account("quota-account")
    finally:
        await service.close()

    account = vault.list_accounts()[0]
    assert result.status == QuotaRefreshStatus.INVALID
    assert "未订阅或订阅已到期" in result.message
    assert account.status == AccountStatus.INVALID
    assert account.quota_total is None
    assert account.quota_used is None
    assert account.quota_updated_at is None
    assert account.quota_checked_at is not None
    assert account.quota_invalid_reason == QuotaInvalidReason.SUBSCRIPTION_REQUIRED
    assert browser_client.closed is True


class FakeQuotaCheckService(QuotaCheckService):
    """
    周期检查生命周期测试替身
    """

    def __init__(self) -> None:
        """
        初始化调用次数
        """

        self.calls = 0
        self.called_event = asyncio.Event()

    async def refresh_all(self) -> List[QuotaRefreshResult]:
        """
        记录一次周期检查

        :return List: 空测试结果
        """

        self.calls += 1
        self.called_event.set()
        return []


class LockedQuotaCheckService(FakeQuotaCheckService):
    """
    周期检查锁库测试替身
    """

    async def refresh_all(self) -> List[QuotaRefreshResult]:
        """
        模拟账号库尚未解锁

        :return List: 不会返回

        :raises VaultLockedError: 始终模拟锁库
        """

        raise VaultLockedError("测试账号库尚未解锁")


@pytest.mark.anyio
async def test_quota_scheduler_starts_once_and_closes_promptly() -> None:
    """
    验证周期任务启动幂等且关闭不会等待完整检查间隔
    """

    service = FakeQuotaCheckService()
    scheduler = QuotaScheduler(service, 60)
    scheduler.start()
    scheduler.start()
    await asyncio.wait_for(service.called_event.wait(), timeout=1)
    await scheduler.close()

    assert service.calls == 1


@pytest.mark.anyio
async def test_quota_scheduler_retries_locked_vault_within_one_minute() -> None:
    """
    验证启动时锁库不会延迟整个正常保活周期
    """

    scheduler = QuotaScheduler(LockedQuotaCheckService(), 3_600)

    delay_seconds = await scheduler._refresh_delay()

    assert delay_seconds == 60
