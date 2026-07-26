from typing import Dict, List, Optional, cast

import pytest
from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import Error as BrowserError
from playwright.async_api import TimeoutError as BrowserTimeoutError

import browser.cloakbrowser_client as cloakbrowser_client_module
import browser.github_register as github_register_module
from browser.cloakbrowser_client import CloakBrowserClient, CloakBrowserSession
from browser.github_register import GitHubRegister
from browser.models import GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE, GitHubPageStatus


class FailingCloakBrowserSession(CloakBrowserSession):
    """
    浏览器启动失败测试会话
    """

    async def page(self) -> Page:
        """
        模拟 CloakBrowser 无法启动页面

        :return Page: 不会返回页面

        :raises BrowserError: 固定的浏览器启动失败
        """

        raise BrowserError("test browser launch failure")


class FakeFormLocator:
    """
    GitHub 当前注册表单控件测试替身
    """

    def __init__(
        self,
        *,
        is_checked: bool = False,
        fails_on_click: bool = False,
        is_visible: bool = True,
    ) -> None:
        """
        初始化表单控件状态

        :param is_checked (bool): 控件是否已勾选
        :param fails_on_click (bool): 点击后是否模拟浏览器错误
        :param is_visible (bool): 控件是否可见
        """

        self.value: Optional[str] = None
        self.is_checked_value = is_checked
        self.fails_on_click = fails_on_click
        self.is_visible_value = is_visible
        self.was_clicked = False

    @property
    def first(self) -> "FakeFormLocator":
        """
        返回第一个匹配控件

        :return FakeFormLocator: 当前控件
        """

        return self

    async def fill(self, value: str) -> None:
        """
        记录填入的表单值

        :param value (str): 表单值

        :return None: 无返回值
        """

        self.value = value

    async def count(self) -> int:
        """
        返回唯一匹配数量

        :return int: 固定为一个匹配
        """

        return 1

    async def is_checked(self) -> bool:
        """
        返回当前勾选状态

        :return bool: 是否已勾选
        """

        return self.is_checked_value

    async def wait_for(self, state: str, timeout: int) -> None:
        """
        等待控件进入可见状态

        :param state (str): 目标状态
        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值

        :raises BrowserTimeoutError: 控件不可见时模拟等待超时
        """

        assert state == "visible"
        del timeout
        if not self.is_visible_value:
            raise BrowserTimeoutError("test locator is not visible")

    async def uncheck(self, force: bool) -> None:
        """
        取消附加产品选项

        :param force (bool): 是否跳过控件可操作性检查

        :return None: 无返回值
        """

        assert force
        self.is_checked_value = False

    async def click(self, timeout: int) -> None:
        """
        记录提交点击并按需终止后续页面检测

        :param timeout (int): 点击超时毫秒数

        :return None: 无返回值

        :raises BrowserError: 配置为失败时模拟提交阶段错误
        """

        del timeout
        self.was_clicked = True
        if self.fails_on_click:
            raise BrowserError("stop after form assertions")


class FakeCurrentSignupPage:
    """
    GitHub 当前注册页面测试替身
    """

    def __init__(self, *, username_unavailable: bool = False) -> None:
        """
        初始化表单控件与页面地址

        :param username_unavailable (bool): 是否显示用户名占用提示
        """

        self.url = "https://github.com/signup"
        self.email = FakeFormLocator()
        self.password = FakeFormLocator()
        self.username = FakeFormLocator()
        self.copilot = FakeFormLocator(is_checked=True)
        self.create_account = FakeFormLocator(fails_on_click=True)
        self.username_unavailable = FakeFormLocator(is_visible=username_unavailable)

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        """
        模拟打开 GitHub 注册页

        :param url (str): 目标地址
        :param wait_until (str): 页面等待状态
        :param timeout (int): 导航超时毫秒数

        :return None: 无返回值
        """

        del wait_until, timeout
        self.url = url

    def locator(self, selector: str) -> FakeFormLocator:
        """
        按稳定选择器返回表单控件

        :param selector (str): 页面选择器

        :return FakeFormLocator: 对应表单控件
        """

        return {
            "#email": self.email,
            "#password": self.password,
            "#login": self.username,
        }[selector]

    def get_by_role(self, role: str, name: object, exact: bool = False) -> FakeFormLocator:
        """
        按可访问角色返回附加选项或提交按钮

        :param role (str): 控件角色
        :param name (object): 可访问名称
        :param exact (bool): 是否精确匹配名称

        :return FakeFormLocator: 对应表单控件
        """

        del name, exact
        if role == "checkbox":
            return self.copilot
        return self.create_account

    def get_by_text(self, text: object) -> FakeFormLocator:
        """
        返回用户名占用提示

        :param text (object): 文本匹配条件

        :return FakeFormLocator: 用户名占用提示控件
        """

        del text
        return self.username_unavailable


