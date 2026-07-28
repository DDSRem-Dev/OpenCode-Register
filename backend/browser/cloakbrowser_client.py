import asyncio
from typing import Dict, List, Optional, Set, Tuple, cast
from urllib.parse import urlparse

from cloakbrowser import launch_async  # type: ignore[import-untyped]
from playwright._impl._api_structures import StorageState
from playwright.async_api import Browser, BrowserContext, Page
from pydantic import SecretStr

from storage.models import (
    BrowserAuthState,
    BrowserCookieState,
    BrowserOriginState,
    BrowserStorageEntry,
)

from .initializer import BrowserInitializer

OPENCODE_ORIGIN = "https://opencode.ai"
OPENCODE_CLIPBOARD_PERMISSIONS = ["clipboard-read", "clipboard-write"]


class CloakBrowserSession:
    """
    单个账号流程使用的隔离浏览器会话
    """

    def __init__(self, manager: "CloakBrowserClient", auth_states: Optional[List[BrowserAuthState]] = None) -> None:
        """
        初始化尚未创建页面的浏览器会话

        :param manager (CloakBrowserClient): 共享浏览器生命周期管理器
        :param auth_states (List): 创建上下文前恢复的浏览器认证状态
        """

        self._manager = manager
        self._auth_states = auth_states or []
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    async def page(self) -> Page:
        """
        获取本流程页面，首次调用时创建隔离上下文

        :return Page: 当前流程使用的 CloakBrowser 页面
        """

        if self._page is None:
            self._context, self._page = await self._manager._create_page(self._auth_states)
        return self._page

    def restore_auth_states(self, auth_states: List[BrowserAuthState]) -> None:
        """
        在上下文创建前设置待恢复的浏览器认证状态

        :param auth_states (List): GitHub 与 OpenCode 浏览器认证状态

        :return None: 无返回值

        :raises RuntimeError: 浏览器上下文已经创建
        """

        if self._context is not None:
            raise RuntimeError("浏览器上下文已经创建")
        self._auth_states = list(auth_states)

    async def capture_auth_state(self, allowed_hosts: Set[str]) -> BrowserAuthState:
        """
        捕获并按受信任主机过滤当前上下文认证状态

        :param allowed_hosts (Set): 允许保存 Cookie 与 localStorage 的主机集合

        :return BrowserAuthState: 不会进入日志或接口响应的类型化认证状态

        :raises RuntimeError: 浏览器上下文尚未创建
        """

        if self._context is None:
            raise RuntimeError("浏览器上下文尚未创建")
        storage_state = await self._context.storage_state()
        cookies = [
            BrowserCookieState(
                name=cookie["name"],
                value=SecretStr(cookie["value"]),
                domain=cookie["domain"],
                path=cookie["path"],
                expires=cookie["expires"],
                http_only=cookie["httpOnly"],
                secure=cookie["secure"],
                same_site=cookie["sameSite"],
            )
            for cookie in storage_state["cookies"]
            if _host_is_allowed(cookie["domain"], allowed_hosts)
        ]
        origins = [
            BrowserOriginState(
                origin=origin["origin"],
                local_storage=[
                    BrowserStorageEntry(name=entry["name"], value=SecretStr(entry["value"]))
                    for entry in origin["localStorage"]
                ],
            )
            for origin in storage_state["origins"]
            if _origin_is_allowed(origin["origin"], allowed_hosts)
        ]
        return BrowserAuthState(cookies=cookies, origins=origins)

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

    def __init__(self, headless: bool = False, initializer: Optional[BrowserInitializer] = None) -> None:
        """
        初始化尚未启动的浏览器管理器

        :param headless (bool): 是否在后台无窗口运行浏览器
        :param initializer (BrowserInitializer): 可选的共享浏览器初始化管理器
        """

        self._browser: Optional[Browser] = None
        self._start_lock = asyncio.Lock()
        self._headless = headless
        self._initializer = initializer

    def create_session(self, auth_states: Optional[List[BrowserAuthState]] = None) -> CloakBrowserSession:
        """
        创建使用独立浏览器上下文的流程会话

        :param auth_states (List): 创建上下文前恢复的浏览器认证状态

        :return CloakBrowserSession: 尚未启动页面的隔离会话
        """

        return CloakBrowserSession(self, auth_states)

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

    async def _create_page(self, auth_states: List[BrowserAuthState]) -> Tuple[BrowserContext, Page]:
        browser = await self._ensure_browser()
        context = await browser.new_context(
            viewport={"width": 1920, "height": 1080},
            storage_state=_playwright_storage_state(auth_states),
        )
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
                if self._initializer is not None:
                    await self._initializer.wait_until_ready()
                self._browser = await launch_async(headless=self._headless)
            return self._browser


def _host_is_allowed(domain: str, allowed_hosts: Set[str]) -> bool:
    normalized = domain.lstrip(".").casefold()
    return any(normalized == host or normalized.endswith(f".{host}") for host in allowed_hosts)


def _origin_is_allowed(origin: str, allowed_hosts: Set[str]) -> bool:
    parsed = urlparse(origin)
    return parsed.scheme == "https" and parsed.hostname is not None and _host_is_allowed(parsed.hostname, allowed_hosts)


def _playwright_storage_state(auth_states: List[BrowserAuthState]) -> StorageState:
    cookies: Dict[Tuple[str, str, str], Dict[str, object]] = {}
    origins: Dict[str, Dict[str, SecretStr]] = {}
    for state in auth_states:
        for cookie in state.cookies:
            cookies[(cookie.name, cookie.domain, cookie.path)] = {
                "name": cookie.name,
                "value": cookie.value.get_secret_value(),
                "domain": cookie.domain,
                "path": cookie.path,
                "expires": cookie.expires,
                "httpOnly": cookie.http_only,
                "secure": cookie.secure,
                "sameSite": cookie.same_site,
            }
        for origin in state.origins:
            entries = origins.setdefault(origin.origin, {})
            for entry in origin.local_storage:
                entries[entry.name] = entry.value
    return cast(
        StorageState,
        {
            "cookies": list(cookies.values()),
            "origins": [
                {
                    "origin": origin,
                    "localStorage": [
                        {"name": name, "value": value.get_secret_value()} for name, value in entries.items()
                    ],
                }
                for origin, entries in origins.items()
            ],
        },
    )
