import asyncio
from datetime import datetime
from typing import Callable, Dict, List, Optional

from browser.base import OpenCodeQuotaBrowserClient
from browser.cloakbrowser_client import CloakBrowserClient
from browser.initializer import BrowserInitializer
from browser.models import OpenCodeQuotaPageStatus
from browser.opencode_quota import OpenCodeQuotaBrowser
from scheduler.models import QuotaRefreshResult, QuotaRefreshStatus
from storage.models import Account, AccountStatus, BrowserAuthState, QuotaInvalidReason, utc_now
from storage.repositories import AccountNotFoundError
from storage.service import AccountVaultService

from .models import ManualInterventionReason


class QuotaCheckService:
    """
    账号额度探测与持久化协调服务
    """

    def __init__(
        self,
        vault_service: AccountVaultService,
        browser_client_factory: Optional[Callable[[], OpenCodeQuotaBrowserClient]] = None,
        browser_initializer: Optional[BrowserInitializer] = None,
    ) -> None:
        """
        初始化额度检查服务

        :param vault_service (AccountVaultService): 已加密账号库服务
        :param browser_client_factory (Callable): 可选的后台浏览器额度客户端工厂
        :param browser_initializer (BrowserInitializer): 可选的共享浏览器初始化管理器
        """

        self._vault_service = vault_service
        self._account_locks: Dict[str, asyncio.Lock] = {}
        self._cloakbrowser_client: Optional[CloakBrowserClient] = None
        if browser_client_factory is None:
            self._cloakbrowser_client = CloakBrowserClient(headless=True, initializer=browser_initializer)
            self._browser_client_factory = self._create_browser_client
        else:
            self._browser_client_factory = browser_client_factory

    async def refresh_account(self, account_id: str) -> QuotaRefreshResult:
        """
        刷新一个账号额度并按可信结果更新状态

        :param account_id (str): 账号稳定 UUID

        :return QuotaRefreshResult: 不包含凭据的额度刷新结果

        :raises AccountNotFoundError: 账号记录不存在
        :raises VaultLockedError: 本地账号库尚未解锁
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            return await self._refresh_locked(account_id)

    async def _refresh_locked(self, account_id: str) -> QuotaRefreshResult:
        account = await asyncio.to_thread(self._vault_service.get_account, account_id)
        if not isinstance(account, Account):
            raise AccountNotFoundError("账号记录不存在")
        return await self._refresh_browser(account)

    async def refresh_all(self) -> List[QuotaRefreshResult]:
        """
        顺序刷新当前账号库中的全部账号

        单个上游失败被表示为结果，不会阻止后续账号检查

        :return List: 各账号的额度刷新结果

        :raises VaultLockedError: 本地账号库尚未解锁
        """

        accounts = await asyncio.to_thread(self._vault_service.list_complete_accounts)
        account_ids = [account.uuid for account in accounts]
        results: List[QuotaRefreshResult] = []
        for account_id in account_ids:
            try:
                results.append(await self.refresh_account(account_id))
            except AccountNotFoundError:
                continue
        return results

    async def mark_exhausted(self, account_id: str) -> Account:
        """
        按用户明确意图把账号标记为额度已用尽

        :param account_id (str): 账号稳定 UUID

        :return Account: 更新后的完整账号记录

        :raises AccountNotFoundError: 账号记录不存在
        :raises VaultLockedError: 本地账号库尚未解锁
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            account = await asyncio.to_thread(self._vault_service.get_account, account_id)
            if not isinstance(account, Account):
                raise AccountNotFoundError("账号记录不存在")
            return await asyncio.to_thread(self._vault_service.update_status, account_id, AccountStatus.EXHAUSTED)

    async def close_account_session(self, account_id: str) -> None:
        """
        等待指定账号的后台额度检查结束

        供同层账号清理流程在不可逆操作前协调资源，不读取或修改账号状态

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            return

    async def close(self) -> None:
        """
        关闭后台额度浏览器资源

        :return None: 无返回值
        """

        if self._cloakbrowser_client is not None:
            await self._cloakbrowser_client.close()

    async def _refresh_browser(self, account: Account) -> QuotaRefreshResult:
        if account.github_auth_state is None or account.opencode_auth_state is None:
            return QuotaRefreshResult(
                account_id=account.uuid,
                status=QuotaRefreshStatus.UNAVAILABLE,
                message="账号尚未保存浏览器登录状态，需要重新完成一次登录授权",
            )
        client = self._browser_client_factory()
        try:
            result = await client.start_check(
                account.github_username,
                account.opencode_workspace_id,
                account.github_auth_state,
                account.opencode_auth_state,
            )
        finally:
            await client.close()
        if result.status == OpenCodeQuotaPageStatus.UPDATED and result.usage_percent is not None:
            return await self._store_snapshot(
                account.uuid,
                result.usage_percent,
                utc_now(),
                result.github_auth_state,
                result.opencode_auth_state,
            )
        if result.github_auth_state is not None and result.opencode_auth_state is not None:
            await asyncio.to_thread(
                self._vault_service.update_auth_states,
                account.uuid,
                result.github_auth_state,
                result.opencode_auth_state,
            )
        if result.status == OpenCodeQuotaPageStatus.AUTH_REQUIRED:
            return QuotaRefreshResult(
                account_id=account.uuid,
                status=QuotaRefreshStatus.UNAVAILABLE,
                message=result.error_message or "保存的浏览器登录状态已失效，需要重新授权",
            )
        if result.status == OpenCodeQuotaPageStatus.INVALID:
            await asyncio.to_thread(
                self._vault_service.clear_quota,
                account.uuid,
                AccountStatus.INVALID,
                utc_now(),
                QuotaInvalidReason.GITHUB_CREDENTIALS_INVALID,
            )
            return QuotaRefreshResult(
                account_id=account.uuid,
                status=QuotaRefreshStatus.INVALID,
                message="保存的 GitHub 登录凭据已失效，账号已标记为失效",
            )
        if result.status == OpenCodeQuotaPageStatus.SUBSCRIPTION_REQUIRED:
            await asyncio.to_thread(
                self._vault_service.clear_quota,
                account.uuid,
                AccountStatus.INVALID,
                utc_now(),
                QuotaInvalidReason.SUBSCRIPTION_REQUIRED,
            )
            return QuotaRefreshResult(
                account_id=account.uuid,
                status=QuotaRefreshStatus.INVALID,
                message="OpenCode Go 当前未订阅或订阅已到期，账号已标记为失效",
            )
        if result.status == OpenCodeQuotaPageStatus.MANUAL_REQUIRED:
            return QuotaRefreshResult(
                account_id=account.uuid,
                status=QuotaRefreshStatus.UNAVAILABLE,
                message=_manual_message(result.manual_reason),
            )
        return QuotaRefreshResult(
            account_id=account.uuid,
            status=QuotaRefreshStatus.UNAVAILABLE,
            message=result.error_message or "后台浏览器未能取得可信额度数据",
        )

    def _create_browser_client(self) -> OpenCodeQuotaBrowserClient:
        if self._cloakbrowser_client is None:
            raise RuntimeError("OpenCode Go 额度浏览器管理器不可用")
        return OpenCodeQuotaBrowser(self._cloakbrowser_client.create_session())

    async def _store_snapshot(
        self,
        account_id: str,
        usage_percent: int,
        checked_at: datetime,
        github_auth_state: Optional[BrowserAuthState],
        opencode_auth_state: Optional[BrowserAuthState],
    ) -> QuotaRefreshResult:
        status = AccountStatus.EXHAUSTED if usage_percent >= 100 else AccountStatus.ACTIVE
        await asyncio.to_thread(
            self._vault_service.update_quota,
            account_id,
            100,
            usage_percent,
            checked_at,
            status,
            github_auth_state,
            opencode_auth_state,
        )
        refresh_status = (
            QuotaRefreshStatus.EXHAUSTED if status == AccountStatus.EXHAUSTED else QuotaRefreshStatus.UPDATED
        )
        return QuotaRefreshResult(
            account_id=account_id,
            status=refresh_status,
            quota_total=100,
            quota_used=usage_percent,
            quota_updated_at=checked_at.isoformat(),
            message="OpenCode Go 月度用量已更新" if status == AccountStatus.ACTIVE else "OpenCode Go 月度用量已用尽",
        )


def _manual_message(reason: Optional[ManualInterventionReason]) -> str:
    if reason == ManualInterventionReason.CAPTCHA:
        return "后台额度检查遇到 CAPTCHA，已停止且未更新额度"
    if reason == ManualInterventionReason.PHONE_VERIFICATION:
        return "后台额度检查遇到二次或设备验证，已停止且未更新额度"
    if reason == ManualInterventionReason.TIMEOUT:
        return "后台额度检查登录超时，未更新额度"
    return "后台额度检查遇到未知登录或授权阻断，已停止且未更新额度"
