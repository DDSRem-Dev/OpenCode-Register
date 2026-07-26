from typing import Dict, Optional, cast

import pytest
from playwright.async_api import Page
from pydantic import SecretStr

from browser.cloakbrowser_client import CloakBrowserSession
from browser.github_cleanup import (
    ACTOR_LOGIN_SELECTOR,
    DELETE_ACCOUNT_BUTTON_NAME,
    DELETE_CONFIRMATION_LABEL,
    DELETE_CONFIRMATION_TEXT,
    DELETE_SUBMIT_BUTTON_NAME,
    DELETE_USERNAME_LABEL,
    GITHUB_ADMIN_PATH,
    GITHUB_ADMIN_URL,
    GITHUB_LOGIN_PATH,
    LOGIN_ERROR_SELECTOR,
    LOGIN_USERNAME_SELECTOR,
    PASSWORD_SELECTOR,
    SUDO_CONFIRM_BUTTON_NAME,
    SUDO_PASSWORD_LABEL,
    VISIBLE_CAPTCHA_SELECTORS,
    GitHubAccountCleanup,
)
from browser.models import GitHubCleanupPageStatus
from engine.models import ManualInterventionReason


class FakeLocator:
    """
    GitHub 清理页面控件测试替身
    """

    def __init__(self, page: "FakeCleanupPage", selector: str) -> None:
        """
        初始化控件选择器

        :param page (FakeCleanupPage): 所属测试页面
        :param selector (str): 控件选择器
        """

        self._page = page
        self._selector = selector

    async def count(self) -> int:
        """
        返回当前测试控件数量

        :return int: 控件数量
        """

        if self._selector == ACTOR_LOGIN_SELECTOR:
            return 1 if self._page.actor_login is not None else 0
        if self._selector == LOGIN_ERROR_SELECTOR:
            is_sudo = self._page.url.endswith(f"/users/{self._page.expected_username}")
            return 1 if self._page.login_invalid or (self._page.sudo_invalid and is_sudo) else 0
        if self._selector == VISIBLE_CAPTCHA_SELECTORS:
            return 1 if self._page.has_captcha else 0
        if self._selector in {DELETE_USERNAME_LABEL, DELETE_CONFIRMATION_LABEL}:
            return 1 if self._page.delete_dialog_open else 0
        if self._selector == SUDO_PASSWORD_LABEL:
            return 1 if self._page.url.endswith(f"/users/{self._page.expected_username}") else 0
        return 1

    async def fill(self, value: str) -> None:
        """
        记录登录表单输入

        :param value (str): 输入值

        :return None: 无返回值
        """

        self._page.filled[self._selector] = value

    async def get_attribute(self, name: str) -> Optional[str]:
        """
        返回登录身份 meta 内容

        :param name (str): 属性名称

        :return str: 当前身份内容
        """

        assert name == "content"
        return self._page.actor_login


class FakeButton:
    """
    GitHub 登录按钮测试替身
    """

    def __init__(self, page: "FakeCleanupPage", name: str) -> None:
        """
        初始化 GitHub 操作按钮

        :param page (FakeCleanupPage): 所属测试页面
        """

        self._page = page
        self._name = name

    async def count(self) -> int:
        """
        返回当前测试按钮数量

        :return int: 按钮数量
        """

        if self._name == DELETE_SUBMIT_BUTTON_NAME:
            return 1 if self._page.delete_dialog_open else 0
        if self._name == DELETE_ACCOUNT_BUTTON_NAME:
            return self._page.delete_button_count
        if self._name == SUDO_CONFIRM_BUTTON_NAME:
            return 1 if self._page.url.endswith(f"/users/{self._page.expected_username}") else 0
        return 1

    async def click(self, timeout: int) -> None:
        """
        模拟登录、删除确认或 sudo 提交

        :param timeout (int): 浏览器点击超时

        :return None: 无返回值
        """

        assert timeout == 15_000
        if self._name == "Sign in":
            if self._page.login_invalid or self._page.has_captcha:
                return
            self._page.url = "https://github.com/"
            self._page.actor_login = self._page.authenticated_as or self._page.filled.get(LOGIN_USERNAME_SELECTOR)
            self._page.expected_username = self._page.filled.get(LOGIN_USERNAME_SELECTOR, "")
            return
        if self._name == DELETE_ACCOUNT_BUTTON_NAME:
            self._page.delete_dialog_open = True
            return
        if self._name == DELETE_SUBMIT_BUTTON_NAME:
            assert self._page.filled[DELETE_USERNAME_LABEL] == self._page.expected_username
            assert self._page.filled[DELETE_CONFIRMATION_LABEL] == DELETE_CONFIRMATION_TEXT
            self._page.url = f"https://github.com/users/{self._page.expected_username}"
            return
        assert self._name == SUDO_CONFIRM_BUTTON_NAME
        if self._page.sudo_invalid:
            return
        self._page.actor_login = None
        self._page.profile_status = 404
        self._page.url = "https://github.com/"


