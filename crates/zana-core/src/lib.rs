//! Shared Rust foundation for the ZANA local core.

pub mod auth;
pub mod db;
pub mod error;
pub mod platform;

pub use error::{CoreError, ErrorDetail};

pub const CORE_VERSION: &str = env!("CARGO_PKG_VERSION");
