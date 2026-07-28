import asyncio
import re
import secrets
from time import monotonic
from typing import Awaitable, Callable, List, Optional
from urllib.parse import urlparse

from playwright.async_api import Error as BrowserError
from playwright.async_api import Page
from playwright.async_api import TimeoutError as BrowserTimeoutError

from browser.base import GitHubRegistrationClient
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE, GitHubPageResult, GitHubPageStatus
from engine.models import ManualInterventionReason

GITHUB_SIGNUP_URL = "https://github.com/signup"
GITHUB_HOST = "github.com"
GITHUB_AUTH_HOSTS = {GITHUB_HOST}
GITHUB_LOGIN_PATH = "/login"
EMAIL_SELECTOR = "#email"
PASSWORD_SELECTOR = "#password"
USERNAME_SELECTOR = "#login"
LOGIN_USERNAME_SELECTOR = "#login_field"
EMAIL_CODE_FIELD_COUNT = 8
VISIBLE_CAPTCHA_SELECTORS = "iframe[src*='captcha']:visible, [data-sitekey]:visible, .js-captcha:visible"
CREATE_ACCOUNT_NAME = "Create account"
COPILOT_OPT_IN_PATTERN = re.compile(r"Sign up for Copilot Free", re.IGNORECASE)
USERNAME_UNAVAILABLE_PATTERN = re.compile(r"Username .+ is not available", re.IGNORECASE)
COMPLETED_PATHS = {"/", "/dashboard"}
PHONE_TEXT_PATTERN = re.compile(r"phone|手机号|mobile verification", re.IGNORECASE)
EMAIL_CODE_PATTERN = re.compile(r"^[0-9]{8}$")
MIN_ACTION_DELAY_MILLISECONDS = 500
ACTION_DELAY_RANGE_MILLISECONDS = 1_501
USERNAME_VALIDATION_TIMEOUT_MILLISECONDS = 3_000


