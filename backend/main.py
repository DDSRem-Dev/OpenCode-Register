from __future__ import annotations

import argparse
from contextlib import AsyncExitStack, asynccontextmanager
from typing import AsyncGenerator, Optional, Tuple

import httpx
import uvicorn
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware

from api.accounts import create_account_transfer_router, create_accounts_router
from api.cleanup import create_cleanup_router
from api.errors import ApiError, api_error_handler, unexpected_error_handler, validation_error_handler
from api.quota import create_quota_router
from api.routes import create_flow_control_router, create_router
from api.settings import create_settings_router
from api.websocket import create_websocket_router
from browser.cloakbrowser_client import CloakBrowserClient
from browser.github_cleanup import GitHubAccountCleanup
from browser.github_register import GitHubRegister
from browser.initializer import BrowserInitializer
from browser.opencode_login import OpenCodeLogin
from config.model_catalog import OpenCodeGoModelClient
from config.omo_writer import OmoConfigWriter
from config.opencode_writer import OpenCodeConfigWriter
from config.pool_service import OpenCodePoolConfigService
from config.settings import AppSettings
from engine.cleanup_service import AccountCleanupService
from engine.completion import AccountCompletionService
from engine.quota_service import QuotaCheckService
from engine.service import CreateAccountService
from process_watchdog import start_owner_watchdog
from scheduler.quota_scheduler import QuotaScheduler
from storage.screenshots import ScreenshotStore
from storage.service import AccountVaultService

APP_VERSION = "0.0.9"


def create_app(
    vault_service: Optional[AccountVaultService] = None,
    quota_service: Optional[QuotaCheckService] = None,
    cleanup_service: Optional[AccountCleanupService] = None,
    application_version: str = APP_VERSION,
    browser_initializer: Optional[BrowserInitializer] = None,
) -> FastAPI:
    """
    创建并配置本地 FastAPI 应用

    :param vault_service (AccountVaultService): 可选的账号库服务替身
    :param quota_service (QuotaCheckService): 可选的额度检查服务替身
    :param cleanup_service (AccountCleanupService): 可选的账号清理服务替身
    :param application_version (str): 应用程序版本
    :param browser_initializer (BrowserInitializer): 可选的浏览器初始化管理器

    :return FastAPI: 配置完成的本地服务应用
    """

    http_client = httpx.AsyncClient()
    if browser_initializer is None:
        browser_initializer = BrowserInitializer()
    browser_client = CloakBrowserClient(initializer=browser_initializer)
    settings = AppSettings.from_environment()
    if vault_service is None:
        vault_service = AccountVaultService(settings.data_directory / "accounts.db")
    opencode_writer = OpenCodeConfigWriter(settings.opencode_paths)
    pool_service = OpenCodePoolConfigService(
        OpenCodeGoModelClient(http_client),
        opencode_writer,
        OmoConfigWriter(settings.opencode_paths),
    )
    completion_service = AccountCompletionService(vault_service, opencode_writer, pool_service)
    screenshot_store = ScreenshotStore(
        settings.data_directory / "screenshots",
        enabled=settings.screenshots_enabled,
        retention_hours=settings.screenshot_retention_hours,
        max_per_flow=settings.screenshot_max_per_flow,
    )

    def create_account_browsers() -> Tuple[GitHubRegister, OpenCodeLogin]:
        browser_session = browser_client.create_session()
        return GitHubRegister(browser_session), OpenCodeLogin(browser_session)

    service = CreateAccountService(
        completion_service.complete,
        browser_factory=create_account_browsers,
        pending_handler=completion_service.persist_pending,
        pending_status_handler=completion_service.mark_pending_status,
        auth_state_handler=completion_service.update_pending_auth_states,
        screenshot_store=screenshot_store,
        browser_initializer=browser_initializer,
    )
    if quota_service is None:
        quota_service = QuotaCheckService(vault_service, browser_initializer=browser_initializer)
    if cleanup_service is None:
        cleanup_service = AccountCleanupService(
            vault_service,
            pool_service,
            client_factory=lambda: GitHubAccountCleanup(browser_client.create_session()),
            account_session_cleanup=quota_service.close_account_session,
        )
    quota_scheduler = QuotaScheduler(quota_service, settings.quota_check_interval_seconds)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncGenerator[None, None]:
        """
        管理本地服务拥有的共享异步资源

        :param application (FastAPI): 当前 FastAPI 应用

        :yields None: 应用运行控制权
        """

        del application
        async with AsyncExitStack() as resources:
            resources.push_async_callback(http_client.aclose)
            resources.push_async_callback(browser_initializer.close)
            resources.push_async_callback(browser_client.close)
            resources.push_async_callback(service.close)
            resources.push_async_callback(cleanup_service.close)
            resources.push_async_callback(quota_service.close)
            resources.push_async_callback(quota_scheduler.close)
            quota_scheduler.start()
            browser_initializer.start()
            yield

    app = FastAPI(
        title="OpenCode Register Local Service",
        version=application_version,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://127.0.0.1:1420",
            "http://tauri.localhost",
            "https://tauri.localhost",
            "tauri://localhost",
        ],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(ApiError, api_error_handler)
    app.add_exception_handler(RequestValidationError, validation_error_handler)
    app.add_exception_handler(Exception, unexpected_error_handler)
    app.include_router(
        create_router(service, vault_service, settings.storage_mode, app.version, browser_initializer),
        prefix="/api",
    )
    app.include_router(create_flow_control_router(service), prefix="/api")
    app.include_router(create_accounts_router(vault_service), prefix="/api")
    app.include_router(create_account_transfer_router(vault_service, completion_service.import_bundle), prefix="/api")
    app.include_router(create_quota_router(quota_service), prefix="/api")
    app.include_router(create_settings_router(vault_service, completion_service, pool_service), prefix="/api")
    app.include_router(create_cleanup_router(cleanup_service), prefix="/api")
    app.include_router(create_websocket_router(service), prefix="/ws")
    return app


app = create_app()


def parse_args() -> argparse.Namespace:
    """
    解析本地服务启动参数

    :return Namespace: 解析后的命令行参数
    """

    parser = argparse.ArgumentParser(description="Run the OpenCode Register local service")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", default=17891, type=int)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    start_owner_watchdog()
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
