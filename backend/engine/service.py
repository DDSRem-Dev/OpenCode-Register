import asyncio
from typing import AsyncGenerator, Awaitable, Callable, Dict, List, Optional, Set, Tuple

from browser.base import GitHubRegistrationClient, OpenCodeAutomationClient
from browser.cloakbrowser_client import CloakBrowserClient
from browser.github_register import GitHubRegister
from browser.opencode_login import OpenCodeLogin
from browser.temp_mail import TempMailBrowser
from engine.events import FlowEvent, create_flow_event
from engine.flow import CreateAccountFlow, FlowTransitionError
from engine.models import AccountCompletionData, FlowSession, FlowStatus, PendingAccountData
from providers.base import EmailProvider
from providers.integrations.temp_mail import TempMailProvider
from providers.models import TempMailProviderSettings
from storage.models import AccountStatus
from storage.screenshots import ScreenshotStore


class FlowNotFoundError(Exception):
    """
    指定流程不存在异常
    """


class FlowBusyError(Exception):
    """
    指定流程仍有操作正在执行异常
    """


class CreateAccountService:
    """
    账号创建流程生命周期服务

    服务统一拥有共享浏览器、活动流程和所有后台任务
    """

    def __init__(
        self,
        completion_handler: Callable[[AccountCompletionData], Awaitable[str]],
        manual_timeout_seconds: float = 300,
        browser_factory: Optional[Callable[[], Tuple[GitHubRegistrationClient, OpenCodeAutomationClient]]] = None,
        provider_factory: Optional[Callable[[], EmailProvider]] = None,
        pending_handler: Optional[Callable[[PendingAccountData], Awaitable[str]]] = None,
        pending_status_handler: Optional[Callable[[str, AccountStatus], Awaitable[None]]] = None,
        screenshot_store: Optional[ScreenshotStore] = None,
    ) -> None:
        """
        初始化账号创建流程服务

        :param completion_handler (Callable): 账号配置与持久化完成边界
        :param manual_timeout_seconds (float): 人工介入最大等待秒数
        :param browser_factory (Callable): 可选的浏览器边界构造函数
        :param provider_factory (Callable): 可选的临时邮箱 provider 构造函数
        :param pending_handler (Callable): GitHub 注册完成后的持久化边界
        :param pending_status_handler (Callable): 未完成账号状态更新边界
        :param screenshot_store (ScreenshotStore): 可选的已遮罩截图存储
        """

        self._completion_handler = completion_handler
        self._pending_handler = pending_handler
        self._pending_status_handler = pending_status_handler
        self._screenshot_store = screenshot_store
        self._flows: Dict[str, CreateAccountFlow] = {}
        self._tasks: Dict[str, asyncio.Task[None]] = {}
        self._manual_timeout_tasks: Dict[str, asyncio.Task[None]] = {}
        self._subscribers: Dict[str, Set[asyncio.Queue[FlowEvent]]] = {}
        self._manual_timeout_seconds = manual_timeout_seconds
        self._account_browser_client: Optional[CloakBrowserClient] = None
        self._email_browser_client: Optional[CloakBrowserClient] = None
        if browser_factory is None:
            self._account_browser_client = CloakBrowserClient()
        if provider_factory is None:
            self._email_browser_client = CloakBrowserClient(headless=True)
        if browser_factory is None:
            self._browser_factory = self._create_browsers
        else:
            self._browser_factory = browser_factory
        if provider_factory is None:
            self._provider_factory = self._create_provider
        else:
            self._provider_factory = provider_factory

    def create(self) -> FlowSession:
        """
        创建流程并在后台启动执行

        :return FlowSession: 新流程的初始权威快照
        """

        providers: List[EmailProvider] = [self._provider_factory()]
        github_client, opencode_client = self._browser_factory()
        flow = CreateAccountFlow(
            providers,
            github_client,
            opencode_client,
            self._completion_handler,
            self._publish_snapshot,
            self._pending_handler,
            self._pending_status_handler,
            self._screenshot_store,
        )
        snapshot = flow.snapshot()
        self._flows[snapshot.flow_id] = flow
        self._start_task(snapshot.flow_id, flow.start)
        return snapshot

    async def events(self, flow_id: str) -> AsyncGenerator[FlowEvent, None]:
        """
        订阅指定流程的有界事件流

        连接后首先返回权威快照；慢客户端队列只保留最近状态

        :param flow_id (str): 流程唯一标识

        :yields FlowEvent: 类型化流程事件

        :raises FlowNotFoundError: 指定流程不存在
        """

        flow = self._get_flow(flow_id)
        queue: asyncio.Queue[FlowEvent] = asyncio.Queue(maxsize=8)
        subscribers = self._subscribers.setdefault(flow_id, set())
        subscribers.add(queue)
        try:
            yield create_flow_event(flow.snapshot(), is_initial=True)
            while True:
                yield await queue.get()
        finally:
            subscribers.discard(queue)
            if not subscribers:
                self._subscribers.pop(flow_id, None)

    def snapshot(self, flow_id: str) -> FlowSession:
        """
        获取指定流程的权威快照

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 当前流程会话快照

        :raises FlowNotFoundError: 指定流程不存在
        """

        return self._get_flow(flow_id).snapshot()

    def screenshot(self, flow_id: str, screenshot_id: str) -> bytes:
        """
        读取指定流程当前可访问的已遮罩截图

        :param flow_id (str): 流程稳定 UUID
        :param screenshot_id (str): 截图稳定 UUID

        :return bytes: 已遮罩 PNG 数据

        :raises FlowNotFoundError: 流程不存在或截图功能未启用
        :raises ScreenshotStoreError: 截图不存在或无法安全读取
        """

        self._get_flow(flow_id)
        if self._screenshot_store is None:
            raise FlowNotFoundError(flow_id)
        return self._screenshot_store.read(flow_id, screenshot_id)

    async def resume(self, flow_id: str, api_key: Optional[str] = None) -> FlowSession:
        """
        请求恢复等待人工介入的流程

        :param flow_id (str): 流程唯一标识
        :param api_key (str): 自动复制失败时用户手动提交的 API Key

        :return FlowSession: 接受恢复请求时的流程快照

        :raises FlowNotFoundError: 指定流程不存在
        :raises FlowBusyError: 流程已有操作正在执行
        :raises FlowTransitionError: 当前状态不可恢复
        """

        flow = self._get_flow(flow_id)
        resumable_statuses = {FlowStatus.MANUAL_VERIFY, FlowStatus.PENDING_PAYMENT}
        if flow.snapshot().status not in resumable_statuses:
            raise FlowTransitionError(f"当前流程不可恢复: {flow.snapshot().status.value}")
        self._ensure_idle(flow_id)
        await self._cancel_manual_timeout(flow_id)
        if flow.snapshot().status not in resumable_statuses:
            raise FlowTransitionError(f"当前流程不可恢复: {flow.snapshot().status.value}")
        self._start_task(flow_id, lambda: flow.resume(api_key))
        return flow.snapshot()

    async def cancel(self, flow_id: str) -> FlowSession:
        """
        取消指定流程并回收其外部资源

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 取消后的权威流程快照

        :raises FlowNotFoundError: 指定流程不存在
        :raises FlowTransitionError: 当前状态不可取消
        """

        flow = self._get_flow(flow_id)
        await self._cancel_manual_timeout(flow_id)
        task = self._tasks.get(flow_id)
        if task is not None and not task.done():
            task.cancel()
            await self._reap(task)
        await flow.cancel()
        return flow.snapshot()

    async def pause(self, flow_id: str) -> FlowSession:
        """
        请求指定流程在安全点暂停

        邮箱验证码轮询可立即中断并从同一步骤恢复，其他原子操作完成后暂停

        :param flow_id (str): 流程唯一标识

        :return FlowSession: 接受暂停请求后的权威流程快照

        :raises FlowNotFoundError: 指定流程不存在
        :raises FlowTransitionError: 当前状态不可暂停
        """

        flow = self._get_flow(flow_id)
        result = flow.request_pause()
        if result.session.status == FlowStatus.GITHUB_EMAIL_VERIFY and flow.can_interrupt_for_pause:
            task = self._tasks.get(flow_id)
            if task is not None and not task.done():
                task.cancel()
                await self._reap(task)
            if flow.snapshot().status == FlowStatus.GITHUB_EMAIL_VERIFY:
                flow.pause_after_interruption()
        return flow.snapshot()

    async def close(self) -> None:
        """
        取消全部活动任务并回收流程资源

        :return None: 无返回值
        """

        for task in self._tasks.values():
            if not task.done():
                task.cancel()
        for task in self._manual_timeout_tasks.values():
            if not task.done():
                task.cancel()
        for task in self._tasks.values():
            await self._reap(task)
        for task in self._manual_timeout_tasks.values():
            await self._reap(task)
        for flow in self._flows.values():
            if flow.snapshot().status not in {FlowStatus.DONE, FlowStatus.ERROR, FlowStatus.CANCELLED}:
                try:
                    await flow.cancel()
                except FlowTransitionError:
                    continue
        if self._account_browser_client is not None:
            await self._account_browser_client.close()
        if self._email_browser_client is not None:
            await self._email_browser_client.close()

    def _start_task(self, flow_id: str, operation: Callable[[], Awaitable[object]]) -> None:
        self._tasks[flow_id] = asyncio.create_task(self._run(flow_id, operation))

    async def _run(self, flow_id: str, operation: Callable[[], Awaitable[object]]) -> None:
        try:
            await operation()
            snapshot = self._flows[flow_id].snapshot()
            self._publish_snapshot(snapshot)
            if snapshot.manual_intervention is not None:
                self._schedule_manual_timeout(flow_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            flow = self._flows[flow_id]
            if flow.snapshot().status not in {FlowStatus.DONE, FlowStatus.ERROR, FlowStatus.CANCELLED}:
                await flow.fail("flow_resource_failed", "账号创建流程遇到本地资源故障")

    def _publish_snapshot(self, session: FlowSession) -> None:
        event = create_flow_event(session)
        for queue in self._subscribers.get(session.flow_id, set()):
            if queue.full():
                queue.get_nowait()
            queue.put_nowait(event)

    def _schedule_manual_timeout(self, flow_id: str) -> None:
        previous_task = self._manual_timeout_tasks.get(flow_id)
        if previous_task is not None and not previous_task.done():
            return
        self._manual_timeout_tasks[flow_id] = asyncio.create_task(self._expire_manual(flow_id))

    async def _cancel_manual_timeout(self, flow_id: str) -> None:
        task = self._manual_timeout_tasks.pop(flow_id, None)
        if task is not None and not task.done():
            flow = self._flows[flow_id]
            if flow.snapshot().manual_intervention is not None:
                task.cancel()
            await self._reap(task)

    async def _expire_manual(self, flow_id: str) -> None:
        try:
            await asyncio.sleep(self._manual_timeout_seconds)
        except asyncio.CancelledError:
            return
        flow = self._flows[flow_id]
        if flow.snapshot().manual_intervention is not None:
            await flow.fail("manual_intervention_timeout", "人工操作等待超时")

    def _get_flow(self, flow_id: str) -> CreateAccountFlow:
        flow = self._flows.get(flow_id)
        if flow is None:
            raise FlowNotFoundError(flow_id)
        return flow

    def _ensure_idle(self, flow_id: str) -> None:
        task: Optional[asyncio.Task[None]] = self._tasks.get(flow_id)
        if task is not None and not task.done():
            raise FlowBusyError(flow_id)

    @staticmethod
    async def _reap(task: asyncio.Task[None]) -> None:
        try:
            await task
        except asyncio.CancelledError:
            return

    def _create_browsers(self) -> Tuple[GitHubRegistrationClient, OpenCodeAutomationClient]:
        """
        为账号流程创建共享同一隔离上下文的浏览器适配器

        :return Tuple: GitHub 与 OpenCode 浏览器适配器

        :raises RuntimeError: CloakBrowser 管理器未初始化
        """

        if self._account_browser_client is None:
            raise RuntimeError("CloakBrowser 浏览器管理器未初始化")
        browser_session = self._account_browser_client.create_session()
        return GitHubRegister(browser_session), OpenCodeLogin(browser_session)

    def _create_provider(self) -> EmailProvider:
        """
        为账号流程创建独立的 Temp-Mail 浏览器 provider

        :return EmailProvider: 使用独立浏览器上下文的 Temp-Mail provider

        :raises RuntimeError: CloakBrowser 管理器未初始化
        """

        if self._email_browser_client is None:
            raise RuntimeError("Temp-Mail 后台浏览器管理器未初始化")
        settings = TempMailProviderSettings()
        mailbox_client = TempMailBrowser(self._email_browser_client.create_session(), settings)
        return TempMailProvider(mailbox_client, settings.poll_interval_seconds)
