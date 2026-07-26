import asyncio
import base64
import binascii
from datetime import datetime
from typing import Awaitable, Callable, List, Optional

from fastapi import APIRouter, Response
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_serializer

from api.errors import ApiError, ErrorResponse
from engine.completion import AccountCompletionError
from storage.bundles import BundleValidationError
from storage.models import Account, AccountStatus, AccountSummary, QuotaInvalidReason
from storage.repositories import AccountAlreadyExistsError
from storage.service import (
    AccountVaultService,
    InvalidMasterPasswordError,
    MasterPasswordConfirmationError,
    VaultLockedError,
)


class UnlockVaultRequest(BaseModel):
    """
    本地账号库解锁请求
    """

    model_config = ConfigDict(extra="forbid")

    master_password: SecretStr = Field(..., min_length=8, max_length=256, description="仅驻留内存的主密码")
    master_password_confirmation: Optional[SecretStr] = Field(
        default=None,
        min_length=8,
        max_length=256,
        description="首次设置主密码时的确认值",
    )


class VaultStatusResponse(BaseModel):
    """
    本地账号库锁定状态响应
    """

    unlocked: bool = Field(..., description="当前进程是否已解锁账号库")
    initialized: bool = Field(..., description="账号库是否已经完成首次初始化")


class AccountSummaryResponse(BaseModel):
    """
    不包含敏感字段的账号列表条目
    """

    uuid: str = Field(..., description="账号稳定唯一标识")
    github_username: str = Field(..., description="GitHub 用户名")
    github_email_masked: str = Field(..., description="已脱敏的 GitHub 注册邮箱")
    opencode_provider_name: Optional[str] = Field(default=None, description="OpenCode provider 名称")
    opencode_workspace_id: Optional[str] = Field(default=None, description="OpenCode 工作区标识")
    status: AccountStatus = Field(..., description="账号状态")
    opencode_configured: bool = Field(..., description="OpenCode 配置是否已写入")
    omo_configured: bool = Field(..., description="Oh My OpenCode 配置是否已写入")
    quota_total: Optional[int] = Field(default=None, description="总额度")
    quota_used: Optional[int] = Field(default=None, description="已用额度")
    quota_updated_at: Optional[datetime] = Field(default=None, description="额度更新时间")
    quota_checked_at: Optional[datetime] = Field(default=None, description="最近一次确定性额度检查时间")
    quota_invalid_reason: Optional[QuotaInvalidReason] = Field(default=None, description="额度检查确认的失效原因")
    created_at: datetime = Field(..., description="本地记录创建时间")
    updated_at: datetime = Field(..., description="本地记录更新时间")
    notes: Optional[str] = Field(default=None, description="用户备注")


class AccountListResponse(BaseModel):
    """
    账号列表响应
    """

    accounts: List[AccountSummaryResponse] = Field(..., description="脱敏账号摘要列表")


class AccountApiKeyResponse(BaseModel):
    """
    用户明确请求复制的单账号 API Key 响应
    """

    account_id: str = Field(..., description="账号稳定唯一标识")
    api_key: SecretStr = Field(..., description="仅用于当前复制操作的 OpenCode API Key")

    @field_serializer("api_key", when_used="json")
    def serialize_api_key(self, api_key: SecretStr) -> str:
        """
        仅在专用响应 JSON 中输出 API Key 明文

        :param api_key (SecretStr): 内存中的 OpenCode API Key

        :return str: 供当前剪贴板操作使用的 API Key
        """

        return api_key.get_secret_value()


class ExportAccountsRequest(BaseModel):
    """
    加密账号包导出请求
    """

    model_config = ConfigDict(extra="forbid")

    bundle_password: SecretStr = Field(..., min_length=12, max_length=256, description="导出包独立密码")


class ImportAccountsRequest(BaseModel):
    """
    加密账号包导入请求
    """

    model_config = ConfigDict(extra="forbid")

    bundle_password: SecretStr = Field(..., min_length=12, max_length=256, description="导出包密码")
    bundle_base64: str = Field(..., min_length=1, max_length=14_000_000, description="Base64 编码的加密账号包")


class ImportAccountsResponse(BaseModel):
    """
    加密账号包导入结果
    """

    imported_count: int = Field(..., ge=0, description="成功导入的账号数量")


