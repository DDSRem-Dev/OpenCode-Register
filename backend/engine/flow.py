import asyncio
import secrets
import string
from typing import Awaitable, Callable, Dict, List, Optional, Set

from pydantic import SecretStr

from browser.base import GitHubRegistrationClient, OpenCodeAutomationClient
from browser.models import (
    GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE,
    GitHubPageResult,
    GitHubPageStatus,
    OpenCodePageResult,
    OpenCodePageStatus,
)
from engine.completion import AccountCompletionError
from engine.manual_intervention import create_flow_manual_intervention
from engine.models import (
    AccountCompletionData,
    FlowSession,
    FlowStatus,
    FlowStepResult,
    FlowStepStatus,
    ManualInterventionReason,
    PendingAccountData,
)
from engine.steps import CreateEmailStep
from providers.base import EmailProvider
from providers.errors import EmailProviderError
from storage.models import AccountStatus, BrowserAuthState
from storage.screenshots import ScreenshotStore, ScreenshotStoreError

USERNAME_PREFIXES = (
    "amber",
    "cedar",
    "cloud",
    "cobalt",
    "ember",
    "harbor",
    "lunar",
    "maple",
    "meadow",
    "north",
    "river",
    "silver",
    "solar",
    "willow",
)
USERNAME_SUFFIXES = (
    "atlas",
    "byte",
    "canvas",
    "craft",
    "field",
    "forge",
    "journal",
    "labs",
    "notes",
    "orbit",
    "pixel",
    "studio",
    "trail",
    "works",
)
MAX_GITHUB_USERNAME_ATTEMPTS = 5


class FlowTransitionError(Exception):
    """
    非法流程状态转换异常
    """


