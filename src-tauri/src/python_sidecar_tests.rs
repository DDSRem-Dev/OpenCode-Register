use super::{
    available_backend_port, executable_command, forward_owner_process_id, resolve_backend_launch,
    resolve_environment_path, stopped_status, BackendLaunch, CommandFactory, PythonSidecar,
    OWNER_PROCESS_ID_ENV, SIDECAR_FILE_NAME,
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
    factory: impl Fn(u16) -> Result<Command, String> + Send + Sync + 'static,
) -> PythonSidecar {
    let command_factory: CommandFactory = Arc::new(factory);
    PythonSidecar {
        process: Default::default(),
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

#[test]
fn backend_command_receives_the_allocated_port() {
    let port = available_backend_port().expect("an available loopback port should be allocated");
    let command = executable_command(PathBuf::from("backend"), port);
    let arguments = command
        .get_args()
        .map(|argument| argument.to_string_lossy().into_owned())
        .collect::<Vec<_>>();
    assert_eq!(
        arguments,
        ["--host", "127.0.0.1", "--port", &port.to_string()]
    );
}

#[cfg(unix)]
fn long_running_command(_port: u16) -> Result<Command, String> {
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
        stopped_status()
    );
}

#[cfg(unix)]
#[test]
fn started_sidecar_owns_a_dedicated_process_group() {
    let sidecar = sidecar_with(long_running_command);
    let status = sidecar.start().expect("sidecar should start");
    let pid = status.pid.expect("running sidecar should expose its PID") as libc::pid_t;
    assert!(status.port.is_some());
    // SAFETY: getpgid only inspects the live child PID returned by the sidecar owner.
    let process_group_id = unsafe { libc::getpgid(pid) };
    assert_eq!(process_group_id, pid);
    sidecar.stop().expect("sidecar should stop");
}

#[cfg(unix)]
#[test]
fn crashed_child_is_reported_as_stopped_and_reaped() {
    let sidecar = sidecar_with(|_port| Ok(Command::new("true")));
    sidecar.start().expect("start should succeed");
    {
        let mut slot = sidecar
            .process
            .lock()
            .expect("test lock should be available");
        slot.as_mut()
            .expect("child should be stored")
            .child
            .wait()
            .expect("child should exit");
    }
    assert_eq!(
        sidecar.status().expect("status should reap exited child"),
        stopped_status()
    );
    assert!(sidecar
        .process
        .lock()
        .expect("test lock should be available")
        .is_none());
}

#[test]
fn missing_executable_returns_safe_error_and_keeps_stopped_state() {
    let missing_path = PathBuf::from("definitely-missing-opencode-register-backend");
    let sidecar = sidecar_with(move |_port| Ok(Command::new(&missing_path)));
    let error = sidecar.start().expect_err("missing executable should fail");
    assert_eq!(error, "failed to start Python backend executable");
    assert_eq!(
        sidecar.status().expect("status should remain available"),
        stopped_status()
    );
}

#[cfg(unix)]
#[test]
fn dropping_sidecar_terminates_and_reaps_child() {
    let sidecar = sidecar_with(long_running_command);
    let status = sidecar.start().expect("start should succeed");
    let pid = status.pid.expect("running child should have a pid");
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
    let sidecar = sidecar_with(move |_port| {
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
    let sidecar = sidecar_with(|_port| {
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
        stopped_status()
    );
}
