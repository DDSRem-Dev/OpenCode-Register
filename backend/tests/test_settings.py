from pathlib import Path

import pytest

from config.settings import AppSettings


def test_sandbox_directory_overrides_all_system_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证沙盒目录优先覆盖账号库和全部 OpenCode 配置路径
    """

    sandbox_root = tmp_path / "sandbox"
    monkeypatch.setenv("OPENCODE_REGISTER_SANDBOX_DIR", str(sandbox_root))
    monkeypatch.setenv("OPENCODE_REGISTER_DATA_DIR", str(tmp_path / "real-app-data"))
    monkeypatch.setenv("OPENCODE_REGISTER_AUTH_PATH", str(tmp_path / "real" / "auth.json"))
    monkeypatch.setenv("OPENCODE_REGISTER_CONFIG_PATH", str(tmp_path / "real" / "opencode.json"))
    monkeypatch.setenv(
        "OPENCODE_REGISTER_OMO_PATH",
        str(tmp_path / "real" / "oh-my-openagent.json"),
    )

    settings = AppSettings.from_environment()

    assert settings.storage_mode == "sandbox"
    assert settings.data_directory == sandbox_root / "app-data"
    assert settings.opencode_paths.auth_path == sandbox_root / "opencode-data" / "auth.json"
    assert settings.opencode_paths.opencode_path == sandbox_root / "opencode-config" / "opencode.json"
    assert settings.opencode_paths.omo_path == sandbox_root / "opencode-config" / "oh-my-openagent.json"


def test_explicit_system_paths_remain_available_without_sandbox(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证未启用沙盒时继续使用显式系统路径配置
    """

    data_directory = tmp_path / "app-data"
    auth_path = tmp_path / "system" / "auth.json"
    opencode_path = tmp_path / "system" / "opencode.json"
    omo_path = tmp_path / "system" / "oh-my-openagent.json"
    monkeypatch.delenv("OPENCODE_REGISTER_SANDBOX_DIR", raising=False)
    monkeypatch.setenv("OPENCODE_REGISTER_DATA_DIR", str(data_directory))
    monkeypatch.setenv("OPENCODE_REGISTER_AUTH_PATH", str(auth_path))
    monkeypatch.setenv("OPENCODE_REGISTER_CONFIG_PATH", str(opencode_path))
    monkeypatch.setenv("OPENCODE_REGISTER_OMO_PATH", str(omo_path))

    settings = AppSettings.from_environment()

    assert settings.storage_mode == "system"
    assert settings.data_directory == data_directory
    assert settings.opencode_paths.auth_path == auth_path
    assert settings.opencode_paths.opencode_path == opencode_path
    assert settings.opencode_paths.omo_path == omo_path


def test_quota_interval_uses_bounded_environment_value(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证 Phase 7 周期检查间隔从环境读取并拒绝过短值
    """

    monkeypatch.setenv("OPENCODE_REGISTER_QUOTA_CHECK_INTERVAL_SECONDS", "900")
    assert AppSettings.from_environment().quota_check_interval_seconds == 900

    monkeypatch.setenv("OPENCODE_REGISTER_QUOTA_CHECK_INTERVAL_SECONDS", "30")
    with pytest.raises(ValueError):
        AppSettings.from_environment()


def test_screenshots_require_explicit_bounded_environment_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    验证截图默认关闭且显式启用时留存参数受到范围约束
    """

    monkeypatch.delenv("OPENCODE_REGISTER_SCREENSHOTS_ENABLED", raising=False)
    assert AppSettings.from_environment().screenshots_enabled is False

    monkeypatch.setenv("OPENCODE_REGISTER_SCREENSHOTS_ENABLED", "true")
    monkeypatch.setenv("OPENCODE_REGISTER_SCREENSHOT_RETENTION_HOURS", "12")
    monkeypatch.setenv("OPENCODE_REGISTER_SCREENSHOT_MAX_PER_FLOW", "2")
    settings = AppSettings.from_environment()
    assert settings.screenshots_enabled is True
    assert settings.screenshot_retention_hours == 12
    assert settings.screenshot_max_per_flow == 2

    monkeypatch.setenv("OPENCODE_REGISTER_SCREENSHOT_MAX_PER_FLOW", "100")
    with pytest.raises(ValueError):
        AppSettings.from_environment()
