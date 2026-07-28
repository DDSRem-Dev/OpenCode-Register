from typing import List, Optional, Set, cast

import pytest
from playwright.async_api import Page

from browser.cloakbrowser_client import CloakBrowserSession
from browser.models import OpenCodeQuotaPageStatus
from browser.opencode_quota import (
    GITHUB_ACTOR_SELECTOR,
    GITHUB_HOME_URL,
    GO_USAGE_VALUE_SELECTOR,
    VISIBLE_CAPTCHA_SELECTORS,
    OpenCodeQuotaBrowser,
)
from engine.models import ManualInterventionReason
from storage.models import BrowserAuthState


class FakeQuotaLocator:
    """
    OpenCode Go 额度页面定位器测试替身
    """

    def __init__(self, page: "FakeQuotaPage", selector: str) -> None:
        """
        初始化测试定位器

        :param page (FakeQuotaPage): 所属测试页面
        :param selector (str): 选择器或按钮类型
        """

        self._page = page
        self._selector = selector

    async def count(self) -> int:
        """
        返回测试定位器数量

        :return int: 匹配数量
        """

        if self._selector == GITHUB_ACTOR_SELECTOR:
            return 1 if self._page.actor_login is not None else 0
        if self._selector == VISIBLE_CAPTCHA_SELECTORS:
            return 1 if self._page.has_captcha else 0
        if self._selector == "subscribe":
            return 1 if self._page.subscription_required else 0
        if self._selector == GO_USAGE_VALUE_SELECTOR:
            return len(self._page.usage_values)
        return 0

    async def all_text_contents(self) -> List[str]:
        """
        返回仪表盘三个额度百分比文本

        :return List: 额度百分比文本
        """

        assert self._selector == GO_USAGE_VALUE_SELECTOR
        return self._page.usage_values

    async def is_enabled(self) -> bool:
        """
        返回测试按钮启用状态

        :return bool: 始终启用
        """

        return True

    async def get_attribute(self, name: str) -> Optional[str]:
        """
        返回 GitHub 登录身份 meta 内容

        :param name (str): 属性名称

        :return str: 当前登录身份
        """

        assert name == "content"
        return self._page.actor_login


class FakeQuotaPage:
    """
    OpenCode Go 额度检查页面测试替身
    """

    def __init__(
        self,
        *,
        usage_values: Optional[List[str]] = None,
        authenticated_as: Optional[str] = "quota-user",
        workspace_redirect: str = "wrk_quota",
        has_captcha: bool = False,
        subscription_required: bool = False,
    ) -> None:
        """
        初始化可控认证与额度页面状态

        :param usage_values (List): 仪表盘三个额度百分比文本
        :param authenticated_as (str): Cookie 恢复后的 GitHub 身份
        :param workspace_redirect (str): OpenCode 实际工作区
        :param has_captcha (bool): 是否显示 CAPTCHA
        :param subscription_required (bool): 是否显示 OpenCode Go 订阅入口
        """

        self.url = "about:blank"
        self.actor_login: Optional[str] = None
        self.authenticated_as = authenticated_as
        self.workspace_redirect = workspace_redirect
        self.has_captcha = has_captcha
        self.subscription_required = subscription_required
        self.usage_values = usage_values if usage_values is not None else ["21%", "82%", "43%"]

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        """
        模拟受信任页面导航

        :param url (str): 目标 URL
        :param wait_until (str): 页面等待条件
        :param timeout (int): 导航超时毫秒数

        :return None: 无返回值
        """

        assert wait_until == "domcontentloaded"
        assert timeout == 30_000
        if url == GITHUB_HOME_URL:
            self.url = GITHUB_HOME_URL
            self.actor_login = self.authenticated_as
            return
        self.url = f"https://opencode.ai/workspace/{self.workspace_redirect}/go"

    def locator(self, selector: str) -> FakeQuotaLocator:
        """
        返回可控测试定位器

        :param selector (str): 页面选择器

        :return FakeQuotaLocator: 测试定位器
        """

        assert selector in {GITHUB_ACTOR_SELECTOR, VISIBLE_CAPTCHA_SELECTORS, GO_USAGE_VALUE_SELECTOR}
        return FakeQuotaLocator(self, selector)

    def get_by_role(self, role: str, name: str, exact: bool) -> FakeQuotaLocator:
        """
        返回订阅按钮测试替身

        :param role (str): 控件角色
        :param name (str): 控件名称
        :param exact (bool): 是否精确匹配

        :return FakeQuotaLocator: 可控按钮定位器
        """

        assert role == "button"
        assert exact is True
        assert name in {"订阅 Go", "Subscribe to Go", "Subscribe Go"}
        return FakeQuotaLocator(self, "subscribe")


