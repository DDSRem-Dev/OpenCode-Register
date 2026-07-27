from pathlib import Path

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from api.routes import create_router
from browser.initializer import BrowserInitializer
from engine.service import CreateAccountService
from main import create_app
from storage.service import AccountVaultService


class FakeScreenshotService(CreateAccountService):
    """
    截图 HTTP 响应测试服务
    """

    def __init__(self) -> None:
        """
        初始化无外部资源的截图服务替身
        """

    def screenshot(self, flow_id: str, screenshot_id: str) -> bytes:
        """
        返回固定的已遮罩 PNG

        :param flow_id (str): 流程稳定 UUID
        :param screenshot_id (str): 截图稳定 UUID

        :return bytes: 虚构已遮罩 PNG
        """

        assert flow_id == "00000000-0000-4000-8000-000000000093"
        assert screenshot_id == "00000000-0000-4000-8000-000000000094"
        return b"\x89PNG\r\n\x1a\nmasked-api-screenshot"


def test_create_account_contract_has_no_request_body() -> None:
    """
    验证创建流程接口不再暴露邮箱 provider 配置请求体
    """

    schema = create_app().openapi()
    operation = schema["paths"]["/api/accounts"]["post"]

    assert "requestBody" not in operation


@pytest.mark.anyio
async def test_missing_flow_uses_stable_error_envelope() -> None:
    """
    验证不存在的流程返回稳定且净化的错误信封
    """

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.get("/api/flow/missing-flow")

    assert response.status_code == 404
    assert response.json() == {
        "code": "flow_not_found",
        "message": "账号流程不存在",
        "details": None,
    }


@pytest.mark.anyio
async def test_manual_confirmation_must_be_explicit() -> None:
    """
    验证人工操作接口拒绝未确认请求且不接收验证码字段
    """

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/flow/missing-flow/manual-input",
            json={"confirmed": False},
        )

    assert response.status_code == 400
    assert response.json()["code"] == "manual_confirmation_required"


@pytest.mark.anyio
async def test_manual_api_key_must_match_opencode_format() -> None:
    """
    验证人工 API Key 在进入流程服务前完成格式校验
    """

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/flow/missing-flow/manual-input",
            json={"confirmed": True, "api_key": "not-an-api-key"},
        )

    assert response.status_code == 422
    assert response.json() == {
        "code": "request_validation_failed",
        "message": "请求数据格式无效",
        "details": None,
    }
    assert "not-an-api-key" not in response.text


@pytest.mark.anyio
async def test_valid_manual_api_key_reaches_flow_boundary_without_exposure() -> None:
    """
    验证合法人工 API Key 可通过请求校验且不会出现在错误响应
    """

    api_key = "sk-" + "m" * 64
    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as client:
        response = await client.post(
            "/api/flow/missing-flow/manual-input",
            json={"confirmed": True, "api_key": api_key},
        )

    assert response.status_code == 404
    assert response.json()["code"] == "flow_not_found"
    assert api_key not in response.text


@pytest.mark.anyio
async def test_sanitized_screenshot_response_is_png_and_never_cacheable(tmp_path: Path) -> None:
    """
    验证截图端点仅返回 PNG 且禁止客户端缓存
    """

    app = FastAPI()
    app.include_router(
        create_router(
            FakeScreenshotService(),
            AccountVaultService(tmp_path / "accounts.db"),
            "sandbox",
            app.version,
            BrowserInitializer(lambda: str(tmp_path / "chrome")),
        ),
        prefix="/api",
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get(
            "/api/flow/00000000-0000-4000-8000-000000000093/screenshot/00000000-0000-4000-8000-000000000094"
        )

    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.headers["cache-control"] == "no-store"
    assert response.content.startswith(b"\x89PNG")
