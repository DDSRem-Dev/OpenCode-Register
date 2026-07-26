from typing import Dict, Optional, cast

import pytest
from playwright.async_api import Locator, Page

from browser.cloakbrowser_client import CloakBrowserClient, CloakBrowserSession
from browser.temp_mail import (
    INBOX_REFRESH_SETTLE_MILLISECONDS,
    MAILBOX_SELECTOR,
    MESSAGE_CODE_SELECTOR,
    MESSAGE_CONTENT_SELECTOR,
    MESSAGE_ROW_SELECTOR,
    OPEN_MESSAGE_SELECTOR,
    REFRESH_LINK_NAME,
    SENDER_SELECTOR,
    SUBJECT_SELECTOR,
    TempMailBrowser,
)
from providers.errors import EmailProviderResponseError
from providers.models import TempMailProviderSettings

TEMP_MAIL_RENDER_DELAY_MILLISECONDS = 7000


class FakeTempMailLocator:
    """
    Temp-Mail 页面 locator 测试替身
    """

    def __init__(self, page: "FakeTempMailPage", kind: str) -> None:
        """
        初始化 locator 测试状态

        :param page (FakeTempMailPage): 所属测试页面
        :param kind (str): locator 类型
        """

        self._page = page
        self._kind = kind

    async def count(self) -> int:
        """
        返回当前 locator 数量

        :return int: 匹配数量
        """

        if self._kind == "rows":
            inbox_ready = (
                not self._page.refresh_clicked
                or self._page.refresh_wait_milliseconds >= TEMP_MAIL_RENDER_DELAY_MILLISECONDS
            )
            return 2 if self._page.has_message and inbox_ready else 1
        if self._kind == "content":
            return 1 if self._page.message_opened else 0
        return 1

    async def wait_for(self, state: str, timeout: int) -> None:
        """
        接受可见状态等待

        :param state (str): 目标状态
        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值
        """

        del state, timeout

    async def input_value(self, timeout: int) -> str:
        """
        返回测试邮箱地址

        :param timeout (int): 最大等待毫秒数

        :return str: 测试邮箱地址
        """

        del timeout
        return "Browser.Box@Example.Test"

    async def inner_text(self, timeout: Optional[int] = None) -> str:
        """
        返回当前邮件字段文本

        :param timeout (int): 可选最大等待毫秒数

        :return str: 字段文本
        """

        del timeout
        values = {
            "sender": "GitHub <noreply@github.com>",
            "subject": "Your GitHub launch code",
            "content": self._page.direct_body,
        }
        return values.get(self._kind, "")

    async def click(self, timeout: int) -> None:
        """
        打开测试邮件详情

        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值
        """

        del timeout
        if self._kind == "refresh":
            self._page.refresh_clicked = True
        else:
            self._page.message_opened = True

    def locator(self, selector: str) -> Locator:
        """
        返回邮件行内字段 locator

        :param selector (str): 页面选择器

        :return Locator: locator 测试替身
        """

        if selector == "iframe":
            self._page.iframe_lookup_count += 1
            if self._page.iframe_body is not None:
                return cast(Locator, FakeIframeLocator(self._page))
            return cast(Locator, FakeEmptyLocator())
        if self._kind == "template":
            return cast(Locator, FakeEmptyLocator())
        kinds = {
            SENDER_SELECTOR: "sender",
            SUBJECT_SELECTOR: "subject",
            OPEN_MESSAGE_SELECTOR: "open",
        }
        return cast(Locator, FakeTempMailLocator(self._page, kinds[selector]))

    def nth(self, index: int) -> Locator:
        """
        返回指定测试邮件行

        :param index (int): 邮件行序号

        :return Locator: 邮件行 locator
        """

        assert index in {0, 1}
        kind = "template" if index == 0 else "row"
        return cast(Locator, FakeTempMailLocator(self._page, kind))


