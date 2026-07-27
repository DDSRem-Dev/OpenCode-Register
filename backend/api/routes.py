import re
from typing import Literal, Optional

from fastapi import APIRouter, Response, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

from api.errors import ApiError, ErrorResponse
from browser.initializer import BrowserInitializer
from engine.flow import FlowTransitionError
from engine.models import FlowSession
from engine.service import CreateAccountService, FlowBusyError, FlowNotFoundError
from storage.screenshots import ScreenshotStoreError
from storage.service import AccountVaultService

_API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9]{64}$")


class HealthResponse(BaseModel):
    """
    本地服务健康状态响应
    """

    status: str = Field(..., description="服务状态")
    service: str = Field(..., description="服务名称")
    version: str = Field(..., description="服务版本")
    storage_mode: Literal["system", "sandbox"] = Field(..., description="本地文件写入模式")
    browser_status: Literal["initializing", "ready", "error"] = Field(..., description="浏览器初始化状态")


class ManualInputRequest(BaseModel):
    """
    人工操作确认请求

    不接收验证码或密码；仅在自动复制失败时短暂接收经过格式约束的 OpenCode API Key
    """

    model_config = ConfigDict(extra="forbid")

    confirmed: bool = Field(..., description="用户是否确认已完成浏览器中的人工操作")
    api_key: Optional[SecretStr] = Field(
        default=None,
        description="自动复制失败时用户手动提交的 OpenCode API Key",
    )

    @field_validator("api_key")
    @classmethod
    def validate_api_key(cls, value: Optional[SecretStr]) -> Optional[SecretStr]:
        """
        校验可选 OpenCode API Key 且保持错误输出脱敏

        :param value (SecretStr): 待校验的可选 API Key

        :return Optional[SecretStr]: 格式有效的可选 API Key

        :raises ValueError: API Key 格式无效
        """

        if value is not None and _API_KEY_PATTERN.fullmatch(value.get_secret_value()) is None:
            raise ValueError("OpenCode API Key 格式无效")
        return value


