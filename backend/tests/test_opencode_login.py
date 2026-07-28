import asyncio
from typing import List, Optional, Set, cast

import pytest
from playwright.async_api import Page

import browser.opencode_login as opencode_login_module
from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import OpenCodePageStatus
from browser.opencode_login import OPENCODE_AUTH_URL, OpenCodeLogin
from engine.models import ManualInterventionReason
from storage.models import BrowserAuthState


class FakeOpenCodeLocator:
    """
    OpenCode 页面测试定位器
    """

    def __init__(self, page: "FakeOpenCodePage", kind: str, count: int = 1) -> None:
        """
        初始化可控定位器

        :param page (FakeOpenCodePage): 所属测试页面
        :param kind (str): 定位器行为类型
        :param count (int): 匹配数量
        """

        self._page = page
        self._kind = kind
        self._count = count

    async def count(self) -> int:
        """
        返回定位器匹配数量

        :return int: 匹配数量
        """

        return self._count

    async def click(self, timeout: int) -> None:
        """
        模拟点击并推进页面状态

        :param timeout (int): 点击超时毫秒数

        :return None: 无返回值
        """

        del timeout
        if self._kind == "github_provider":
            self._page.url = "https://github.com/login/oauth/authorize?client_id=fake"
        elif self._kind == "authorize":
            self._page.url = "https://opencode.ai/workspace/wrk_REAL123"
        elif self._kind == "copy":
            self._page.copy_clicked = True

    async def is_enabled(self) -> bool:
        """
        返回测试按钮启用状态

        :return bool: 始终启用
        """

        return True

    def locator(self, selector: str) -> "FakeOpenCodeLocator":
        """
        返回默认密钥行中的复制按钮

        :param selector (str): 子元素选择器

        :return FakeOpenCodeLocator: 复制按钮定位器
        """

        self._page.copy_selector = selector
        return FakeOpenCodeLocator(self._page, "copy")


class FakeOpenCodePage:
    """
    OpenCode 适配器测试页面
    """

    def __init__(
        self,
        clipboard_value: object = None,
        hostile_auth_redirect: bool = False,
        clipboard_delay: float = 0,
    ) -> None:
        """
        初始化可控页面

        :param clipboard_value (object): 剪贴板返回值
        :param hostile_auth_redirect (bool): 是否模拟未知主机跳转
        :param clipboard_delay (float): 剪贴板读取延迟秒数
        """

        self.url = "about:blank"
        self.clipboard_value = clipboard_value
        self.hostile_auth_redirect = hostile_auth_redirect
        self.clipboard_delay = clipboard_delay
        self.visited_urls: List[str] = []
        self.row_selector: Optional[str] = None
        self.copy_selector: Optional[str] = None
        self.copy_clicked = False

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        """
        模拟导航并记录目标 URL

        :param url (str): 导航目标
        :param wait_until (str): 页面等待条件
        :param timeout (int): 导航超时毫秒数

        :return None: 无返回值
        """

        del wait_until, timeout
        self.visited_urls.append(url)
        if url == OPENCODE_AUTH_URL:
            self.url = (
                "https://evil.example/login"
                if self.hostile_auth_redirect
                else ("https://auth.opencode.ai/authorize?client_id=app")
            )
        else:
            self.url = url

    def locator(self, selector: str) -> FakeOpenCodeLocator:
        """
        根据真实页面选择器返回测试定位器

        :param selector (str): 页面选择器

        :return FakeOpenCodeLocator: 可控定位器
        """

        if selector == 'a[href="/github/authorize"]':
            return FakeOpenCodeLocator(self, "github_provider")
        self.row_selector = selector
        return FakeOpenCodeLocator(self, "row")

    def get_by_role(self, role: str, name: str, exact: bool) -> FakeOpenCodeLocator:
        """
        返回 GitHub OAuth 授权按钮

        :param role (str): 可访问角色
        :param name (str): 可访问名称
        :param exact (bool): 是否精确匹配

        :return FakeOpenCodeLocator: 授权按钮定位器
        """

        del role, name, exact
        return FakeOpenCodeLocator(self, "authorize")

    async def evaluate(self, expression: str) -> object:
        """
        返回测试剪贴板内容

        :param expression (str): 页面求值表达式

        :return object: 剪贴板测试值
        """

        del expression
        await asyncio.sleep(self.clipboard_delay)
        return self.clipboard_value


