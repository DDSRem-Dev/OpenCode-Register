use std::{
    env,
    ffi::OsString,
    net::{Ipv4Addr, TcpListener},
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

const SANDBOX_DIRECTORY_ENV: &str = "OPENCODE_REGISTER_SANDBOX_DIR";
const BACKEND_EXECUTABLE_ENV: &str = "OPENCODE_REGISTER_BACKEND_EXECUTABLE";
const OWNER_PROCESS_ID_ENV: &str = "OPENCODE_REGISTER_OWNER_PID";
const SHUTDOWN_TIMEOUT: Duration = Duration::from_secs(5);
const SHUTDOWN_POLL_INTERVAL: Duration = Duration::from_millis(50);

/// Tauri `externalBin` 把 sidecar 放在主可执行文件同级目录，并去掉 target triple 后缀。
#[cfg(windows)]
const SIDECAR_FILE_NAME: &str = "backend.exe";
#[cfg(not(windows))]
const SIDECAR_FILE_NAME: &str = "backend";

/// Windows 下阻止冻结后端弹出控制台窗口。
#[cfg(windows)]
const CREATE_NO_WINDOW: u32 = 0x0800_0000;

type CommandFactory = Arc<dyn Fn(u16) -> Result<Command, String> + Send + Sync>;

/// Python 本地服务的进程与动态端口状态。
#[derive(Debug, PartialEq, Eq)]
pub struct PythonSidecarStatus {
    pub running: bool,
    pub pid: Option<u32>,
    pub port: Option<u16>,
}

struct BackendProcess {
    child: Child,
    port: u16,
}

/// 后端启动方式：已冻结的可执行文件，或开发期的 Python 解释器。
#[derive(Debug, PartialEq, Eq)]
enum BackendLaunch {
    Executable(PathBuf),
    Development,
}

/// Python 后端子进程的唯一生命周期所有者，覆盖内嵌 sidecar 与开发期解释器两种启动方式。
pub struct PythonSidecar {
    process: Mutex<Option<BackendProcess>>,
    command_factory: CommandFactory,
    shutdown_timeout: Duration,
}

impl Default for PythonSidecar {
    fn default() -> Self {
        Self {
            process: Mutex::new(None),
            command_factory: Arc::new(backend_command),
            shutdown_timeout: SHUTDOWN_TIMEOUT,
        }
    }
}

impl PythonSidecar {
    /// 启动后端；已有存活子进程时返回其当前状态。
    pub fn start(&self) -> Result<PythonSidecarStatus, String> {
        let mut slot = self
            .process
            .lock()
            .map_err(|_| "backend process lock poisoned")?;
        if let Some(process) = slot.as_mut() {
            if process
                .child
                .try_wait()
                .map_err(|_| "failed to inspect backend process")?
                .is_none()
            {
                return Ok(running_status(process));
            }
            *slot = None;
        }

        let port = available_backend_port()?;
        let mut command = (self.command_factory)(port)?;
        isolate_process_group(&mut command);
        let child = command
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|_| "failed to start Python backend executable")?;
        let pid = child.id();
        *slot = Some(BackendProcess { child, port });
        Ok(PythonSidecarStatus {
            running: true,
            pid: Some(pid),
            port: Some(port),
        })
    }

    /// 检查并回收已经退出的子进程。
    pub fn status(&self) -> Result<PythonSidecarStatus, String> {
        let mut slot = self
            .process
            .lock()
            .map_err(|_| "backend process lock poisoned")?;
        let Some(process) = slot.as_mut() else {
            return Ok(stopped_status());
        };
        if process
            .child
            .try_wait()
            .map_err(|_| "failed to inspect backend process")?
            .is_some()
        {
            *slot = None;
            return Ok(stopped_status());
        }
        Ok(running_status(process))
    }

    /// 请求后端优雅退出，并在超时后强制停止和回收。
    pub fn stop(&self) -> Result<(), String> {
        let child = self
            .process
            .lock()
            .map_err(|_| "backend process lock poisoned")?
            .take()
            .map(|process| process.child);
        if let Some(mut process_child) = child {
            terminate_child(&mut process_child, self.shutdown_timeout)?;
        }
        Ok(())
    }
}