class FakeQuotaSession(CloakBrowserSession):
    """
    OpenCode Go 额度浏览器会话测试替身
    """

    def __init__(self, page: FakeQuotaPage) -> None:
        """
        初始化固定页面会话

        :param page (FakeQuotaPage): 测试页面
        """

        self._fake_page = page
        self.restored_states: List[BrowserAuthState] = []
        self.closed = False

    def restore_auth_states(self, auth_states: List[BrowserAuthState]) -> None:
        """
        记录恢复的认证状态

        :param auth_states (List): GitHub 与 OpenCode 认证状态

        :return None: 无返回值
        """

        self.restored_states = auth_states

    async def capture_auth_state(self, allowed_hosts: Set[str]) -> BrowserAuthState:
        """
        返回滚动更新后的测试认证状态

        :param allowed_hosts (Set): 允许保存的主机

        :return BrowserAuthState: 测试认证状态
        """

        assert allowed_hosts
        return BrowserAuthState()

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


def _auth_state() -> BrowserAuthState:
    return BrowserAuthState()


@pytest.mark.anyio
async def test_opencode_quota_browser_reuses_session_and_reads_monthly_usage() -> None:
    """
    验证后台浏览器复用认证状态、核对身份并读取月度用量
    """

    page = FakeQuotaPage(usage_values=["21%", "82%", "43%"])
    session = FakeQuotaSession(page)
    browser = OpenCodeQuotaBrowser(session)

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.UPDATED
    assert result.usage_percent == 43
    assert page.actor_login == "quota-user"
    assert len(session.restored_states) == 2
    assert result.github_auth_state is not None
    assert result.opencode_auth_state is not None


@pytest.mark.anyio
async def test_opencode_quota_browser_reports_expired_github_session() -> None:
    """
    验证 GitHub Cookie 失效时要求重新认证且不填写密码
    """

    page = FakeQuotaPage(authenticated_as=None)
    browser = OpenCodeQuotaBrowser(FakeQuotaSession(page))

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.AUTH_REQUIRED
    assert result.error_code == "quota_github_session_expired"


@pytest.mark.anyio
async def test_opencode_quota_browser_preserves_captcha_boundary() -> None:
    """
    验证 Cookie 访问遇到 CAPTCHA 时始终请求人工处理
    """

    page = FakeQuotaPage(has_captcha=True)
    browser = OpenCodeQuotaBrowser(FakeQuotaSession(page))

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.MANUAL_REQUIRED
    assert result.manual_reason == ManualInterventionReason.CAPTCHA


@pytest.mark.anyio
async def test_opencode_quota_browser_rejects_workspace_mismatch() -> None:
    """
    验证 Cookie 指向的 OpenCode workspace 与保存目标不一致时安全失败
    """

    page = FakeQuotaPage(workspace_redirect="wrk_other")
    browser = OpenCodeQuotaBrowser(FakeQuotaSession(page))

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.MANUAL_REQUIRED


@pytest.mark.anyio
async def test_opencode_quota_browser_rejects_malformed_dashboard_values() -> None:
    """
    验证仪表盘额度节点格式异常时不会猜测页面数据
    """

    page = FakeQuotaPage(usage_values=["12%", "not-a-percent", "34%"])
    browser = OpenCodeQuotaBrowser(FakeQuotaSession(page))

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.UNAVAILABLE
    assert result.error_code == "quota_dashboard_dom_invalid"


@pytest.mark.anyio
async def test_opencode_quota_browser_detects_missing_subscription() -> None:
    """
    验证仪表盘明确显示订阅入口时返回无有效订阅状态
    """

    page = FakeQuotaPage(subscription_required=True, usage_values=[])
    browser = OpenCodeQuotaBrowser(FakeQuotaSession(page))

    result = await browser.start_check("quota-user", "wrk_quota", _auth_state(), _auth_state())

    assert result.status == OpenCodeQuotaPageStatus.SUBSCRIPTION_REQUIRED
