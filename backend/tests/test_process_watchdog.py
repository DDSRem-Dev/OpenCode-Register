import os

from process_watchdog import _parse_owner_process_id


def test_owner_watchdog_rejects_missing_or_invalid_process_ids() -> None:
    """
    验证宿主监控不会接受缺失、无效或当前进程标识
    """

    assert _parse_owner_process_id(None) is None
    assert _parse_owner_process_id("invalid") is None
    assert _parse_owner_process_id("0") is None
    assert _parse_owner_process_id("1") is None
    assert _parse_owner_process_id(str(os.getpid())) is None


def test_owner_watchdog_accepts_another_process_id() -> None:
    """
    验证宿主监控接受其他有效进程标识
    """

    assert _parse_owner_process_id(str(os.getpid() + 1)) == os.getpid() + 1
