use std::{
    env,
    ffi::OsString,
    path::{Path, PathBuf},
    process::{Child, Command, Stdio},
    sync::{Arc, Mutex},
    thread,
    time::{Duration, Instant},
};

const BACKEND_PORT: &str = "17891";
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

type CommandFactory = Arc<dyn Fn() -> Result<Command, String> + Send + Sync>;

/// 后端启动方式：已冻结的可执行文件，或开发期的 Python 解释器。
#[derive(Debug, PartialEq, Eq)]
enum BackendLaunch {
    Executable(PathBuf),
    Development,
}

/// Python 后端子进程的唯一生命周期所有者，覆盖内嵌 sidecar 与开发期解释器两种启动方式。
pub struct PythonSidecar {
    child: Mutex<Option<Child>>,
    command_factory: CommandFactory,
    shutdown_timeout: Duration,
}

impl Default for PythonSidecar {
    fn default() -> Self {
        Self {
            child: Mutex::new(None),
            command_factory: Arc::new(backend_command),
            shutdown_timeout: SHUTDOWN_TIMEOUT,
        }
    }
}

impl PythonSidecar {
    /// 启动后端；已有存活子进程时返回其当前状态。
    pub fn start(&self) -> Result<(bool, Option<u32>), String> {
        let mut slot = self
            .child
            .lock()
            .map_err(|_| "backend process lock poisoned")?;
        if let Some(child) = slot.as_mut() {
            if child
                .try_wait()
                .map_err(|_| "failed to inspect backend process")?
                .is_none()
            {
                return Ok((true, Some(child.id())));
            }
            *slot = None;
        }

        let mut command = (self.command_factory)()?;
        isolate_process_group(&mut command);
        let child = command
            .stdout(Stdio::inherit())
            .stderr(Stdio::inherit())
            .spawn()
            .map_err(|_| "failed to start Python backend executable")?;
        let pid = child.id();
        *slot = Some(child);
        Ok((true, Some(pid)))
    }

    /// 检查并回收已经退出的子进程。
    pub fn status(&self) -> Result<(bool, Option<u32>), String> {
        let mut slot = self
            .child
            .lock()
            .map_err(|_| "backend process lock poisoned")?;
        let Some(child) = slot.as_mut() else {
            return Ok((false, None));
        };
        if child
            .try_wait()
            .map_err(|_| "failed to inspect backend process")?
            .is_some()
        {
            *slot = None;
            return Ok((false, None));
        }
        Ok((true, Some(child.id())))
    }

    /// 请求后端优雅退出，并在超时后强制停止和回收。
    pub fn stop(&self) -> Result<(), String> {
        let child = self
            .child
            .lock()
            .map_err(|_| "backend process lock poisoned")?
            .take();
        if let Some(mut child) = child {
            terminate_child(&mut child, self.shutdown_timeout)?;
        }
        Ok(())
    }
}

