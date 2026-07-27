import asyncio
from typing import Dict, Optional

import pytest

import engine.service as service_module
from browser.base import GitHubRegistrationClient, OpenCodeAutomationClient
from browser.initializer import BrowserInitializer
from browser.models import GitHubPageResult, GitHubPageStatus, OpenCodePageResult, OpenCodePageStatus
from engine.events import FlowEvent
from engine.models import AccountCompletionData, FlowStatus, ManualInterventionReason
from engine.service import CreateAccountService
from providers.base import EmailProvider


@pytest.mark.anyio
async def test_default_service_runs_email_browser_in_background(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证默认服务分别启动可见账号浏览器和后台邮箱浏览器
    """

    launch_modes: list[bool] = []
    closed_modes: list[bool] = []

    class _RecordingBrowserClient:
        def __init__(self, headless: bool = False, initializer: Optional[BrowserInitializer] = None) -> None:
            del initializer
            self._headless = headless
            launch_modes.append(headless)

        async def close(self) -> None:
            closed_modes.append(self._headless)

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    monkeypatch.setattr(service_module, "CloakBrowserClient", _RecordingBrowserClient)
    service = CreateAccountService(complete_account)

    await service.close()

    assert launch_modes == [False, True]
    assert closed_modes == [False, True]


class ManualBrowserClient(GitHubRegistrationClient):
    """
    人工超时测试用浏览器边界
    """

    def __init__(self) -> None:
        """
        初始化测试浏览器状态
        """

        self.closed = False

    async def start_registration(self, email: str, username: str, password: str) -> GitHubPageResult:
        """
        模拟页面要求人工验证

        :param email (str): 测试邮箱
        :param username (str): 测试用户名
        :param password (str): 测试密码

        :return GitHubPageResult: 人工介入结果
        """

        return GitHubPageResult(
            status=GitHubPageStatus.MANUAL_REQUIRED,
            manual_reason=ManualInterventionReason.CAPTCHA,
        )

    async def inspect_after_manual(self) -> GitHubPageResult:
        """
        保持人工介入状态

        :return GitHubPageResult: 人工介入结果
        """

        return GitHubPageResult(status=GitHubPageStatus.MANUAL_REQUIRED)

    async def submit_email_code(self, code: str) -> GitHubPageResult:
        """
        返回不会使用的邮箱验证结果

        :param code (str): 测试验证码

        :return GitHubPageResult: 页面完成结果
        """

        return GitHubPageResult(status=GitHubPageStatus.COMPLETED)

    async def close(self) -> None:
        """
        记录浏览器资源已关闭

        :return None: 无返回值
        """

        self.closed = True


class UnusedOpenCodeClient(OpenCodeAutomationClient):
    """
    人工超时测试中不应调用的 OpenCode 边界
    """

    async def start_login(self) -> OpenCodePageResult:
        """
        返回不会到达的完成状态

        :return OpenCodePageResult: 测试错误结果
        """

        return OpenCodePageResult(status=OpenCodePageStatus.ERROR)

    async def inspect_after_manual(self) -> OpenCodePageResult:
        """
        返回不会到达的人工检查结果

        :return OpenCodePageResult: 测试错误结果
        """

        return OpenCodePageResult(status=OpenCodePageStatus.ERROR)

    async def confirm_payment(self) -> OpenCodePageResult:
        """
        返回不会到达的付款结果

        :return OpenCodePageResult: 测试错误结果
        """

        return OpenCodePageResult(status=OpenCodePageStatus.ERROR)

    async def submit_api_key(self, api_key: str) -> OpenCodePageResult:
        """
        返回不会到达的密钥结果

        :param api_key (str): 测试 API Key

        :return OpenCodePageResult: 测试错误结果
        """

        return OpenCodePageResult(status=OpenCodePageStatus.ERROR)


class ServiceEmailProvider(EmailProvider):
    """
    创建流程服务测试邮箱 provider
    """

    def __init__(self, state: Dict[str, bool]) -> None:
        """
        初始化测试邮箱状态

        :param state (Dict): 可观察的资源状态
        """

        self._state = state

    @property
    def provider_name(self) -> str:
        """
        获取测试 provider 名称

        :return str: 测试 provider 名称
        """

        return "temp_mail"

    async def create_email(self) -> str:
        """
        返回固定测试邮箱

        :return str: 测试邮箱地址
        """

        return "flow@example.test"

    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        返回不会使用的测试验证码

        :param email (str): 测试邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 测试验证码
        """

        del email, timeout
        return "12345678"

    async def dispose(self, email: str) -> None:
        """
        记录邮箱会话已释放

        :param email (str): 测试邮箱地址

        :return None: 无返回值
        """

        del email
        self._state["mailbox_disposed"] = True


@pytest.mark.anyio
async def test_manual_timeout_fails_flow_and_releases_browser() -> None:
    """
    验证人工介入超时会失败流程并回收浏览器资源
    """

    browser = ManualBrowserClient()

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    provider_state: Dict[str, bool] = {"mailbox_disposed": False}
    service = CreateAccountService(
        complete_account,
        manual_timeout_seconds=0,
        browser_factory=lambda: (browser, UnusedOpenCodeClient()),
        provider_factory=lambda: ServiceEmailProvider(provider_state),
    )
    session = service.create()
    event_stream = service.events(session.flow_id)
    final_event: Optional[FlowEvent] = None

    for _ in range(8):
        event = await asyncio.wait_for(anext(event_stream), timeout=1)
        if event.payload.status == FlowStatus.ERROR:
            final_event = event
            break

    await event_stream.aclose()
    await service.close()

    assert final_event is not None
    assert final_event.payload.error_code == "manual_intervention_timeout"
    assert browser.closed is True


@pytest.mark.anyio
async def test_service_close_cancels_manual_flow_and_releases_resources() -> None:
    """
    验证服务关闭会取消人工暂停流程并释放邮箱与浏览器
    """

    state: Dict[str, bool] = {"mailbox_disposed": False}

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    browser = ManualBrowserClient()
    service = CreateAccountService(
        complete_account,
        manual_timeout_seconds=60,
        browser_factory=lambda: (browser, UnusedOpenCodeClient()),
        provider_factory=lambda: ServiceEmailProvider(state),
    )
    session = service.create()
    event_stream = service.events(session.flow_id)
    for _ in range(8):
        event = await asyncio.wait_for(anext(event_stream), timeout=1)
        if event.payload.status == FlowStatus.MANUAL_VERIFY:
            break
    await event_stream.aclose()

    await service.close()

    assert service.snapshot(session.flow_id).status == FlowStatus.CANCELLED
    assert state["mailbox_disposed"] is True
    assert browser.closed is True
