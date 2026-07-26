import asyncio
import re
from time import monotonic
from typing import Optional
from urllib.parse import urlparse

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page
from pydantic import SecretStr

from browser.base import OpenCodeAutomationClient
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import OpenCodePageResult, OpenCodePageStatus
from engine.models import ManualInterventionReason

OPENCODE_AUTH_URL = "https://opencode.ai/auth"
OPENCODE_HOST = "opencode.ai"
OPENCODE_AUTH_HOST = "auth.opencode.ai"
GITHUB_HOST = "github.com"
GITHUB_PROVIDER_SELECTOR = 'a[href="/github/authorize"]'
GITHUB_OAUTH_PATH = "/login/oauth/authorize"
AUTHORIZE_BUTTON_NAME = "Authorize"
WORKSPACE_PATTERN = re.compile(r"^/workspace/(wrk_[A-Za-z0-9]+)(?:/|$)")
WORKSPACE_ID_PATTERN = re.compile(r"^wrk_[A-Za-z0-9]+$")
API_KEY_PATTERN = re.compile(r"^sk-[A-Za-z0-9]{64}$")
DEFAULT_KEY_NAME = "Default API Key"
CLIPBOARD_READ_TIMEOUT_SECONDS = 5.0


class OpenCodeLogin(OpenCodeAutomationClient):
    """
    OpenCode Go OAuth、付款导航与默认 API Key 读取适配器

    选择器与路由来自 2026-07-25 的真实页面验证；未知状态始终交给用户
    """

    def __init__(self, browser_session: CloakBrowserSession) -> None:
        """
        初始化 OpenCode 浏览器适配器

        :param browser_session (CloakBrowserSession): 当前流程的隔离浏览器会话
        """

        self._browser_session = browser_session
        self._workspace_id: Optional[str] = None

    async def start_login(self) -> OpenCodePageResult:
        """
        通过真实 OpenCode Auth 入口启动 GitHub OAuth

        :return OpenCodePageResult: 登录后的类型化页面状态
        """

        try:
            page = await self._browser_session.page()
            await page.goto(OPENCODE_AUTH_URL, wait_until="domcontentloaded", timeout=30_000)
        except BrowserError:
            return self._error("opencode_auth_navigation_failed", "无法打开 OpenCode 登录入口")
        if not self._is_auth_page(page):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        try:
            github_provider = page.locator(GITHUB_PROVIDER_SELECTOR)
            if await github_provider.count() != 1:
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            await github_provider.click(timeout=15_000)
            return await self._wait_for_workspace(page)
        except BrowserError:
            return self._error("opencode_oauth_failed", "OpenCode GitHub OAuth 登录失败")

    async def inspect_after_manual(self) -> OpenCodePageResult:
        """
        用户处理 OAuth 阻断后重新检查工作区跳转

        :return OpenCodePageResult: 当前页面的类型化状态
        """

        try:
            page = await self._browser_session.page()
            return await self._wait_for_workspace(page)
        except BrowserError:
            return self._error("opencode_oauth_inspection_failed", "无法检查 OpenCode 登录状态")

    async def confirm_payment(self) -> OpenCodePageResult:
        """
        用户确认付款后读取 Default API Key

        :return OpenCodePageResult: 密钥读取后的类型化状态
        """

        workspace_id = self._workspace_id
        if workspace_id is None:
            return self._error("opencode_workspace_missing", "OpenCode 工作区标识不可用")
        try:
            page = await self._browser_session.page()
            await page.goto(
                f"https://{OPENCODE_HOST}/workspace/{workspace_id}/keys",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            if not self._is_opencode_page(page):
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            row = page.locator(f'tr:has(td[data-slot="key-name"]:text-is("{DEFAULT_KEY_NAME}"))')
            if await row.count() != 1:
                return self._api_key_input()
            copy_button = row.locator('td[data-slot="key-value"] button[data-color="ghost"]')
            if await copy_button.count() != 1:
                return self._api_key_input()
            await copy_button.click(timeout=15_000)
            clipboard_value: object = await asyncio.wait_for(
                page.evaluate("navigator.clipboard.readText()"),
                timeout=CLIPBOARD_READ_TIMEOUT_SECONDS,
            )
        except (BrowserError, TimeoutError):
            return self._api_key_input()
        if not isinstance(clipboard_value, str) or API_KEY_PATTERN.fullmatch(clipboard_value) is None:
            return self._api_key_input()
        return OpenCodePageResult(
            status=OpenCodePageStatus.COMPLETED,
            workspace_id=workspace_id,
            api_key=SecretStr(clipboard_value),
        )

    async def submit_api_key(self, api_key: str) -> OpenCodePageResult:
        """
        校验用户手动复制的 OpenCode API Key

        :param api_key (str): 用户提交的 OpenCode API Key

        :return OpenCodePageResult: 密钥校验后的类型化状态
        """

        if API_KEY_PATTERN.fullmatch(api_key) is None:
            return self._error("opencode_api_key_invalid", "OpenCode API Key 格式无效")
        if self._workspace_id is None:
            return self._error("opencode_workspace_missing", "OpenCode 工作区标识不可用")
        return OpenCodePageResult(
            status=OpenCodePageStatus.COMPLETED,
            workspace_id=self._workspace_id,
            api_key=SecretStr(api_key),
        )

    async def _wait_for_workspace(self, page: Page) -> OpenCodePageResult:
        deadline = monotonic() + 30
        while monotonic() < deadline:
            workspace_id = self._workspace_from_url(page.url)
            if workspace_id is not None:
                self._workspace_id = workspace_id
                return await self._open_go_page(page, workspace_id)
            if self._is_github_oauth_page(page):
                authorize_button = page.get_by_role("button", name=AUTHORIZE_BUTTON_NAME, exact=True)
                if await authorize_button.count() == 1 and await authorize_button.is_enabled():
                    await authorize_button.click(timeout=15_000)
            elif not self._is_auth_page(page):
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _open_go_page(self, page: Page, workspace_id: str) -> OpenCodePageResult:
        try:
            await page.goto(
                f"https://{OPENCODE_HOST}/workspace/{workspace_id}/go",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
        except BrowserError:
            return self._error("opencode_go_navigation_failed", "无法打开 OpenCode Go 页面")
        if not self._is_opencode_page(page):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        return OpenCodePageResult(
            status=OpenCodePageStatus.PAYMENT_REQUIRED,
            workspace_id=workspace_id,
            manual_reason=ManualInterventionReason.PAYMENT,
        )

    @staticmethod
    def _workspace_from_url(url: str) -> Optional[str]:
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname != OPENCODE_HOST:
            return None
        match = WORKSPACE_PATTERN.match(parsed_url.path)
        if match is None or WORKSPACE_ID_PATTERN.fullmatch(match.group(1)) is None:
            return None
        return match.group(1)

    @staticmethod
    def _is_auth_page(page: Page) -> bool:
        parsed_url = urlparse(page.url)
        return parsed_url.scheme == "https" and parsed_url.hostname == OPENCODE_AUTH_HOST

    @staticmethod
    def _is_opencode_page(page: Page) -> bool:
        parsed_url = urlparse(page.url)
        return parsed_url.scheme == "https" and parsed_url.hostname == OPENCODE_HOST

    @staticmethod
    def _is_github_oauth_page(page: Page) -> bool:
        parsed_url = urlparse(page.url)
        return (
            parsed_url.scheme == "https" and parsed_url.hostname == GITHUB_HOST and parsed_url.path == GITHUB_OAUTH_PATH
        )

    def _api_key_input(self) -> OpenCodePageResult:
        return OpenCodePageResult(
            status=OpenCodePageStatus.API_KEY_INPUT_REQUIRED,
            workspace_id=self._workspace_id,
            manual_reason=ManualInterventionReason.API_KEY_INPUT,
        )

    @staticmethod
    def _manual(reason: ManualInterventionReason) -> OpenCodePageResult:
        return OpenCodePageResult(status=OpenCodePageStatus.MANUAL_REQUIRED, manual_reason=reason)

    @staticmethod
    def _error(code: str, message: str) -> OpenCodePageResult:
        return OpenCodePageResult(status=OpenCodePageStatus.ERROR, error_code=code, error_message=message)
