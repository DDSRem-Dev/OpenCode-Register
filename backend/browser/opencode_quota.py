import asyncio
import re
from time import monotonic
from typing import List, Optional
from urllib.parse import urlparse

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page
from pydantic import SecretStr

from browser.base import OpenCodeQuotaBrowserClient
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import OpenCodeQuotaPageResult, OpenCodeQuotaPageStatus
from engine.models import ManualInterventionReason

GITHUB_HOST = "github.com"
GITHUB_LOGIN_URL = "https://github.com/login"
GITHUB_LOGIN_PATH = "/login"
GITHUB_OAUTH_PATH = "/login/oauth/authorize"
GITHUB_USERNAME_SELECTOR = "#login_field"
GITHUB_PASSWORD_SELECTOR = "#password"
GITHUB_ACTOR_SELECTOR = 'meta[name="user-login"]'
GITHUB_LOGIN_ERROR_SELECTOR = ".flash-error:visible, #js-flash-container .flash:visible"
VISIBLE_CAPTCHA_SELECTORS = "iframe[src*='captcha']:visible, [data-sitekey]:visible, .js-captcha:visible"
TWO_FACTOR_PATH_PATTERN = re.compile(r"^/sessions/(?:two-factor|verified-device)")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

OPENCODE_AUTH_URL = "https://opencode.ai/auth"
OPENCODE_HOST = "opencode.ai"
OPENCODE_AUTH_HOST = "auth.opencode.ai"
OPENCODE_PROVIDER_SELECTOR = 'a[href="/github/authorize"]'
OPENCODE_WORKSPACE_PATTERN = re.compile(r"^/workspace/(wrk_[A-Za-z0-9]+)(?:/|$)")
WORKSPACE_ID_PATTERN = re.compile(r"^wrk_[A-Za-z0-9]+$")
GO_SUBSCRIBE_BUTTON_NAMES = ("订阅 Go", "Subscribe to Go", "Subscribe Go")
GO_USAGE_VALUE_SELECTOR = "[data-slot='usage-item'] [data-slot='usage-value']"
GO_USAGE_PERCENT_PATTERN = re.compile(r"^\s*(\d{1,3})\s*%\s*$")


