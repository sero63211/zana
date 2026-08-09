#[cfg(unix)]
use crate::errors;
#[cfg(unix)]
use std::fs::File;
#[cfg(unix)]
use std::io::Read;

/// Generate a fresh bearer token from the operating system CSPRNG.
///
/// The token reaches Core only through the child environment and never appears
/// on a command line, in logs, in error text, or on disk.
#[cfg(unix)]
pub(crate) fn generate_token() -> Result<String, String> {
    let mut bytes = [0u8; 32];
    let mut urandom = File::open("/dev/urandom").map_err(|_| errors::token_unavailable())?;
    urandom
        .read_exact(&mut bytes)
        .map_err(|_| errors::token_unavailable())?;
    Ok(hex(&bytes))
}

#[cfg(not(unix))]
pub(crate) fn generate_token() -> Result<String, String> {
    // Each UUIDv4 provides 122 random bits from the OS CSPRNG through
    // getrandom. Three independent draws guarantee at least 256 random bits.
    let mut token = String::with_capacity(96);
    for _ in 0..3 {
        token.push_str(&uuid::Uuid::new_v4().simple().to_string());
    }
    Ok(token)
}

#[cfg(unix)]
const HEX: &[u8; 16] = b"0123456789abcdef";

#[cfg(unix)]
fn hex(bytes: &[u8]) -> String {
    let mut output = String::with_capacity(bytes.len() * 2);
    for byte in bytes {
        output.push(HEX[(byte >> 4) as usize] as char);
        output.push(HEX[(byte & 0x0f) as usize] as char);
    }
    output
}
