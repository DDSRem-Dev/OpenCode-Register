import asyncio
import re
from time import monotonic
from typing import Optional
from urllib.parse import quote, urlparse

from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page
from pydantic import SecretStr

from browser.base import GitHubCleanupClient
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import GitHubCleanupPageResult, GitHubCleanupPageStatus
from engine.models import ManualInterventionReason

GITHUB_HOST = "github.com"
GITHUB_LOGIN_URL = "https://github.com/login"
GITHUB_ADMIN_URL = "https://github.com/settings/admin"
GITHUB_LOGIN_PATH = "/login"
GITHUB_ADMIN_PATH = "/settings/admin"
LOGIN_USERNAME_SELECTOR = "#login_field"
PASSWORD_SELECTOR = "#password"
ACTOR_LOGIN_SELECTOR = 'meta[name="user-login"]'
VISIBLE_CAPTCHA_SELECTORS = "iframe[src*='captcha']:visible, [data-sitekey]:visible, .js-captcha:visible"
LOGIN_ERROR_SELECTOR = ".flash-error:visible, #js-flash-container .flash:visible"
TWO_FACTOR_PATH_PATTERN = re.compile(r"^/sessions/(?:two-factor|verified-device)")
USERNAME_PATTERN = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?$")
DELETE_ACCOUNT_BUTTON_NAME = "Delete your account"
DELETE_USERNAME_LABEL = "Your username or email:"
DELETE_CONFIRMATION_LABEL = "To verify, type delete my account exactly as it appears:"
DELETE_CONFIRMATION_TEXT = "delete my account"
DELETE_SUBMIT_BUTTON_NAME = "Cancel plan and delete this account"
SUDO_PASSWORD_LABEL = "Password"
SUDO_CONFIRM_BUTTON_NAME = "Confirm"