class FixedPageSession(CloakBrowserSession):
    """
    返回固定 GitHub 注册页面的测试会话
    """

    def __init__(self, manager: CloakBrowserClient, page: FakeCurrentSignupPage) -> None:
        """
        初始化固定页面测试会话

        :param manager (CloakBrowserClient): 浏览器生命周期管理器
        :param page (FakeCurrentSignupPage): 固定注册页面
        """

        super().__init__(manager)
        self._fixed_page = page

    async def page(self) -> Page:
        """
        返回固定注册页面

        :return Page: GitHub 注册页面测试替身
        """

        return cast(Page, self._fixed_page)


class FakeScreenshotPage:
    """
    已遮罩截图调用记录页面
    """

    def __init__(self) -> None:
        """
        初始化遮罩与截图参数记录
        """

        self.mask_selectors: List[str] = []
        self.masked_texts: List[str] = []
        self.screenshot_options: Dict[str, object] = {}

    def locator(self, selector: str) -> object:
        """
        记录必须遮罩的结构选择器

        :param selector (str): Playwright 选择器

        :return object: 虚构 locator
        """

        self.mask_selectors.append(selector)
        return ("selector", selector)

    def get_by_text(self, text: str, exact: bool = False) -> object:
        """
        记录必须遮罩的已知身份文本

        :param text (str): 敏感文本
        :param exact (bool): 是否精确匹配

        :return object: 虚构 locator
        """

        assert exact is False
        self.masked_texts.append(text)
        return ("text", text)

    async def screenshot(self, **options: object) -> bytes:
        """
        记录截图参数并返回虚构 PNG

        :param options (object): Playwright 截图参数

        :return bytes: 虚构 PNG
        """

        self.screenshot_options = options
        return b"\x89PNG\r\n\x1a\nmasked"


class ScreenshotPageSession(CloakBrowserSession):
    """
    返回截图参数记录页面的测试会话
    """

    def __init__(self, manager: CloakBrowserClient, page: FakeScreenshotPage) -> None:
        """
        初始化固定截图页面会话

        :param manager (CloakBrowserClient): 浏览器生命周期管理器
        :param page (FakeScreenshotPage): 截图页面替身
        """

        super().__init__(manager)
        self._fixed_screenshot_page = page

    async def page(self) -> Page:
        """
        返回固定截图页面

        :return Page: Playwright 页面测试替身
        """

        return cast(Page, self._fixed_screenshot_page)


class FakeCodeFieldGroup:
    """
    GitHub 八位验证码控件组测试替身
    """

    async def count(self) -> int:
        """
        返回当前 GitHub 验证码位数

        :return int: 固定为八位
        """

        return 8


class FakeVerificationPage:
    """
    GitHub 邮箱验证码页面测试替身
    """

    url = "https://github.com/account_verifications"

    def get_by_role(self, role: str) -> FakeCodeFieldGroup:
        """
        返回验证码输入控件组

        :param role (str): 页面控件角色

        :return FakeCodeFieldGroup: 验证码输入控件组
        """

        assert role == "spinbutton"
        return FakeCodeFieldGroup()


class FakeBrowserContext:
    """
    CloakBrowser 隔离上下文测试替身
    """

    def __init__(self) -> None:
        """
        初始化权限授予记录
        """

        self.permissions: Optional[List[str]] = None
        self.permission_origin: Optional[str] = None

    async def grant_permissions(self, permissions: List[str], origin: Optional[str] = None) -> None:
        """
        记录隔离上下文授予的站点权限

        :param permissions (List): 权限名称列表
        :param origin (str): 权限限定来源

        :return None: 无返回值
        """

        self.permissions = permissions
        self.permission_origin = origin

    async def new_page(self) -> Page:
        """
        返回无需真实浏览器的页面测试替身

        :return Page: 页面测试替身
        """

        return cast(Page, object())

    async def close(self) -> None:
        """
        模拟关闭隔离上下文

        :return None: 无返回值
        """


