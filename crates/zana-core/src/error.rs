//! Sanitized, bounded error primitives shared by the Rust Core.

use std::collections::BTreeMap;
use std::fmt;

use serde::Serialize;

/// Canonical API error envelope shared by every ZANA Core route.
#[derive(Debug, Clone, PartialEq, Eq, Serialize)]
pub struct ErrorDetail {
    pub code: &'static str,
    pub message: &'static str,
    pub details: BTreeMap<String, serde_json::Value>,
    pub recoverable: bool,
    pub actions: Vec<&'static str>,
}

impl ErrorDetail {
    pub fn new(
        code: &'static str,
        message: &'static str,
        recoverable: bool,
        actions: Vec<&'static str>,
    ) -> Self {
        Self {
            code,
            message,
            details: BTreeMap::new(),
            recoverable,
            actions,
        }
    }
}

pub fn unauthorized() -> ErrorDetail {
    ErrorDetail::new(
        "UNAUTHORIZED",
        "Missing or invalid bearer token.",
        false,
        vec!["provide_valid_token"],
    )
}

pub fn not_found() -> ErrorDetail {
    ErrorDetail::new("NOT_FOUND", "Endpoint not found.", true, vec![])
}

pub fn method_not_allowed() -> ErrorDetail {
    ErrorDetail::new("METHOD_NOT_ALLOWED", "Method not allowed.", true, vec![])
}

pub fn bad_request() -> ErrorDetail {
    ErrorDetail::new("BAD_REQUEST", "Malformed HTTP request.", true, vec![])
}

pub fn payload_too_large() -> ErrorDetail {
    ErrorDetail::new(
        "PAYLOAD_TOO_LARGE",
        "Request body is too large.",
        true,
        vec![],
    )
}

pub fn headers_too_large() -> ErrorDetail {
    ErrorDetail::new(
        "HEADERS_TOO_LARGE",
        "Request headers are too large.",
        true,
        vec![],
    )
}

pub fn request_timeout() -> ErrorDetail {
    ErrorDetail::new("REQUEST_TIMEOUT", "Request timed out.", true, vec![])
}

pub fn cors_disallowed() -> ErrorDetail {
    ErrorDetail::new(
        "CORS_ORIGIN_DISALLOWED",
        "Cross-origin request origin is not allowed.",
        false,
        vec![],
    )
}

pub fn service_unavailable() -> ErrorDetail {
    ErrorDetail::new(
        "SERVICE_UNAVAILABLE",
        "ZANA Core is busy. Retry shortly.",
        true,
        vec!["retry_request"],
    )
}

pub fn internal() -> ErrorDetail {
    ErrorDetail::new(
        "INTERNAL_ERROR",
        "An internal error occurred.",
        false,
        vec![],
    )
}

/// Startup or runtime failure with a fixed sanitized message.
#[derive(Debug)]
pub struct CoreError {
    detail: ErrorDetail,
}

impl CoreError {
    pub fn detail(&self) -> &ErrorDetail {
        &self.detail
    }

    pub fn database() -> Self {
        Self {
            detail: ErrorDetail::new(
                "DATABASE_UNAVAILABLE",
                "ZANA Core could not initialize its database.",
                false,
                vec![],
            ),
        }
    }

    pub fn data_root() -> Self {
        Self {
            detail: ErrorDetail::new(
                "DATA_ROOT_UNAVAILABLE",
                "ZANA Core could not resolve or prepare its data directory.",
                false,
                vec![],
            ),
        }
    }

    pub fn server() -> Self {
        Self {
            detail: ErrorDetail::new(
                "SERVER_UNAVAILABLE",
                "ZANA Core could not start its loopback server.",
                false,
                vec![],
            ),
        }
    }
}

impl fmt::Display for CoreError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.detail.message)
    }
}

impl std::error::Error for CoreError {}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn unauthorized_envelope_has_exact_shape() {
        let body = serde_json::to_value(unauthorized()).expect("serializes");
        assert_eq!(
            body,
            serde_json::json!({
                "code": "UNAUTHORIZED",
                "message": "Missing or invalid bearer token.",
                "details": {},
                "recoverable": false,
                "actions": ["provide_valid_token"]
            })
        );
    }

    #[test]
    fn core_error_display_is_sanitized() {
        assert_eq!(
            CoreError::database().to_string(),
            "ZANA Core could not initialize its database."
        );
    }
}
