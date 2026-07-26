import asyncio

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from api.errors import ApiError, ErrorResponse
from engine.completion import AccountCompletionError, AccountCompletionService
from storage.models import AutomaticConfigurationSettings
from storage.service import AccountVaultService, InvalidConfigurationSettingsError


class AutomaticConfigurationRequest(BaseModel):
    """
    自动配置开关更新请求
    """

    model_config = ConfigDict(extra="forbid")

    auto_configure_opencode: bool = Field(..., description="新增账号时是否自动写入 OpenCode 配置")
    auto_configure_omo: bool = Field(..., description="新增账号时是否自动写入 Oh My OpenCode 配置")


class AutomaticConfigurationResponse(BaseModel):
    """
    自动配置设置及待应用账号数量响应
    """

    auto_configure_opencode: bool = Field(..., description="是否自动写入 OpenCode 配置")
    auto_configure_omo: bool = Field(..., description="是否自动写入 Oh My OpenCode 配置")
    opencode_pending_count: int = Field(..., ge=0, description="尚未写入 OpenCode 配置的账号数量")
    omo_pending_count: int = Field(..., ge=0, description="尚未写入 Oh My OpenCode 配置的账号数量")
    applied_count: int = Field(default=0, ge=0, description="本次完成配置应用的账号数量")


def create_settings_router(
    vault_service: AccountVaultService,
    completion_service: AccountCompletionService,
) -> APIRouter:
    """
    创建自动配置设置与应用路由

    :param vault_service (AccountVaultService): 本地账号库服务
    :param completion_service (AccountCompletionService): 账号配置协调服务

    :return APIRouter: 配置完成的设置路由
    """

    router = APIRouter()

    @router.get(
        "/settings",
        response_model=AutomaticConfigurationResponse,
        responses={500: {"model": ErrorResponse}},
        tags=["settings"],
    )
    async def get_settings() -> AutomaticConfigurationResponse:
        """
        读取自动配置开关和待应用数量

        :return AutomaticConfigurationResponse: 当前自动配置状态
        """

        return await _settings_response(vault_service)

    @router.put(
        "/settings",
        response_model=AutomaticConfigurationResponse,
        responses={409: {"model": ErrorResponse}, 500: {"model": ErrorResponse}},
        tags=["settings"],
    )
    async def update_settings(request: AutomaticConfigurationRequest) -> AutomaticConfigurationResponse:
        """
        更新自动配置开关并保持 Oh My OpenCode 对 OpenCode 的依赖

        :param request (AutomaticConfigurationRequest): 目标自动配置开关

        :return AutomaticConfigurationResponse: 已保存的自动配置状态
        """

        if request.auto_configure_omo and not request.auto_configure_opencode:
            raise ApiError(409, "omo_configuration_requires_opencode", "Oh My OpenCode 自动配置依赖 OpenCode")
        try:
            await asyncio.to_thread(
                vault_service.update_automatic_configuration,
                AutomaticConfigurationSettings(**request.model_dump()),
            )
        except InvalidConfigurationSettingsError as error:
            raise ApiError(500, "configuration_settings_invalid", "自动配置设置无法保存") from error
        return await _settings_response(vault_service)

    @router.post(
        "/settings/apply",
        response_model=AutomaticConfigurationResponse,
        responses={409: {"model": ErrorResponse}, 423: {"model": ErrorResponse}},
        tags=["settings"],
    )
    async def apply_settings() -> AutomaticConfigurationResponse:
        """
        按当前开关为已有账号补写待应用配置

        :return AutomaticConfigurationResponse: 应用后的自动配置状态
        """

        if not vault_service.is_unlocked:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库")
        try:
            applied_count = await completion_service.apply_pending_configuration()
        except AccountCompletionError as error:
            raise ApiError(409, "account_configuration_apply_failed", str(error)) from error
        return await _settings_response(vault_service, applied_count)

    return router


async def _settings_response(
    vault_service: AccountVaultService,
    applied_count: int = 0,
) -> AutomaticConfigurationResponse:
    try:
        settings = await asyncio.to_thread(vault_service.get_automatic_configuration)
        opencode_pending, omo_pending = await asyncio.to_thread(vault_service.count_pending_configuration)
    except InvalidConfigurationSettingsError as error:
        raise ApiError(500, "configuration_settings_invalid", "自动配置设置无法读取") from error
    return AutomaticConfigurationResponse(
        auto_configure_opencode=settings.auto_configure_opencode,
        auto_configure_omo=settings.auto_configure_omo,
        opencode_pending_count=opencode_pending,
        omo_pending_count=omo_pending,
        applied_count=applied_count,
    )
