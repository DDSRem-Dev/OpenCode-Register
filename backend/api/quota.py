from typing import List

from fastapi import APIRouter
from pydantic import BaseModel, Field

from api.errors import ApiError, ErrorResponse
from engine.quota_service import QuotaCheckService
from scheduler.models import QuotaRefreshResult
from storage.models import AccountStatus
from storage.repositories import AccountNotFoundError
from storage.service import VaultLockedError


class QuotaRefreshListResponse(BaseModel):
    """
    全部账号额度刷新结果
    """

    results: List[QuotaRefreshResult] = Field(..., description="各账号额度刷新结果")


class AccountStatusResponse(BaseModel):
    """
    账号状态更新响应
    """

    account_id: str = Field(..., description="账号稳定 UUID")
    status: AccountStatus = Field(..., description="更新后的账号状态")


def create_quota_router(
    quota_service: QuotaCheckService,
) -> APIRouter:
    """
    创建 Phase 7 额度与状态路由

    :param quota_service (QuotaCheckService): 额度检查业务服务

    :return APIRouter: 配置完成的额度路由
    """

    router = APIRouter()

    @router.post(
        "/accounts/{account_id}/quota/refresh",
        response_model=QuotaRefreshResult,
        responses={404: {"model": ErrorResponse}, 423: {"model": ErrorResponse}},
        tags=["quota"],
    )
    async def refresh_account_quota(account_id: str) -> QuotaRefreshResult:
        """
        显式刷新一个账号的 OpenCode Go 额度

        :param account_id (str): 账号稳定 UUID

        :return QuotaRefreshResult: 本次额度检查结果
        """

        try:
            return await quota_service.refresh_account(account_id)
        except AccountNotFoundError as error:
            raise ApiError(404, "account_not_found", "账号不存在") from error
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error

    @router.post(
        "/quota/refresh",
        response_model=QuotaRefreshListResponse,
        responses={423: {"model": ErrorResponse}},
        tags=["quota"],
    )
    async def refresh_all_quotas() -> QuotaRefreshListResponse:
        """
        显式刷新全部账号的 OpenCode Go 额度

        :return QuotaRefreshListResponse: 全部账号的刷新结果
        """

        try:
            results = await quota_service.refresh_all()
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        return QuotaRefreshListResponse(results=results)

    @router.post(
        "/accounts/{account_id}/mark-exhausted",
        response_model=AccountStatusResponse,
        responses={404: {"model": ErrorResponse}, 423: {"model": ErrorResponse}},
        tags=["quota"],
    )
    async def mark_account_exhausted(account_id: str) -> AccountStatusResponse:
        """
        按用户明确意图把账号标记为额度已用尽

        :param account_id (str): 账号稳定 UUID

        :return AccountStatusResponse: 更新后的账号状态
        """

        try:
            account = await quota_service.mark_exhausted(account_id)
        except AccountNotFoundError as error:
            raise ApiError(404, "account_not_found", "账号不存在") from error
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        return AccountStatusResponse(account_id=account.uuid, status=account.status)

    return router