class GitHubRegister(GitHubRegistrationClient):
    """
    GitHub 注册页面自动化适配器

    适配器只执行普通表单操作；风险控制、身份验证和未知状态始终暂停并交给用户
    """

    def __init__(
        self,
        browser_session: CloakBrowserSession,
        action_delay: Optional[Callable[[], Awaitable[None]]] = None,
    ) -> None:
        """
        初始化 GitHub 注册适配器

        :param browser_session (CloakBrowserSession): 当前流程的隔离浏览器会话
        :param action_delay (Callable): 可选的表单操作间延迟函数
        """

        self._browser_session = browser_session
        self._action_delay = action_delay or _wait_for_action_delay
        self._github_username: Optional[str] = None
        self._github_password: Optional[str] = None

    async def start_registration(self, email: str, username: str, password: str) -> GitHubPageResult:
        """
        打开 GitHub 注册页并提交注册表单

        :param email (str): 注册邮箱
        :param username (str): 生成的 GitHub 用户名
        :param password (str): 生成的 GitHub 密码

        :return GitHubPageResult: 页面操作后的类型化状态
        """

        try:
            page = await self._browser_session.page()
        except BrowserError:
            return self._error("github_browser_launch_failed", "无法启动 GitHub 注册浏览器")
        self._github_username = username
        self._github_password = password
        try:
            await page.goto(GITHUB_SIGNUP_URL, wait_until="domcontentloaded", timeout=30_000)
        except BrowserError:
            return self._error("github_navigation_failed", "无法打开 GitHub 注册页面")
        if not self._is_github_page(page):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        try:
            await page.locator(EMAIL_SELECTOR).fill(email)
            await self._action_delay()
            await page.locator(PASSWORD_SELECTOR).fill(password)
            await self._action_delay()
            await page.locator(USERNAME_SELECTOR).fill(username)
        except BrowserError:
            return self._error("github_form_fill_failed", "无法填写 GitHub 注册表单")
        try:
            copilot_opt_in = page.get_by_role("checkbox", name=COPILOT_OPT_IN_PATTERN)
            if await copilot_opt_in.count() == 1 and await copilot_opt_in.is_checked():
                await self._action_delay()
                await copilot_opt_in.uncheck(force=True)
                if await copilot_opt_in.is_checked():
                    return self._error("github_copilot_opt_out_failed", "无法取消 GitHub 附加产品选项")
        except BrowserError:
            return self._error("github_copilot_opt_out_failed", "无法取消 GitHub 附加产品选项")
        try:
            await self._action_delay()
            if await self._username_is_unavailable(page):
                return self._error(GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE, "GitHub 用户名不可用")
            await page.get_by_role("button", name=CREATE_ACCOUNT_NAME, exact=True).click(timeout=15_000)
        except BrowserError:
            return self._error("github_form_submit_failed", "无法提交 GitHub 注册表单")
        try:
            return await self._wait_for_known_state(page)
        except BrowserError:
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)

    async def inspect_after_manual(self) -> GitHubPageResult:
        """
        用户确认完成人工操作后重新检查 GitHub 页面

        :return GitHubPageResult: 当前页面的类型化状态
        """

        try:
            page = await self._browser_session.page()
            return await self._wait_for_known_state(page)
        except BrowserError:
            return self._error("github_browser_failed", "无法检查 GitHub 注册页面")

    async def submit_email_code(self, code: str) -> GitHubPageResult:
        """
        逐位提交经过 provider 校验的 GitHub 邮箱验证码

        :param code (str): 八位数字邮箱验证码

        :return GitHubPageResult: 提交后的类型化状态
        """

        if EMAIL_CODE_PATTERN.fullmatch(code) is None:
            return self._error("github_email_code_invalid", "GitHub 邮箱验证码格式无效")
        try:
            page = await self._browser_session.page()
            if not self._is_github_page(page):
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            code_fields = page.get_by_role("spinbutton")
            if await code_fields.count() != EMAIL_CODE_FIELD_COUNT:
                return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
            for index, digit in enumerate(code):
                await code_fields.nth(index).fill(digit)
                await self._action_delay()
        except BrowserError:
            return self._error("github_email_code_fill_failed", "GitHub 邮箱验证码填写失败")
        try:
            continue_button = page.get_by_role("button", name=re.compile("continue", re.IGNORECASE))
            if await continue_button.count() == 1 and await continue_button.is_visible():
                await continue_button.click(timeout=15_000)
            return await self._wait_for_known_state(page)
        except BrowserError:
            if self._is_login_page(page):
                return await self._sign_in_after_registration(page)
            return self._error("github_email_code_submit_failed", "GitHub 邮箱验证码提交失败")

    async def close(self) -> None:
        """
        关闭注册流程拥有的浏览器资源

        :return None: 无返回值
        """

        self._github_username = None
        self._github_password = None
        await self._browser_session.close()

    async def capture_sanitized_screenshot(self, sensitive_texts: List[str]) -> Optional[bytes]:
        """
        捕获遮罩输入、挑战控件和已知身份文本的当前 GitHub 页面

        :param sensitive_texts (List): 必须额外遮罩的可见身份文本

        :return bytes: 已遮罩 PNG；浏览器不可用时返回空值
        """

        try:
            return await self._browser_session.capture_sanitized_screenshot(sensitive_texts)
        except BrowserError:
            return None

    async def _wait_for_known_state(self, page: Page, allow_login: bool = True) -> GitHubPageResult:
        deadline = monotonic() + 15
        while monotonic() < deadline:
            if allow_login and self._is_login_page(page):
                return await self._sign_in_after_registration(page)
            result = await self._detect_state(page)
            if result.status != GitHubPageStatus.MANUAL_REQUIRED:
                return result
            if result.manual_reason != ManualInterventionReason.UNKNOWN_BLOCK:
                return result
            await asyncio.sleep(0.25)
        return self._manual(ManualInterventionReason.TIMEOUT)

    async def _sign_in_after_registration(self, page: Page) -> GitHubPageResult:
        username = self._github_username
        password = self._github_password
        if username is None or password is None:
            return self._error("github_post_registration_credentials_missing", "GitHub 注册后登录凭据不可用")
        try:
            await page.locator(LOGIN_USERNAME_SELECTOR).fill(username)
            await self._action_delay()
            await page.locator(PASSWORD_SELECTOR).fill(password)
            await self._action_delay()
            await page.get_by_role("button", name="Sign in", exact=True).click(timeout=15_000)
            return await self._wait_for_known_state(page, allow_login=False)
        except BrowserError:
            return self._error("github_post_registration_login_failed", "GitHub 注册后登录失败")

    async def _detect_state(self, page: Page) -> GitHubPageResult:
        if not self._is_github_page(page):
            return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)
        if await page.get_by_role("spinbutton").count() == EMAIL_CODE_FIELD_COUNT:
            return GitHubPageResult(status=GitHubPageStatus.EMAIL_CODE_REQUIRED)
        if await page.locator(VISIBLE_CAPTCHA_SELECTORS).count() > 0:
            return self._manual(ManualInterventionReason.CAPTCHA)
        body_text = await page.locator("body").inner_text(timeout=5_000)
        if PHONE_TEXT_PATTERN.search(body_text) is not None:
            return self._manual(ManualInterventionReason.PHONE_VERIFICATION)
        if urlparse(page.url).path in COMPLETED_PATHS:
            return GitHubPageResult(
                status=GitHubPageStatus.COMPLETED,
                github_auth_state=await self._browser_session.capture_auth_state(GITHUB_AUTH_HOSTS),
            )
        return self._manual(ManualInterventionReason.UNKNOWN_BLOCK)

    async def _username_is_unavailable(self, page: Page) -> bool:
        unavailable_message = page.get_by_text(USERNAME_UNAVAILABLE_PATTERN).first
        try:
            await unavailable_message.wait_for(
                state="visible",
                timeout=USERNAME_VALIDATION_TIMEOUT_MILLISECONDS,
            )
        except BrowserTimeoutError:
            return False
        return True

    @staticmethod
    def _is_github_page(page: Page) -> bool:
        parsed_url = urlparse(page.url)
        return parsed_url.scheme == "https" and parsed_url.hostname == GITHUB_HOST

    @staticmethod
    def _is_login_page(page: Page) -> bool:
        return GitHubRegister._is_github_page(page) and urlparse(page.url).path == GITHUB_LOGIN_PATH

    @staticmethod
    def _manual(reason: ManualInterventionReason) -> GitHubPageResult:
        return GitHubPageResult(status=GitHubPageStatus.MANUAL_REQUIRED, manual_reason=reason)

    @staticmethod
    def _error(code: str, message: str) -> GitHubPageResult:
        return GitHubPageResult(status=GitHubPageStatus.ERROR, error_code=code, error_message=message)


async def _wait_for_action_delay() -> None:
    delay_milliseconds = MIN_ACTION_DELAY_MILLISECONDS + secrets.randbelow(ACTION_DELAY_RANGE_MILLISECONDS)
    await asyncio.sleep(delay_milliseconds / 1_000)
