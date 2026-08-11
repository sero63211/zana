//! Shared Rust foundation for the ZANA local core.

pub mod acquisition;
pub mod auth;
pub mod db;
pub mod diagnostics;
pub mod domain;
pub mod error;
pub mod jobs;
pub mod observability;
pub mod platform;
pub mod repositories;
pub mod resources;
pub mod runtimes;
pub mod settings;
pub mod sha256;
pub mod streaming;
pub mod time;

pub use error::{CoreError, ErrorDetail};

pub const CORE_VERSION: &str = env!("CARGO_PKG_VERSION");