class FakeTempMailPage:
    """
    Temp-Mail 页面测试替身
    """

    def __init__(
        self,
        url: str = "https://temp-mail.org/en/",
        direct_body: str = "Enter 24681357 to continue.",
        iframe_body: Optional[str] = None,
        message_code: Optional[str] = None,
        delayed_message_code: Optional[str] = None,
    ) -> None:
        """
        初始化测试页面

        :param url (str): 页面最终地址
        :param direct_body (str): 邮件外层正文
        :param iframe_body (str): 可选 iframe 正文
        :param message_code (str): 可选大字号验证码节点文本
        :param delayed_message_code (str): 打开详情后延迟渲染的验证码节点文本
        """

        self.url = url
        self.direct_body = direct_body
        self.iframe_body = iframe_body
        self.message_code = message_code
        self.delayed_message_code = delayed_message_code
        self.has_message = True
        self.message_opened = False
        self.message_render_waited = False
        self.refresh_clicked = False
        self.refresh_wait_milliseconds = 0
        self.reload_called = False
        self.iframe_lookup_count = 0

    async def goto(self, url: str, wait_until: str, timeout: int) -> None:
        """
        记录页面导航但保留配置的最终地址

        :param url (str): 请求页面地址
        :param wait_until (str): 页面等待状态
        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值
        """

        del url, wait_until, timeout

    async def reload(self, wait_until: str, timeout: int) -> None:
        """
        模拟刷新收件箱

        :param wait_until (str): 页面等待状态
        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值
        """

        del wait_until, timeout
        self.reload_called = True

    async def wait_for_function(
        self,
        expression: str,
        arg: Optional[Dict[str, str]] = None,
        timeout: int = 0,
    ) -> None:
        """
        模拟等待页面异步填入邮箱

        :param expression (str): 页面条件表达式
        :param arg (Dict): 页面条件参数
        :param timeout (int): 最大等待毫秒数

        :return None: 无返回值
        """

        del expression, timeout
        if arg is None:
            return
        assert arg == {
            "contentSelector": MESSAGE_CONTENT_SELECTOR,
            "codeSelector": MESSAGE_CODE_SELECTOR,
        }
        self.message_render_waited = True
        if self.delayed_message_code is not None:
            self.message_code = self.delayed_message_code

    async def wait_for_timeout(self, timeout: int) -> None:
        """
        模拟收件箱刷新后的短暂稳定等待

        :param timeout (int): 等待毫秒数

        :return None: 无返回值
        """

        self.refresh_wait_milliseconds = timeout

    def get_by_role(self, role: str, name: str, exact: bool = False) -> Locator:
        """
        按可访问名称返回刷新控件

        :param role (str): 控件角色
        :param name (str): 可访问名称
        :param exact (bool): 是否精确匹配名称

        :return Locator: 刷新控件 locator
        """

        assert role == "link"
        assert name == REFRESH_LINK_NAME
        assert exact is True
        return cast(Locator, FakeTempMailLocator(self, "refresh"))

    def locator(self, selector: str) -> Locator:
        """
        按选择器返回页面 locator

        :param selector (str): 页面选择器

        :return Locator: locator 测试替身
        """

        kinds = {
            MAILBOX_SELECTOR: "mailbox",
            MESSAGE_ROW_SELECTOR: "rows",
            MESSAGE_CONTENT_SELECTOR: "content",
        }
        if selector == MESSAGE_CODE_SELECTOR:
            return cast(Locator, FakeMessageCodeLocator(self))
        return cast(Locator, FakeTempMailLocator(self, kinds[selector]))


class FakeEmptyLocator:
    """
    始终不匹配元素的 locator 测试替身
    """

    async def count(self) -> int:
        """
        返回零个匹配元素

        :return int: 零
        """

        return 0


class FakeIframeLocator:
    """
    Temp-Mail 邮件 iframe 测试替身
    """

    def __init__(self, page: FakeTempMailPage) -> None:
        """
        初始化 iframe 测试替身

        :param page (FakeTempMailPage): 所属测试页面
        """

        self._page = page

    async def count(self) -> int:
        """
        返回固定 iframe 数量

        :return int: 固定为一个 iframe
        """

        return 1

    def nth(self, index: int) -> Locator:
        """
        返回指定 iframe

        :param index (int): iframe 序号

        :return Locator: 当前 iframe
        """

        assert index == 0
        return cast(Locator, self)

    @property
    def content_frame(self) -> "FakeIframeFrameLocator":
        """
        返回 iframe frame locator

        :return FakeIframeFrameLocator: iframe frame locator
        """

        return FakeIframeFrameLocator(self._page)


class FakeIframeFrameLocator:
    """
    Temp-Mail iframe frame locator 测试替身
    """

    def __init__(self, page: FakeTempMailPage) -> None:
        """
        初始化 iframe frame locator

        :param page (FakeTempMailPage): 所属测试页面
        """

        self._page = page

    def locator(self, selector: str) -> Locator:
        """
        返回 iframe 正文 locator

        :param selector (str): iframe 内选择器

        :return Locator: iframe 正文 locator
        """

        assert selector == "body"
        return cast(Locator, FakeIframeBodyLocator(self._page))


class FakeIframeBodyLocator:
    """
    Temp-Mail iframe 正文测试替身
    """

    def __init__(self, page: FakeTempMailPage) -> None:
        """
        初始化 iframe 正文 locator

        :param page (FakeTempMailPage): 所属测试页面
        """

        self._page = page

    async def count(self) -> int:
        """
        返回固定正文数量

        :return int: 固定为一个正文
        """

        return 1

    async def inner_text(self, timeout: int) -> str:
        """
        返回 iframe 邮件正文

        :param timeout (int): 最大等待毫秒数

        :return str: iframe 邮件正文
        """

        del timeout
        return self._page.iframe_body or ""


