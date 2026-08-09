use tauri::{AppHandle, State};

use crate::supervisor::{CoreConnection, CoreSupervisor};

#[tauri::command]
pub(crate) fn core_connection(supervisor: State<'_, CoreSupervisor>) -> CoreConnection {
    supervisor.connection()
}

#[tauri::command]
pub(crate) fn restart_core(
    app: AppHandle,
    supervisor: State<'_, CoreSupervisor>,
) -> Result<(), String> {
    supervisor.launch(&app)
}