class FakeBrowser:
    """
    CloakBrowser 共享浏览器测试替身
    """

    def __init__(self) -> None:
        """
        初始化上下文参数记录
        """

        self.viewport: Optional[Dict[str, int]] = None
        self.context = FakeBrowserContext()

    async def new_context(self, viewport: Optional[Dict[str, int]] = None) -> BrowserContext:
        """
        记录会话创建使用的 viewport

        :param viewport (Dict): 浏览器页面尺寸

        :return BrowserContext: 隔离上下文测试替身
        """

        self.viewport = viewport
        return cast(BrowserContext, self.context)

    async def close(self) -> None:
        """
        模拟关闭共享浏览器

        :return None: 无返回值
        """

    def is_connected(self) -> bool:
        """
        返回浏览器连接状态

        :return bool: 固定为已连接
        """

        return True


class ClosedTransportBrowser(FakeBrowser):
    """
    关闭时报告传输通道已结束的浏览器测试替身
    """

    async def close(self) -> None:
        """
        模拟 CloakBrowser 清理竞态

        :return None: 无返回值

        :raises RuntimeError: 固定的传输通道关闭错误
        """

        raise RuntimeError("handler is closed")


@pytest.mark.anyio
async def test_invalid_email_code_does_not_open_browser() -> None:
    """
    验证无效邮箱验证码在浏览器操作前被拒绝
    """

    browser_client = CloakBrowserClient()
    register = GitHubRegister(browser_client.create_session())

    result = await register.submit_email_code("not-a-code")

    assert result.status == GitHubPageStatus.ERROR
    assert result.error_code == "github_email_code_invalid"
    await register.close()


@pytest.mark.anyio
async def test_cloakbrowser_client_close_is_idempotent_before_start() -> None:
    """
    验证浏览器尚未启动时重复关闭保持幂等
    """

    browser_client = CloakBrowserClient()

    await browser_client.close()
    await browser_client.close()


@pytest.mark.anyio
async def test_cloakbrowser_client_close_tolerates_closed_transport() -> None:
    """
    验证 CloakBrowser 已提前关闭传输通道时服务清理仍保持幂等
    """

    browser_client = CloakBrowserClient()
    browser_client._browser = cast(Browser, ClosedTransportBrowser())

    await browser_client.close()

    assert browser_client._browser is None


@pytest.mark.anyio
async def test_cloakbrowser_client_launches_headed_isolated_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证客户端使用可见 CloakBrowser 并创建固定尺寸的隔离上下文
    """

    fake_browser = FakeBrowser()
    launch_options: Dict[str, bool] = {}

    async def fake_launch_async(*, headless: bool) -> Browser:
        launch_options["headless"] = headless
        return cast(Browser, fake_browser)

    monkeypatch.setattr(cloakbrowser_client_module, "launch_async", fake_launch_async)
    browser_client = CloakBrowserClient()

    await browser_client.create_session().page()

    assert launch_options == {"headless": False}
    assert fake_browser.viewport == {"width": 1920, "height": 1080}
    assert fake_browser.context.permissions == ["clipboard-read", "clipboard-write"]
    assert fake_browser.context.permission_origin == "https://opencode.ai"
    await browser_client.close()


@pytest.mark.anyio
async def test_cloakbrowser_client_can_launch_background_session(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证额度检查可显式启动无窗口 CloakBrowser
    """

    fake_browser = FakeBrowser()
    launch_options: Dict[str, bool] = {}

    async def fake_launch_async(*, headless: bool) -> Browser:
        launch_options["headless"] = headless
        return cast(Browser, fake_browser)

    monkeypatch.setattr(cloakbrowser_client_module, "launch_async", fake_launch_async)
    browser_client = CloakBrowserClient(headless=True)

    await browser_client.create_session().page()

    assert launch_options == {"headless": True}
    await browser_client.close()


@pytest.mark.anyio
async def test_current_signup_form_opts_out_of_copilot_before_submit() -> None:
    """
    验证当前 GitHub 表单取消附加产品并通过可访问按钮提交
    """

    browser_client = CloakBrowserClient()
    page = FakeCurrentSignupPage()
    delay_count = 0

    async def record_delay() -> None:
        nonlocal delay_count
        delay_count += 1

    register = GitHubRegister(FixedPageSession(browser_client, page), record_delay)

    result = await register.start_registration("test@example.test", "river-notes42", "Secret123456789!")

    assert result.error_code == "github_form_submit_failed"
    assert page.email.value == "test@example.test"
    assert page.password.value == "Secret123456789!"
    assert page.username.value == "river-notes42"
    assert not page.copilot.is_checked_value
    assert page.create_account.was_clicked
    assert delay_count == 4