class FakeMessageCodeLocator:
    """
    Temp-Mail 大字号验证码节点测试替身
    """

    def __init__(self, page: FakeTempMailPage) -> None:
        """
        初始化验证码节点 locator

        :param page (FakeTempMailPage): 所属测试页面
        """

        self._page = page

    async def count(self) -> int:
        """
        返回验证码节点数量

        :return int: 验证码存在时返回一，否则返回零
        """

        return 1 if self._page.message_code is not None else 0

    def nth(self, index: int) -> Locator:
        """
        返回指定验证码节点

        :param index (int): 节点序号

        :return Locator: 当前验证码节点
        """

        assert index == 0
        return cast(Locator, self)

    async def inner_text(self) -> str:
        """
        返回验证码节点文本

        :return str: 验证码节点文本
        """

        return self._page.message_code or ""


class FakeTempMailSession(CloakBrowserSession):
    """
    固定返回 Temp-Mail 测试页面的浏览器会话
    """

    def __init__(self, page: FakeTempMailPage) -> None:
        """
        初始化固定页面会话

        :param page (FakeTempMailPage): Temp-Mail 测试页面
        """

        super().__init__(CloakBrowserClient())
        self._fixed_page = page
        self.closed = False

    async def page(self) -> Page:
        """
        返回固定 Temp-Mail 测试页面

        :return Page: Temp-Mail 页面测试替身
        """

        return cast(Page, self._fixed_page)

    async def close(self) -> None:
        """
        记录浏览器会话关闭

        :return None: 无返回值
        """

        self.closed = True


@pytest.mark.anyio
async def test_temp_mail_browser_reads_mailbox_and_message() -> None:
    """
    验证 Temp-Mail 浏览器读取邮箱、发件人、主题和正文
    """

    page = FakeTempMailPage()
    session = FakeTempMailSession(page)
    mailbox = TempMailBrowser(session, TempMailProviderSettings())

    address = await mailbox.create_mailbox()
    messages = await mailbox.read_messages()
    await mailbox.close()

    assert address == "browser.box@example.test"
    assert len(messages) == 1
    assert messages[0].sender == "GitHub <noreply@github.com>"
    assert messages[0].body == "Enter 24681357 to continue."
    assert page.refresh_clicked is True
    assert page.refresh_wait_milliseconds == INBOX_REFRESH_SETTLE_MILLISECONDS
    assert page.refresh_wait_milliseconds > TEMP_MAIL_RENDER_DELAY_MILLISECONDS
    assert page.iframe_lookup_count == 1
    assert page.reload_called is False
    assert session.closed is True


@pytest.mark.anyio
async def test_temp_mail_browser_combines_outer_and_iframe_message_body() -> None:
    """
    验证验证码仅位于 iframe 时仍会合并到邮件正文
    """

    page = FakeTempMailPage(
        direct_body="GitHub verification message",
        iframe_body="Enter 13572468 to continue.",
    )
    mailbox = TempMailBrowser(FakeTempMailSession(page), TempMailProviderSettings())

    await mailbox.create_mailbox()
    messages = await mailbox.read_messages()

    assert len(messages) == 1
    assert messages[0].body == "GitHub verification message\nEnter 13572468 to continue."


@pytest.mark.anyio
async def test_temp_mail_browser_reads_large_code_span_from_message_page() -> None:
    """
    验证详情页大字号 span 中的八位验证码会加入邮件正文
    """

    page = FakeTempMailPage(
        direct_body="GitHub verification message",
        message_code="86421357",
    )
    mailbox = TempMailBrowser(FakeTempMailSession(page), TempMailProviderSettings())

    await mailbox.create_mailbox()
    messages = await mailbox.read_messages()

    assert len(messages) == 1
    assert messages[0].body == "GitHub verification message\n86421357"


@pytest.mark.anyio
async def test_temp_mail_browser_waits_for_delayed_message_render() -> None:
    """
    验证进入详情页后会等待异步渲染的验证码正文
    """

    page = FakeTempMailPage(
        direct_body="GitHub verification message",
        delayed_message_code="75318642",
    )
    mailbox = TempMailBrowser(FakeTempMailSession(page), TempMailProviderSettings())

    await mailbox.create_mailbox()
    messages = await mailbox.read_messages()

    assert page.message_render_waited is True
    assert len(messages) == 1
    assert messages[0].body == "GitHub verification message\n75318642"


@pytest.mark.anyio
async def test_temp_mail_browser_rejects_untrusted_redirect() -> None:
    """
    验证 Temp-Mail 浏览器拒绝未批准主机并关闭会话
    """

    session = FakeTempMailSession(FakeTempMailPage("https://untrusted.example/en/"))
    mailbox = TempMailBrowser(session, TempMailProviderSettings())

    with pytest.raises(EmailProviderResponseError, match="未受信任地址"):
        await mailbox.create_mailbox()

    assert session.closed is True
