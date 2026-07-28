import asyncio
import sqlite3
from typing import Union

from browser.base import GitHubCleanupClient
from browser.models import GitHubCleanupPageResult, GitHubCleanupPageStatus
from config.errors import ConfigFileError
from config.pool_service import OpenCodePoolConfigService
from engine.cleanup_models import AccountCleanupSession, AccountCleanupStatus
from engine.models import ManualIntervention, ManualInterventionReason
from storage.models import Account, AccountCleanupState, AccountStatus, PendingAccount
from storage.repositories import AccountNotFoundError
from storage.service import AccountVaultService


class AccountCleanupConflictError(Exception):
    """
    GitHub 账号清理目标或当前状态冲突异常
    """


class AccountCleanupFlow:
    """
    单个 GitHub 账号确认删除与本地清理状态机
    """

    def __init__(
        self,
        account: Union[Account, PendingAccount],
        github_client: GitHubCleanupClient,
        vault_service: AccountVaultService,
        pool_service: OpenCodePoolConfigService,
    ) -> None:
        """
        初始化账号清理状态机

        :param account (Union): 待清理完整或未完成账号记录
        :param github_client (GitHubCleanupClient): GitHub 删除浏览器边界
        :param vault_service (AccountVaultService): 本地加密账号库服务
        :param pool_service (OpenCodePoolConfigService): OpenCode 号池配置协调服务
        """

        self._account = account
        self._github_client = github_client
        self._vault_service = vault_service
        self._pool_service = pool_service
        self._session = AccountCleanupSession(
            account_id=account.uuid,
            github_username=account.github_username,
            status=AccountCleanupStatus.STARTING,
        )

    def snapshot(self) -> AccountCleanupSession:
        """
        返回不包含凭据的当前清理快照

        :return AccountCleanupSession: 当前清理流程快照副本
        """

        return self._session.model_copy(deep=True)

    async def start(self, persisted_state: AccountCleanupState) -> AccountCleanupSession:
        """
        启动远端删除或恢复已经验证的本地清理

        :param persisted_state (AccountCleanupState): 当前持久化清理状态

        :return AccountCleanupSession: 操作后的流程快照
        """

        if persisted_state == AccountCleanupState.REMOTE_DELETED:
            return await self._finish_local_cleanup()
        if self._account.github_auth_state is None:
            return await self._fail("github_cleanup_auth_required", "账号尚未保存 GitHub 登录状态，需要重新授权")
        result = await self._github_client.start_cleanup(
            self._account.github_username,
            self._account.github_password,
            self._account.github_auth_state,
        )
        return await self._apply_browser_result(result)

    async def resume(self) -> AccountCleanupSession:
        """
        用户处理安全验证后继续清理

        :return AccountCleanupSession: 操作后的流程快照

        :raises AccountCleanupConflictError: 当前流程状态不可继续
        """

        if self._session.status != AccountCleanupStatus.MANUAL_REQUIRED:
            raise AccountCleanupConflictError("当前 GitHub 清理流程不可继续")
        return await self._apply_browser_result(await self._github_client.inspect_after_manual())

    async def cancel(self) -> AccountCleanupSession:
        """
        在远端删除确认前取消清理并回收浏览器

        :return AccountCleanupSession: 取消后的流程快照

        :raises AccountCleanupConflictError: 远端删除已验证或流程已经结束
        """

        if self._session.status not in {
            AccountCleanupStatus.STARTING,
            AccountCleanupStatus.MANUAL_REQUIRED,
        }:
            raise AccountCleanupConflictError("当前 GitHub 清理流程不可取消")
        await asyncio.to_thread(self._vault_service.cancel_cleanup, self._account.uuid)
        await self._github_client.close()
        self._session.status = AccountCleanupStatus.CANCELLED
        self._session.manual_intervention = None
        return self.snapshot()

    async def close(self) -> None:
        """
        回收清理流程浏览器资源

        :return None: 无返回值
        """

        await self._github_client.close()

    async def _apply_browser_result(self, result: GitHubCleanupPageResult) -> AccountCleanupSession:
        if result.status == GitHubCleanupPageStatus.DELETED:
            await asyncio.to_thread(self._vault_service.mark_remote_deleted, self._account.uuid)
            return await self._finish_local_cleanup()
        if result.status == GitHubCleanupPageStatus.MANUAL_REQUIRED:
            reason = result.manual_reason or ManualInterventionReason.UNKNOWN_BLOCK
            self._session.status = AccountCleanupStatus.MANUAL_REQUIRED
            self._session.manual_intervention = _manual_intervention(reason)
            return self.snapshot()
        if result.status == GitHubCleanupPageStatus.AUTH_REQUIRED:
            return await self._fail(
                result.error_code or "github_cleanup_auth_required",
                result.error_message or "保存的 GitHub 登录状态已失效，需要重新授权",
            )
        if result.status == GitHubCleanupPageStatus.INVALID:
            if isinstance(self._account, PendingAccount):
                await asyncio.to_thread(
                    self._vault_service.update_pending_status,
                    self._account.uuid,
                    AccountStatus.INVALID,
                )
            else:
                await asyncio.to_thread(self._vault_service.update_status, self._account.uuid, AccountStatus.INVALID)
            return await self._fail("github_cleanup_credentials_invalid", "GitHub 登录凭据已失效，未删除本地账号")
        return await self._fail(
            result.error_code or "github_cleanup_failed",
            result.error_message or "GitHub 账号清理失败",
        )

    async def _finish_local_cleanup(self) -> AccountCleanupSession:
        self._session.status = AccountCleanupStatus.LOCAL_CLEANUP
        self._session.manual_intervention = None
        if isinstance(self._account, PendingAccount):
            try:
                await asyncio.to_thread(self._vault_service.delete_pending_account, self._account.uuid)
            except (AccountNotFoundError, sqlite3.Error):
                return await self._fail("account_local_cleanup_failed", "GitHub 已删除，但本地记录清理失败，可重试")
            await self._github_client.close()
            self._session.status = AccountCleanupStatus.DONE
            self._session.error_code = None
            self._session.error_message = None
            return self.snapshot()
        stored_accounts = await asyncio.to_thread(self._vault_service.list_complete_accounts)
        remaining_accounts = [account for account in stored_accounts if account.uuid != self._account.uuid]
        try:
            config_result = await self._pool_service.remove_account(self._account, remaining_accounts)
        except ConfigFileError:
            return await self._fail("account_config_cleanup_failed", "GitHub 已删除，但本地号池配置清理失败，可重试")
        try:
            await asyncio.to_thread(
                self._vault_service.delete_and_promote,
                self._account.uuid,
                config_result.promoted_account_id,
            )
        except (AccountNotFoundError, sqlite3.Error):
            try:
                await self._pool_service.rollback_account_removal(config_result)
            except ConfigFileError:
                return await self._fail(
                    "account_local_cleanup_rollback_failed",
                    "GitHub 已删除，但本地记录清理及配置回滚失败",
                )
            return await self._fail("account_local_cleanup_failed", "GitHub 已删除，但本地记录清理失败，可重试")
        await self._github_client.close()
        self._session.status = AccountCleanupStatus.DONE
        self._session.promoted_account_id = config_result.promoted_account_id
        self._session.error_code = None
        self._session.error_message = None
        return self.snapshot()

    async def _fail(self, code: str, message: str) -> AccountCleanupSession:
        await self._github_client.close()
        self._session.status = AccountCleanupStatus.ERROR
        self._session.manual_intervention = None
        self._session.error_code = code
        self._session.error_message = message
        return self.snapshot()


def _manual_intervention(reason: ManualInterventionReason) -> ManualIntervention:
    if reason == ManualInterventionReason.CAPTCHA:
        return ManualIntervention(
            reason=reason,
            title="GitHub 需要人工验证",
            instruction="请在可见浏览器中完成 CAPTCHA 或风险验证，完成后返回应用继续。",
        )
    if reason == ManualInterventionReason.PHONE_VERIFICATION:
        return ManualIntervention(
            reason=reason,
            title="GitHub 需要身份验证",
            instruction="请在可见浏览器中完成二次或设备验证，完成后返回应用继续。",
        )
    return ManualIntervention(
        reason=reason,
        title="GitHub 页面需要人工处理",
        instruction="请在可见浏览器中完成当前阻断步骤，确认目标无误后返回应用继续。",
    )