class GitHubAccountCleanup(GitHubCleanupClient):
    """
    GitHub 账号确认删除浏览器适配器

    用户确认精确用户名后自动提交删除；验证码、二次验证和未知页面仍交给用户
    """

    def __init__(self, browser_session: CloakBrowserSession) -> None:
        """
        初始化 GitHub 账号清理适配器

        :param browser_session (CloakBrowserSession): 当前清理流程隔离浏览器会话
        """

        self._browser_session = browser_session
        self._username: Optional[str] = None
        self._password: Optional[SecretStr] = None

    async def start_cleanup(self, username: str, password: SecretStr) -> GitHubCleanupPageResult:
        """
        登录目标 GitHub 账号并完成已确认的删除流程

        :param username (str): 待删除 GitHub 用户名
        :param password (SecretStr): 待删除 GitHub 账号密码

        :return GitHubCleanupPageResult: 当前清理页面状态
        """

        if USERNAME_PATTERN.fullmatch(username) is None:
            return self._error("github_cleanup_username_invalid", "GitHub 账号标识格式无效")
        self._username = username
        self._password = password
        try:
            page = await self._browser_session.page()
            await page.goto(GITHUB_LOGIN_URL, wait_until="domcontentloaded", timeout=30_000)
            if not self._is_github_page(page) or urlparse(page.url).path != GITHUB_LOGIN_PATH:
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            await page.locator(LOGIN_USERNAME_SELECTOR).fill(username)
            await page.locator(PASSWORD_SELECTOR).fill(password.get_secret_value())
            await page.get_by_role("button", name="Sign in", exact=True).click(timeout=15_000)
            return await self._wait_for_authenticated(page)
        except BrowserError:
            return self._error("github_cleanup_browser_failed", "GitHub 删除页面操作失败")

    async def inspect_after_manual(self) -> GitHubCleanupPageResult:
        """
        用户处理安全验证后继续删除并检查目标账号状态

        :return GitHubCleanupPageResult: 当前清理页面状态
        """

        username = self._username
        if username is None:
            return self._error("github_cleanup_identity_missing", "GitHub 清理目标不可用")
        try:
            page = await self._browser_session.page()
            if self._is_sudo_page(page, username):
                return await self._confirm_sudo(page)
            if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
                return self._manual(ManualInterventionReason.CAPTCHA)
            actor = await self._actor_login(page)
            if actor is not None:
                if actor.casefold() != username.casefold():
                    return self._error("github_cleanup_identity_mismatch", "当前 GitHub 登录身份与删除目标不一致")
                return await self._open_admin(page)
            if self._is_login_or_verification_page(page):
                return await self._wait_for_authenticated(page)
            if await self._profile_deleted(page, username):
                return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        except BrowserError:
            return self._error("github_cleanup_inspection_failed", "无法验证 GitHub 账号删除结果")

    async def close(self) -> None:
        """
        关闭清理浏览器资源并清空目标身份

        :return None: 无返回值
        """

        self._username = None
        self._password = None
        await self._browser_session.close()

    async def _wait_for_authenticated(self, page: Page) -> GitHubCleanupPageResult:
        deadline = monotonic() + 15
        while monotonic() < deadline:
            actor = await self._actor_login(page)
            if actor is not None:
                username = self._username
                if username is None or actor.casefold() != username.casefold():
                    return self._error("github_cleanup_identity_mismatch", "当前 GitHub 登录身份与删除目标不一致")
                return await self._open_admin(page)
            if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
                return self._manual(ManualInterventionReason.CAPTCHA)
            if TWO_FACTOR_PATH_PATTERN.match(urlparse(page.url).path) is not None:
                return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
            if urlparse(page.url).path == GITHUB_LOGIN_PATH and await page.locator(LOGIN_ERROR_SELECTOR).count() > 0:
                return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.INVALID)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _open_admin(self, page: Page) -> GitHubCleanupPageResult:
        await page.goto(GITHUB_ADMIN_URL, wait_until="domcontentloaded", timeout=30_000)
        if not self._is_github_page(page) or urlparse(page.url).path != GITHUB_ADMIN_PATH:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        actor = await self._actor_login(page)
        username = self._username
        if actor is None or username is None or actor.casefold() != username.casefold():
            return self._error("github_cleanup_identity_mismatch", "当前 GitHub 登录身份与删除目标不一致")
        return await self._submit_deletion(page)

    async def _submit_deletion(self, page: Page) -> GitHubCleanupPageResult:
        username = self._username
        if username is None:
            return self._error("github_cleanup_identity_missing", "GitHub 清理目标不可用")
        delete_button = page.get_by_role("button", name=DELETE_ACCOUNT_BUTTON_NAME, exact=True)
        if await delete_button.count() != 1:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        await delete_button.click(timeout=15_000)
        username_input = page.get_by_label(DELETE_USERNAME_LABEL, exact=True)
        confirmation_input = page.get_by_label(DELETE_CONFIRMATION_LABEL, exact=True)
        submit_button = page.get_by_role("button", name=DELETE_SUBMIT_BUTTON_NAME, exact=True)
        if not await self._has_unique_controls(username_input, confirmation_input, submit_button):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        await username_input.fill(username)
        await confirmation_input.fill(DELETE_CONFIRMATION_TEXT)
        await submit_button.click(timeout=15_000)
        return await self._wait_for_sudo(page)

    async def _wait_for_sudo(self, page: Page) -> GitHubCleanupPageResult:
        deadline = monotonic() + 15
        while monotonic() < deadline:
            username = self._username
            if username is None:
                return self._error("github_cleanup_identity_missing", "GitHub 清理目标不可用")
            if self._is_sudo_page(page, username):
                return await self._confirm_sudo(page)
            if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
                return self._manual(ManualInterventionReason.CAPTCHA)
            if TWO_FACTOR_PATH_PATTERN.match(urlparse(page.url).path) is not None:
                return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
            if await self._profile_deleted(page, username):
                return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _confirm_sudo(self, page: Page) -> GitHubCleanupPageResult:
        username = self._username
        password = self._password
        if username is None or password is None or not self._is_sudo_page(page, username):
            return self._error("github_cleanup_identity_mismatch", "GitHub sudo 身份与删除目标不一致")
        password_input = page.get_by_label(SUDO_PASSWORD_LABEL, exact=True)
        confirm_button = page.get_by_role("button", name=SUDO_CONFIRM_BUTTON_NAME, exact=True)
        if not await self._has_unique_controls(password_input, confirm_button):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        await password_input.fill(password.get_secret_value())
        await confirm_button.click(timeout=15_000)
        return await self._wait_for_deleted(page, username)

    async def _wait_for_deleted(self, page: Page, username: str) -> GitHubCleanupPageResult:
        deadline = monotonic() + 15
        while monotonic() < deadline:
            if await self._profile_deleted(page, username):
                return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.DELETED)
            if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
                return self._manual(ManualInterventionReason.CAPTCHA)
            if TWO_FACTOR_PATH_PATTERN.match(urlparse(page.url).path) is not None:
                return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
            if self._is_sudo_page(page, username) and await page.locator(LOGIN_ERROR_SELECTOR).count() > 0:
                return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.INVALID)
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    @staticmethod
    async def _profile_deleted(page: Page, username: str) -> bool:
        profile_response = await page.request.get(
            f"https://{GITHUB_HOST}/{quote(username, safe='')}",
            timeout=15_000,
            fail_on_status_code=False,
        )
        return profile_response.status == 404

    @staticmethod
    async def _has_unique_controls(*controls: Locator) -> bool:
        for control in controls:
            if await control.count() != 1:
                return False
        return True

    @staticmethod
    async def _actor_login(page: Page) -> Optional[str]:
        actor_meta = page.locator(ACTOR_LOGIN_SELECTOR)
        if await actor_meta.count() != 1:
            return None
        value = await actor_meta.get_attribute("content")
        return value if value else None

    @staticmethod
    def _is_github_page(page: Page) -> bool:
        parsed_url = urlparse(page.url)
        return parsed_url.scheme == "https" and parsed_url.hostname == GITHUB_HOST

    @staticmethod
    def _is_sudo_page(page: Page, username: str) -> bool:
        return (
            GitHubAccountCleanup._is_github_page(page)
            and urlparse(page.url).path.casefold() == (f"/users/{quote(username, safe='')}").casefold()
        )

    @staticmethod
    def _is_login_or_verification_page(page: Page) -> bool:
        path = urlparse(page.url).path
        return GitHubAccountCleanup._is_github_page(page) and (
            path == GITHUB_LOGIN_PATH or TWO_FACTOR_PATH_PATTERN.match(path) is not None
        )

    @staticmethod
    def _manual(reason: ManualInterventionReason) -> GitHubCleanupPageResult:
        return GitHubCleanupPageResult(status=GitHubCleanupPageStatus.MANUAL_REQUIRED, manual_reason=reason)

    @staticmethod
    def _error(code: str, message: str) -> GitHubCleanupPageResult:
        return GitHubCleanupPageResult(
            status=GitHubCleanupPageStatus.ERROR,
            error_code=code,
            error_message=message,
        )