class CreateAccountFlow:
    """
    账号创建流程
    """

    _ALLOWED_TRANSITIONS: Dict[FlowStatus, Set[FlowStatus]] = {
        FlowStatus.IDLE: {FlowStatus.CREATING_EMAIL, FlowStatus.CANCELLED},
        FlowStatus.CREATING_EMAIL: {
            FlowStatus.GITHUB_REGISTER,
            FlowStatus.MANUAL_VERIFY,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.GITHUB_REGISTER: {
            FlowStatus.MANUAL_VERIFY,
            FlowStatus.GITHUB_EMAIL_VERIFY,
            FlowStatus.OPENCODE_LOGIN,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.MANUAL_VERIFY: {
            FlowStatus.GITHUB_REGISTER,
            FlowStatus.GITHUB_EMAIL_VERIFY,
            FlowStatus.OPENCODE_LOGIN,
            FlowStatus.FETCH_API_KEY,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.GITHUB_EMAIL_VERIFY: {
            FlowStatus.MANUAL_VERIFY,
            FlowStatus.OPENCODE_LOGIN,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.OPENCODE_LOGIN: {
            FlowStatus.MANUAL_VERIFY,
            FlowStatus.PENDING_PAYMENT,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.PENDING_PAYMENT: {FlowStatus.FETCH_API_KEY, FlowStatus.ERROR, FlowStatus.CANCELLED},
        FlowStatus.FETCH_API_KEY: {
            FlowStatus.MANUAL_VERIFY,
            FlowStatus.DONE,
            FlowStatus.ERROR,
            FlowStatus.CANCELLED,
        },
        FlowStatus.DONE: set(),
        FlowStatus.ERROR: set(),
        FlowStatus.CANCELLED: set(),
    }

    def __init__(
        self,
        providers: List[EmailProvider],
        github_client: GitHubRegistrationClient,
        opencode_client: OpenCodeAutomationClient,
        completion_handler: Callable[[AccountCompletionData], Awaitable[str]],
        state_listener: Optional[Callable[[FlowSession], None]] = None,
        pending_handler: Optional[Callable[[PendingAccountData], Awaitable[str]]] = None,
        pending_status_handler: Optional[Callable[[str, AccountStatus], Awaitable[None]]] = None,
        screenshot_store: Optional[ScreenshotStore] = None,
        auth_state_handler: Optional[Callable[[str, BrowserAuthState, BrowserAuthState], Awaitable[None]]] = None,
    ) -> None:
        """
        初始化账号创建流程

        :param providers (List): 按优先级排列的临时邮箱 provider
        :param github_client (GitHubRegistrationClient): GitHub 注册浏览器边界
        :param opencode_client (OpenCodeAutomationClient): OpenCode Go 浏览器边界
        :param completion_handler (Callable): 账号配置和加密持久化完成边界
        :param state_listener (Callable): 可选的状态快照监听器
        :param pending_handler (Callable): GitHub 注册完成后的加密持久化边界
        :param pending_status_handler (Callable): 未完成账号状态更新边界
        :param screenshot_store (ScreenshotStore): 可选的已遮罩截图存储
        :param auth_state_handler (Callable): 未完成账号认证状态更新边界
        """

        self._create_email_step = CreateEmailStep(providers)
        self._github_client = github_client
        self._opencode_client = opencode_client
        self._completion_handler = completion_handler
        self._state_listener = state_listener
        self._pending_handler = pending_handler
        self._pending_status_handler = pending_status_handler
        self._auth_state_handler = auth_state_handler
        self._screenshot_store = screenshot_store
        self._session = FlowSession()
        self._github_password: Optional[str] = self._generate_password()
        self._opencode_api_key: Optional[SecretStr] = None
        self._github_auth_state: Optional[BrowserAuthState] = None
        self._opencode_auth_state: Optional[BrowserAuthState] = None
        self._session.github_username = self._generate_username()
        self._paused_from: Optional[FlowStatus] = None
        self._is_waiting_for_email_code = False

    @property
    def can_interrupt_for_pause(self) -> bool:
        """
        判断当前异步等待是否可在不重复副作用的情况下中断

        :return bool: 是否可以安全取消当前任务并立即暂停
        """

        return self._session.status == FlowStatus.GITHUB_EMAIL_VERIFY and self._is_waiting_for_email_code

    async def start(self) -> FlowStepResult:
        """
        启动流程并执行到人工付款、人工处理或账号创建完成

        :return FlowStepResult: 当前阶段执行结果

        :raises FlowTransitionError: 流程不在可启动状态
        """

        self._transition(FlowStatus.CREATING_EMAIL)
        step_result = await self._create_email_step.execute()
        if self._session.status == FlowStatus.CANCELLED:
            if step_result.temp_email is not None:
                await self._dispose_email(step_result.temp_email)
            return self._result(FlowStepStatus.CANCELLED)
        if step_result.status == FlowStepStatus.ERROR:
            return await self._fail_and_cleanup(
                step_result.error_code or "email_creation_failed",
                step_result.error_message or "临时邮箱创建失败",
            )

        self._session.email_provider = step_result.email_provider
        self._session.temp_email = step_result.temp_email
        if self._session.pause_requested:
            return self._pause_at(FlowStatus.GITHUB_REGISTER)
        self._transition(FlowStatus.GITHUB_REGISTER)
        return await self._start_github_registration()

    async def _start_github_registration(self) -> FlowStepResult:
        if self._session.temp_email is None or self._session.github_username is None or self._github_password is None:
            return await self._fail_and_cleanup("flow_state_invalid", "账号流程状态无效")
        for attempt in range(MAX_GITHUB_USERNAME_ATTEMPTS):
            page_result = await self._github_client.start_registration(
                self._session.temp_email,
                self._session.github_username,
                self._github_password,
            )
            if page_result.error_code != GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE:
                return await self._handle_github_result(page_result)
            if attempt + 1 < MAX_GITHUB_USERNAME_ATTEMPTS:
                self._session.github_username = self._generate_username()
                self._notify()
        return await self._fail_and_cleanup(GITHUB_USERNAME_UNAVAILABLE_ERROR_CODE, "未能生成可用的 GitHub 用户名")

    async def resume(self, api_key: Optional[str] = None) -> FlowStepResult:
        """
        用户完成人工操作、确认付款或提交密钥后恢复流程

        :param api_key (str): 自动复制失败时用户手动提交的 API Key

        :return FlowStepResult: 恢复后的流程结果

        :raises FlowTransitionError: 当前流程不在人工介入状态
        """

        await self._clear_screenshots()
        if self._session.status == FlowStatus.PENDING_PAYMENT:
            if api_key is not None:
                raise FlowTransitionError("付款确认不接受 API Key")
            self._session.manual_intervention = None
            self._transition(FlowStatus.FETCH_API_KEY)
            return await self._handle_opencode_result(await self._opencode_client.confirm_payment())
        if self._session.status != FlowStatus.MANUAL_VERIFY:
            raise FlowTransitionError(f"当前流程不可恢复: {self._session.status.value}")
        if self._session.manual_intervention is not None:
            if self._session.manual_intervention.reason == ManualInterventionReason.USER_PAUSED:
                return await self._resume_user_pause()
            if self._session.manual_intervention.reason == ManualInterventionReason.API_KEY_INPUT:
                if api_key is None:
                    raise FlowTransitionError("当前流程需要 API Key")
                return await self._handle_opencode_result(await self._opencode_client.submit_api_key(api_key))
        if api_key is not None:
            raise FlowTransitionError("当前人工操作不接受 API Key")
        if self._paused_from == FlowStatus.OPENCODE_LOGIN:
            self._restore_manual_state(FlowStatus.OPENCODE_LOGIN)
            return await self._handle_opencode_result(await self._opencode_client.inspect_after_manual())
        if self._paused_from == FlowStatus.FETCH_API_KEY:
            self._restore_manual_state(FlowStatus.FETCH_API_KEY)
            return await self._handle_opencode_result(await self._opencode_client.confirm_payment())
        page_result = await self._github_client.inspect_after_manual()
        return await self._handle_github_result(page_result)

    def request_pause(self) -> FlowStepResult:
        """
        请求流程在下一个不会重复副作用的安全点暂停

        :return FlowStepResult: 已接受暂停意图的流程快照

        :raises FlowTransitionError: 当前状态不可暂停
        """

        if self._session.status not in {
            FlowStatus.IDLE,
            FlowStatus.CREATING_EMAIL,
            FlowStatus.GITHUB_REGISTER,
            FlowStatus.GITHUB_EMAIL_VERIFY,
        }:
            raise FlowTransitionError(f"当前流程不可暂停: {self._session.status.value}")
        self._session.pause_requested = True
        self._notify()
        return self._result(FlowStepStatus.NEED_MANUAL)

    def pause_after_interruption(self) -> FlowStepResult:
        """
        在可安全取消的等待操作被中断后进入人工暂停状态

        :return FlowStepResult: 人工暂停后的流程结果

        :raises FlowTransitionError: 当前步骤不支持中断后暂停
        """

        if self._session.status != FlowStatus.GITHUB_EMAIL_VERIFY:
            raise FlowTransitionError(f"当前流程不可中断暂停: {self._session.status.value}")
        return self._pause_at(FlowStatus.GITHUB_EMAIL_VERIFY)

    async def cancel(self) -> FlowStepResult:
        """
        取消流程并尽力释放已创建邮箱

        :return FlowStepResult: 取消后的流程结果

        :raises FlowTransitionError: 当前流程已结束且不可取消
        """

        email = self._session.temp_email
        self._create_email_step.cancel()
        self._session.pause_requested = False
        await self._mark_pending_status(AccountStatus.CANCELLED)
        self._github_password = None
        self._opencode_api_key = None
        self._github_auth_state = None
        self._opencode_auth_state = None
        self._transition(FlowStatus.CANCELLED)
        if email is not None:
            await self._dispose_email(email)
        await self._github_client.close()
        await self._clear_screenshots()
        return self._result(FlowStepStatus.CANCELLED)

    def snapshot(self) -> FlowSession:
        """
        获取不可共享引用的流程会话快照

        :return FlowSession: 当前流程会话副本
        """

        return self._session.model_copy(deep=True)

    async def fail(self, error_code: str, error_message: str) -> FlowStepResult:
        """
        将未预期资源故障收敛为安全失败状态

        :param error_code (str): 稳定错误代码
        :param error_message (str): 不包含敏感细节的错误消息

        :return FlowStepResult: 失败后的流程结果
        """

        return await self._fail_and_cleanup(error_code, error_message)

    def _transition(self, target: FlowStatus) -> None:
        allowed_targets = self._ALLOWED_TRANSITIONS[self._session.status]
        if target not in allowed_targets:
            raise FlowTransitionError(f"非法流程状态转换: {self._session.status.value} -> {target.value}")
        self._session.status = target
        self._notify()

    def _notify(self) -> None:
        if self._state_listener is not None:
            self._state_listener(self.snapshot())

    def _result(self, status: FlowStepStatus) -> FlowStepResult:
        return FlowStepResult(status=status, session=self.snapshot())

    async def _dispose_email(self, email: str) -> None:
        provider = self._create_email_step.selected_provider
        if provider is not None:
            await provider.dispose(email)

    async def _handle_github_result(self, page_result: GitHubPageResult) -> FlowStepResult:
        if page_result.github_auth_state is not None:
            self._github_auth_state = page_result.github_auth_state
        if page_result.status == GitHubPageStatus.MANUAL_REQUIRED:
            self._session.pause_requested = False
            self._paused_from = self._session.status
            reason = page_result.manual_reason or ManualInterventionReason.UNKNOWN_BLOCK
            self._session.manual_intervention = create_flow_manual_intervention(reason)
            await self._capture_manual_screenshot()
            if self._session.status != FlowStatus.MANUAL_VERIFY:
                self._transition(FlowStatus.MANUAL_VERIFY)
            return self._result(FlowStepStatus.NEED_MANUAL)
        self._session.manual_intervention = None
        self._paused_from = None
        if page_result.status == GitHubPageStatus.EMAIL_CODE_REQUIRED:
            if self._session.pause_requested:
                return self._pause_at(FlowStatus.GITHUB_EMAIL_VERIFY)
            if self._session.status != FlowStatus.GITHUB_EMAIL_VERIFY:
                self._transition(FlowStatus.GITHUB_EMAIL_VERIFY)
            return await self._verify_email()
        if page_result.status == GitHubPageStatus.COMPLETED:
            self._session.pause_requested = False
            if self._session.account_id is None and self._pending_handler is not None:
                pending_data = self._pending_data()
                if pending_data is None:
                    return await self._fail_and_cleanup("flow_state_invalid", "账号流程状态无效")
                try:
                    self._session.account_id = await self._pending_handler(pending_data)
                except AccountCompletionError as error:
                    return await self._fail_and_cleanup("github_persistence_failed", str(error))
            self._transition(FlowStatus.OPENCODE_LOGIN)
            return await self._handle_opencode_result(await self._opencode_client.start_login())
        return await self._fail_and_cleanup(
            page_result.error_code or "github_registration_failed",
            page_result.error_message or "GitHub 注册流程失败",
        )

    async def _verify_email(self) -> FlowStepResult:
        email = self._session.temp_email
        provider = self._create_email_step.selected_provider
        if email is None or provider is None:
            return await self._fail_and_cleanup("flow_state_invalid", "账号流程状态无效")
        try:
            self._is_waiting_for_email_code = True
            code = await provider.wait_for_code(email, timeout=300)
        except EmailProviderError:
            return await self._fail_and_cleanup("github_email_code_failed", "未能获取 GitHub 邮箱验证码")
        finally:
            self._is_waiting_for_email_code = False
        page_result = await self._github_client.submit_email_code(code)
        return await self._handle_github_result(page_result)

    async def _handle_opencode_result(self, page_result: OpenCodePageResult) -> FlowStepResult:
        self._remember_auth_states(page_result)
        if page_result.workspace_id is not None:
            self._session.opencode_workspace_id = page_result.workspace_id
        if page_result.status == OpenCodePageStatus.PAYMENT_REQUIRED:
            return await self._handle_payment_required()
        if page_result.status in {
            OpenCodePageStatus.MANUAL_REQUIRED,
            OpenCodePageStatus.API_KEY_INPUT_REQUIRED,
        }:
            self._paused_from = self._session.status
            reason = page_result.manual_reason or ManualInterventionReason.UNKNOWN_BLOCK
            self._session.manual_intervention = create_flow_manual_intervention(reason)
            if self._session.status != FlowStatus.MANUAL_VERIFY:
                self._transition(FlowStatus.MANUAL_VERIFY)
            return self._result(FlowStepStatus.NEED_MANUAL)
        if page_result.status == OpenCodePageStatus.COMPLETED:
            if page_result.workspace_id is None or page_result.api_key is None:
                return await self._fail_and_cleanup("opencode_result_invalid", "OpenCode 密钥结果无效")
            self._opencode_api_key = page_result.api_key
            self._session.opencode_workspace_id = page_result.workspace_id
            self._session.api_key_captured = True
            self._restore_manual_state(FlowStatus.FETCH_API_KEY)
            completion_data = self._completion_data()
            if completion_data is None:
                return await self._fail_and_cleanup("flow_state_invalid", "账号流程状态无效")
            completion_task: asyncio.Future[str] = asyncio.ensure_future(self._completion_handler(completion_data))
            try:
                provider_name = await asyncio.shield(completion_task)
            except asyncio.CancelledError:
                try:
                    provider_name = await completion_task
                except Exception:
                    await self._fail_and_cleanup(
                        "account_completion_failed",
                        "账号保存或号池配置写入失败",
                    )
                    raise
                await self._finish_completed_account(provider_name)
                raise
            except AccountCompletionError as error:
                return await self._fail_and_cleanup("account_completion_failed", str(error))
            except Exception:
                return await self._fail_and_cleanup("account_completion_failed", "账号保存或号池配置写入失败")
            await self._finish_completed_account(provider_name)
            return self._result(FlowStepStatus.DONE)
        return await self._fail_and_cleanup(
            page_result.error_code or "opencode_flow_failed",
            page_result.error_message or "OpenCode Go 流程失败",
        )

    def _remember_auth_states(self, page_result: OpenCodePageResult) -> None:
        if page_result.github_auth_state is not None:
            self._github_auth_state = page_result.github_auth_state
        if page_result.opencode_auth_state is not None:
            self._opencode_auth_state = page_result.opencode_auth_state

    async def _handle_payment_required(self) -> FlowStepResult:
        try:
            await self._persist_auth_states()
        except AccountCompletionError as error:
            return await self._fail_and_cleanup("auth_state_persistence_failed", str(error))
        await self._mark_pending_status(AccountStatus.PENDING_PAYMENT)
        self._session.manual_intervention = create_flow_manual_intervention(ManualInterventionReason.PAYMENT)
        self._transition(FlowStatus.PENDING_PAYMENT)
        return self._result(FlowStepStatus.NEED_MANUAL)

    def _completion_data(self) -> Optional[AccountCompletionData]:
        session = self._session
        if (
            session.github_username is None
            or session.temp_email is None
            or session.email_provider is None
            or session.opencode_workspace_id is None
            or self._github_password is None
            or self._opencode_api_key is None
        ):
            return None
        return AccountCompletionData(
            account_id=session.account_id,
            github_username=session.github_username,
            github_email=session.temp_email,
            github_password=SecretStr(self._github_password),
            opencode_workspace_id=session.opencode_workspace_id,
            opencode_api_key=self._opencode_api_key,
            github_auth_state=self._github_auth_state,
            opencode_auth_state=self._opencode_auth_state,
            email_provider=session.email_provider,
            temp_email=session.temp_email,
        )

    def _pending_data(self) -> Optional[PendingAccountData]:
        session = self._session
        if (
            session.github_username is None
            or session.temp_email is None
            or session.email_provider is None
            or self._github_password is None
        ):
            return None
        return PendingAccountData(
            github_username=session.github_username,
            github_email=session.temp_email,
            github_password=SecretStr(self._github_password),
            github_auth_state=self._github_auth_state,
            email_provider=session.email_provider,
            temp_email=session.temp_email,
        )

    async def _mark_pending_status(self, status: AccountStatus) -> None:
        account_id = self._session.account_id
        if account_id is None or self._pending_status_handler is None:
            return
        try:
            await self._pending_status_handler(account_id, status)
        except AccountCompletionError:
            return

    async def _persist_auth_states(self) -> None:
        account_id = self._session.account_id
        github_auth_state = self._github_auth_state
        opencode_auth_state = self._opencode_auth_state
        if (
            account_id is None
            or github_auth_state is None
            or opencode_auth_state is None
            or self._auth_state_handler is None
        ):
            return
        await self._auth_state_handler(account_id, github_auth_state, opencode_auth_state)

    async def _capture_manual_screenshot(self) -> None:
        store = self._screenshot_store
        if store is None or not store.is_enabled:
            self._session.screenshot_id = None
            return
        sensitive_texts = [
            self._session.temp_email or "",
            self._session.github_username or "",
            self._session.opencode_workspace_id or "",
            self._github_password or "",
        ]
        png = await self._github_client.capture_sanitized_screenshot(sensitive_texts)
        if png is None:
            self._session.screenshot_id = None
            return
        try:
            self._session.screenshot_id = await asyncio.to_thread(store.save, self._session.flow_id, png)
        except ScreenshotStoreError:
            self._session.screenshot_id = None

    async def _clear_screenshots(self) -> None:
        self._session.screenshot_id = None
        if self._screenshot_store is None:
            return
        try:
            await asyncio.to_thread(self._screenshot_store.delete_flow, self._session.flow_id)
        except ScreenshotStoreError:
            return

    async def _finish_completed_account(self, provider_name: str) -> None:
        self._session.opencode_provider_name = provider_name
        self._github_password = None
        self._opencode_api_key = None
        self._github_auth_state = None
        self._opencode_auth_state = None
        self._transition(FlowStatus.DONE)
        if self._session.temp_email is not None:
            await self._dispose_email(self._session.temp_email)
        await self._github_client.close()
        await self._clear_screenshots()

    async def _fail_and_cleanup(self, error_code: str, error_message: str) -> FlowStepResult:
        self._session.pause_requested = False
        await self._mark_pending_status(AccountStatus.PENDING_SETUP)
        self._github_password = None
        self._opencode_api_key = None
        self._github_auth_state = None
        self._opencode_auth_state = None
        result = self._fail(error_code, error_message)
        if self._session.temp_email is not None:
            await self._dispose_email(self._session.temp_email)
        await self._github_client.close()
        await self._clear_screenshots()
        return result

    def _pause_at(self, resume_status: FlowStatus) -> FlowStepResult:
        self._paused_from = resume_status
        self._session.pause_requested = False
        self._session.manual_intervention = create_flow_manual_intervention(ManualInterventionReason.USER_PAUSED)
        self._transition(FlowStatus.MANUAL_VERIFY)
        return self._result(FlowStepStatus.NEED_MANUAL)

    async def _resume_user_pause(self) -> FlowStepResult:
        resume_status = self._paused_from
        if resume_status is None:
            return await self._fail_and_cleanup("flow_state_invalid", "账号流程暂停状态无效")
        self._paused_from = None
        self._session.manual_intervention = None
        self._transition(resume_status)
        if resume_status == FlowStatus.GITHUB_REGISTER:
            return await self._start_github_registration()
        if resume_status == FlowStatus.GITHUB_EMAIL_VERIFY:
            return await self._verify_email()
        if resume_status == FlowStatus.OPENCODE_LOGIN:
            return await self._handle_opencode_result(await self._opencode_client.inspect_after_manual())
        return await self._fail_and_cleanup("flow_state_invalid", "账号流程恢复状态无效")

    def _restore_manual_state(self, target: FlowStatus) -> None:
        if self._session.status != FlowStatus.MANUAL_VERIFY:
            return
        self._paused_from = None
        self._session.manual_intervention = None
        self._transition(target)

    def _fail(self, error_code: str, error_message: str) -> FlowStepResult:
        self._session.error_code = error_code
        self._session.error_message = error_message
        self._transition(FlowStatus.ERROR)
        return self._result(FlowStepStatus.ERROR)

    @staticmethod
    def _generate_username() -> str:
        prefix = secrets.choice(USERNAME_PREFIXES)
        suffix = secrets.choice(USERNAME_SUFFIXES)
        pattern = secrets.randbelow(4)
        number = secrets.randbelow(9_900) + 100
        if pattern == 0:
            return f"{prefix}{suffix}{number}"
        if pattern == 1:
            return f"{prefix}-{suffix}{number}"
        if pattern == 2:
            return f"{suffix}{prefix}{number}"
        return f"{suffix}-{prefix}{number}"

    @staticmethod
    def _generate_password() -> str:
        alphabet = string.ascii_letters + string.digits + "!@#$%^&*"
        required = [
            secrets.choice(string.ascii_uppercase),
            secrets.choice(string.ascii_lowercase),
            secrets.choice(string.digits),
            secrets.choice("!@#$%^&*"),
        ]
        required.extend(secrets.choice(alphabet) for _ in range(16))
        secrets.SystemRandom().shuffle(required)
        return "".join(required)
