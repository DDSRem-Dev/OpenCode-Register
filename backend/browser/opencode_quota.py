import asyncio
import re
from time import monotonic
from typing import List, Optional
from urllib.parse import urlparse

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page

from browser.base import OpenCodeQuotaBrowserClient
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import OpenCodeQuotaPageResult, OpenCodeQuotaPageStatus
from engine.models import ManualInterventionReason
from storage.models import BrowserAuthState

GITHUB_HOST = "github.com"
GITHUB_HOME_URL = "https://github.com/"
GITHUB_AUTH_HOSTS = {GITHUB_HOST}
GITHUB_ACTOR_SELECTOR = 'meta[name="user-login"]'
VISIBLE_CAPTCHA_SELECTORS = "iframe[src*='captcha']:visible, [data-sitekey]:visible, .js-captcha:visible"
TWO_FACTOR_PATH_PATTERN = re.compile(r"^/sessions/(?:two-factor|verified-device)")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")

OPENCODE_HOST = "opencode.ai"
OPENCODE_AUTH_HOST = "auth.opencode.ai"
OPENCODE_AUTH_HOSTS = {OPENCODE_HOST, OPENCODE_AUTH_HOST}
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
        workspace_id: str,
        github_auth_state: BrowserAuthState,
        opencode_auth_state: BrowserAuthState,
    ) -> OpenCodeQuotaPageResult:
        """
        登录精确 GitHub 身份并检查对应 OpenCode workspace 额度

        :param github_username (str): 待检查账号的 GitHub 用户名
        :param workspace_id (str): 待检查账号的 OpenCode workspace 标识
        :param github_auth_state (BrowserAuthState): 已保存 GitHub 认证状态
        :param opencode_auth_state (BrowserAuthState): 已保存 OpenCode 认证状态

        :return OpenCodeQuotaPageResult: 当前仪表盘额度检查结果
        """

        if USERNAME_PATTERN.fullmatch(github_username) is None or WORKSPACE_ID_PATTERN.fullmatch(workspace_id) is None:
            return self._unavailable("quota_browser_identity_invalid", "额度检查目标格式无效")
        self._github_username = github_username
        self._workspace_id = workspace_id
        try:
            self._browser_session.restore_auth_states([github_auth_state, opencode_auth_state])
            page = await self._browser_session.page()
            await page.goto(GITHUB_HOME_URL, wait_until="domcontentloaded", timeout=30_000)
            github_result = await self._validate_github_session(page)
            if github_result is not None:
                return github_result
            refreshed_github_state = await self._browser_session.capture_auth_state(GITHUB_AUTH_HOSTS)
            return await self._read_dashboard(page, workspace_id, refreshed_github_state)
        except BrowserError:
            return self._unavailable("quota_browser_session_failed", "无法使用保存的浏览器认证状态检查额度")

    async def close(self) -> None:
        """
        关闭当前额度浏览器会话并清空目标身份

        :return None: 无返回值
        """

        self._github_username = None
        self._workspace_id = None
        await self._browser_session.close()

    async def _validate_github_session(self, page: Page) -> Optional[OpenCodeQuotaPageResult]:
        if not self._is_host(page, GITHUB_HOST):
            return self._auth_required("quota_github_session_expired", "保存的 GitHub 登录状态已失效")
        if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
            return self._manual(ManualInterventionReason.CAPTCHA)
        if TWO_FACTOR_PATH_PATTERN.match(urlparse(page.url).path) is not None:
            return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
        actor = await self._actor_login(page)
        if actor is None:
            return self._auth_required("quota_github_session_expired", "保存的 GitHub 登录状态已失效")
        username = self._github_username
        if username is None or actor.casefold() != username.casefold():
            return self._unavailable("quota_browser_identity_mismatch", "当前 GitHub 身份与额度检查目标不一致")
        return None

    async def _read_dashboard(
        self,
        page: Page,
        workspace_id: str,
        github_auth_state: BrowserAuthState,
    ) -> OpenCodeQuotaPageResult:
        await page.goto(
            f"https://{OPENCODE_HOST}/workspace/{workspace_id}/go",
            wait_until="domcontentloaded",
            timeout=30_000,
        )
        if self._is_host(page, OPENCODE_AUTH_HOST):
            return self._auth_required("quota_opencode_session_expired", "保存的 OpenCode 登录状态已失效")
        if self._workspace_from_url(page.url) != workspace_id:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        result = await self._read_dashboard_usage(page)
        opencode_auth_state = await self._browser_session.capture_auth_state(OPENCODE_AUTH_HOSTS)
        return result.model_copy(
            update={
                "github_auth_state": github_auth_state,
                "opencode_auth_state": opencode_auth_state,
            }
        )

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
    def _auth_required(code: str, message: str) -> OpenCodeQuotaPageResult:
        return OpenCodeQuotaPageResult(
            status=OpenCodeQuotaPageStatus.AUTH_REQUIRED,
            error_code=code,
            error_message=message,
        )

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
