mod commands;
mod python_sidecar;

use python_sidecar::PythonSidecar;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let app = tauri::Builder::default()
        .manage(PythonSidecar::default())
        .invoke_handler(tauri::generate_handler![
            commands::start_backend,
            commands::stop_backend,
            commands::backend_status
        ])
        .setup(|app| {
            let sidecar = app.state::<PythonSidecar>();
            sidecar.start().map_err(|error| error.to_string())?;
            Ok(())
        })
        .on_window_event(|window, event| {
            if matches!(event, tauri::WindowEvent::Destroyed) {
                let sidecar = window.state::<PythonSidecar>();
                let _ = sidecar.stop();
            }
        })
        .build(tauri::generate_context!())
        .expect("failed to build the Tauri application");
    app.run(|app_handle, event| {
        if matches!(
            event,
            tauri::RunEvent::ExitRequested { .. } | tauri::RunEvent::Exit
        ) {
            let sidecar = app_handle.state::<PythonSidecar>();
            let _ = sidecar.stop();
        }
    });
}