class OpenCodeQuotaBrowser(OpenCodeQuotaBrowserClient):
    """
    OpenCode Go 后台浏览器额度检查适配器

    适配器使用已保存的 GitHub 凭据建立 OAuth 会话，但不会绕过任何人工验证
    """

    def __init__(self, browser_session: CloakBrowserSession) -> None:
        """
        初始化 OpenCode Go 额度浏览器适配器

        :param browser_session (CloakBrowserSession): 当前账号的隔离浏览器会话
        """

        self._browser_session = browser_session
        self._github_username: Optional[str] = None
        self._workspace_id: Optional[str] = None

    async def start_check(
        self,
        github_username: str,
        github_password: SecretStr,
        workspace_id: str,
    ) -> OpenCodeQuotaPageResult:
        """
        登录精确 GitHub 身份并检查对应 OpenCode workspace 额度

        :param github_username (str): 待检查账号的 GitHub 用户名
        :param github_password (SecretStr): 待检查账号的 GitHub 密码
        :param workspace_id (str): 待检查账号的 OpenCode workspace 标识

        :return OpenCodeQuotaPageResult: 当前仪表盘额度检查结果
        """

        if USERNAME_PATTERN.fullmatch(github_username) is None or WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None:
            return self._unavailable("quota_browser_identity_invalid", "额度检查目标格式无效")
        self._github_username = github_username
        self._workspace_id = workspace_id
        try:
            page = await self._browser_session.page()
            await page.goto(GITHUB_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            if not self._is_host(page, GITHUB_HOST) or urlparse(page.url).path != GITHUB_LOGIN_PATH:
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            await page.locator(GITHUB_USERNAME_SELECTOR).fill(github_username)
            await page.locator(GITHUB_PASSWORD_SELECTOR).fill(github_password.get_secret_value())
            await page.get_by_role("button", name="Sign in", exact=True).click(timeout=15_000)
            authentication = await self._wait_for_github(page)
            if authentication is not None:
                return authentication
            return await self._open_opencode(page)
        except BrowserError:
            return self._unavailable("quota_browser_login_failed", "无法通过后台浏览器登录额度检查账号")

    async def close(self) -> None:
        """
        关闭当前额度浏览器会话并清空目标身份

        :return None: 无返回值
        """

        self._github_username = None
        self._workspace_id = None
        await self._browser_session.close()

    async def _wait_for_github(self, page: Page) -> Optional[OpenCodeQuotaPageResult]:
        deadline = monotonic() + 15
        while monotonic() < deadline:
            actor = await self._actor_login(page)
            if actor is not None:
                username = self._github_username
                if username is None or actor.casefold() != username.casefold():
                    return self._unavailable("quota_browser_identity_mismatch", "当前 GitHub 身份与额度检查目标不一致")
                return None
            if not self._is_host(page, GITHUB_HOST):
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
                return self._manual(ManualInterventionReason.CAPTCHA)
            path = urlparse(page.url).path
            if TWO_FACTOR_PATH_PATTERN.match(path) is not None:
                return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
            if path == GITHUB_LOGIN_PATH and await page.locator(GITHUB_LOGIN_ERROR_SELECTOR).count() > 0:
                return OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.INVALID)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _open_opencode(self, page: Page) -> OpenCodeQuotaPageResult:
        await page.goto(OPENCODE_AUTH_URL, wait_until="domcontentloaded", timeout=30_000)
        if not self._is_host(page, OPENCODE_AUTH_HOST):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        provider = page.locator(OPENCODE_PROVIDER_SELECTOR)
        if await provider.count() != 1:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        await provider.click(timeout=15_000)
        return await self._wait_for_workspace(page)

    async def _wait_for_workspace(self, page: Page) -> OpenCodeQuotaPageResult:
        deadline = monotonic() + 30
        while monotonic() < deadline:
            workspace_id = self._workspace_from_url(page.url)
            if workspace_id is not None:
                if workspace_id != self._workspace_id:
                    return self._unavailable(
                        "quota_browser_workspace_mismatch",
                        "当前 OpenCode workspace 与额度检查目标不一致",
                    )
                return await self._read_dashboard(page, workspace_id)
            if self._is_host(page, GITHUB_HOST):
                if urlparse(page.url).path != GITHUB_OAUTH_PATH:
                    return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
                authorize = page.get_by_role("button", name="Authorize", exact=True)
                if await authorize.count() != 1 or not await authorize.is_enabled():
                    return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
                await authorize.click(timeout=15_000)
            elif self._is_host(page, OPENCODE_AUTH_HOST):
                await asyncio.sleep(0.25)
            else:
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _read_dashboard(self, page: Page, workspace_id: str) -> OpenCodeQuotaPageResult:
        await page.goto(
            f"https://{OPENCODE_HOST}/workspace/{workspace_id}/go",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        if self._workspace_from_url(page.url) != workspace_id:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        return await self._read_dashboard_usage(page)

    async def _read_dashboard_usage(self, page: Page) -> OpenCodeQuotaPageResult:
        deadline = monotonic() + 15
        usage_values = page.locator(GO_USAGE_VALUE_SELECTOR)
        while monotonic() < deadline:
            if await self._has_subscription_prompt(page):
                return OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.SUBSCRIPTION_REQUIRED)
            if await usage_values.count() == 3:
                try:
                    return self._updated(_monthly_usage(await usage_values.all_text_contents()))
                except ValueError:
                    return self._unavailable("quota_dashboard_dom_invalid", "OpenCode Go 仪表盘额度格式无效")
            await asyncio.sleep(0.25)
        return self._unavailable("quota_dashboard_dom_unavailable", "OpenCode Go 仪表盘未显示完整额度")

    @staticmethod
    async def _has_subscription_prompt(page: Page) -> bool:
        for button_name in GO_SUBSCRIBE_BUTTON_NAMES:
            subscribe_button = page.get_by_role("button", name=button_name, exact=True)
            if await subscribe_button.count() == 1 and await subscribe_button.is_enabled():
                return True
        return False

    @staticmethod
    async def _actor_login(page: Page) -> Optional[str]:
        actor = page.locator(GITHUB_ACTOR_SELECTOR)
        if await actor.count() != 1:
            return None
        value = await actor.get_attribute("content")
        return value if value else None

    @staticmethod
    def _workspace_from_url(url: str) -> Optional[str]:
        parsed_url = urlparse(url)
        if parsed_url.scheme != "https" or parsed_url.hostname != OPENCODE_HOST:
            return None
        match = OPENCODE_WORKSPACE_PATTERN.match(parsed_url.path)
        return match.group(1) if match is not None else None

    @staticmethod
    def _is_host(page: Page, hostname: str) -> bool:
        parsed_url = urlparse(page.url)
        return parsed_url.scheme == "https" and parsed_url.hostname == hostname

    @staticmethod
    def _updated(usage_percent: int) -> OpenCodeQuotaPageResult:
        return OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.UPDATED, usage_percent=usage_percent)

    @staticmethod
    def _manual(reason: ManualInterventionReason) -> OpenCodeQuotaPageResult:
        return OpenCodeQuotaPageResult(status=OpenCodeQuotaPageStatus.MANUAL_REQUIRED, manual_reason=reason)

    @staticmethod
    def _unavailable(code: str, message: str) -> OpenCodeQuotaPageResult:
        return OpenCodeQuotaPageResult(
            status=OpenCodeQuotaPageStatus.UNAVAILABLE,
            error_code=code,
            error_message=message,
        )


def _monthly_usage(values: List[str]) -> int:
    """
    校验三个额度窗口并返回每月用量

    :param values (List): 按滚动、每周、每月顺序排列的百分比文本

    :return int: 每月用量百分比

    :raises ValueError: 节点数量或任一百分比无效
    """

    if len(values) != 3:
        raise ValueError("OpenCode Go quota window count is invalid")
    percentages: List[int] = []
    for value in values:
        match = GO_USAGE_PERCENT_PATTERN.fullmatch(value)
        if match is None:
            raise ValueError("OpenCode Go quota percentage is invalid")
        percentage = int(match.group(1))
        if percentage > 100:
            raise ValueError("OpenCode Go quota percentage is out of range")
        percentages.append(percentage)
    return percentages[2]
