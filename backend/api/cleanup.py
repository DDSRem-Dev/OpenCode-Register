from fastapi import APIRouter, status
from pydantic import BaseModel, ConfigDict, Field

from api.errors import ApiError, ErrorResponse
from engine.cleanup import AccountCleanupConflictError
from engine.cleanup_models import AccountCleanupSession
from engine.cleanup_service import (
    AccountCleanupService,
    CleanupFlowBusyError,
    CleanupFlowNotFoundError,
    CleanupIdentityMismatchError,
)
from storage.repositories import AccountNotFoundError
from storage.service import VaultLockedError


class StartAccountCleanupRequest(BaseModel):
    """
    GitHub 账号清理启动请求
    """

    model_config = ConfigDict(extra="forbid")

    confirmed_username: str = Field(..., min_length=1, max_length=39, description="用户再次输入的目标 GitHub 用户名")


class ConfirmAccountCleanupRequest(BaseModel):
    """
    GitHub 人工安全验证步骤确认请求
    """

    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(..., description="用户是否确认已完成当前浏览器安全验证")


def create_cleanup_router(service: AccountCleanupService) -> APIRouter:
    """
    创建 Phase 7 GitHub 账号清理路由

    :param service (AccountCleanupService): 账号清理流程服务

    :return APIRouter: 配置完成的清理路由
    """

    router = APIRouter()

    @router.delete(
        "/accounts/{account_id}",
        response_model=AccountCleanupSession,
        status_code=status.HTTP_202_ACCEPTED,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}, 423: {"model": ErrorResponse}},
        tags=["cleanup"],
    )
    async def start_account_cleanup(
        account_id: str,
        request: StartAccountCleanupRequest,
    ) -> AccountCleanupSession:
        """
        对用户再次确认的精确 GitHub 目标启动删除流程

        :param account_id (str): 账号稳定 UUID
        :param request (StartAccountCleanupRequest): 精确用户名确认请求

        :return AccountCleanupSession: 当前清理流程快照
        """

        return await _start_or_error(service, account_id, request.confirmed_username)

    @router.get(
        "/accounts/{account_id}/cleanup",
        response_model=AccountCleanupSession,
        responses={404: {"model": ErrorResponse}},
        tags=["cleanup"],
    )
    async def get_account_cleanup(account_id: str) -> AccountCleanupSession:
        """
        获取账号清理流程权威快照

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupSession: 当前清理流程快照
        """

        return _snapshot_or_error(service, account_id)

    @router.post(
        "/accounts/{account_id}/cleanup/confirm",
        response_model=AccountCleanupSession,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["cleanup"],
    )
    async def confirm_account_cleanup(
        account_id: str,
        request: ConfirmAccountCleanupRequest,
    ) -> AccountCleanupSession:
        """
        用户完成 GitHub 安全验证后继续自动删除和本地清理

        :param account_id (str): 账号稳定 UUID
        :param request (ConfirmAccountCleanupRequest): 安全验证确认请求

        :return AccountCleanupSession: 当前清理流程快照
        """

        if not request.confirmed:
            raise ApiError(400, "manual_confirmation_required", "请先确认已完成浏览器中的安全验证")
        return await _resume_or_error(service, account_id)

    @router.post(
        "/accounts/{account_id}/cleanup/cancel",
        response_model=AccountCleanupSession,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["cleanup"],
    )
    async def cancel_account_cleanup(account_id: str) -> AccountCleanupSession:
        """
        取消尚未完成远端删除的账号清理流程

        :param account_id (str): 账号稳定 UUID

        :return AccountCleanupSession: 取消后的流程快照
        """

        return await _cancel_or_error(service, account_id)

    return router


async def _start_or_error(
    service: AccountCleanupService,
    account_id: str,
    confirmed_username: str,
) -> AccountCleanupSession:
    try:
        return await service.start(account_id, confirmed_username)
    except AccountNotFoundError as error:
        raise ApiError(404, "account_not_found", "账号不存在") from error
    except (CleanupIdentityMismatchError, CleanupFlowBusyError) as error:
        raise ApiError(409, "account_cleanup_conflict", "GitHub 删除目标确认不匹配或已有清理流程") from error
    except VaultLockedError as error:
        raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error


def _snapshot_or_error(service: AccountCleanupService, account_id: str) -> AccountCleanupSession:
    try:
        return service.snapshot(account_id)
    except CleanupFlowNotFoundError as error:
        raise ApiError(404, "account_cleanup_not_found", "账号清理流程不存在") from error


async def _resume_or_error(service: AccountCleanupService, account_id: str) -> AccountCleanupSession:
    try:
        return await service.resume(account_id)
    except CleanupFlowNotFoundError as error:
        raise ApiError(404, "account_cleanup_not_found", "账号清理流程不存在") from error
    except AccountCleanupConflictError as error:
        raise ApiError(409, "account_cleanup_conflict", "当前账号清理流程不可继续") from error


async def _cancel_or_error(service: AccountCleanupService, account_id: str) -> AccountCleanupSession:
    try:
        return await service.cancel(account_id)
    except CleanupFlowNotFoundError as error:
        raise ApiError(404, "account_cleanup_not_found", "账号清理流程不存在") from error
    except AccountCleanupConflictError as error:
        raise ApiError(409, "account_cleanup_conflict", "当前账号清理流程不可取消") from error