impl Drop for PythonSidecar {
    fn drop(&mut self) {
        if let Ok(slot) = self.process.get_mut() {
            if let Some(mut process) = slot.take() {
                let _ = terminate_child(&mut process.child, self.shutdown_timeout);
            }
        }
    }
}

fn available_backend_port() -> Result<u16, String> {
    let listener = TcpListener::bind((Ipv4Addr::LOCALHOST, 0))
        .map_err(|_| "failed to allocate backend port")?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|_| "failed to inspect backend port".to_owned())
}

fn running_status(process: &BackendProcess) -> PythonSidecarStatus {
    PythonSidecarStatus {
        running: true,
        pid: Some(process.child.id()),
        port: Some(process.port),
    }
}

fn stopped_status() -> PythonSidecarStatus {
    PythonSidecarStatus {
        running: false,
        pid: None,
        port: None,
    }
}

#[cfg(unix)]
fn terminate_child(child: &mut Child, timeout: Duration) -> Result<(), String> {
    let process_group_id = child.id() as libc::pid_t;
    signal_process_group(
        process_group_id,
        libc::SIGTERM,
        "failed to request backend shutdown",
    )?;
    let deadline = Instant::now() + timeout;
    loop {
        let _ = child
            .try_wait()
            .map_err(|_| "failed to inspect backend process")?;
        if !process_group_exists(process_group_id)? {
            return Ok(());
        }
        let now = Instant::now();
        if now >= deadline {
            break;
        }
        thread::sleep(SHUTDOWN_POLL_INTERVAL.min(deadline - now));
    }

    signal_process_group(
        process_group_id,
        libc::SIGKILL,
        "failed to force-stop backend process",
    )?;
    if child
        .try_wait()
        .map_err(|_| "failed to inspect backend process")?
        .is_none()
    {
        child.wait().map_err(|_| "failed to reap backend process")?;
    }
    Ok(())
}

#[cfg(unix)]
fn signal_process_group(
    process_group_id: libc::pid_t,
    signal: libc::c_int,
    error_message: &str,
) -> Result<(), String> {
    // SAFETY: the negative ID targets only the dedicated process group created for this sidecar.
    let result = unsafe { libc::kill(-process_group_id, signal) };
    if result == 0 || std::io::Error::last_os_error().raw_os_error() == Some(libc::ESRCH) {
        Ok(())
    } else {
        Err(error_message.to_owned())
    }
}

#[cfg(unix)]
fn process_group_exists(process_group_id: libc::pid_t) -> Result<bool, String> {
    // SAFETY: signal 0 checks the owned process group without delivering a signal.
    let result = unsafe { libc::kill(-process_group_id, 0) };
    if result == 0 {
        return Ok(true);
    }
    match std::io::Error::last_os_error().raw_os_error() {
        Some(libc::ESRCH) => Ok(false),
        Some(libc::EPERM) => Ok(true),
        _ => Err("failed to inspect backend process group".to_owned()),
    }
}

#[cfg(not(unix))]
fn terminate_child(child: &mut Child, timeout: Duration) -> Result<(), String> {
    if child
        .try_wait()
        .map_err(|_| "failed to inspect backend process")?
        .is_some()
    {
        return Ok(());
    }

    child
        .kill()
        .map_err(|_| "failed to request backend shutdown".to_owned())?;
    let deadline = Instant::now() + timeout;
    loop {
        if child
            .try_wait()
            .map_err(|_| "failed to inspect backend process")?
            .is_some()
        {
            return Ok(());
        }
        let now = Instant::now();
        if now >= deadline {
            break;
        }
        thread::sleep(SHUTDOWN_POLL_INTERVAL.min(deadline - now));
    }
    child.wait().map_err(|_| "failed to reap backend process")?;
    Ok(())
}

#[cfg(unix)]
fn isolate_process_group(command: &mut Command) {
    use std::os::unix::process::CommandExt;

    command.process_group(0);
}

#[cfg(not(unix))]
fn isolate_process_group(_command: &mut Command) {}

