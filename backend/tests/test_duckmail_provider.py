import json
from typing import Dict

import httpx
import pytest

from providers.errors import (
    EmailProviderConfigurationError,
    EmailProviderResponseError,
    EmailProviderTimeoutError,
)
from providers.integrations.duckmail import DuckMailProvider
from providers.models import DuckMailProviderSettings


def _settings(base_url: str = "https://duckmail.pro") -> DuckMailProviderSettings:
    return DuckMailProviderSettings(
        base_url=base_url,
        poll_interval_seconds=0.01,
        request_timeout_seconds=1,
    )


@pytest.mark.anyio
async def test_duckmail_creates_mailbox_reads_code_and_deletes_account() -> None:
    """
    验证 DuckMail 注册邮箱、读取 GitHub 验证码并删除同一账户
    """

    state: Dict[str, str] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/register":
            payload = json.loads(request.content)
            state["username"] = payload["username"]
            state["password"] = payload["password"]
            assert payload["displayName"] == payload["username"]
            assert request.headers.get("Authorization") is None
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
            )
        if request.url.path == "/api/emails":
            assert request.headers["Authorization"] == "Bearer fake-duckmail-token"
            assert request.url.params["folder"] == "inbox"
            assert request.url.params["limit"] == "50"
            return httpx.Response(
                200,
                json={
                    "emails": [
                        {
                            "id": 456,
                            "fromEmail": "noreply@github.com",
                            "toAddresses": [f"{state['username']}@duckmail.pro"],
                            "subject": "Your GitHub launch code",
                            "body": "Enter 12345678 to continue.",
                            "bodyHtml": "",
                        }
                    ]
                },
            )
        if request.url.path == "/api/auth/account" and request.method == "DELETE":
            payload = json.loads(request.content)
            assert payload["password"] == state["password"]
            assert request.headers["Authorization"] == "Bearer fake-duckmail-token"
            state["deleted"] = "true"
            return httpx.Response(204)
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as client:
        provider = DuckMailProvider(client, _settings())
        mailbox_address = await provider.create_email()
        code = await provider.wait_for_code(mailbox_address.upper(), timeout=1)
        await provider.dispose(mailbox_address)

    assert mailbox_address == f"{state['username']}@duckmail.pro"
    assert code == "12345678"
    assert state["deleted"] == "true"


@pytest.mark.anyio
async def test_duckmail_rejects_malformed_register_payload() -> None:
    """
    验证 DuckMail 不把畸形注册响应当作成功
    """

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(201, json={"unexpected": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as client:
        provider = DuckMailProvider(client, _settings())
        with pytest.raises(EmailProviderResponseError, match="注册响应格式无效"):
            await provider.create_email()


@pytest.mark.anyio
async def test_duckmail_rejects_mismatched_registered_account() -> None:
    """
    验证 DuckMail 拒绝与本次注册请求不一致的账户响应
    """

    def handle_request(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            201,
            json={
                "user": {"id": 123, "username": "different", "email": "different@duckmail.pro"},
                "token": "fake-duckmail-token",
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as client:
        provider = DuckMailProvider(client, _settings())
        with pytest.raises(EmailProviderResponseError, match="注册账户与请求不一致"):
            await provider.create_email()


@pytest.mark.anyio
async def test_duckmail_rejects_untrusted_api_host() -> None:
    """
    验证 DuckMail 拒绝向未批准主机发送请求
    """

    async with httpx.AsyncClient() as client:
        with pytest.raises(EmailProviderConfigurationError, match="API 地址不受信任"):
            DuckMailProvider(client, _settings("https://untrusted.example"))


@pytest.mark.anyio
async def test_duckmail_rejects_malformed_api_port() -> None:
    """
    验证 DuckMail 将畸形端口净化为稳定配置错误
    """

    async with httpx.AsyncClient() as client:
        with pytest.raises(EmailProviderConfigurationError, match="API 地址不受信任"):
            DuckMailProvider(client, _settings("https://duckmail.pro:invalid"))


@pytest.mark.anyio
async def test_duckmail_times_out_without_a_message() -> None:
    """
    验证 DuckMail 在截止时间后返回稳定超时异常
    """

    state: Dict[str, str] = {}

    def handle_request(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/register":
            payload = json.loads(request.content)
            state["username"] = payload["username"]
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
            )
        if request.url.path == "/api/emails":
            return httpx.Response(200, json={"emails": []})
        return httpx.Response(404)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle_request)) as client:
        provider = DuckMailProvider(client, _settings())
        email = await provider.create_email()
        with pytest.raises(EmailProviderTimeoutError, match="等待 GitHub 邮箱验证码超时"):
            await provider.wait_for_code(email, timeout=1)
