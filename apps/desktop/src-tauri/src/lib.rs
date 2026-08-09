mod commands;
mod errors;
mod loopback;
mod secret;
mod supervisor;

use tauri::{Manager, RunEvent};

use supervisor::CoreSupervisor;

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let supervisor = CoreSupervisor::new();
            app.manage(supervisor);
            let state = app.state::<CoreSupervisor>();
            // Core failure must not block the desktop shell; it is surfaced
            // through the launchError part of the invoke contract instead.
            let _ = state.launch(app.handle());
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![
            commands::core_connection,
            commands::restart_core
        ])
        .build(tauri::generate_context!())
        .expect("failed to build ZANA desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            app_handle.state::<CoreSupervisor>().shutdown();
        }
    });
}