def create_accounts_router(vault_service: AccountVaultService) -> APIRouter:
    """
    创建本地账号库与列表路由

    :param vault_service (AccountVaultService): 本地账号库服务

    :return APIRouter: 配置完成的账号路由
    """

    router = APIRouter()

    @router.get("/vault", response_model=VaultStatusResponse, tags=["accounts"])
    def get_vault_status() -> VaultStatusResponse:
        """
        获取当前进程的账号库锁定状态

        :return VaultStatusResponse: 当前锁定状态
        """

        return VaultStatusResponse(
            unlocked=vault_service.is_unlocked,
            initialized=vault_service.is_initialized,
        )

    @router.post(
        "/vault/unlock",
        response_model=VaultStatusResponse,
        responses={400: {"model": ErrorResponse}, 401: {"model": ErrorResponse}},
        tags=["accounts"],
    )
    def unlock_vault(request: UnlockVaultRequest) -> VaultStatusResponse:
        """
        使用主密码解锁本地账号库

        :param request (UnlockVaultRequest): 主密码请求

        :return VaultStatusResponse: 解锁后的状态
        """

        try:
            vault_service.unlock(request.master_password, request.master_password_confirmation)
        except MasterPasswordConfirmationError as error:
            raise ApiError(400, "master_password_confirmation_mismatch", "两次输入的主密码不一致") from error
        except InvalidMasterPasswordError as error:
            raise ApiError(401, "invalid_master_password", "主密码不正确") from error
        return VaultStatusResponse(unlocked=True, initialized=True)

    @router.get(
        "/accounts",
        response_model=AccountListResponse,
        responses={423: {"model": ErrorResponse}},
        tags=["accounts"],
    )
    def list_accounts() -> AccountListResponse:
        """
        获取不包含密码或 API Key 的账号列表

        :return AccountListResponse: 账号摘要列表
        """

        try:
            accounts = vault_service.list_accounts()
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        return AccountListResponse(accounts=[_to_response(account) for account in accounts])

    @router.get(
        "/accounts/{account_id}/api-key",
        response_model=AccountApiKeyResponse,
        responses={404: {"model": ErrorResponse}, 423: {"model": ErrorResponse}},
        tags=["accounts"],
    )
    def get_account_api_key(account_id: str, response: Response) -> AccountApiKeyResponse:
        """
        按用户明确操作读取单个完整账号的 API Key

        :param account_id (str): 账号稳定 UUID
        :param response (Response): 用于设置禁止缓存响应头的 FastAPI 响应

        :return AccountApiKeyResponse: 仅供当前复制操作使用的 API Key
        """

        try:
            account = vault_service.get_account(account_id)
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        if not isinstance(account, Account):
            raise ApiError(404, "account_api_key_not_found", "账号 API Key 不存在")
        response.headers["Cache-Control"] = "no-store"
        return AccountApiKeyResponse(account_id=account.uuid, api_key=account.opencode_api_key)

    return router


def create_account_transfer_router(
    vault_service: AccountVaultService,
    import_handler: Optional[Callable[[bytes, SecretStr], Awaitable[int]]] = None,
) -> APIRouter:
    """
    创建账号加密导入与导出路由

    :param vault_service (AccountVaultService): 本地账号库服务
    :param import_handler (Callable): 可选的事务化配置重建与导入边界

    :return APIRouter: 配置完成的账号传输路由
    """

    router = APIRouter()

    @router.post(
        "/export",
        response_class=Response,
        responses={423: {"model": ErrorResponse}},
        tags=["accounts"],
    )
    def export_accounts(request: ExportAccountsRequest) -> Response:
        """
        导出全部账号为认证加密文件

        :param request (ExportAccountsRequest): 导出包密码请求

        :return Response: 二进制加密账号包
        """

        try:
            bundle = vault_service.export_accounts(request.bundle_password)
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        return Response(
            content=bundle,
            media_type="application/vnd.opencode-register.bundle",
            headers={"Content-Disposition": 'attachment; filename="opencode-accounts.ocrbundle"'},
        )

    @router.post(
        "/import",
        response_model=ImportAccountsResponse,
        responses={
            400: {"model": ErrorResponse, "description": "导入包无效"},
            409: {"model": ErrorResponse, "description": "账号冲突或目标配置重建失败"},
            423: {"model": ErrorResponse, "description": "账号库尚未解锁"},
        },
        tags=["accounts"],
    )
    async def import_accounts(request: ImportAccountsRequest) -> ImportAccountsResponse:
        """
        认证并事务导入加密账号包

        :param request (ImportAccountsRequest): 加密包和密码请求

        :return ImportAccountsResponse: 导入数量
        """

        try:
            bundle = base64.b64decode(request.bundle_base64, validate=True)
        except (binascii.Error, ValueError) as error:
            raise ApiError(400, "invalid_import_bundle", "导入包格式无效") from error
        try:
            if import_handler is None:
                imported_count = await asyncio.to_thread(
                    vault_service.import_accounts,
                    bundle,
                    request.bundle_password,
                )
            else:
                imported_count = await import_handler(bundle, request.bundle_password)
        except VaultLockedError as error:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库") from error
        except BundleValidationError as error:
            raise ApiError(400, "invalid_import_bundle", "导入包密码错误、已损坏或版本不受支持") from error
        except AccountAlreadyExistsError as error:
            raise ApiError(409, "account_import_conflict", "导入包包含已存在的账号或 provider") from error
        except AccountCompletionError as error:
            raise ApiError(409, "account_import_configuration_failed", "无法在目标机器重建账号配置") from error
        return ImportAccountsResponse(imported_count=imported_count)

    return router


def _to_response(account: AccountSummary) -> AccountSummaryResponse:
    return AccountSummaryResponse(
        uuid=account.uuid,
        github_username=account.github_username,
        github_email_masked=_mask_email(account.github_email),
        opencode_provider_name=account.opencode_provider_name,
        opencode_workspace_id=account.opencode_workspace_id,
        status=account.status,
        opencode_configured=account.opencode_configured,
        omo_configured=account.omo_configured,
        quota_total=account.quota_total,
        quota_used=account.quota_used,
        quota_updated_at=account.quota_updated_at,
        quota_checked_at=account.quota_checked_at,
        quota_invalid_reason=account.quota_invalid_reason,
        created_at=account.created_at,
        updated_at=account.updated_at,
        notes=account.notes,
    )


def _mask_email(email: str) -> str:
    local_part, separator, domain = email.partition("@")
    if not separator:
        return "***"
    visible_prefix = local_part[:1]
    return f"{visible_prefix}***@{domain}"
