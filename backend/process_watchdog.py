import os
import signal
import threading
from typing import Optional

OWNER_PROCESS_ID_ENV = "OPENCODE_REGISTER_OWNER_PID"
OWNER_POLL_INTERVAL_SECONDS = 1.0


def start_owner_watchdog() -> Optional[threading.Thread]:
    """
    启动宿主进程退出监控

    仅当 Tauri 宿主传入有效进程标识时启用。宿主异常消失后向当前后端发送终止信号，
    让 Uvicorn 的生命周期清理浏览器和其他子进程

    :return Thread: 已启动的后台监控线程，未配置宿主时返回空值
    """

    if os.name != "posix":
        return None
    owner_process_id = _parse_owner_process_id(os.environ.get(OWNER_PROCESS_ID_ENV))
    if owner_process_id is None:
        return None
    thread = threading.Thread(
        target=_watch_owner,
        args=(owner_process_id,),
        name="owner-process-watchdog",
        daemon=True,
    )
    thread.start()
    return thread


def _parse_owner_process_id(raw_process_id: Optional[str]) -> Optional[int]:
    if raw_process_id is None:
        return None
    try:
        process_id = int(raw_process_id)
    except ValueError:
        return None
    if process_id <= 1 or process_id == os.getpid():
        return None
    return process_id


def _watch_owner(owner_process_id: int) -> None:
    while _process_exists(owner_process_id):
        threading.Event().wait(OWNER_POLL_INTERVAL_SECONDS)
    os.kill(os.getpid(), signal.SIGTERM)


def _process_exists(process_id: int) -> bool:
    try:
        os.kill(process_id, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True
