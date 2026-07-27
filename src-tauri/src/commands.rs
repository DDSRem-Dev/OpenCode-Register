use serde::Serialize;
use tauri::State;

use crate::python_sidecar::{PythonSidecar, PythonSidecarStatus};

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
/// Python 本地服务的脱敏进程状态。
pub struct BackendStatus {
    running: bool,
    pid: Option<u32>,
    port: Option<u16>,
}

impl From<PythonSidecarStatus> for BackendStatus {
    fn from(status: PythonSidecarStatus) -> Self {
        Self {
            running: status.running,
            pid: status.pid,
            port: status.port,
        }
    }
}

#[tauri::command]
/// 启动 Python 本地服务；重复调用复用仍在运行的子进程。
pub fn start_backend(sidecar: State<'_, PythonSidecar>) -> Result<BackendStatus, String> {
    sidecar.start().map(BackendStatus::from)
}

#[tauri::command]
/// 停止并回收 Python 本地服务；服务已停止时保持幂等。
pub fn stop_backend(sidecar: State<'_, PythonSidecar>) -> Result<(), String> {
    sidecar.stop()
}

#[tauri::command]
/// 返回当前 Python 本地服务的脱敏进程状态。
pub fn backend_status(sidecar: State<'_, PythonSidecar>) -> Result<BackendStatus, String> {
    sidecar.status().map(BackendStatus::from)
}
