import asyncio
import re
from typing import List, Optional
from urllib.parse import urlsplit

from playwright.async_api import Error as BrowserError
from playwright.async_api import Locator, Page
from playwright.async_api import TimeoutError as BrowserTimeoutError

from browser.cloakbrowser_client import CloakBrowserSession
from providers.base import TempMailMailboxClient
from providers.errors import EmailProviderResponseError
from providers.models import TempMailMessage, TempMailProviderSettings
from providers.validation import normalize_email_address

ALLOWED_TEMP_MAIL_HOST = "temp-mail.org"
MAILBOX_SELECTOR = "#mail"
MESSAGE_ROW_SELECTOR = ".inbox-dataList li"
SENDER_SELECTOR = ".inboxSenderEmail"
SUBJECT_SELECTOR = ".inboxSubject a[data-mail-id]"
OPEN_MESSAGE_SELECTOR = ".viewLink.title-subject[data-mail-id]"
MESSAGE_CONTENT_SELECTOR = ".inbox-data-content-intro"
MESSAGE_CODE_SELECTOR = "span[style*='font-size: 40px'][style*='font-weight: 300']"
REFRESH_LINK_NAME = "Refresh"
MAX_INBOX_MESSAGES = 50
MAX_MESSAGE_CODE_NODES = 10
INBOX_REFRESH_SETTLE_MILLISECONDS = 10000
MESSAGE_CODE_PATTERN = re.compile(r"^[0-9]{8}$")