@pytest.mark.anyio
async def test_current_signup_form_reports_unavailable_username_before_submit() -> None:
    """
    验证 GitHub 用户名被占用时返回稳定错误且不提交表单
    """

    browser_client = CloakBrowserClient()
    page = FakeCurrentSignupPage(username_unavailable=True)

    async def skip_delay() -> None:
        return None

    register = GitHubRegister(FixedPageSession(browser_client, page), skip_delay)

    result = await register.start_registration("test@example.test", "cedar-field451", "Secret123456789!")

    assert result.status == GitHubPageStatus.ERROR
    assert result.error_code == GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE
    assert not page.create_account.was_clicked


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("random_offset", "expected_seconds"),
    [(0, 0.5), (1_500, 2.0)],
)
async def test_github_action_delay_stays_within_required_range(
    monkeypatch: pytest.MonkeyPatch,
    random_offset: int,
    expected_seconds: float,
) -> None:
    """
    验证 GitHub 表单操作延迟覆盖文档规定的闭区间

    :param monkeypatch (MonkeyPatch): Pytest 属性替换工具
    :param random_offset (int): 随机毫秒偏移测试值
    :param expected_seconds (float): 预期等待秒数
    """

    delays: List[float] = []

    def fake_randbelow(upper_bound: int) -> int:
        assert upper_bound == 1_501
        return random_offset

    async def fake_sleep(seconds: float) -> None:
        delays.append(seconds)

    monkeypatch.setattr(github_register_module.secrets, "randbelow", fake_randbelow)
    monkeypatch.setattr(github_register_module.asyncio, "sleep", fake_sleep)

    await github_register_module._wait_for_action_delay()

    assert delays == [expected_seconds]


@pytest.mark.anyio
async def test_email_verification_page_is_not_misclassified_as_completed() -> None:
    """
    验证 GitHub 新版邮箱验证码页面进入验证码处理状态
    """

    browser_client = CloakBrowserClient()
    register = GitHubRegister(browser_client.create_session())

    result = await register._detect_state(cast(Page, FakeVerificationPage()))

    assert result.status == GitHubPageStatus.EMAIL_CODE_REQUIRED


@pytest.mark.anyio
async def test_browser_launch_failure_has_stable_stage_error() -> None:
    """
    验证浏览器启动失败使用不含第三方细节的稳定阶段错误
    """

    browser_client = CloakBrowserClient()
    register = GitHubRegister(FailingCloakBrowserSession(browser_client))

    result = await register.start_registration("test@example.test", "river-notes42", "Secret123!")

    assert result.status == GitHubPageStatus.ERROR
    assert result.error_code == "github_browser_launch_failed"
    assert "test browser launch failure" not in (result.error_message or "")


@pytest.mark.anyio
async def test_screenshot_capture_masks_inputs_challenges_and_known_identity_text() -> None:
    """
    验证截图在浏览器层遮罩输入、挑战画布和已知账号身份文本
    """

    browser_client = CloakBrowserClient()
    page = FakeScreenshotPage()
    session = ScreenshotPageSession(browser_client, page)

    png = await session.capture_sanitized_screenshot(
        ["private@example.test", "private-user", "Fake-Password!"],
    )

    selector = page.mask_selectors[0]
    assert "input" in selector
    assert "iframe" in selector
    assert "canvas" in selector
    assert "data-slot='key-value'" in selector
    assert page.masked_texts == ["private@example.test", "private-user", "Fake-Password!"]
    assert page.screenshot_options["mask_color"] == "#202124"
    assert png.startswith(b"\x89PNG")


@pytest.mark.anyio
async def test_session_close_does_not_close_shared_manager() -> None:
    """
    验证关闭未启动的流程会话不会关闭共享浏览器管理器
    """

    browser_client = CloakBrowserClient()
    first_session = browser_client.create_session()

    await first_session.close()

    second_session = browser_client.create_session()
    assert second_session is not first_session
    await second_session.close()
    await browser_client.close()
