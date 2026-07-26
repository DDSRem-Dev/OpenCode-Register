import asyncio
import secrets
import string
import time
from typing import Dict, List, Optional, Set
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from providers.base import EmailProvider
from providers.code_parser import extract_github_verification_code, html_to_text
from providers.errors import (
    EmailProviderConfigurationError,
    EmailProviderResponseError,
    EmailProviderTimeoutError,
)
from providers.models import (
    DuckMailEmail,
    DuckMailEmailCollection,
    DuckMailProviderSettings,
    DuckMailRegisterResponse,
    MailboxSession,
)
from providers.validation import normalize_email_address

ALLOWED_API_HOST = "duckmail.pro"
MAX_CONSECUTIVE_POLL_FAILURES = 3
USERNAME_ALPHABET = string.ascii_lowercase + string.digits
PASSWORD_ALPHABET = string.ascii_letters + string.digits


class DuckMailProvider(EmailProvider):
    """
    DuckMail 临时邮箱 provider

    Attributes:
        _client: 共享异步 HTTP 客户端
        _settings: provider 配置
        _mailboxes: 仅存于内存的邮箱会话
    """

    def __init__(self, client: httpx.AsyncClient, settings: DuckMailProviderSettings) -> None:
        """
        初始化 DuckMail provider

        :param client (AsyncClient): 共享异步 HTTP 客户端
        :param settings (DuckMailProviderSettings): provider 配置

        :raises EmailProviderConfigurationError: API 地址不受信任或配置名称无效
        """

        self._client = client
        self._settings = settings
        self._base_url = self._validate_base_url(settings.base_url)
        self._mailboxes: Dict[str, MailboxSession] = {}

    @property
    def provider_name(self) -> str:
        """
        获取 provider 稳定名称

        :return str: provider 稳定名称
        """

        return "duckmail"

    async def create_email(self) -> str:
        """
        注册 DuckMail 临时邮箱账户

        :return str: 规范化后的临时邮箱地址

        :raises EmailProviderResponseError: provider 请求或响应无效
        """

        username = self._random_value(USERNAME_ALPHABET, 14)
        password = self._random_value(PASSWORD_ALPHABET, 24)
        payload = await self._request_json(
            "POST",
            "/api/auth/register",
            json_body={"username": username, "password": password, "displayName": username},
        )
        try:
            registration = DuckMailRegisterResponse.model_validate(payload)
        except ValidationError:
            raise EmailProviderResponseError("DuckMail 注册响应格式无效") from None
        address = normalize_email_address(registration.user.email, "DuckMail")
        if registration.user.username != username or address != f"{username}@duckmail.pro":
            raise EmailProviderResponseError("DuckMail 注册账户与请求不一致")
        self._mailboxes[address] = MailboxSession(
            address=address,
            token=registration.token,
            password=password,
        )
        return address

    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        轮询邮箱并提取 GitHub 八位数字验证码

        :param email (str): 临时邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 收到的验证码

        :raises EmailProviderConfigurationError: 邮箱不属于当前 provider 会话
        :raises EmailProviderResponseError: provider 连续请求失败或响应无效
        :raises EmailProviderTimeoutError: 超时仍未收到有效验证码
        """

        normalized_email = email.strip().lower()
        session = self._mailboxes.get(normalized_email)
        if session is None:
            raise EmailProviderConfigurationError("邮箱不属于当前 DuckMail 会话")
        if timeout <= 0:
            raise EmailProviderConfigurationError("验证码等待时间必须大于零")

        deadline = time.monotonic() + timeout
        seen_message_ids: Set[str] = set()
        consecutive_failures = 0
        while time.monotonic() < deadline:
            try:
                messages = await self._get_messages(session.token)
                consecutive_failures = 0
            except EmailProviderResponseError:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    raise
                await self._sleep_until_next_poll(deadline)
                continue

            for message in messages:
                if message.message_id in seen_message_ids:
                    continue
                seen_message_ids.add(message.message_id)
                if not self._is_target_message(message, normalized_email) or not self._is_github_sender(message):
                    continue
                bodies = [message.body, html_to_text(message.body_html)]
                code = extract_github_verification_code(message.subject, bodies)
                if code is not None:
                    return code
            await self._sleep_until_next_poll(deadline)

        raise EmailProviderTimeoutError("等待 GitHub 邮箱验证码超时")

    async def dispose(self, email: str) -> None:
        """
        尽力删除远端邮箱账户并清除本地会话

        :param email (str): 临时邮箱地址

        :return None: 无返回值
        """

        normalized_email = email.strip().lower()
        session = self._mailboxes.pop(normalized_email, None)
        if session is None:
            return
        try:
            await self._request(
                "DELETE",
                "/api/auth/account",
                json_body={"password": session.password},
                bearer_token=session.token,
            )
        except EmailProviderResponseError:
            return

    async def _get_messages(self, token: str) -> List[DuckMailEmail]:
        payload = await self._request_json(
            "GET",
            "/api/emails",
            bearer_token=token,
            query={"folder": "inbox", "limit": "50"},
        )
        try:
            return DuckMailEmailCollection.model_validate(payload).emails
        except ValidationError:
            raise EmailProviderResponseError("DuckMail 邮件列表响应格式无效") from None

    async def _request_json(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, object]] = None,
        bearer_token: Optional[str] = None,
        query: Optional[Dict[str, str]] = None,
    ) -> object:
        response = await self._request(
            method,
            path,
            json_body=json_body,
            bearer_token=bearer_token,
            query=query,
        )
        try:
            payload: object = response.json()
            return payload
        except ValueError:
            raise EmailProviderResponseError("DuckMail 返回了非 JSON 响应") from None

    async def _request(
        self,
        method: str,
        path: str,
        json_body: Optional[Dict[str, object]] = None,
        bearer_token: Optional[str] = None,
        query: Optional[Dict[str, str]] = None,
    ) -> httpx.Response:
        headers = {"Accept": "application/json"}
        if bearer_token is not None:
            headers["Authorization"] = f"Bearer {bearer_token}"
        try:
            response = await self._client.request(
                method,
                f"{self._base_url}{path}",
                headers=headers,
                json=json_body,
                params=query,
                timeout=httpx.Timeout(self._settings.request_timeout_seconds),
            )
            response.raise_for_status()
            return response
        except httpx.HTTPError:
            raise EmailProviderResponseError("DuckMail 请求失败") from None

    async def _sleep_until_next_poll(self, deadline: float) -> None:
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(self._settings.poll_interval_seconds, remaining))

    def _is_target_message(self, message: DuckMailEmail, email: str) -> bool:
        return any(recipient.strip().lower() == email for recipient in message.to_addresses)

    def _is_github_sender(self, message: DuckMailEmail) -> bool:
        if message.from_email is None:
            return False
        return message.from_email.strip().lower().endswith("@github.com")

    def _random_value(self, alphabet: str, length: int) -> str:
        return "".join(secrets.choice(alphabet) for _ in range(length))

    def _validate_base_url(self, base_url: str) -> str:
        normalized = base_url.strip().rstrip("/")
        try:
            parsed = urlsplit(normalized)
            port = parsed.port
        except ValueError:
            raise EmailProviderConfigurationError("DuckMail API 地址不受信任") from None
        if (
            parsed.scheme != "https"
            or parsed.hostname != ALLOWED_API_HOST
            or port is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
            or parsed.username is not None
        ):
            raise EmailProviderConfigurationError("DuckMail API 地址不受信任")
        return normalized