fn backend_command(port: u16) -> Result<Command, String> {
    let executable_directory = env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf));
    let launch = resolve_backend_launch(
        env::var_os(BACKEND_EXECUTABLE_ENV),
        executable_directory.as_deref(),
    );

    match launch {
        BackendLaunch::Executable(executable) => Ok(executable_command(executable, port)),
        BackendLaunch::Development => development_command(port),
    }
}

/// 按「显式覆盖 → 内嵌 sidecar → 开发环境」的顺序决定后端启动方式。
///
/// 该函数不读取进程状态，因此可以直接用临时目录断言解析优先级。
fn resolve_backend_launch(
    executable_override: Option<OsString>,
    executable_directory: Option<&Path>,
) -> BackendLaunch {
    if let Some(executable) = executable_override {
        return BackendLaunch::Executable(PathBuf::from(executable));
    }
    if let Some(directory) = executable_directory {
        let sidecar = directory.join(SIDECAR_FILE_NAME);
        if is_usable_sidecar(&sidecar) {
            return BackendLaunch::Executable(sidecar);
        }
    }
    BackendLaunch::Development
}

/// 空文件是 `scripts/build_backend.py --placeholder` 写出的占位文件，只为满足
/// `tauri-build` 对 externalBin 的存在性校验。Tauri 开发模式会把它复制到可执行文件旁边，
/// 因此必须按长度排除，否则开发期会尝试启动一个空文件而不是回落到 Python 解释器。
fn is_usable_sidecar(candidate: &Path) -> bool {
    candidate
        .metadata()
        .map(|metadata| metadata.is_file() && metadata.len() > 0)
        .unwrap_or(false)
}

fn executable_command(executable: PathBuf, port: u16) -> Command {
    let mut command = Command::new(executable);
    command.args(["--host", "127.0.0.1", "--port", &port.to_string()]);
    forward_sandbox_directory(&mut command, None);
    forward_owner_process_id(&mut command);
    suppress_console_window(&mut command);
    command
}

fn development_command(port: u16) -> Result<Command, String> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("unable to resolve project root")?
        .to_path_buf();
    let python = find_python(&project_root)
        .ok_or("Python backend environment not found; run `uv sync --project backend` first")?;
    let mut command = Command::new(python);
    command
        .arg(project_root.join("backend/main.py"))
        .args(["--host", "127.0.0.1", "--port", &port.to_string()])
        .current_dir(project_root.join("backend"));
    forward_sandbox_directory(&mut command, Some(&project_root));
    forward_owner_process_id(&mut command);
    Ok(command)
}

fn forward_owner_process_id(command: &mut Command) {
    command.env(OWNER_PROCESS_ID_ENV, std::process::id().to_string());
}

/// 传递沙盒目录；只有开发期才把相对路径按项目根目录展开，打包后没有项目根目录可依赖。
fn forward_sandbox_directory(command: &mut Command, project_root: Option<&Path>) {
    let Some(configured) = env::var_os(SANDBOX_DIRECTORY_ENV) else {
        return;
    };
    let resolved = match project_root {
        Some(root) => resolve_environment_path(root, Path::new(&configured)),
        None => PathBuf::from(&configured),
    };
    command.env(SANDBOX_DIRECTORY_ENV, resolved);
}

#[cfg(windows)]
fn suppress_console_window(command: &mut Command) {
    use std::os::windows::process::CommandExt;

    command.creation_flags(CREATE_NO_WINDOW);
}

#[cfg(not(windows))]
fn suppress_console_window(_command: &mut Command) {}

fn resolve_environment_path(project_root: &Path, configured: &Path) -> PathBuf {
    if configured.is_absolute() || configured.starts_with("~") {
        return configured.to_path_buf();
    }
    project_root.join(configured)
}

fn find_python(project_root: &Path) -> Option<PathBuf> {
    let venv_python = if cfg!(windows) {
        project_root.join("backend/.venv/Scripts/python.exe")
    } else {
        project_root.join("backend/.venv/bin/python")
    };
    venv_python.is_file().then_some(venv_python)
}

#[cfg(test)]
#[path = "python_sidecar_tests.rs"]
mod tests;
