import asyncio
from time import monotonic
from typing import List, Optional

from providers.base import EmailProvider, TempMailMailboxClient
from providers.code_parser import extract_github_verification_code
from providers.errors import (
    EmailProviderConfigurationError,
    EmailProviderResponseError,
    EmailProviderTimeoutError,
)
from providers.models import TempMailMessage
from providers.validation import normalize_email_address

MAX_CONSECUTIVE_POLL_FAILURES = 3


class TempMailProvider(EmailProvider):
    """
    Temp-Mail 浏览器临时邮箱 provider

    Attributes:
        _mailbox_client: Temp-Mail 浏览器边界
        _poll_interval_seconds: 收件箱轮询间隔
        _mailbox_address: 当前页面生成的邮箱地址
    """

    def __init__(self, mailbox_client: TempMailMailboxClient, poll_interval_seconds: float = 3.0) -> None:
        """
        初始化 Temp-Mail provider

        :param mailbox_client (TempMailMailboxClient): Temp-Mail 浏览器边界
        :param poll_interval_seconds (float): 收件箱轮询间隔秒数

        :raises EmailProviderConfigurationError: 轮询间隔无效
        """

        if poll_interval_seconds <= 0:
            raise EmailProviderConfigurationError("Temp-Mail 轮询间隔必须大于零")
        self._mailbox_client = mailbox_client
        self._poll_interval_seconds = poll_interval_seconds
        self._mailbox_address: Optional[str] = None

    @property
    def provider_name(self) -> str:
        """
        获取 provider 稳定名称

        :return str: provider 稳定名称
        """

        return "temp_mail"

    async def create_email(self) -> str:
        """
        通过 Temp-Mail 页面创建临时邮箱

        :return str: 规范化后的临时邮箱地址

        :raises EmailProviderResponseError: 页面返回无效邮箱
        """

        address = normalize_email_address(await self._mailbox_client.create_mailbox(), "Temp-Mail")
        self._mailbox_address = address
        return address

    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        轮询 Temp-Mail 页面并提取 GitHub 八位数字验证码

        :param email (str): 临时邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 收到的验证码

        :raises EmailProviderConfigurationError: 邮箱或等待时间与当前会话不匹配
        :raises EmailProviderResponseError: 页面连续读取失败
        :raises EmailProviderTimeoutError: 超时仍未收到有效验证码
        """

        normalized_email = email.strip().lower()
        if self._mailbox_address is None or normalized_email != self._mailbox_address:
            raise EmailProviderConfigurationError("邮箱不属于当前 Temp-Mail 会话")
        if timeout <= 0:
            raise EmailProviderConfigurationError("验证码等待时间必须大于零")

        deadline = monotonic() + timeout
        consecutive_failures = 0
        while monotonic() < deadline:
            try:
                messages = await self._mailbox_client.read_messages()
                consecutive_failures = 0
            except EmailProviderResponseError:
                consecutive_failures += 1
                if consecutive_failures >= MAX_CONSECUTIVE_POLL_FAILURES:
                    raise
                await self._sleep_until_next_poll(deadline)
                continue

            code = self._find_github_code(messages)
            if code is not None:
                return code
            await self._sleep_until_next_poll(deadline)

        raise EmailProviderTimeoutError("等待 GitHub 邮箱验证码超时")

    async def dispose(self, email: str) -> None:
        """
        关闭 Temp-Mail 浏览器会话并清除本地邮箱引用

        :param email (str): 临时邮箱地址

        :return None: 无返回值
        """

        if self._mailbox_address is None or email.strip().lower() != self._mailbox_address:
            return
        self._mailbox_address = None
        try:
            await self._mailbox_client.close()
        except EmailProviderResponseError:
            return

    async def _sleep_until_next_poll(self, deadline: float) -> None:
        remaining = deadline - monotonic()
        if remaining > 0:
            await asyncio.sleep(min(self._poll_interval_seconds, remaining))

    def _find_github_code(self, messages: List[TempMailMessage]) -> Optional[str]:
        for message in messages:
            if not self._is_github_sender(message.sender):
                continue
            code = extract_github_verification_code(message.subject, [message.body])
            if code is not None:
                return code
        return None

    def _is_github_sender(self, sender: str) -> bool:
        normalized = sender.strip().lower()
        return normalized.endswith("@github.com") or normalized.endswith("@github.com>")
