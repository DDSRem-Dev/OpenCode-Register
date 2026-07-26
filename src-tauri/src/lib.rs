mod commands;
mod python_sidecar;

use python_sidecar::PythonSidecar;
use tauri::Manager;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
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
        .run(tauri::generate_context!())
        .expect("error while running the Tauri application");
}