class FakeOpenCodeSession:
    """
    OpenCode 适配器测试浏览器会话
    """

    def __init__(self, page: FakeOpenCodePage) -> None:
        """
        初始化测试浏览器会话

        :param page (FakeOpenCodePage): 测试页面
        """

        self._page = page

    async def page(self) -> Page:
        """
        返回类型适配后的测试页面

        :return Page: Playwright 页面测试替身
        """

        return cast(Page, self._page)

    async def capture_auth_state(self, allowed_hosts: Set[str]) -> BrowserAuthState:
        """
        返回带目标域标记的空认证状态

        :param allowed_hosts (Set): 允许的认证主机

        :return BrowserAuthState: 测试认证状态
        """

        assert allowed_hosts
        return BrowserAuthState()


@pytest.mark.anyio
async def test_opencode_login_uses_verified_auth_and_go_routes() -> None:
    """
    验证适配器使用现场确认的 OAuth 与 OpenCode Go 路由
    """

    page = FakeOpenCodePage()
    client = OpenCodeLogin(cast(CloakBrowserSession, FakeOpenCodeSession(page)))

    result = await client.start_login()

    assert result.status == OpenCodePageStatus.PAYMENT_REQUIRED
    assert result.workspace_id == "wrk_REAL123"
    assert result.manual_reason == ManualInterventionReason.PAYMENT
    assert page.visited_urls == [
        "https://opencode.ai/auth",
        "https://opencode.ai/workspace/wrk_REAL123/go",
    ]


@pytest.mark.anyio
async def test_opencode_login_rejects_unknown_auth_host() -> None:
    """
    验证登录入口跳到未知主机时必须人工介入
    """

    page = FakeOpenCodePage(hostile_auth_redirect=True)
    client = OpenCodeLogin(cast(CloakBrowserSession, FakeOpenCodeSession(page)))

    result = await client.start_login()

    assert result.status == OpenCodePageStatus.MANUAL_REQUIRED
    assert result.manual_reason == ManualInterventionReason.UNKNOWN_BLOCK


@pytest.mark.anyio
async def test_opencode_login_reads_only_valid_default_api_key() -> None:
    """
    验证默认密钥选择器与剪贴板格式校验
    """

    api_key = "sk-" + "d" * 64
    page = FakeOpenCodePage(clipboard_value=api_key)
    client = OpenCodeLogin(cast(CloakBrowserSession, FakeOpenCodeSession(page)))
    client._workspace_id = "wrk_REAL123"

    result = await client.confirm_payment()

    assert result.status == OpenCodePageStatus.COMPLETED
    assert result.api_key is not None
    assert result.api_key.get_secret_value() == api_key
    assert api_key not in repr(result)
    assert page.row_selector == 'tr:has(td[data-slot="key-name"]:text-is("Default API Key"))'
    assert page.copy_selector == 'td[data-slot="key-value"] button[data-color="ghost"]'
    assert page.copy_clicked is True


@pytest.mark.anyio
async def test_opencode_login_falls_back_when_clipboard_is_invalid() -> None:
    """
    验证剪贴板不是合法 API Key 时请求人工复制
    """

    page = FakeOpenCodePage(clipboard_value="not-an-api-key")
    client = OpenCodeLogin(cast(CloakBrowserSession, FakeOpenCodeSession(page)))
    client._workspace_id = "wrk_REAL123"

    result = await client.confirm_payment()

    assert result.status == OpenCodePageStatus.API_KEY_INPUT_REQUIRED
    assert result.manual_reason == ManualInterventionReason.API_KEY_INPUT


@pytest.mark.anyio
async def test_opencode_login_falls_back_when_clipboard_permission_stalls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证剪贴板权限读取阻塞时限时转为人工输入
    """

    monkeypatch.setattr(opencode_login_module, "CLIPBOARD_READ_TIMEOUT_SECONDS", 0.01)
    page = FakeOpenCodePage(clipboard_value="sk-" + "d" * 64, clipboard_delay=30)
    client = OpenCodeLogin(cast(CloakBrowserSession, FakeOpenCodeSession(page)))
    client._workspace_id = "wrk_REAL123"

    result = await client.confirm_payment()

    assert result.status == OpenCodePageStatus.API_KEY_INPUT_REQUIRED
    assert result.manual_reason == ManualInterventionReason.API_KEY_INPUT
