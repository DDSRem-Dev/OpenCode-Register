import os
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from config.models import OpenCodeConfigPaths


class AppSettings(BaseModel):
    """
    本地服务运行设置
    """

    model_config = ConfigDict(extra="forbid")

    data_directory: Path = Field(..., description="应用私有数据目录")
    opencode_paths: OpenCodeConfigPaths = Field(..., description="OpenCode 与 OMO 配置路径")
    storage_mode: Literal["system", "sandbox"] = Field(..., description="本地文件写入模式")
    quota_check_interval_seconds: int = Field(
        default=3_600,
        ge=60,
        le=86_400,
        description="OpenCode Go 周期额度检查间隔秒数",
    )
    screenshots_enabled: bool = Field(default=False, description="是否显式启用已遮罩的人工介入截图")
    screenshot_retention_hours: int = Field(default=24, ge=1, le=168, description="活动流程截图最长保留小时数")
    screenshot_max_per_flow: int = Field(default=3, ge=1, le=10, description="每个活动流程最多保留截图数量")

    @classmethod
    def from_environment(cls) -> "AppSettings":
        """
        从进程环境解析一次运行设置

        :return AppSettings: 已验证的运行设置
        """

        sandbox_directory = os.environ.get("OPENCODE_REGISTER_SANDBOX_DIR")
        quota_check_interval = _quota_check_interval()
        screenshots_enabled = _environment_boolean("OPENCODE_REGISTER_SCREENSHOTS_ENABLED", default=False)
        screenshot_retention_hours = int(os.environ.get("OPENCODE_REGISTER_SCREENSHOT_RETENTION_HOURS", "24"))
        screenshot_max_per_flow = int(os.environ.get("OPENCODE_REGISTER_SCREENSHOT_MAX_PER_FLOW", "3"))
        if sandbox_directory:
            sandbox_root = Path(sandbox_directory).expanduser().resolve()
            return cls(
                data_directory=sandbox_root / "app-data",
                opencode_paths=OpenCodeConfigPaths(
                    auth_path=sandbox_root / "opencode-data" / "auth.json",
                    opencode_path=sandbox_root / "opencode-config" / "opencode.json",
                    omo_path=sandbox_root / "opencode-config" / "oh-my-openagent.json",
                ),
                storage_mode="sandbox",
                quota_check_interval_seconds=quota_check_interval,
                screenshots_enabled=screenshots_enabled,
                screenshot_retention_hours=screenshot_retention_hours,
                screenshot_max_per_flow=screenshot_max_per_flow,
            )

        configured_directory = os.environ.get("OPENCODE_REGISTER_DATA_DIR")
        if configured_directory:
            data_directory = Path(configured_directory).expanduser().resolve()
        else:
            data_directory = _default_data_directory()
        return cls(
            data_directory=data_directory,
            opencode_paths=OpenCodeConfigPaths(
                auth_path=Path(
                    os.environ.get(
                        "OPENCODE_REGISTER_AUTH_PATH",
                        "~/.local/share/opencode/auth.json",
                    )
                ),
                opencode_path=Path(
                    os.environ.get(
                        "OPENCODE_REGISTER_CONFIG_PATH",
                        "~/.config/opencode/opencode.json",
                    )
                ),
                omo_path=Path(
                    os.environ.get(
                        "OPENCODE_REGISTER_OMO_PATH",
                        "~/.config/opencode/oh-my-openagent.json",
                    )
                ),
            ),
            storage_mode="system",
            quota_check_interval_seconds=quota_check_interval,
            screenshots_enabled=screenshots_enabled,
            screenshot_retention_hours=screenshot_retention_hours,
            screenshot_max_per_flow=screenshot_max_per_flow,
        )


def _default_data_directory() -> Path:
    if sys.platform == "darwin":
        return (Path.home() / "Library" / "Application Support" / "OpenCode Register").resolve()
    if os.name == "nt":
        local_app_data = os.environ.get("LOCALAPPDATA")
        if local_app_data:
            return (Path(local_app_data) / "OpenCode Register").resolve()
        return (Path.home() / "AppData" / "Local" / "OpenCode Register").resolve()
    xdg_data_home = os.environ.get("XDG_DATA_HOME")
    root = Path(xdg_data_home) if xdg_data_home else Path.home() / ".local" / "share"
    return (root / "opencode-register").resolve()


def _quota_check_interval() -> int:
    value = os.environ.get("OPENCODE_REGISTER_QUOTA_CHECK_INTERVAL_SECONDS", "3600")
    try:
        return int(value)
    except ValueError as error:
        raise ValueError("额度检查间隔必须是整数秒数") from error


def _environment_boolean(name: str, default: bool) -> bool:
    value = os.environ.get(name)
    if value is None:
        return default
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"{name} 必须是布尔值")