def create_router(
    service: CreateAccountService,
    vault_service: AccountVaultService,
    storage_mode: Literal["system", "sandbox"],
    application_version: str,
    browser_initializer: BrowserInitializer,
) -> APIRouter:
    """
    创建绑定账号流程服务的 HTTP 路由

    :param service (CreateAccountService): 账号创建流程生命周期服务
    :param vault_service (AccountVaultService): 本地加密账号库服务
    :param storage_mode (Literal): 本地文件写入模式
    :param application_version (str): 应用程序版本
    :param browser_initializer (BrowserInitializer): 浏览器初始化管理器

    :return APIRouter: 配置完成的 API 路由
    """

    router = APIRouter()

    @router.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        """
        获取本地服务健康状态

        :return HealthResponse: 当前服务状态与版本
        """

        return HealthResponse(
            status="ok",
            service="opencode-register-backend",
            version=application_version,
            storage_mode=storage_mode,
            browser_status=browser_initializer.status.value,
        )

    @router.post("/browser/initialize", response_model=HealthResponse, tags=["system"])
    async def initialize_browser() -> HealthResponse:
        """
        启动或重试 CloakBrowser 浏览器初始化

        :return HealthResponse: 启动初始化后的服务状态
        """

        browser_initializer.start(retry_failed=True)
        return HealthResponse(
            status="ok",
            service="opencode-register-backend",
            version=application_version,
            storage_mode=storage_mode,
            browser_status=browser_initializer.status.value,
        )

    @router.post(
        "/accounts",
        response_model=FlowSession,
        status_code=status.HTTP_202_ACCEPTED,
        responses={423: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def create_account() -> FlowSession:
        """
        创建并异步启动账号注册流程

        :return FlowSession: 新流程的初始权威快照
        """

        if not vault_service.is_unlocked:
            raise ApiError(423, "vault_locked", "请先使用主密码解锁本地账号库")
        return service.create()

    @router.get(
        "/flow/{flow_id}",
        response_model=FlowSession,
        responses={404: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def get_flow(flow_id: str) -> FlowSession:
        """
        获取账号创建流程的权威快照

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 当前流程快照
        """

        return _snapshot_or_404(service, flow_id)

    @router.get(
        "/flow/{flow_id}/screenshot/{screenshot_id}",
        response_class=Response,
        responses={404: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def get_flow_screenshot(flow_id: str, screenshot_id: str) -> Response:
        """
        返回当前流程拥有的已遮罩人工介入截图

        :param flow_id (str): 流程稳定 UUID
        :param screenshot_id (str): 截图稳定 UUID

        :return Response: PNG 图片响应
        """

        try:
            screenshot = service.screenshot(flow_id, screenshot_id)
        except (FlowNotFoundError, ScreenshotStoreError) as error:
            raise ApiError(404, "flow_screenshot_not_found", "流程截图不存在") from error
        return Response(content=screenshot, media_type="image/png", headers={"Cache-Control": "no-store"})

    return router


def create_flow_control_router(service: CreateAccountService) -> APIRouter:
    """
    创建绑定暂停、恢复和取消操作的流程控制路由

    :param service (CreateAccountService): 账号创建流程生命周期服务

    :return APIRouter: 配置完成的流程控制路由
    """

    router = APIRouter()

    @router.post(
        "/flow/{flow_id}/resume",
        response_model=FlowSession,
        status_code=status.HTTP_202_ACCEPTED,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def resume_flow(flow_id: str) -> FlowSession:
        """
        恢复等待人工操作的流程

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 接受恢复请求时的流程快照
        """

        return await _resume_or_error(service, flow_id)

    @router.post(
        "/flow/{flow_id}/manual-input",
        response_model=FlowSession,
        status_code=status.HTTP_202_ACCEPTED,
        responses={400: {"model": ErrorResponse}, 404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def submit_manual_input(flow_id: str, request: ManualInputRequest) -> FlowSession:
        """
        确认用户已在浏览器完成必要的人工操作

        :param flow_id (str): 流程唯一标识
        :param request (ManualInputRequest): 人工操作确认

        :return FlowSession: 接受恢复请求时的流程快照
        """

        if not request.confirmed:
            raise ApiError(400, "manual_confirmation_required", "请先确认已完成人工操作")
        api_key = request.api_key.get_secret_value() if request.api_key is not None else None
        return await _resume_or_error(service, flow_id, api_key)

    @router.post(
        "/flow/{flow_id}/pause",
        response_model=FlowSession,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def pause_flow(flow_id: str) -> FlowSession:
        """
        请求账号创建流程在安全点暂停

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 接受暂停请求后的权威流程快照
        """

        try:
            return await service.pause(flow_id)
        except FlowNotFoundError as error:
            raise ApiError(404, "flow_not_found", "账号流程不存在") from error
        except FlowTransitionError as error:
            raise ApiError(409, "flow_state_conflict", "当前流程状态不可暂停") from error

    @router.post(
        "/flow/{flow_id}/cancel",
        response_model=FlowSession,
        responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
        tags=["flow"],
    )
    async def cancel_flow(flow_id: str, response: Response) -> FlowSession:
        """
        取消账号创建流程并释放浏览器和邮箱资源

        :param flow_id (str): 流程唯一标识
        :param response (Response): FastAPI 响应对象

        :return FlowSession: 取消后的权威流程快照
        """

        del response
        try:
            return await service.cancel(flow_id)
        except FlowNotFoundError as error:
            raise ApiError(404, "flow_not_found", "账号流程不存在") from error
        except FlowTransitionError as error:
            raise ApiError(409, "flow_state_conflict", "当前流程状态不可取消") from error

    return router


def _snapshot_or_404(service: CreateAccountService, flow_id: str) -> FlowSession:
    try:
        return service.snapshot(flow_id)
    except FlowNotFoundError as error:
        raise ApiError(404, "flow_not_found", "账号流程不存在") from error


async def _resume_or_error(
    service: CreateAccountService,
    flow_id: str,
    api_key: Optional[str] = None,
) -> FlowSession:
    """
    恢复人工流程并映射稳定 API 错误

    :param service (CreateAccountService): 账号流程生命周期服务
    :param flow_id (str): 流程唯一标识
    :param api_key (str): 可选的手动 API Key

    :return FlowSession: 接受恢复请求时的流程快照

    :raises ApiError: 流程不存在或当前状态不可恢复
    """

    try:
        return await service.resume(flow_id, api_key)
    except FlowNotFoundError as error:
        raise ApiError(404, "flow_not_found", "账号流程不存在") from error
    except (FlowBusyError, FlowTransitionError) as error:
        raise ApiError(409, "flow_state_conflict", "当前流程状态不可恢复") from error
