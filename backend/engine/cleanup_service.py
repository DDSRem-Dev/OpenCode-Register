import asyncio
from typing import Awaitable, Callable, Dict, Optional

from browser.base import GitHubCleanupClient
from browser.cloakbrowser_client import CloakBrowserClient
from browser.github_cleanup import GitHubAccountCleanup
from config.pool_service import OpenCodePoolConfigService
from engine.cleanup import AccountCleanupFlow
from engine.cleanup_models import AccountCleanupSession, AccountCleanupStatus
from storage.repositories import AccountNotFoundError
from storage.service import AccountVaultService


class CleanupIdentityMismatchError(Exception):
    """
    用户确认的 GitHub 用户名与账号记录不一致异常
    """


class CleanupFlowNotFoundError(Exception):
    """
    账号清理流程不存在异常
    """


class CleanupFlowBusyError(Exception):
    """
    账号已有进行中的清理流程异常
    """


class AccountCleanupService:
    """
    Phase 7 GitHub 账号清理流程生命周期服务
    """

    def __init__(
        self,
        vault_service: AccountVaultService,
        pool_service: OpenCodePoolConfigService,
        client_factory: Optional[Callable[[], GitHubCleanupClient]] = None,
        account_session_cleanup: Optional[Callable[[str], Awaitable[None]]] = None,
    ) -> None:
        """
        初始化账号清理生命周期服务

        :param vault_service (AccountVaultService): 本地加密账号库服务
        :param pool_service (OpenCodePoolConfigService): 号池配置协调服务
        :param client_factory (Callable): 可选 GitHub 清理浏览器边界工厂
        :param account_session_cleanup (Callable): 可选的同账号浏览器会话清理函数
        """

        self._vault_service = vault_service
        self._pool_service = pool_service
        self._flows: Dict[str, AccountCleanupFlow] = {}
        self._account_locks: Dict[str, asyncio.Lock] = {}
        self._account_session_cleanup = account_session_cleanup
        self._cloakbrowser_client: Optional[CloakBrowserClient] = None
        if client_factory is None:
            self._cloakbrowser_client = CloakBrowserClient()
            self._client_factory = self._create_client
        else:
            self._client_factory = client_factory

    async def start(self, account_id: str, confirmed_username: str) -> AccountCleanupSession:
        """
        对用户明确确认的精确账号启动或重试清理

        :param account_id (str): 待清理账号稳定 UUID
        :param confirmed_username (str): 用户重新输入的 GitHub 用户名

        :return AccountCleanupSession: 清理流程权威快照

        :raises AccountNotFoundError: 账号记录不存在
        :raises CleanupIdentityMismatchError: 确认用户名与目标不一致
        :raises CleanupFlowBusyError: 同账号已有活动清理流程
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            return await self._start_locked(account_id, confirmed_username)

    async def _start_locked(self, account_id: str, confirmed_username: str) -> AccountCleanupSession:
        account = await asyncio.to_thread(self._vault_service.get_account, account_id)
        if account is None:
            raise AccountNotFoundError("账号记录不存在")
        if confirmed_username != account.github_username:
            raise CleanupIdentityMismatchError("GitHub 用户名确认不一致")
        await self._close_other_account_session(account_id)
        existing = self._flows.get(account_id)
        if existing is not None and existing.snapshot().status not in {
            AccountCleanupStatus.DONE,
            AccountCleanupStatus.ERROR,
            AccountCleanupStatus.CANCELLED,
        }:
            raise CleanupFlowBusyError(account_id)
        persisted_state = await asyncio.to_thread(self._vault_service.begin_cleanup, account_id)
        flow = AccountCleanupFlow(account, self._client_factory(), self._vault_service, self._pool_service)
        self._flows[account_id] = flow
        return await flow.start(persisted_state)

    async def resume(self, account_id: str) -> AccountCleanupSession:
        """
        用户完成人工操作后继续指定账号清理

        :param account_id (str): 待清理账号稳定 UUID

        :return AccountCleanupSession: 清理流程权威快照

        :raises CleanupFlowNotFoundError: 清理流程不存在
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            await self._close_other_account_session(account_id)
            return await self._flow(account_id).resume()

    async def cancel(self, account_id: str) -> AccountCleanupSession:
        """
        取消尚未完成远端删除的账号清理

        :param account_id (str): 待清理账号稳定 UUID

        :return AccountCleanupSession: 取消后的清理快照

        :raises CleanupFlowNotFoundError: 清理流程不存在
        """

        async with self._account_locks.setdefault(account_id, asyncio.Lock()):
            return await self._flow(account_id).cancel()

    def snapshot(self, account_id: str) -> AccountCleanupSession:
        """
        获取账号清理权威快照

        :param account_id (str): 待清理账号稳定 UUID

        :return AccountCleanupSession: 当前清理快照

        :raises CleanupFlowNotFoundError: 清理流程不存在
        """

        return self._flow(account_id).snapshot()

    async def close(self) -> None:
        """
        关闭全部账号清理浏览器资源

        :return None: 无返回值
        """

        for flow in self._flows.values():
            await flow.close()
        if self._cloakbrowser_client is not None:
            await self._cloakbrowser_client.close()

    def _flow(self, account_id: str) -> AccountCleanupFlow:
        flow = self._flows.get(account_id)
        if flow is None:
            raise CleanupFlowNotFoundError(account_id)
        return flow

    def _create_client(self) -> GitHubCleanupClient:
        if self._cloakbrowser_client is None:
            raise RuntimeError("GitHub 清理浏览器管理器不可用")
        return GitHubAccountCleanup(self._cloakbrowser_client.create_session())

    async def _close_other_account_session(self, account_id: str) -> None:
        """
        在账号清理边界前关闭同账号的其他浏览器会话

        :param account_id (str): 账号稳定 UUID

        :return None: 无返回值
        """

        if self._account_session_cleanup is not None:
            await self._account_session_cleanup(account_id)
