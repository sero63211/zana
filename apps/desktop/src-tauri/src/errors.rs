// Sanitized, actionable error text. Raw OS/plugin details are never forwarded
// because they can include paths, commands, environment values, or tokens.

pub(crate) fn supervisor_unavailable() -> String {
    "ZANA Core supervisor is not responding. Restart the app to recover.".to_owned()
}

pub(crate) fn sidecar_missing() -> String {
    "The bundled ZANA Core sidecar is missing. Rebuild it with scripts/package-core.sh, then restart the app."
        .to_owned()
}

pub(crate) fn core_spawn_failed() -> String {
    "ZANA Core could not be started. Confirm the bundled sidecar is present and executable, then restart Core."
        .to_owned()
}

pub(crate) fn core_stop_failed() -> String {
    "ZANA Core could not be stopped cleanly. Restart the desktop app to recover.".to_owned()
}

pub(crate) fn port_unavailable() -> String {
    "ZANA Core could not reserve a loopback port. Close other local services and try again."
        .to_owned()
}

pub(crate) fn token_unavailable() -> String {
    "ZANA Core could not generate a launch token. Restart the app to recover.".to_owned()
}

pub(crate) fn core_event_error() -> String {
    "ZANA Core reported an internal process error. Restart Core and try again.".to_owned()
}