impl Drop for PythonSidecar {
    fn drop(&mut self) {
        if let Ok(slot) = self.child.get_mut() {
            if let Some(mut child) = slot.take() {
                let _ = terminate_child(&mut child, self.shutdown_timeout);
            }
        }
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

fn backend_command() -> Result<Command, String> {
    let executable_directory = env::current_exe()
        .ok()
        .and_then(|path| path.parent().map(Path::to_path_buf));
    let launch = resolve_backend_launch(
        env::var_os(BACKEND_EXECUTABLE_ENV),
        executable_directory.as_deref(),
    );

    match launch {
        BackendLaunch::Executable(executable) => Ok(executable_command(executable)),
        BackendLaunch::Development => development_command(),
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

fn executable_command(executable: PathBuf) -> Command {
    let mut command = Command::new(executable);
    command.args(["--host", "127.0.0.1", "--port", BACKEND_PORT]);
    forward_sandbox_directory(&mut command, None);
    forward_owner_process_id(&mut command);
    suppress_console_window(&mut command);
    command
}

fn development_command() -> Result<Command, String> {
    let project_root = PathBuf::from(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .ok_or("unable to resolve project root")?
        .to_path_buf();
    let python = find_python(&project_root)
        .ok_or("Python backend environment not found; run `uv sync --project backend` first")?;
    let mut command = Command::new(python);
    command
        .arg(project_root.join("backend/main.py"))
        .args(["--host", "127.0.0.1", "--port", BACKEND_PORT])
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
mod tests {
    use super::{
        forward_owner_process_id, resolve_backend_launch, resolve_environment_path, BackendLaunch,
        CommandFactory, PythonSidecar, OWNER_PROCESS_ID_ENV, SIDECAR_FILE_NAME,
    };
    use std::{
        fs,
        path::PathBuf,
        process::Command,
        sync::Arc,
        thread,
        time::{Duration, Instant, SystemTime},
    };

    fn temporary_directory(label: &str) -> PathBuf {
        let directory = std::env::temp_dir().join(format!(
            "opencode-register-{}-{}-{:?}",
            label,
            std::process::id(),
            SystemTime::now()
        ));
        fs::create_dir_all(&directory).expect("temporary directory should be creatable");
        directory
    }

    fn sidecar_with(
        factory: impl Fn() -> Result<Command, String> + Send + Sync + 'static,
    ) -> PythonSidecar {
        let command_factory: CommandFactory = Arc::new(factory);
        PythonSidecar {
            child: Default::default(),
            command_factory,
            shutdown_timeout: Duration::from_millis(250),
        }
    }

    #[test]
    fn sandbox_path_resolution_uses_the_project_root() {
        let project_root = PathBuf::from("project-root");
        let absolute_path = std::env::temp_dir().join("opencode-register-sandbox");

        assert_eq!(
            resolve_environment_path(&project_root, PathBuf::from(".sandbox").as_path()),
            project_root.join(".sandbox")
        );
        assert_eq!(
            resolve_environment_path(&project_root, &absolute_path),
            absolute_path
        );
        assert_eq!(
            resolve_environment_path(&project_root, PathBuf::from("~/sandbox").as_path()),
            PathBuf::from("~/sandbox")
        );
    }

    #[test]
    fn bundled_sidecar_is_preferred_over_the_development_environment() {
        let directory = temporary_directory("sidecar-present");
        let sidecar = directory.join(SIDECAR_FILE_NAME);
        fs::write(&sidecar, b"frozen backend placeholder").expect("sidecar should be writable");

        assert_eq!(
            resolve_backend_launch(None, Some(directory.as_path())),
            BackendLaunch::Executable(sidecar)
        );

        fs::remove_dir_all(&directory).expect("temporary directory should be removable");
    }

    #[test]
    fn missing_sidecar_falls_back_to_the_development_environment() {
        let directory = temporary_directory("sidecar-absent");

        assert_eq!(
            resolve_backend_launch(None, Some(directory.as_path())),
            BackendLaunch::Development
        );
        assert_eq!(
            resolve_backend_launch(None, None),
            BackendLaunch::Development
        );

        fs::remove_dir_all(&directory).expect("temporary directory should be removable");
    }

    #[test]
    fn empty_placeholder_sidecar_falls_back_to_the_development_environment() {
        let directory = temporary_directory("sidecar-placeholder");
        fs::write(directory.join(SIDECAR_FILE_NAME), b"").expect("placeholder should be writable");

        assert_eq!(
            resolve_backend_launch(None, Some(directory.as_path())),
            BackendLaunch::Development
        );

        fs::remove_dir_all(&directory).expect("temporary directory should be removable");
    }

    #[test]
    fn explicit_executable_override_outranks_the_bundled_sidecar() {
        let directory = temporary_directory("sidecar-override");
        let sidecar = directory.join(SIDECAR_FILE_NAME);
        fs::write(&sidecar, b"frozen backend placeholder").expect("sidecar should be writable");
        let override_path = directory.join("custom-backend");

        assert_eq!(
            resolve_backend_launch(
                Some(override_path.clone().into_os_string()),
                Some(directory.as_path())
            ),
            BackendLaunch::Executable(override_path)
        );

        fs::remove_dir_all(&directory).expect("temporary directory should be removable");
    }

    #[test]
    fn sidecar_file_name_matches_the_target_platform() {
        if cfg!(windows) {
            assert_eq!(SIDECAR_FILE_NAME, "backend.exe");
        } else {
            assert_eq!(SIDECAR_FILE_NAME, "backend");
        }
    }

    #[test]
    fn backend_command_receives_the_owner_process_id() {
        let mut command = Command::new("backend");

        forward_owner_process_id(&mut command);

        let owner_process_id = command
            .get_envs()
            .find(|(name, _)| *name == OWNER_PROCESS_ID_ENV)
            .and_then(|(_, value)| value)
            .expect("owner process ID should be configured");
        assert_eq!(owner_process_id, std::process::id().to_string().as_str());
    }

    #[cfg(unix)]
    fn long_running_command() -> Result<Command, String> {
        let mut command = Command::new("sleep");
        command.arg("30");
        Ok(command)
    }

    #[cfg(unix)]
    #[test]
    fn repeated_start_and_stop_are_idempotent() {
        let sidecar = sidecar_with(long_running_command);

        let first = sidecar.start().expect("first start should succeed");
        let second = sidecar.start().expect("second start should reuse child");

        assert_eq!(first, second);
        assert_eq!(sidecar.status().expect("status should succeed"), first);
        sidecar.stop().expect("first stop should succeed");
        sidecar.stop().expect("second stop should be harmless");
        assert_eq!(
            sidecar.status().expect("stopped status should succeed"),
            (false, None)
        );
    }

    #[cfg(unix)]
    #[test]
    fn started_sidecar_owns_a_dedicated_process_group() {
        let sidecar = sidecar_with(long_running_command);
        let (_, pid) = sidecar.start().expect("sidecar should start");
        let pid = pid.expect("running sidecar should expose its PID") as libc::pid_t;

        // SAFETY: getpgid only inspects the live child PID returned by the sidecar owner.
        let process_group_id = unsafe { libc::getpgid(pid) };
        assert_eq!(process_group_id, pid);

        sidecar.stop().expect("sidecar should stop");
    }

    #[cfg(unix)]
    #[test]
    fn crashed_child_is_reported_as_stopped_and_reaped() {
        let sidecar = sidecar_with(|| Ok(Command::new("true")));
        sidecar.start().expect("start should succeed");
        {
            let mut slot = sidecar.child.lock().expect("test lock should be available");
            slot.as_mut()
                .expect("child should be stored")
                .wait()
                .expect("child should exit");
        }

        assert_eq!(
            sidecar.status().expect("status should reap exited child"),
            (false, None)
        );
        assert!(sidecar
            .child
            .lock()
            .expect("test lock should be available")
            .is_none());
    }

    #[test]
    fn missing_executable_returns_safe_error_and_keeps_stopped_state() {
        let missing_path = PathBuf::from("definitely-missing-opencode-register-backend");
        let sidecar = sidecar_with(move || Ok(Command::new(&missing_path)));

        let error = sidecar.start().expect_err("missing executable should fail");

        assert_eq!(error, "failed to start Python backend executable");
        assert_eq!(
            sidecar.status().expect("status should remain available"),
            (false, None)
        );
    }

    #[cfg(unix)]
    #[test]
    fn dropping_sidecar_terminates_and_reaps_child() {
        let sidecar = sidecar_with(long_running_command);
        let (_, pid) = sidecar.start().expect("start should succeed");
        let pid = pid.expect("running child should have a pid");

        drop(sidecar);

        let process_exists = Command::new("ps")
            .args(["-p", &pid.to_string()])
            .status()
            .expect("ps should be available")
            .success();
        assert!(!process_exists);
    }

    #[cfg(unix)]
    #[test]
    fn stop_requests_graceful_shutdown_before_forcing_exit() {
        let marker = std::env::temp_dir().join(format!(
            "opencode-register-sidecar-{}-{:?}",
            std::process::id(),
            SystemTime::now()
        ));
        let command_marker = marker.clone();
        let sidecar = sidecar_with(move || {
            let mut command = Command::new("sh");
            command
                .arg("-c")
                .arg("trap 'printf stopped > \"$SHUTDOWN_MARKER\"; exit 0' TERM; while :; do :; done")
                .env("SHUTDOWN_MARKER", &command_marker);
            Ok(command)
        });
        sidecar.start().expect("start should succeed");
        thread::sleep(Duration::from_millis(50));

        sidecar.stop().expect("graceful stop should succeed");

        assert_eq!(
            fs::read_to_string(&marker).expect("TERM handler should write marker"),
            "stopped"
        );
        fs::remove_file(marker).expect("test marker should be removable");
    }

    #[cfg(unix)]
    #[test]
    fn stop_force_kills_child_after_graceful_shutdown_timeout() {
        let sidecar = sidecar_with(|| {
            let mut command = Command::new("sh");
            command.arg("-c").arg("trap '' TERM; while :; do :; done");
            Ok(command)
        });
        sidecar.start().expect("start should succeed");
        thread::sleep(Duration::from_millis(50));
        let started = Instant::now();

        sidecar.stop().expect("forced stop should succeed");

        assert!(started.elapsed() >= Duration::from_millis(200));
        assert_eq!(
            sidecar.status().expect("stopped status should succeed"),
            (false, None)
        );
    }
}