class FakeProfileResponse:
    """
    GitHub 公开资料响应测试替身
    """

    def __init__(self, status: int) -> None:
        """
        初始化 HTTP 状态

        :param status (int): 公开资料状态码
        """

        self.status = status


class FakeRequestContext:
    """
    GitHub 页面请求上下文测试替身
    """

    def __init__(self, page: "FakeCleanupPage") -> None:
        """
        初始化公开资料状态

        :param page (FakeCleanupPage): 所属测试页面
        """

        self._page = page

    async def get(self, url: str, timeout: int, fail_on_status_code: bool) -> FakeProfileResponse:
        """
        返回公开资料测试状态

        :param url (str): 公开资料 URL
        :param timeout (int): 请求超时
        :param fail_on_status_code (bool): 是否按非成功状态抛错

        :return FakeProfileResponse: 测试响应
        """

        assert url.startswith("https://github.com/")
        assert timeout == 15_000
        assert fail_on_status_code is False
        return FakeProfileResponse(self._page.profile_status)


class FakeCleanupPage:
    """
    GitHub 账号清理页面测试替身
    """

    def __init__(
        self,
        *,
        authenticated_as: Optional[str] = None,
        login_invalid: bool = False,
        sudo_invalid: bool = False,
        has_captcha: bool = False,
        delete_button_count: int = 1,
        profile_status: int = 200,
    ) -> None:
        """
        初始化测试页面状态

        :param authenticated_as (str): 登录成功后身份
        :param login_invalid (bool): 是否模拟凭据无效
        :param sudo_invalid (bool): 是否模拟 sudo 密码无效
        :param has_captcha (bool): 是否模拟验证码
        :param delete_button_count (int): 删除入口按钮数量
        :param profile_status (int): 公开资料状态码
        """

        self.url = f"https://github.com{GITHUB_LOGIN_PATH}"
        self.actor_login: Optional[str] = None
        self.authenticated_as = authenticated_as
        self.login_invalid = login_invalid
        self.sudo_invalid = sudo_invalid
        self.has_captcha = has_captcha
        self.delete_button_count = delete_button_count
        self.delete_dialog_open = False
        self.expected_username = ""
        self.profile_status = profile_status
        self.filled: Dict[str, str] = {}
        self.request = FakeRequestContext(self)

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        """
        模拟 GitHub 页面导航

        :param url (str): 目标 URL
        :param wait_until (str): 页面等待条件
        :param timeout (int): 导航超时

        :return None: 无返回值
        """

        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        self.url = url
        if url == GITHUB_ADMIN_URL and self.actor_login is not None:
            self.url = f"https://github.com{GITHUB_ADMIN_PATH}"

    def locator(self, selector: str) -> FakeLocator:
        """
        返回控件测试替身

        :param selector (str): 控件选择器

        :return FakeLocator: 控件测试替身
        """

        return FakeLocator(self, selector)

    def get_by_label(self, text: str, exact: bool) -> FakeLocator:
        """
        返回按标签定位的测试输入框

        :param text (str): 输入框标签
        :param exact (bool): 是否精确匹配标签

        :return FakeLocator: 输入框测试替身
        """

        assert exact is True
        return FakeLocator(self, text)

    def get_by_role(self, role: str, name: str, exact: bool) -> FakeButton:
        """
        返回登录按钮测试替身

        :param role (str): 控件角色
        :param name (str): 控件名称
        :param exact (bool): 是否精确匹配名称

        :return FakeButton: GitHub 操作按钮测试替身
        """

        assert role == "button"
        assert name in {
            "Sign in",
            DELETE_ACCOUNT_BUTTON_NAME,
            DELETE_SUBMIT_BUTTON_NAME,
            SUDO_CONFIRM_BUTTON_NAME,
        }
        assert exact is True
        return FakeButton(self, name)


