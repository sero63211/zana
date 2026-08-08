use std::net::TcpListener;
use std::sync::Mutex;

use serde::Serialize;
use tauri::{AppHandle, Manager, RunEvent, State};
use tauri_plugin_shell::process::CommandChild;
use tauri_plugin_shell::ShellExt;
use uuid::Uuid;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
struct CoreConnection {
    base_url: String,
    token: String,
    launch_error: Option<String>,
}

struct CoreSupervisor {
    base_url: String,
    token: String,
    child: Mutex<Option<CommandChild>>,
    launch_error: Mutex<Option<String>>,
}

impl CoreSupervisor {
    fn new() -> Result<Self, String> {
        let listener = TcpListener::bind(("127.0.0.1", 0))
            .map_err(|error| format!("Could not reserve a loopback port: {error}"))?;
        let port = listener
            .local_addr()
            .map_err(|error| format!("Could not inspect the reserved loopback port: {error}"))?
            .port();
        drop(listener);

        Ok(Self {
            base_url: format!("http://127.0.0.1:{port}"),
            token: Uuid::new_v4().simple().to_string(),
            child: Mutex::new(None),
            launch_error: Mutex::new(None),
        })
    }
}

fn launch_core(app: &AppHandle, supervisor: &CoreSupervisor) -> Result<(), String> {
    if let Some(child) = supervisor
        .child
        .lock()
        .map_err(|_| "Core child lock is poisoned")?
        .take()
    {
        let _ = child.kill();
    }

    let port = supervisor
        .base_url
        .rsplit(':')
        .next()
        .ok_or("Core loopback port is unavailable")?;
    let sidecar = app
        .shell()
        .sidecar("zana-core")
        .map_err(|error| format!("ZANA Core sidecar is unavailable: {error}"))?;
    let (_events, child) = sidecar
        .args(["serve", "--host", "127.0.0.1", "--port", port])
        .env("ZANA_CORE_TOKEN", &supervisor.token)
        .spawn()
        .map_err(|error| format!("ZANA Core could not start: {error}"))?;

    *supervisor
        .child
        .lock()
        .map_err(|_| "Core child lock is poisoned")? = Some(child);
    *supervisor
        .launch_error
        .lock()
        .map_err(|_| "Core error lock is poisoned")? = None;
    Ok(())
}

#[tauri::command]
fn core_connection(supervisor: State<'_, CoreSupervisor>) -> CoreConnection {
    let launch_error = supervisor
        .launch_error
        .lock()
        .map(|value| value.clone())
        .unwrap_or_else(|_| Some("Core status is unavailable.".to_owned()));
    CoreConnection {
        base_url: supervisor.base_url.clone(),
        token: supervisor.token.clone(),
        launch_error,
    }
}

#[tauri::command]
fn restart_core(app: AppHandle, supervisor: State<'_, CoreSupervisor>) -> Result<(), String> {
    match launch_core(&app, &supervisor) {
        Ok(()) => Ok(()),
        Err(error) => {
            if let Ok(mut launch_error) = supervisor.launch_error.lock() {
                *launch_error = Some(error.clone());
            }
            Err(error)
        }
    }
}

pub fn run() {
    let app = tauri::Builder::default()
        .plugin(tauri_plugin_shell::init())
        .setup(|app| {
            let supervisor = CoreSupervisor::new().map_err(std::io::Error::other)?;
            app.manage(supervisor);
            let state = app.state::<CoreSupervisor>();
            if let Err(error) = launch_core(app.handle(), &state) {
                if let Ok(mut launch_error) = state.launch_error.lock() {
                    *launch_error = Some(error);
                }
            }
            Ok(())
        })
        .invoke_handler(tauri::generate_handler![core_connection, restart_core])
        .build(tauri::generate_context!())
        .expect("failed to build ZANA desktop application");

    app.run(|app_handle, event| {
        if let RunEvent::Exit = event {
            let state = app_handle.state::<CoreSupervisor>();
            if let Ok(mut child) = state.child.lock() {
                if let Some(child) = child.take() {
                    let _ = child.kill();
                }
            }
        }
    });
}
