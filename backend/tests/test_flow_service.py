import asyncio
import json
from typing import Dict, Optional

import httpx
import pytest

from browser.base import GitHubRegistrationClient, OpenCodeAutomationClient
from browser.models import GitHubPageResult, GitHubPageStatus, OpenCodePageResult, OpenCodePageStatus
from engine.events import FlowEvent
from engine.models import AccountCompletionData, FlowStatus, ManualInterventionReason
from engine.service import CreateAccountService


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


def _duckmail_handler(request: httpx.Request) -> httpx.Response:
    if request.url.path == "/api/auth/register":
        payload = json.loads(request.content)
        return httpx.Response(
            201,
            json={
                "user": {
                    "id": 123,
                    "username": payload["username"],
                    "email": f"{payload['username']}@duckmail.pro",
                },
                "token": "fake-duckmail-token",
            },
            request=request,
        )
    return httpx.Response(404, request=request)


@pytest.mark.anyio
async def test_manual_timeout_fails_flow_and_releases_browser() -> None:
    """
    验证人工介入超时会失败流程并回收浏览器资源
    """

    browser = ManualBrowserClient()

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    transport = httpx.MockTransport(_duckmail_handler)
    async with httpx.AsyncClient(transport=transport) as client:
        service = CreateAccountService(
            client,
            complete_account,
            manual_timeout_seconds=0,
            browser_factory=lambda: (browser, UnusedOpenCodeClient()),
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

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/register":
            payload = json.loads(request.content)
            return httpx.Response(
                201,
                json={
                    "user": {
                        "id": 123,
                        "username": payload["username"],
                        "email": f"{payload['username']}@duckmail.pro",
                    },
                    "token": "fake-duckmail-token",
                },
                request=request,
            )
        if request.url.path == "/api/auth/account" and request.method == "DELETE":
            state["mailbox_disposed"] = True
            return httpx.Response(204, request=request)
        return httpx.Response(404, request=request)

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    browser = ManualBrowserClient()
    transport = httpx.MockTransport(handle_request)
    async with httpx.AsyncClient(transport=transport) as client:
        service = CreateAccountService(
            client,
            complete_account,
            manual_timeout_seconds=60,
            browser_factory=lambda: (browser, UnusedOpenCodeClient()),
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
