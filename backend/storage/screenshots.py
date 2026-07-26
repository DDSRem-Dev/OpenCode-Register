import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

_IDENTIFIER_PATTERN = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_MAX_SCREENSHOT_BYTES = 5 * 1024 * 1024


class ScreenshotStoreError(Exception):
    """
    安全截图存储或读取失败异常
    """


class ScreenshotStore:
    """
    已遮罩人工介入截图的有界私有存储
    """

    def __init__(
        self,
        directory: Path,
        enabled: bool,
        retention_hours: int,
        max_per_flow: int,
    ) -> None:
        """
        初始化截图存储策略

        :param directory (Path): 应用私有截图根目录
        :param enabled (bool): 是否由用户显式启用截图
        :param retention_hours (int): 活动流程截图最长保留小时数
        :param max_per_flow (int): 每流程最大截图数
        """

        self._directory = directory.resolve()
        self._enabled = enabled
        self._retention = timedelta(hours=retention_hours)
        self._max_per_flow = max_per_flow
        if enabled:
            self._directory.mkdir(parents=True, exist_ok=True)
            os.chmod(self._directory, 0o700)
            self.prune()

    @property
    def is_enabled(self) -> bool:
        """
        返回截图是否由用户显式启用

        :return bool: 启用时返回真
        """

        return self._enabled

    def save(self, flow_id: str, png: bytes) -> str:
        """
        原子保存一张已遮罩 PNG 并执行数量与时间留存

        :param flow_id (str): 流程稳定 UUID
        :param png (bytes): 已在浏览器层完成遮罩的 PNG 数据

        :return str: 不包含路径信息的截图稳定标识

        :raises ScreenshotStoreError: 未启用、标识无效或数据无法安全写入
        """

        if not self._enabled:
            raise ScreenshotStoreError("截图功能尚未启用")
        _validate_identifier(flow_id)
        if not png.startswith(_PNG_SIGNATURE) or len(png) > _MAX_SCREENSHOT_BYTES:
            raise ScreenshotStoreError("截图数据格式或大小无效")
        flow_directory = self._directory / flow_id
        screenshot_id = str(uuid4())
        target = flow_directory / f"{screenshot_id}.png"
        temporary = flow_directory / f".{screenshot_id}.tmp"
        try:
            flow_directory.mkdir(parents=True, exist_ok=True)
            os.chmod(flow_directory, 0o700)
            temporary.write_bytes(png)
            os.chmod(temporary, 0o600)
            os.replace(temporary, target)
            os.chmod(target, 0o600)
            self._prune_flow(flow_directory)
        except OSError as error:
            temporary.unlink(missing_ok=True)
            raise ScreenshotStoreError("截图无法写入应用私有目录") from error
        return screenshot_id

    def read(self, flow_id: str, screenshot_id: str) -> bytes:
        """
        读取属于指定流程的已遮罩截图

        :param flow_id (str): 流程稳定 UUID
        :param screenshot_id (str): 截图稳定 UUID

        :return bytes: PNG 数据

        :raises ScreenshotStoreError: 标识无效、文件缺失或文件不安全
        """

        if not self._enabled:
            raise ScreenshotStoreError("截图功能尚未启用")
        _validate_identifier(flow_id)
        _validate_identifier(screenshot_id)
        target = self._directory / flow_id / f"{screenshot_id}.png"
        if not target.is_file() or target.is_symlink():
            raise ScreenshotStoreError("截图不存在")
        try:
            payload = target.read_bytes()
        except OSError as error:
            raise ScreenshotStoreError("截图无法读取") from error
        if not payload.startswith(_PNG_SIGNATURE) or len(payload) > _MAX_SCREENSHOT_BYTES:
            raise ScreenshotStoreError("截图数据格式或大小无效")
        return payload

    def delete_flow(self, flow_id: str) -> None:
        """
        删除指定流程拥有的全部截图

        :param flow_id (str): 流程稳定 UUID

        :return None: 无返回值

        :raises ScreenshotStoreError: 标识无效或删除失败
        """

        if not self._enabled:
            return
        _validate_identifier(flow_id)
        flow_directory = self._directory / flow_id
        if not flow_directory.exists():
            return
        if not flow_directory.is_dir() or flow_directory.is_symlink():
            raise ScreenshotStoreError("截图目录无效")
        try:
            for screenshot in flow_directory.iterdir():
                if screenshot.is_file() and not screenshot.is_symlink():
                    screenshot.unlink()
            flow_directory.rmdir()
        except OSError as error:
            raise ScreenshotStoreError("流程截图无法删除") from error

    def prune(self) -> None:
        """
        删除超过留存时间的截图并收敛每流程数量

        :return None: 无返回值
        """

        if not self._enabled or not self._directory.exists():
            return
        for flow_directory in self._directory.iterdir():
            if flow_directory.is_dir() and not flow_directory.is_symlink():
                self._prune_flow(flow_directory)

    def _prune_flow(self, flow_directory: Path) -> None:
        cutoff = datetime.now(UTC).timestamp() - self._retention.total_seconds()
        screenshots = sorted(
            (path for path in flow_directory.glob("*.png") if path.is_file() and not path.is_symlink()),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
        try:
            for index, screenshot in enumerate(screenshots):
                if index >= self._max_per_flow or screenshot.stat().st_mtime < cutoff:
                    screenshot.unlink()
            if not any(flow_directory.iterdir()):
                flow_directory.rmdir()
        except OSError as error:
            raise ScreenshotStoreError("过期截图无法清理") from error


def _validate_identifier(value: str) -> None:
    if _IDENTIFIER_PATTERN.fullmatch(value) is None:
        raise ScreenshotStoreError("截图标识无效")
