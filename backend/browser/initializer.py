import asyncio
from enum import Enum
from typing import Callable, Optional

from cloakbrowser import ensure_binary  # type: ignore[import-untyped]


class BrowserInitializationStatus(Enum):
    """
    浏览器运行时初始化状态

    Attributes:
        INITIALIZING: 正在检查或下载浏览器
        READY: 浏览器二进制已可用
        ERROR: 浏览器初始化失败
    """

    INITIALIZING = "initializing"
    READY = "ready"
    ERROR = "error"


class BrowserInitializationError(RuntimeError):
    """
    浏览器运行时初始化失败异常
    """


class BrowserInitializer:
    """
    CloakBrowser 二进制初始化任务的唯一生命周期所有者
    """

    def __init__(self, install: Callable[[], str] = ensure_binary) -> None:
        """
        初始化尚未启动的浏览器安装任务

        :param install (Callable): 阻塞式 CloakBrowser 安装函数
        """

        self._install = install
        self._status = BrowserInitializationStatus.INITIALIZING
        self._task: Optional[asyncio.Task[None]] = None

    @property
    def status(self) -> BrowserInitializationStatus:
        """
        获取当前浏览器初始化状态

        :return BrowserInitializationStatus: 当前初始化状态
        """

        return self._status

    def start(self, retry_failed: bool = False) -> None:
        """
        启动浏览器检查与下载，重复调用保持幂等

        :param retry_failed (bool): 是否重试上一次失败的初始化

        :return None: 无返回值
        """

        if self._status == BrowserInitializationStatus.READY:
            return
        if self._task is not None and not self._task.done():
            return
        if self._status == BrowserInitializationStatus.ERROR and not retry_failed:
            return
        self._status = BrowserInitializationStatus.INITIALIZING
        self._task = asyncio.create_task(self._initialize())

    async def wait_until_ready(self) -> None:
        """
        等待浏览器二进制初始化完成

        :return None: 无返回值

        :raises BrowserInitializationError: 浏览器检查或下载失败
        """

        self.start()
        task = self._task
        if task is not None:
            await asyncio.shield(task)
        if self._status != BrowserInitializationStatus.READY:
            raise BrowserInitializationError("CloakBrowser 浏览器初始化失败")

    async def close(self) -> None:
        """
        取消并回收仍在等待的初始化任务

        :return None: 无返回值
        """

        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self._task = None

    async def _initialize(self) -> None:
        try:
            await asyncio.to_thread(self._install)
        except asyncio.CancelledError:
            raise
        except Exception:
            self._status = BrowserInitializationStatus.ERROR
            return
        self._status = BrowserInitializationStatus.READY
