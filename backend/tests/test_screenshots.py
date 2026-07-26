import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from storage.screenshots import ScreenshotStore, ScreenshotStoreError

_PNG = b"\x89PNG\r\n\x1a\n" + b"sanitized-test-pixels"
_FLOW_ID = "00000000-0000-4000-8000-000000000091"


def test_screenshot_store_requires_opt_in_and_rejects_unsafe_identifiers(tmp_path: Path) -> None:
    """
    验证截图默认不落盘且路径标识不能逃逸私有目录
    """

    disabled = ScreenshotStore(tmp_path / "disabled", False, retention_hours=24, max_per_flow=3)
    with pytest.raises(ScreenshotStoreError, match="尚未启用"):
        disabled.save(_FLOW_ID, _PNG)
    assert not (tmp_path / "disabled").exists()

    enabled = ScreenshotStore(tmp_path / "enabled", True, retention_hours=24, max_per_flow=3)
    with pytest.raises(ScreenshotStoreError, match="标识无效"):
        enabled.save("../outside", _PNG)


def test_screenshot_store_bounds_count_permissions_and_flow_deletion(tmp_path: Path) -> None:
    """
    验证每流程截图数量有界、文件权限私有且流程结束会完整删除
    """

    root = tmp_path / "screenshots"
    store = ScreenshotStore(root, True, retention_hours=24, max_per_flow=1)
    first_id = store.save(_FLOW_ID, _PNG)
    second_id = store.save(_FLOW_ID, _PNG + b"-new")

    with pytest.raises(ScreenshotStoreError, match="不存在"):
        store.read(_FLOW_ID, first_id)
    assert store.read(_FLOW_ID, second_id) == _PNG + b"-new"
    screenshot_path = root / _FLOW_ID / f"{second_id}.png"
    assert os.stat(screenshot_path).st_mode & 0o077 == 0

    store.delete_flow(_FLOW_ID)

    assert not (root / _FLOW_ID).exists()


def test_screenshot_store_prunes_expired_files(tmp_path: Path) -> None:
    """
    验证超过配置留存时间的截图会被清理
    """

    root = tmp_path / "screenshots"
    store = ScreenshotStore(root, True, retention_hours=1, max_per_flow=3)
    screenshot_id = store.save(_FLOW_ID, _PNG)
    screenshot_path = root / _FLOW_ID / f"{screenshot_id}.png"
    expired = (datetime.now(UTC) - timedelta(hours=2)).timestamp()
    os.utime(screenshot_path, (expired, expired))

    store.prune()

    assert not screenshot_path.exists()
    assert not (root / _FLOW_ID).exists()