class FakeCleanupSession(CloakBrowserSession):
    """
    GitHub 账号清理浏览器会话测试替身
    """

    def __init__(self, page: FakeCleanupPage) -> None:
        """
        初始化固定页面会话

        :param page (FakeCleanupPage): 测试页面
        """

        self._fake_page = page
        self.closed = False

    async def page(self) -> Page:
        """
        返回固定测试页面

        :return Page: Playwright 页面接口测试替身
        """

        return cast(Page, self._fake_page)

    async def close(self) -> None:
        """
        记录测试会话关闭

        :return None: 无返回值
        """

        self.closed = True


@pytest.mark.anyio
async def test_github_cleanup_verifies_identity_and_submits_confirmed_deletion() -> None:
    """
    验证 GitHub 清理只为完全匹配的身份提交删除和 sudo 密码
    """

    page = FakeCleanupPage(authenticated_as="phase-seven-user")
    cleanup = GitHubAccountCleanup(FakeCleanupSession(page))

    result = await cleanup.start_cleanup("phase-seven-user", SecretStr("Fake-GitHub-Password!"))

    assert result.status == GitHubCleanupPageStatus.DELETED
    assert page.filled[PASSWORD_SELECTOR] == "Fake-GitHub-Password!"
    assert page.filled[DELETE_USERNAME_LABEL] == "phase-seven-user"
    assert page.filled[DELETE_CONFIRMATION_LABEL] == DELETE_CONFIRMATION_TEXT
    assert page.filled[SUDO_PASSWORD_LABEL] == "Fake-GitHub-Password!"
    assert "Fake-GitHub-Password!" not in result.model_dump_json()


@pytest.mark.anyio
async def test_github_cleanup_rejects_mismatched_identity() -> None:
    """
    验证当前登录身份与删除目标不一致时安全失败
    """

    cleanup = GitHubAccountCleanup(FakeCleanupSession(FakeCleanupPage(authenticated_as="other-user")))

    result = await cleanup.start_cleanup("phase-seven-user", SecretStr("Fake-GitHub-Password!"))

    assert result.status == GitHubCleanupPageStatus.ERROR
    assert result.error_code == "github_cleanup_identity_mismatch"


@pytest.mark.anyio
async def test_github_cleanup_preserves_manual_captcha_boundary() -> None:
    """
    验证登录验证码始终返回人工介入而不尝试绕过
    """

    cleanup = GitHubAccountCleanup(FakeCleanupSession(FakeCleanupPage(has_captcha=True)))

    result = await cleanup.start_cleanup("phase-seven-user", SecretStr("Fake-GitHub-Password!"))

    assert result.status == GitHubCleanupPageStatus.MANUAL_REQUIRED
    assert result.manual_reason == ManualInterventionReason.CAPTCHA


@pytest.mark.anyio
async def test_github_cleanup_pauses_when_delete_control_is_not_unique() -> None:
    """
    验证删除入口缺失或重复时安全暂停且不猜测目标控件
    """

    page = FakeCleanupPage(authenticated_as="phase-seven-user", delete_button_count=2)
    cleanup = GitHubAccountCleanup(FakeCleanupSession(page))

    result = await cleanup.start_cleanup("phase-seven-user", SecretStr("Fake-GitHub-Password!"))

    assert result.status == GitHubCleanupPageStatus.MANUAL_REQUIRED
    assert result.manual_reason == ManualInterventionReason.UNKNOWN_BLOCK
    assert DELETE_USERNAME_LABEL not in page.filled


@pytest.mark.anyio
async def test_github_cleanup_only_accepts_profile_not_found_as_deleted() -> None:
    """
    验证只有目标公开资料返回 404 才确认远端删除完成
    """

    page = FakeCleanupPage(authenticated_as="phase-seven-user", sudo_invalid=True)
    session = FakeCleanupSession(page)
    cleanup = GitHubAccountCleanup(session)
    manual = await cleanup.start_cleanup("phase-seven-user", SecretStr("Fake-GitHub-Password!"))
    assert manual.status == GitHubCleanupPageStatus.INVALID

    page.actor_login = None
    page.profile_status = 404
    page.url = "https://github.com/"

    result = await cleanup.inspect_after_manual()

    assert result.status == GitHubCleanupPageStatus.DELETED
