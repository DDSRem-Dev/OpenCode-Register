import asyncio
from pathlib import Path
from typing import Awaitable, Callable, List, Optional

import pytest
from pydantic import SecretStr

from browser.base import GitHubRegistrationClient, OpenCodeAutomationClient
from browser.models import (
    GitHubPageResult,
    GitHubPageStatus,
    OpenCodePageResult,
    OpenCodePageStatus,
)
from engine.flow import CreateAccountFlow
from engine.models import (
    AccountCompletionData,
    FlowStatus,
    FlowStepStatus,
    ManualInterventionReason,
    PendingAccountData,
)
from providers.base import EmailProvider
from providers.errors import EmailProviderResponseError
from storage.screenshots import ScreenshotStore, ScreenshotStoreError


class FakeEmailProvider(EmailProvider):
    """
    流程测试用可控邮箱 provider
    """

    def __init__(
        self,
        create_gate: Optional[asyncio.Event] = None,
        create_started: Optional[asyncio.Event] = None,
    ) -> None:
        """
        初始化测试 provider

        :param create_gate (Event): 可选创建阻塞门闩
        :param create_started (Event): 可选创建已开始信号
        """

        self.create_gate = create_gate
        self.create_started = create_started
        self.disposed_email: Optional[str] = None

    @property
    def provider_name(self) -> str:
        """
        获取测试 provider 名称

        :return str: provider 名称
        """

        return "fake"

    async def create_email(self) -> str:
        """
        创建测试邮箱

        :return str: 测试邮箱地址
        """

        if self.create_started is not None:
            self.create_started.set()
        if self.create_gate is not None:
            await self.create_gate.wait()
        return "flow@example.test"

    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        返回测试验证码

        :param email (str): 测试邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 测试验证码
        """

        return "12345678"

    async def dispose(self, email: str) -> None:
        """
        记录被释放的测试邮箱

        :param email (str): 测试邮箱地址

        :return None: 无返回值
        """

        self.disposed_email = email


class FailingEmailProvider(FakeEmailProvider):
    """
    流程测试用失败邮箱 provider
    """

    async def create_email(self) -> str:
        """
        模拟邮箱创建失败

        :return str: 不会返回

        :raises EmailProviderResponseError: 始终模拟 provider 失败
        """

        raise EmailProviderResponseError("包含不应进入流程结果的第三方细节")


class FailingCodeProvider(FakeEmailProvider):
    """
    流程测试用验证码拉取失败 provider
    """

    async def wait_for_code(self, email: str, timeout: int) -> str:
        """
        模拟验证码拉取失败

        :param email (str): 测试邮箱地址
        :param timeout (int): 最大等待秒数

        :return str: 不会返回

        :raises EmailProviderResponseError: 始终模拟 provider 失败
        """

        raise EmailProviderResponseError("包含验证码邮件原文")


class FakeGitHubRegistrationClient(GitHubRegistrationClient):
    """
    流程测试用可控 GitHub 浏览器边界
    """

    def __init__(self, results: Optional[List[GitHubPageResult]] = None) -> None:
        """
        初始化测试浏览器边界

        :param results (List): 按调用顺序返回的页面结果
        """

        self.results = results or [GitHubPageResult(status=GitHubPageStatus.COMPLETED)]
        self.started_email: Optional[str] = None
        self.started_username: Optional[str] = None
        self.started_password: Optional[str] = None
        self.submitted_code: Optional[str] = None
        self.closed = False
        self.screenshot_sensitive_texts: Optional[List[str]] = None

    async def start_registration(self, email: str, username: str, password: str) -> GitHubPageResult:
        """
        记录注册参数并返回下一页面结果

        :param email (str): 注册邮箱
        :param username (str): GitHub 用户名
        :param password (str): GitHub 密码

        :return GitHubPageResult: 下一页面结果
        """

        self.started_email = email
        self.started_username = username
        self.started_password = password
        return self.results.pop(0)

    async def inspect_after_manual(self) -> GitHubPageResult:
        """
        返回人工处理后的页面结果

        :return GitHubPageResult: 下一页面结果
        """

        return self.results.pop(0)

    async def submit_email_code(self, code: str) -> GitHubPageResult:
        """
        记录验证码并返回下一页面结果

        :param code (str): GitHub 邮箱验证码

        :return GitHubPageResult: 下一页面结果
        """

        self.submitted_code = code
        return self.results.pop(0)

    async def close(self) -> None:
        """
        记录关闭操作

        :return None: 无返回值
        """

        self.closed = True

    async def capture_sanitized_screenshot(self, sensitive_texts: List[str]) -> Optional[bytes]:
        """
        返回带 PNG 签名的虚构已遮罩截图

        :param sensitive_texts (List): 流程要求遮罩的文本

        :return bytes: 虚构 PNG 数据
        """

        self.screenshot_sensitive_texts = sensitive_texts
        return b"\x89PNG\r\n\x1a\nmasked-flow-screenshot"


class FakeOpenCodeAutomationClient(OpenCodeAutomationClient):
    """
    流程测试用可控 OpenCode 浏览器边界
    """

    def __init__(self, results: Optional[List[OpenCodePageResult]] = None) -> None:
        """
        初始化测试 OpenCode 边界

        :param results (List): 按调用顺序返回的页面结果
        """

        self.results = results or [
            OpenCodePageResult(
                status=OpenCodePageStatus.PAYMENT_REQUIRED,
                workspace_id="wrk_test123",
                manual_reason=ManualInterventionReason.PAYMENT,
            ),
            OpenCodePageResult(
                status=OpenCodePageStatus.COMPLETED,
                workspace_id="wrk_test123",
                api_key=SecretStr("sk-" + "a" * 64),
            ),
        ]
        self.submitted_api_key: Optional[str] = None

    async def start_login(self) -> OpenCodePageResult:
        """
        返回 OpenCode 登录结果

        :return OpenCodePageResult: 下一页面结果
        """

        return self.results.pop(0)

    async def inspect_after_manual(self) -> OpenCodePageResult:
        """
        返回人工处理后的 OpenCode 结果

        :return OpenCodePageResult: 下一页面结果
        """

        return self.results.pop(0)

    async def confirm_payment(self) -> OpenCodePageResult:
        """
        返回付款确认后的密钥结果

        :return OpenCodePageResult: 下一页面结果
        """

        return self.results.pop(0)

    async def submit_api_key(self, api_key: str) -> OpenCodePageResult:
        """
        记录手动 API Key 并返回下一结果

        :param api_key (str): 测试 API Key

        :return OpenCodePageResult: 下一页面结果
        """

        self.submitted_api_key = api_key
        return self.results.pop(0)


def create_test_flow(
    providers: List[EmailProvider],
    github_client: GitHubRegistrationClient,
    opencode_client: Optional[OpenCodeAutomationClient] = None,
    completion_handler: Optional[Callable[[AccountCompletionData], Awaitable[str]]] = None,
    pending_handler: Optional[Callable[[PendingAccountData], Awaitable[str]]] = None,
    screenshot_store: Optional[ScreenshotStore] = None,
) -> CreateAccountFlow:
    """
    创建带默认 OpenCode 边界的测试流程

    :param providers (List): 测试邮箱 provider
    :param github_client (GitHubRegistrationClient): 测试 GitHub 边界
    :param opencode_client (OpenCodeAutomationClient): 可选测试 OpenCode 边界
    :param completion_handler (Callable): 可选账号完成边界
    :param pending_handler (Callable): 可选 GitHub 完成持久化边界
    :param screenshot_store (ScreenshotStore): 可选安全截图存储

    :return CreateAccountFlow: 测试账号流程
    """

    async def complete_account(data: AccountCompletionData) -> str:
        del data
        return "opencode-go"

    return CreateAccountFlow(
        providers,
        github_client,
        opencode_client or FakeOpenCodeAutomationClient(),
        completion_handler or complete_account,
        pending_handler=pending_handler,
        screenshot_store=screenshot_store,
    )


@pytest.mark.anyio
async def test_flow_completes_account_creation_without_exposing_api_key() -> None:
    """
    验证流程完成账号创建且公开快照不包含 API Key
    """

    browser = FakeGitHubRegistrationClient()
    flow = create_test_flow([FakeEmailProvider()], browser)

    pending = await flow.start()
    result = await flow.resume()

    assert pending.session.status == FlowStatus.PENDING_PAYMENT
    assert result.status == FlowStepStatus.DONE
    assert result.session.status == FlowStatus.DONE
    assert result.session.opencode_workspace_id == "wrk_test123"
    assert result.session.opencode_provider_name == "opencode-go"
    assert result.session.api_key_captured is True
    assert result.session.email_provider == "fake"
    assert result.session.temp_email == "flow@example.test"
    assert browser.started_email == "flow@example.test"
    assert browser.started_username == result.session.github_username
    assert browser.started_password is not None
    assert "password" not in result.session.model_dump()
    assert "sk-" not in result.session.model_dump_json()


@pytest.mark.anyio
async def test_flow_calls_completion_boundary_before_done_without_exposing_secrets() -> None:
    """
    验证流程仅在完成边界成功后结束且凭据不进入公开快照
    """

    captured: Optional[AccountCompletionData] = None

    async def capture_completion(data: AccountCompletionData) -> str:
        nonlocal captured
        captured = data
        return "opencode-go2"

    flow = create_test_flow(
        [FakeEmailProvider()],
        FakeGitHubRegistrationClient(),
        completion_handler=capture_completion,
    )

    await flow.start()
    completed = await flow.resume()

    assert captured is not None
    assert captured.github_username == completed.session.github_username
    assert captured.opencode_workspace_id == "wrk_test123"
    assert captured.github_password.get_secret_value()
    assert captured.opencode_api_key.get_secret_value().startswith("sk-")
    assert completed.session.status == FlowStatus.DONE
    assert completed.session.opencode_provider_name == "opencode-go2"
    assert "sk-" not in completed.session.model_dump_json()
    assert captured.opencode_api_key.get_secret_value() not in repr(captured)
    assert captured.github_password.get_secret_value() not in repr(captured)


@pytest.mark.anyio
async def test_flow_persists_github_account_before_waiting_for_payment() -> None:
    """
    验证 GitHub 注册完成即持久化凭据并把稳定账号标识交给完成边界
    """

    pending_data: Optional[PendingAccountData] = None
    completion_data: Optional[AccountCompletionData] = None

    async def persist_pending(data: PendingAccountData) -> str:
        nonlocal pending_data
        pending_data = data
        return "00000000-0000-4000-8000-000000000010"

    async def complete_account(data: AccountCompletionData) -> str:
        nonlocal completion_data
        completion_data = data
        return "opencode-go"

    flow = create_test_flow(
        [FakeEmailProvider()],
        FakeGitHubRegistrationClient(),
        completion_handler=complete_account,
        pending_handler=persist_pending,
    )

    payment = await flow.start()
    completed = await flow.resume()

    assert payment.session.status == FlowStatus.PENDING_PAYMENT
    assert payment.session.account_id == "00000000-0000-4000-8000-000000000010"
    assert pending_data is not None
    assert pending_data.github_username == payment.session.github_username
    assert completion_data is not None
    assert completion_data.account_id == payment.session.account_id
    assert completed.session.status == FlowStatus.DONE


@pytest.mark.anyio
async def test_manual_github_screenshot_is_sanitized_bounded_and_deleted_on_resume(tmp_path: Path) -> None:
    """
    验证 GitHub 人工介入只暴露已遮罩截图标识且恢复时立即删除文件
    """

    browser = FakeGitHubRegistrationClient(
        [
            GitHubPageResult(
                status=GitHubPageStatus.MANUAL_REQUIRED,
                manual_reason=ManualInterventionReason.CAPTCHA,
            ),
            GitHubPageResult(status=GitHubPageStatus.COMPLETED),
        ]
    )
    store = ScreenshotStore(tmp_path / "screenshots", True, retention_hours=24, max_per_flow=3)
    flow = create_test_flow(
        [FakeEmailProvider()],
        browser,
        screenshot_store=store,
    )

    manual = await flow.start()

    assert manual.session.status == FlowStatus.MANUAL_VERIFY
    assert manual.session.screenshot_id is not None
    screenshot_id = manual.session.screenshot_id
    assert store.read(manual.session.flow_id, screenshot_id).startswith(b"\x89PNG")
    assert browser.started_email in (browser.screenshot_sensitive_texts or [])
    assert browser.started_username in (browser.screenshot_sensitive_texts or [])
    assert browser.started_password in (browser.screenshot_sensitive_texts or [])

    resumed = await flow.resume()

    assert resumed.session.status == FlowStatus.PENDING_PAYMENT
    assert resumed.session.screenshot_id is None
    with pytest.raises(ScreenshotStoreError, match="不存在"):
        store.read(manual.session.flow_id, screenshot_id)


@pytest.mark.anyio
async def test_flow_fails_when_account_completion_fails() -> None:
    """
    验证配置或加密持久化失败时流程不得标记完成
    """

    async def fail_completion(data: AccountCompletionData) -> str:
        del data
        raise RuntimeError("包含不应公开的保存失败细节")

    flow = create_test_flow(
        [FakeEmailProvider()],
        FakeGitHubRegistrationClient(),
        completion_handler=fail_completion,
    )

    await flow.start()
    failed = await flow.resume()

    assert failed.status == FlowStepStatus.ERROR
    assert failed.session.status == FlowStatus.ERROR
    assert failed.session.error_code == "account_completion_failed"
    assert failed.session.opencode_provider_name is None
    assert failed.session.error_message is not None
    assert "保存失败细节" not in failed.session.error_message


@pytest.mark.anyio
async def test_flow_waits_for_payment_confirmation_before_fetching_api_key() -> None:
    """
    验证 OpenCode Go 付款是显式人工状态且确认后才读取密钥
    """

    opencode_client = FakeOpenCodeAutomationClient(
        [
            OpenCodePageResult(
                status=OpenCodePageStatus.PAYMENT_REQUIRED,
                workspace_id="wrk_payment123",
                manual_reason=ManualInterventionReason.PAYMENT,
            ),
            OpenCodePageResult(
                status=OpenCodePageStatus.COMPLETED,
                workspace_id="wrk_payment123",
                api_key=SecretStr("sk-" + "b" * 64),
            ),
        ]
    )
    flow = create_test_flow([FakeEmailProvider()], FakeGitHubRegistrationClient(), opencode_client)

    pending_payment = await flow.start()
    completed = await flow.resume()

    assert pending_payment.status == FlowStepStatus.NEED_MANUAL
    assert pending_payment.session.status == FlowStatus.PENDING_PAYMENT
    assert pending_payment.session.manual_intervention is not None
    assert pending_payment.session.manual_intervention.reason == ManualInterventionReason.PAYMENT
    assert pending_payment.session.api_key_captured is False
    assert completed.session.status == FlowStatus.DONE
    assert completed.session.api_key_captured is True


@pytest.mark.anyio
async def test_flow_accepts_manual_api_key_without_exposing_it() -> None:
    """
    验证自动复制失败后只在私有边界接收手动 API Key
    """

    api_key = "sk-" + "c" * 64
    opencode_client = FakeOpenCodeAutomationClient(
        [
            OpenCodePageResult(
                status=OpenCodePageStatus.PAYMENT_REQUIRED,
                workspace_id="wrk_manual123",
                manual_reason=ManualInterventionReason.PAYMENT,
            ),
            OpenCodePageResult(
                status=OpenCodePageStatus.API_KEY_INPUT_REQUIRED,
                workspace_id="wrk_manual123",
                manual_reason=ManualInterventionReason.API_KEY_INPUT,
            ),
            OpenCodePageResult(
                status=OpenCodePageStatus.COMPLETED,
                workspace_id="wrk_manual123",
                api_key=SecretStr(api_key),
            ),
        ]
    )
    flow = create_test_flow([FakeEmailProvider()], FakeGitHubRegistrationClient(), opencode_client)

    await flow.start()
    manual = await flow.resume()
    completed = await flow.resume(api_key)

    assert manual.session.status == FlowStatus.MANUAL_VERIFY
    assert manual.session.manual_intervention is not None
    assert manual.session.manual_intervention.reason == ManualInterventionReason.API_KEY_INPUT
    assert opencode_client.submitted_api_key == api_key
    assert completed.session.status == FlowStatus.DONE
    assert api_key not in completed.session.model_dump_json()


def test_generated_github_username_matches_current_constraints() -> None:
    """
    验证生成的 GitHub 用户名仅包含字母、数字和单个连字符
    """

    username = CreateAccountFlow._generate_username()

    assert username.startswith("learner-")
    assert len(username) == 18
    assert all(character.isalnum() or character == "-" for character in username)
    assert "--" not in username


@pytest.mark.anyio
async def test_flow_maps_provider_failure_to_safe_result() -> None:
    """
    验证基础流程将 provider 异常映射为安全结果
    """

    flow = create_test_flow([FailingEmailProvider()], FakeGitHubRegistrationClient())

    result = await flow.start()

    assert result.status == FlowStepStatus.ERROR
    assert result.session.status == FlowStatus.ERROR
    assert result.session.error_code == "email_provider_failed"
    assert result.session.error_message == "临时邮箱创建失败"
    assert "第三方细节" not in result.session.error_message


@pytest.mark.anyio
async def test_flow_falls_back_to_next_provider() -> None:
    """
    验证邮箱创建失败时流程尝试下一优先级 provider
    """

    flow = create_test_flow([FailingEmailProvider(), FakeEmailProvider()], FakeGitHubRegistrationClient())

    pending = await flow.start()
    result = await flow.resume()

    assert pending.session.status == FlowStatus.PENDING_PAYMENT
    assert result.status == FlowStepStatus.DONE
    assert result.session.email_provider == "fake"
    assert result.session.temp_email == "flow@example.test"
