use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{Arc, Mutex, MutexGuard};

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
///
/// Every lifecycle state transition that can change the current child or
/// connection is serialized by `lifecycle_lock` and revalidates the child
/// generation. A stale watcher therefore cannot clear a newer child's slot or
/// connection, and restart/shutdown cannot run concurrently with cleanup.
pub(crate) struct CoreSupervisor {
    lifecycle_lock: Mutex<()>,
    connection: Mutex<CoreConnection>,
    child: Mutex<Option<ChildSlot>>,
    // Once cleanup is uncertain, the supervisor refuses to spawn a
    // replacement so restart cannot layer another child over a process it may
    // not have stopped.
    replacement_blocked: AtomicBool,
    next_generation: AtomicU64,
}

/// Recover a poisoned mutex instead of leaking a child or dropping state. The
/// recovered value remains usable; raw poison errors are never surfaced.
fn lock<T>(mutex: &Mutex<T>) -> MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

impl CoreSupervisor {
    pub(crate) fn new() -> Self {
        Self {
            lifecycle_lock: Mutex::new(()),
            connection: Mutex::new(CoreConnection::not_started()),
            child: Mutex::new(None),
            replacement_blocked: AtomicBool::new(false),
            next_generation: AtomicU64::new(0),
        }
    }

    pub(crate) fn connection(&self) -> CoreConnection {
        lock(&self.connection).clone()
    }

    pub(crate) fn launch(&self, app: &AppHandle) -> Result<(), String> {
        if self.replacement_blocked.load(Ordering::SeqCst) {
            let error = errors::core_unrecoverable();
            self.record_failure(&error);
            return Err(error);
        }

        // Serialize restart/shutdown with the child watcher's state
        // transitions. A blocked supervisor never spawns a replacement.
        let _guard = lock(&self.lifecycle_lock);
        if self.replacement_blocked.load(Ordering::SeqCst) {
            let error = errors::core_unrecoverable();
            self.record_failure(&error);
            return Err(error);
        }

        if let Err(error) = self.stop_current_child() {
            self.record_failure(&error);
            return Err(error);
        }
        match self.spawn_child(app) {
            Ok(()) => Ok(()),
            Err(error) => {
                self.record_failure(&error);
                Err(error)
            }
        }
    }

    pub(crate) fn shutdown(&self) {
        // Clean app exit still respects the lifecycle lock so it cannot race a
        // restart in progress.
        let _guard = lock(&self.lifecycle_lock);
        let slot = lock(&self.child).take();
        if let Some(slot) = slot {
            slot.expected_stop.store(true, Ordering::SeqCst);
            let _ = slot.child.kill();
        }
    }

    fn stop_current_child(&self) -> Result<(), String> {
        let slot = lock(&self.child).take();
        if let Some(slot) = slot {
            slot.expected_stop.store(true, Ordering::SeqCst);
            if slot.child.kill().is_err() {
                // The handle is consumed by kill; the process state is no
                // longer certain, so never spawn over it.
                self.replacement_blocked.store(true, Ordering::SeqCst);
                return Err(errors::core_stop_failed());
            }
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

        // State publication below is infallible: poisoned locks are recovered.
        // If that guarantee changes, the spawned child must be killed before
        // any early return so a successful spawn is never orphaned.
        let generation = self.next_generation.fetch_add(1, Ordering::Relaxed);
        let expected_stop = Arc::new(AtomicBool::new(false));
        *lock(&self.child) = Some(ChildSlot {
            child,
            expected_stop: Arc::clone(&expected_stop),
            generation,
        });
        *lock(&self.connection) = CoreConnection::started(base_url, token);

        watch_core_events(app.clone(), generation, expected_stop, events);
        Ok(())
    }

    fn record_failure(&self, message: &str) {
        let mut connection = lock(&self.connection);
        connection.base_url.clear();
        connection.token.clear();
        connection.launch_error = Some(message.to_owned());
    }

    fn generation_is_current(&self, generation: u64) -> bool {
        lock(&self.child)
            .as_ref()
            .is_some_and(|slot| slot.generation == generation)
    }

    /// One lifecycle-serialized transition for an unexpected child exit.
    ///
    /// A stale generation never changes the current child or connection.
    fn on_unexpected_exit(&self, generation: u64, message: String) {
        let _guard = lock(&self.lifecycle_lock);
        if self.replacement_blocked.load(Ordering::SeqCst)
            || !self.generation_is_current(generation)
        {
            return;
        }
        lock(&self.child).take();
        self.record_failure(&message);
    }

    /// Record a sanitized plugin error for this generation without abandoning
    /// the live child. The watcher keeps draining toward `Terminated`.
    fn on_event_error(&self, generation: u64) {
        let _guard = lock(&self.lifecycle_lock);
        if self.replacement_blocked.load(Ordering::SeqCst)
            || !self.generation_is_current(generation)
        {
            return;
        }
        self.record_failure(errors::core_event_error());
    }

    /// Channel closure means no more events will arrive for this child. Under
    /// the lifecycle lock, perform bounded best-effort cleanup for exactly
    /// this generation; uncertain cleanup blocks future replacements.
    fn on_channel_closed(&self, generation: u64) {
        let _guard = lock(&self.lifecycle_lock);
        if self.replacement_blocked.load(Ordering::SeqCst)
            || !self.generation_is_current(generation)
        {
            return;
        }
        let slot = lock(&self.child).take();
        let mut cleanup_failed = false;
        if let Some(slot) = slot {
            slot.expected_stop.store(true, Ordering::SeqCst);
            if slot.child.kill().is_err() {
                cleanup_failed = true;
            }
        }
        if cleanup_failed {
            self.replacement_blocked.store(true, Ordering::SeqCst);
        }
        self.record_failure(errors::core_event_error());
    }
}

/// One bounded watcher task per child. It drains the plugin's event receiver
/// until the channel closes after termination, so no polling, busy loop, or
/// unbounded event retention remains.
fn watch_core_events(
    app: AppHandle,
    generation: u64,
    expected_stop: Arc<AtomicBool>,
    mut events: tauri::async_runtime::Receiver<CommandEvent>,
) {
    tauri::async_runtime::spawn(async move {
        loop {
            match events.recv().await {
                Some(event) => {
                    if expected_stop.load(Ordering::SeqCst) {
                        if matches!(event, CommandEvent::Terminated(_)) {
                            break;
                        }
                        continue;
                    }
                    match event {
                        CommandEvent::Terminated(payload) => {
                            app.state::<CoreSupervisor>().on_unexpected_exit(
                                generation,
                                exit_message(payload.code, payload.signal),
                            );
                            break;
                        }
                        CommandEvent::Error(_) => {
                            app.state::<CoreSupervisor>().on_event_error(generation);
                            // Continue draining: the channel has bounded
                            // capacity and the child may still terminate.
                        }
                        // The plugin enum is non-exhaustive; stdout/stderr are
                        // intentionally discarded and unknown events are safe
                        // to ignore while waiting for Terminated.
                        _ => {}
                    }
                }
                None => {
                    app.state::<CoreSupervisor>().on_channel_closed(generation);
                    break;
                }
            }
        }
    });
}

fn exit_message(code: Option<i32>, signal: Option<i32>) -> String {
    match (code, signal) {
        (Some(code), _) => format!("ZANA Core stopped unexpectedly (exit code {code})."),
        (None, Some(signal)) => format!("ZANA Core stopped unexpectedly (signal {signal})."),
        (None, None) => "ZANA Core stopped unexpectedly.".to_owned(),
    }
}