class TempMailBrowser(TempMailMailboxClient):
    """
    Temp-Mail 页面浏览器适配器

    Attributes:
        _browser_session: 独立临时邮箱浏览器会话
        _settings: Temp-Mail 页面设置
        _page: 已打开的 Temp-Mail 页面
    """

    def __init__(self, browser_session: CloakBrowserSession, settings: TempMailProviderSettings) -> None:
        """
        初始化 Temp-Mail 浏览器适配器

        :param browser_session (CloakBrowserSession): 独立临时邮箱浏览器会话
        :param settings (TempMailProviderSettings): Temp-Mail 页面设置
        """

        self._browser_session = browser_session
        self._settings = settings
        self._page: Optional[Page] = None

    async def create_mailbox(self) -> str:
        """
        打开 Temp-Mail 页面并读取自动生成的邮箱地址

        :return str: 规范化后的临时邮箱地址

        :raises EmailProviderResponseError: 页面不可用、跳转异常或邮箱无效
        """

        try:
            page = await self._browser_session.page()
            await page.goto(
                self._settings.page_url,
                wait_until="domcontentloaded",
                timeout=self._timeout_milliseconds,
            )
            self._validate_page_url(page.url)
            mailbox = page.locator(MAILBOX_SELECTOR)
            if await mailbox.count() != 1:
                raise EmailProviderResponseError("Temp-Mail 邮箱控件不可用")
            await mailbox.wait_for(state="visible", timeout=self._timeout_milliseconds)
            await page.wait_for_function(
                """() => {
                    const mailbox = document.querySelector("#mail");
                    return mailbox instanceof HTMLInputElement && mailbox.value.includes("@");
                }""",
                timeout=self._timeout_milliseconds,
            )
            address = await mailbox.input_value(timeout=self._timeout_milliseconds)
            self._page = page
            return normalize_email_address(address, "Temp-Mail")
        except EmailProviderResponseError:
            await self.close()
            raise
        except asyncio.CancelledError:
            await self.close()
            raise
        except (BrowserError, BrowserTimeoutError):
            await self.close()
            raise EmailProviderResponseError("Temp-Mail 页面不可用") from None

    async def read_messages(self) -> List[TempMailMessage]:
        """
        刷新 Temp-Mail 收件箱并读取 GitHub 发件人的最新邮件

        :return List: 当前可验证的 GitHub 邮件

        :raises EmailProviderResponseError: 页面不可用或结构无效
        """

        if self._page is None:
            raise EmailProviderResponseError("Temp-Mail 邮箱会话尚未创建")
        try:
            await self._refresh_inbox()
            self._validate_page_url(self._page.url)
            rows = self._page.locator(MESSAGE_ROW_SELECTOR)
            row_count = min(await rows.count(), MAX_INBOX_MESSAGES)
            for index in range(row_count):
                message = await self._read_github_message(rows.nth(index))
                if message is not None:
                    return [message]
            return []
        except EmailProviderResponseError:
            raise
        except (BrowserError, BrowserTimeoutError):
            raise EmailProviderResponseError("Temp-Mail 收件箱读取失败") from None

    async def _refresh_inbox(self) -> None:
        if self._page is None:
            raise EmailProviderResponseError("Temp-Mail 邮箱会话尚未创建")
        refresh_link = self._page.get_by_role("link", name=REFRESH_LINK_NAME, exact=True)
        if await refresh_link.count() != 1:
            raise EmailProviderResponseError("Temp-Mail 刷新控件不可用")
        await refresh_link.wait_for(state="visible", timeout=self._timeout_milliseconds)
        await refresh_link.click(timeout=self._timeout_milliseconds)
        await self._page.wait_for_function(
            """() => {
                const mailbox = document.querySelector("#mail");
                return mailbox instanceof HTMLInputElement && mailbox.value.includes("@");
            }""",
            timeout=self._timeout_milliseconds,
        )
        await self._page.wait_for_timeout(INBOX_REFRESH_SETTLE_MILLISECONDS)

    async def close(self) -> None:
        """
        关闭 Temp-Mail 浏览器上下文，多次调用保持幂等

        :return None: 无返回值
        """

        self._page = None
        try:
            await self._browser_session.close()
        except (BrowserError, RuntimeError):
            return

    async def _read_github_message(self, row: Locator) -> Optional[TempMailMessage]:
        open_locator = row.locator(OPEN_MESSAGE_SELECTOR)
        if await open_locator.count() != 1:
            return None
        sender_locator = row.locator(SENDER_SELECTOR)
        if await sender_locator.count() != 1:
            return None
        sender = (await sender_locator.inner_text()).strip()
        if not self._is_github_sender(sender):
            return None

        subject_locator = row.locator(SUBJECT_SELECTOR)
        if await subject_locator.count() != 1:
            raise EmailProviderResponseError("Temp-Mail 邮件列表结构无效")
        subject = (await subject_locator.inner_text()).strip()
        await open_locator.click(timeout=self._timeout_milliseconds)
        if self._page is None:
            raise EmailProviderResponseError("Temp-Mail 邮箱会话已关闭")
        self._validate_page_url(self._page.url)
        content = self._page.locator(MESSAGE_CONTENT_SELECTOR)
        if await content.count() != 1:
            raise EmailProviderResponseError("Temp-Mail 邮件正文结构无效")
        await content.wait_for(state="visible", timeout=self._timeout_milliseconds)
        await self._wait_for_message_code()
        body = await self._read_message_body(content)
        return TempMailMessage(sender=sender, subject=subject, body=body)

    async def _wait_for_message_code(self) -> None:
        if self._page is None:
            raise EmailProviderResponseError("Temp-Mail 邮箱会话已关闭")
        await self._page.wait_for_function(
            """({ contentSelector, codeSelector }) => {
                const hasCode = (value) => /(^|\\D)\\d{8}(?!\\d)/.test(value || "");
                const codeNodes = document.querySelectorAll(codeSelector);
                if (Array.from(codeNodes).some((node) => hasCode(node.textContent))) {
                    return true;
                }
                const content = document.querySelector(contentSelector);
                if (!(content instanceof HTMLElement)) {
                    return false;
                }
                if (hasCode(content.innerText)) {
                    return true;
                }
                return Array.from(content.querySelectorAll("iframe")).some((iframe) => {
                    try {
                        return hasCode(iframe.contentDocument?.body?.innerText);
                    } catch {
                        return false;
                    }
                });
            }""",
            arg={
                "contentSelector": MESSAGE_CONTENT_SELECTOR,
                "codeSelector": MESSAGE_CODE_SELECTOR,
            },
            timeout=self._timeout_milliseconds,
        )

    async def _read_message_body(self, content: Locator) -> str:
        direct_body = (await content.inner_text(timeout=self._timeout_milliseconds)).strip()
        iframe = content.locator("iframe")
        body_parts = [direct_body] if direct_body else []
        if self._page is not None:
            code_nodes = self._page.locator(MESSAGE_CODE_SELECTOR)
            for index in range(min(await code_nodes.count(), MAX_MESSAGE_CODE_NODES)):
                code_candidate = (await code_nodes.nth(index).inner_text()).strip()
                if MESSAGE_CODE_PATTERN.fullmatch(code_candidate) is not None:
                    body_parts.append(code_candidate)
        for index in range(await iframe.count()):
            try:
                frame_body = iframe.nth(index).content_frame.locator("body")
                if await frame_body.count() == 1:
                    body = (await frame_body.inner_text(timeout=self._timeout_milliseconds)).strip()
                    if body:
                        body_parts.append(body)
            except (BrowserError, BrowserTimeoutError):
                continue
        return "\n".join(body_parts)

    def _validate_page_url(self, url: str) -> None:
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError:
            raise EmailProviderResponseError("Temp-Mail 页面跳转到未受信任地址") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != ALLOWED_TEMP_MAIL_HOST
            or port is not None
            or parsed.username is not None
            or not parsed.path.startswith("/en/")
        ):
            raise EmailProviderResponseError("Temp-Mail 页面跳转到未受信任地址")

    def _is_github_sender(self, sender: str) -> bool:
        normalized = sender.strip().lower()
        return normalized.endswith("@github.com") or normalized.endswith("@github.com>")

    @property
    def _timeout_milliseconds(self) -> int:
        return int(self._settings.page_timeout_seconds * 1000)
