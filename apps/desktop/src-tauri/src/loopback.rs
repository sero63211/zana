use std::net::TcpListener;

use crate::errors;

/// Reserve a port on the loopback interface without scanning the network.
///
/// The shell plugin has no way to pass a pre-bound socket to the sidecar, so
/// the listener is released immediately before spawn. This keeps the reuse
/// window as small as the available APIs allow and never touches a non-loopback
/// address.
pub(crate) fn reserve_loopback_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0)).map_err(|_| errors::port_unavailable())?;
    let port = listener
        .local_addr()
        .map_err(|_| errors::port_unavailable())?
        .port();
    drop(listener);
    Ok(port)
}
