from typing import List

import pytest

import providers.integrations.temp_mail as temp_mail_provider_module
from providers.base import TempMailMailboxClient
from providers.errors import EmailProviderConfigurationError, EmailProviderResponseError, EmailProviderTimeoutError
from providers.integrations.temp_mail import TempMailProvider
from providers.models import TempMailMessage


class FakeTempMailMailboxClient(TempMailMailboxClient):
    """
    Temp-Mail provider 测试浏览器边界
    """

    def __init__(self, messages: List[TempMailMessage]) -> None:
        """
        初始化测试邮箱页面

        :param messages (List): 页面返回的邮件
        """

        self.messages = messages
        self.closed = False
        self.read_error = False

    async def create_mailbox(self) -> str:
        """
        返回固定测试邮箱

        :return str: 测试邮箱地址
        """

        return "Flow.Box@Example.Test"

    async def read_messages(self) -> List[TempMailMessage]:
        """
        返回测试邮件或模拟页面失败

        :return List: 测试邮件

        :raises EmailProviderResponseError: 配置为页面失败时抛出
        """

        if self.read_error:
            raise EmailProviderResponseError("测试页面失败")
        return self.messages

    async def close(self) -> None:
        """
        记录浏览器会话关闭

        :return None: 无返回值
        """

        self.closed = True


@pytest.mark.anyio
async def test_temp_mail_creates_mailbox_reads_github_code_and_closes_session() -> None:
    """
    验证 Temp-Mail 规范化邮箱、过滤发件人、读取验证码并关闭会话
    """

    mailbox_client = FakeTempMailMailboxClient(
        [
            TempMailMessage(sender="notice@example.test", subject="Receipt 87654321", body="Ignore this message"),
            TempMailMessage(
                sender="GitHub <noreply@github.com>",
                subject="Your GitHub launch code",
                body="Enter 12345678 to continue.",
            ),
        ]
    )
    provider = TempMailProvider(mailbox_client, poll_interval_seconds=0.01)

    address = await provider.create_email()
    code = await provider.wait_for_code(address.upper(), timeout=1)
    await provider.dispose(address)

    assert address == "flow.box@example.test"
    assert code == "12345678"
    assert mailbox_client.closed is True
    assert provider.provider_name == "temp_mail"


@pytest.mark.anyio
async def test_temp_mail_rejects_email_outside_current_session() -> None:
    """
    验证 Temp-Mail 不读取其他邮箱会话
    """

    provider = TempMailProvider(FakeTempMailMailboxClient([]))
    await provider.create_email()

    with pytest.raises(EmailProviderConfigurationError, match="不属于当前 Temp-Mail 会话"):
        await provider.wait_for_code("other@example.test", timeout=1)


@pytest.mark.anyio
async def test_temp_mail_times_out_without_github_message(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    验证 Temp-Mail 在截止时间后返回稳定超时异常

    :param monkeypatch (MonkeyPatch): 单调时钟替换工具
    """

    times = iter([0.0, 2.0])
    monkeypatch.setattr(temp_mail_provider_module, "monotonic", lambda: next(times))
    provider = TempMailProvider(FakeTempMailMailboxClient([]))
    email = await provider.create_email()

    with pytest.raises(EmailProviderTimeoutError, match="等待 GitHub 邮箱验证码超时"):
        await provider.wait_for_code(email, timeout=1)


def test_temp_mail_rejects_non_positive_poll_interval() -> None:
    """
    验证 Temp-Mail 拒绝无效轮询间隔
    """

    with pytest.raises(EmailProviderConfigurationError, match="轮询间隔必须大于零"):
        TempMailProvider(FakeTempMailMailboxClient([]), poll_interval_seconds=0)
