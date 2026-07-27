from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from browser.initializer import BrowserInitializationError, BrowserInitializer
from main import create_app


@pytest.mark.anyio
async def test_health_endpoint(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证健康检查接口返回稳定响应
    """

    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(tmp_path))
    application_version = "test-version"
    browser_initializer = BrowserInitializer(lambda: str(tmp_path / "chrome"))
    application = create_app(application_version=application_version, browser_initializer=browser_initializer)
    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "opencode-register-backend",
        "version": application_version,
        "storage_mode": "sandbox",
        "browser_status": "initializing",
    }
    assert application.version == application_version


@pytest.mark.anyio
async def test_browser_initialization_endpoint_retries_failed_install(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证浏览器初始化接口可重试失败的下载任务
    """

    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(tmp_path))
    attempts = 0

    def install() -> str:
        """
        首次失败后返回测试浏览器路径

        :return str: 测试浏览器路径

        :raises RuntimeError: 首次测试安装失败
        """

        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("test download failure")
        return str(tmp_path / "chrome")

    initializer = BrowserInitializer(install)
    application = create_app(browser_initializer=initializer)
    with pytest.raises(BrowserInitializationError):
        await initializer.wait_until_ready()

    async with AsyncClient(transport=ASGITransport(app=application), base_url="http://test") as client:
        response = await client.post("/api/browser/initialize")
        await initializer.wait_until_ready()
        health_response = await client.get("/api/health")

    assert response.status_code == 200
    assert response.json()["browser_status"] == "initializing"
    assert health_response.json()["browser_status"] == "ready"


@pytest.mark.anyio
async def test_unexpected_api_error_uses_sanitized_envelope(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证未预期异常不会把内部错误内容带入 HTTP 响应
    """

    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(tmp_path))
    application = create_app()

    @application.get("/api/test-unexpected-error")
    async def raise_unexpected_error() -> None:
        """
        抛出仅供错误边界测试的内部异常

        :return None: 不会正常返回

        :raises RuntimeError: 固定测试异常
        """

        raise RuntimeError("sensitive internal test detail")

    async with AsyncClient(
        transport=ASGITransport(app=application, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        response = await client.get("/api/test-unexpected-error")

    assert response.status_code == 500
    assert response.json() == {
        "code": "internal_error",
        "message": "本地服务发生未预期错误",
        "details": None,
    }
    assert "sensitive internal test detail" not in response.text
