import asyncio
from typing import List, Optional, Tuple

from cloakbrowser import launch_async  # type: ignore[import-untyped]
from playwright.async_api import Browser, BrowserContext, Page

OPENCODE_ORIGIN = "https://opencode.ai"
OPENCODE_CLIPBOARD_PERMISSIONS = ["clipboard-read", "clipboard-write"]


class CloakBrowserSession:
    """
    单个账号流程使用的隔离浏览器会话
    """

    def __init__(self, manager: "CloakBrowserClient") -> None:
        """
        初始化尚未创建页面的浏览器会话

        :param manager (CloakBrowserClient): 共享浏览器生命周期管理器
        """

        self._manager = manager
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def page(self) -> Page:
        """
        获取本流程页面，首次调用时创建隔离上下文

        :return Page: 当前流程使用的 CloakBrowser 页面
        """

        if self._page is None:
            self._context, self._page = await self._manager._create_page()
        return self._page

    async def close(self) -> None:
        """
        关闭本流程拥有的浏览器上下文，多次调用保持幂等

        :return None: 无返回值
        """

        if self._context is not None:
            await self._context.close()
        self._page = None
        self._context = None

    async def capture_sanitized_screenshot(self, sensitive_texts: List[str]) -> bytes:
        """
        使用 Playwright mask 捕获不含输入、挑战内容和已知身份文本的 PNG

        :param sensitive_texts (List): 邮箱、用户名和其他必须遮罩的可见文本

        :return bytes: 已遮罩的当前视口 PNG
        """

        page = await self.page()
        masks = [
            page.locator(
                "input, textarea, [contenteditable='true'], pre, code, "
                "iframe, canvas, [data-sitekey], [data-slot='key-value']"
            )
        ]
        masks.extend(page.get_by_text(text, exact=False) for text in sensitive_texts if text)
        return await page.screenshot(
            type="png",
            animations="disabled",
            caret="hide",
            mask=masks,
            mask_color="#202124",
        )


class CloakBrowserClient:
    """
    CloakBrowser 浏览器生命周期管理器
    """

    def __init__(self, headless: bool = False) -> None:
        """
        初始化尚未启动的浏览器管理器

        :param headless (bool): 是否在后台无窗口运行浏览器
        """

        self._browser: Optional[Browser] = None
        self._start_lock = asyncio.Lock()
        self._headless = headless

    def create_session(self) -> CloakBrowserSession:
        """
        创建使用独立浏览器上下文的流程会话

        :return CloakBrowserSession: 尚未启动页面的隔离会话
        """

        return CloakBrowserSession(self)

    async def close(self) -> None:
        """
        按所有权顺序关闭浏览器资源，多次调用保持幂等

        :return None: 无返回值
        """

        if self._browser is not None and self._browser.is_connected():
            try:
                await self._browser.close()
            except RuntimeError:
                # CloakBrowser may close its transport before service shutdown reaches the shared owner.
                pass
        self._browser = None

    async def _create_page(self) -> Tuple[BrowserContext, Page]:
        browser = await self._ensure_browser()
        context = await browser.new_context(viewport={"width": 1920, "height": 1080})
        try:
            await context.grant_permissions(
                OPENCODE_CLIPBOARD_PERMISSIONS,
                origin=OPENCODE_ORIGIN,
            )
            page = await context.new_page()
        except Exception:
            await context.close()
            raise
        return context, page

    async def _ensure_browser(self) -> Browser:
        async with self._start_lock:
            if self._browser is None:
                self._browser = await launch_async(headless=self._headless)
            return self._browser
