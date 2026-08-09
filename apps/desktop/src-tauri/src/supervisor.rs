use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex};

use serde::Serialize;
use tauri::{AppHandle, Manager};
use tauri_plugin_shell::process::{CommandChild, CommandEvent};
use tauri_plugin_shell::ShellExt;

use crate::errors;
use crate::loopback::reserve_loopback_port;
use crate::secret::generate_token;

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct CoreConnection {
    base_url: String,
    token: String,
    launch_error: Option<String>,
}

impl CoreConnection {
    fn not_started() -> Self {
        Self {
            base_url: String::new(),
            token: String::new(),
            launch_error: Some("ZANA Core has not started yet.".to_owned()),
        }
    }

    fn unavailable() -> Self {
        Self {
            base_url: String::new(),
            token: String::new(),
            launch_error: Some(errors::supervisor_unavailable()),
        }
    }

    fn started(base_url: String, token: String) -> Self {
        Self {
            base_url,
            token,
            launch_error: None,
        }
    }
}

struct ChildSlot {
    child: CommandChild,
    expected_stop: Arc<AtomicBool>,
    generation: u64,
}

/// One supervisor owns exactly one Core child at a time.
pub(crate) struct CoreSupervisor {
    // Serializes lifecycle transitions so concurrent restarts or an app exit
    // cannot race a spawn and leave two children.
    lifecycle_lock: Mutex<()>,
    connection: Mutex<CoreConnection>,
    child: Mutex<Option<ChildSlot>>,
    next_generation: AtomicU64,
}

impl CoreSupervisor {
    pub(crate) fn new() -> Self {
        Self {
            lifecycle_lock: Mutex::new(()),
            connection: Mutex::new(CoreConnection::not_started()),
            child: Mutex::new(None),
            next_generation: AtomicU64::new(0),
        }
    }

    pub(crate) fn connection(&self) -> CoreConnection {
        match self.connection.lock() {
            Ok(connection) => connection.clone(),
            Err(_) => CoreConnection::unavailable(),
        }
    }

    pub(crate) fn launch(&self, app: &AppHandle) -> Result<(), String> {
        let _guard = self
            .lifecycle_lock
            .lock()
            .map_err(|_| errors::supervisor_unavailable())?;

        if let Err(error) = self.stop_current_child() {
            self.record_launch_failure(&error);
            return Err(error);
        }
        match self.spawn_child(app) {
            Ok(()) => Ok(()),
            Err(error) => {
                self.record_launch_failure(&error);
                Err(error)
            }
        }
    }

    pub(crate) fn shutdown(&self) {
        let _guard = match self.lifecycle_lock.lock() {
            Ok(guard) => guard,
            Err(poisoned) => poisoned.into_inner(),
        };
        let slot = match self.child.lock() {
            Ok(mut child) => child.take(),
            Err(poisoned) => poisoned.into_inner().take(),
        };
        if let Some(slot) = slot {
            slot.expected_stop.store(true, Ordering::SeqCst);
            let _ = slot.child.kill();
        }
    }

    fn stop_current_child(&self) -> Result<(), String> {
        let slot = self
            .child
            .lock()
            .map_err(|_| errors::supervisor_unavailable())?
            .take();
        if let Some(slot) = slot {
            slot.expected_stop.store(true, Ordering::SeqCst);
            slot.child.kill().map_err(|_| errors::core_stop_failed())?;
        }
        Ok(())
    }

    fn spawn_child(&self, app: &AppHandle) -> Result<(), String> {
        let port = reserve_loopback_port()?;
        let token = generate_token()?;
        let base_url = format!("http://127.0.0.1:{port}");

        let sidecar = app
            .shell()
            .sidecar("zana-core")
            .map_err(|_| errors::sidecar_missing())?;
        let port_arg = port.to_string();
        let (events, child) = sidecar
            .args(["serve", "--host", "127.0.0.1", "--port", port_arg.as_str()])
            .env("ZANA_CORE_TOKEN", &token)
            .spawn()
            .map_err(|_| errors::core_spawn_failed())?;

        let generation = self.next_generation.fetch_add(1, Ordering::Relaxed);
        let expected_stop = Arc::new(AtomicBool::new(false));
        *self
            .child
            .lock()
            .map_err(|_| errors::supervisor_unavailable())? = Some(ChildSlot {
            child,
            expected_stop: Arc::clone(&expected_stop),
            generation,
        });
        *self
            .connection
            .lock()
            .map_err(|_| errors::supervisor_unavailable())? =
            CoreConnection::started(base_url, token);

        watch_core_events(app.clone(), generation, expected_stop, events);
        Ok(())
    }

    fn record_launch_failure(&self, message: &str) {
        let mut connection = match self.connection.lock() {
            Ok(connection) => connection,
            Err(poisoned) => poisoned.into_inner(),
        };
        connection.base_url.clear();
        connection.token.clear();
        connection.launch_error = Some(message.to_owned());
    }
}

/// One bounded watcher task per child. It ends as soon as the child's event
/// channel closes after termination, so no polling or unbounded work remains.
fn watch_core_events(
    app: AppHandle,
    generation: u64,
    expected_stop: Arc<AtomicBool>,
    mut events: tauri::async_runtime::Receiver<CommandEvent>,
) {
    tauri::async_runtime::spawn(async move {
        while let Some(event) = events.recv().await {
            if expected_stop.load(Ordering::SeqCst) {
                if matches!(event, CommandEvent::Terminated(_)) {
                    break;
                }
                continue;
            }
            match event {
                CommandEvent::Terminated(payload) => {
                    forget_exited_child(&app, generation);
                    record_unexpected_exit(&app, exit_message(payload.code, payload.signal));
                    break;
                }
                CommandEvent::Error(_) => {
                    record_unexpected_exit(&app, errors::core_event_error());
                    break;
                }
                // The plugin enum is non-exhaustive; stdout/stderr are
                // intentionally discarded and unknown future events are safe
                // to ignore.
                _ => {}
            }
        }
    });
}

fn forget_exited_child(app: &AppHandle, generation: u64) {
    let supervisor = app.state::<CoreSupervisor>();
    if let Ok(mut child) = supervisor.child.lock() {
        if child
            .as_ref()
            .is_some_and(|slot| slot.generation == generation)
        {
            child.take();
        }
    }
}

fn record_unexpected_exit(app: &AppHandle, message: String) {
    let supervisor = app.state::<CoreSupervisor>();
    let mut connection = match supervisor.connection.lock() {
        Ok(connection) => connection,
        Err(poisoned) => poisoned.into_inner(),
    };
    connection.base_url.clear();
    connection.token.clear();
    connection.launch_error = Some(message);
}

fn exit_message(code: Option<i32>, signal: Option<i32>) -> String {
    match (code, signal) {
        (Some(code), _) => format!("ZANA Core stopped unexpectedly (exit code {code})."),
        (None, Some(signal)) => format!("ZANA Core stopped unexpectedly (signal {signal})."),
        (None, None) => "ZANA Core stopped unexpectedly.".to_owned(),
    }
}
