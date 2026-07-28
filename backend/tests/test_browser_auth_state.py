from typing import Dict, List, Optional, cast

import pytest
from playwright._impl._api_structures import StorageState
from playwright.async_api import Browser, BrowserContext, Page
from pydantic import SecretStr, ValidationError

from browser.cloakbrowser_client import CloakBrowserClient
from storage.models import BrowserAuthState, BrowserCookieState, BrowserOriginState, BrowserStorageEntry


class FakePage:
    """
    浏览器认证状态测试页面替身
    """


class FakeContext:
    """
    浏览器认证状态测试上下文替身
    """

    def __init__(self, storage_state: StorageState) -> None:
        """
        初始化带可捕获状态的测试上下文

        :param storage_state (StorageState): 测试浏览器存储状态
        """

        self._storage_state = storage_state
        self.permissions: List[str] = []
        self.closed = False

    async def grant_permissions(self, permissions: List[str], origin: Optional[str] = None) -> None:
        """
        记录测试权限授予

        :param permissions (List): 浏览器权限列表
        :param origin (str): 权限来源

        :return None: 无返回值
        """

        del origin
        self.permissions = permissions

    async def new_page(self) -> Page:
        """
        返回测试页面

        :return Page: 测试页面替身
        """

        return cast(Page, FakePage())

    async def storage_state(self) -> StorageState:
        """
        返回可捕获的测试浏览器状态

        :return StorageState: 测试浏览器存储状态
        """

        return self._storage_state

    async def close(self) -> None:
        """
        记录测试上下文关闭

        :return None: 无返回值
        """

        self.closed = True


class FakeBrowser:
    """
    浏览器认证状态测试浏览器替身
    """

    def __init__(self, capture_state: StorageState) -> None:
        """
        初始化测试浏览器

        :param capture_state (StorageState): 新上下文返回的捕获状态
        """

        self.capture_state = capture_state
        self.received_storage_state: Optional[StorageState] = None
        self.context: Optional[FakeContext] = None

    async def new_context(self, **kwargs: object) -> BrowserContext:
        """
        记录恢复状态并返回测试上下文

        :return BrowserContext: 测试浏览器上下文
        """

        self.received_storage_state = cast(StorageState, kwargs["storage_state"])
        self.context = FakeContext(self.capture_state)
        return cast(BrowserContext, self.context)


def _cookie(name: str, value: str, domain: str) -> Dict[str, object]:
    return {
        "name": name,
        "value": value,
        "domain": domain,
        "path": "/",
        "expires": 2_000_000_000,
        "httpOnly": True,
        "secure": True,
        "sameSite": "Lax",
    }


@pytest.mark.anyio
async def test_browser_session_restores_and_captures_only_allowed_auth_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证认证状态恢复到新上下文且捕获时排除非信任域
    """

    github_cookie = "fake-github-cookie-value"
    opencode_storage = "fake-opencode-storage-value"
    restored = BrowserAuthState(
        cookies=[
            BrowserCookieState(
                name="user_session",
                value=SecretStr(github_cookie),
                domain=".github.com",
                path="/",
                expires=2_000_000_000,
                http_only=True,
                secure=True,
                same_site="Lax",
            )
        ],
        origins=[
            BrowserOriginState(
                origin="https://opencode.ai",
                local_storage=[BrowserStorageEntry(name="auth", value=SecretStr(opencode_storage))],
            )
        ],
    )
    capture_state = cast(
        StorageState,
        {
            "cookies": [
                _cookie("user_session", "fake-refreshed-cookie", ".github.com"),
                _cookie("ignored", "fake-untrusted-cookie", ".example.test"),
            ],
            "origins": [
                {
                    "origin": "https://github.com",
                    "localStorage": [{"name": "actor", "value": "fake-actor-state"}],
                },
                {
                    "origin": "https://example.test",
                    "localStorage": [{"name": "ignored", "value": "fake-untrusted-storage"}],
                },
            ],
        },
    )
    fake_browser = FakeBrowser(capture_state)
    manager = CloakBrowserClient(headless=True)

    async def ensure_browser() -> Browser:
        return cast(Browser, fake_browser)

    monkeypatch.setattr(manager, "_ensure_browser", ensure_browser)
    session = manager.create_session([restored])
    await session.page()
    captured = await session.capture_auth_state({"github.com"})

    assert fake_browser.received_storage_state is not None
    assert fake_browser.received_storage_state["cookies"][0]["value"] == github_cookie
    assert fake_browser.received_storage_state["origins"][0]["localStorage"][0]["value"] == opencode_storage
    assert [cookie.domain for cookie in captured.cookies] == [".github.com"]
    assert [origin.origin for origin in captured.origins] == ["https://github.com"]
    assert "fake-refreshed-cookie" not in repr(captured)
    assert "fake-actor-state" not in repr(captured)


def test_browser_auth_state_rejects_oversized_collections() -> None:
    """
    验证异常大的认证状态在进入持久化边界前被拒绝
    """

    cookie = BrowserCookieState(
        name="session",
        value=SecretStr("fake-cookie"),
        domain="github.com",
        path="/",
        expires=-1,
        http_only=True,
        secure=True,
        same_site="Lax",
    )
    with pytest.raises(ValidationError):
        BrowserAuthState(cookies=[cookie] * 513)
