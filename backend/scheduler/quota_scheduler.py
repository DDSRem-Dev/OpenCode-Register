import asyncio
from typing import List, Optional, Protocol

from scheduler.models import QuotaRefreshResult
from storage.service import VaultLockedError


class QuotaSchedulerTarget(Protocol):
    """
    周期额度任务调用的最小服务协议
    """

    async def refresh_all(self) -> List[QuotaRefreshResult]:
        """
        使用后台浏览器刷新全部账号

        :return List: 各账号额度刷新结果
        """


class QuotaScheduler:
    """
    OpenCode Go 周期额度检查生命周期所有者
    """

    def __init__(self, service: QuotaSchedulerTarget, interval_seconds: int) -> None:
        """
        初始化周期额度检查器

        :param service (QuotaSchedulerTarget): 额度刷新服务协议
        :param interval_seconds (int): 两次检查之间的秒数

        :raises ValueError: 检查间隔小于一分钟
        """

        if interval_seconds < 60:
            raise ValueError("额度检查间隔不能小于一分钟")
        self._service = service
        self._interval_seconds = interval_seconds
        self._stop_event = asyncio.Event()
        self._task: Optional[asyncio.Task[None]] = None

    def start(self) -> None:
        """
        启动唯一的周期检查任务，多次调用保持幂等

        :return None: 无返回值
        """

        if self._task is None or self._task.done():
            self._stop_event.clear()
            self._task = asyncio.create_task(self._run())

    async def close(self) -> None:
        """
        停止并回收周期检查任务，多次调用保持幂等

        :return None: 无返回值
        """

        task = self._task
        if task is None:
            return
        self._stop_event.set()
        await task
        self._task = None

    async def _run(self) -> None:
        while not self._stop_event.is_set():
            try:
                await self._service.refresh_all()
            except VaultLockedError:
                pass
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self._interval_seconds)
            except TimeoutError:
                continue
