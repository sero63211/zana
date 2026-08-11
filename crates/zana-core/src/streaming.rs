//! Bounded canonical SSE encoding, cursors, and terminal semantics.

use serde_json::Value;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKind {
    JobCreated,
    JobProgress,
    JobStatus,
    JobError,
    JobCancelled,
    MessageStart,
    Retrieval,
    ToolRequest,
    ToolResult,
    Token,
    MessageEnd,
    Error,
    Keepalive,
}

impl EventKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::JobCreated => "job_created",
            Self::JobProgress => "job_progress",
            Self::JobStatus => "job_status",
            Self::JobError => "job_error",
            Self::JobCancelled => "job_cancelled",
            Self::MessageStart => "message_start",
            Self::Retrieval => "retrieval",
            Self::ToolRequest => "tool_request",
            Self::ToolResult => "tool_result",
            Self::Token => "token",
            Self::MessageEnd => "message_end",
            Self::Error => "error",
            Self::Keepalive => "keepalive",
        }
    }
}

#[derive(Debug, Clone)]
pub struct StreamLimits {
    pub max_data_bytes: usize,
    pub max_event_bytes: usize,
    pub max_identifier_chars: usize,
    pub max_name_chars: usize,
    pub max_retry_ms: i64,
    pub max_total_bytes: usize,
}

impl Default for StreamLimits {
    fn default() -> Self {
        Self {
            max_data_bytes: 64 * 1024,
            max_event_bytes: 96 * 1024,
            max_identifier_chars: 128,
            max_name_chars: 64,
            max_retry_ms: 30_000,
            max_total_bytes: 4 * 1024 * 1024,
        }
    }
}

#[derive(Debug, Clone)]
pub struct ErrorMetadata {
    pub code: String,
    pub message: String,
    pub recoverable: bool,
    pub recovery_action: String,
    pub terminal: bool,
}

#[derive(Debug, Clone)]
pub struct StreamEvent {
    pub name: EventKind,
    pub data: Value,
    pub id: Option<String>,
    pub retry_ms: Option<i64>,
    pub terminal: bool,
    pub error: Option<ErrorMetadata>,
}

#[derive(Debug, Clone)]
pub struct InvalidCursorError;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CursorStatus {
    Valid,
    Stale,
    Invalid,
    Ahead,
}

#[derive(Debug, Clone)]
pub struct EventCursor {
    pub source_id: String,
    pub sequence: i64,
}

impl EventCursor {
    pub fn parse(value: &str, default_source: &str) -> Result<Self, InvalidCursorError> {
        if value.is_empty() || value.bytes().any(|b| matches!(b, b'\r' | b'\n' | 0)) {
            return Err(InvalidCursorError);
        }
        let (source, sequence) = match value.split_once(':') {
            Some((source, sequence)) if !source.is_empty() && !sequence.contains(':') => {
                (source, sequence)
            }
            Some(_) => return Err(InvalidCursorError),
            None => (default_source, value),
        };
        let sequence: i64 = sequence.parse().map_err(|_| InvalidCursorError)?;
        if sequence < 0 {
            return Err(InvalidCursorError);
        }
        Ok(Self {
            source_id: source.to_owned(),
            sequence,
        })
    }

    pub fn to_header(&self) -> String {
        format!("{}:{}", self.source_id, self.sequence)
    }
}

pub fn check_cursor(cursor: &EventCursor, expected: i64) -> CursorStatus {
    if cursor.sequence < expected {
        CursorStatus::Stale
    } else if cursor.sequence > expected {
        CursorStatus::Ahead
    } else {
        CursorStatus::Valid
    }
}

pub struct SSEEncoder {
    limits: StreamLimits,
    total_bytes: usize,
}

impl SSEEncoder {
    pub fn new(limits: StreamLimits) -> Self {
        Self {
            limits,
            total_bytes: 0,
        }
    }

    pub fn encode(&mut self, event: &StreamEvent) -> Result<Vec<u8>, String> {
        let mut body = Vec::new();
        if let Some(id) = &event.id {
            if id.len() > self.limits.max_identifier_chars
                || id.bytes().any(|b| matches!(b, b'\r' | b'\n' | 0))
            {
                return Err("event id is invalid".to_owned());
            }
            body.extend_from_slice(format!("id: {id}\n").as_bytes());
        }
        let name = event.name.as_str();
        if name.len() > self.limits.max_name_chars {
            return Err("event name exceeds the cap".to_owned());
        }
        body.extend_from_slice(format!("event: {name}\n").as_bytes());
        if let Some(retry) = event.retry_ms {
            if retry < 0 || retry > self.limits.max_retry_ms {
                return Err("retry exceeds the cap".to_owned());
            }
            body.extend_from_slice(format!("retry: {retry}\n").as_bytes());
        }
        let data = serde_json::to_string(&event.data)
            .map_err(|_| "event data is not JSON serializable".to_owned())?;
        if data.len() > self.limits.max_data_bytes {
            return Err("event data exceeds the cap".to_owned());
        }
        for line in data.split('\n') {
            body.extend_from_slice(format!("data: {line}\n").as_bytes());
        }
        if let Some(error) = &event.error {
            let error_json = serde_json::json!({
                "error": {
                    "code": error.code,
                    "message": error.message,
                    "recoverable": error.recoverable,
                    "recovery_action": error.recovery_action,
                    "terminal": error.terminal,
                }
            });
            let error_text = serde_json::to_string(&error_json)
                .map_err(|_| "event error is not JSON serializable".to_owned())?;
            for line in error_text.split('\n') {
                body.extend_from_slice(format!("data: {line}\n").as_bytes());
            }
        }
        if event.terminal {
            body.extend_from_slice(b"data: [DONE]\n");
        }
        body.push(b'\n');
        if body.len() > self.limits.max_event_bytes {
            return Err("event exceeds the byte cap".to_owned());
        }
        if self.total_bytes.saturating_add(body.len()) > self.limits.max_total_bytes {
            return Err("total stream would exceed the byte cap".to_owned());
        }
        self.total_bytes += body.len();
        Ok(body)
    }

    pub fn encode_keepalive(&mut self, comment: &str) -> Result<Vec<u8>, String> {
        if comment.is_empty()
            || comment.len() > self.limits.max_identifier_chars
            || comment.bytes().any(|b| matches!(b, b'\r' | b'\n' | 0))
        {
            return Err("keepalive comment is invalid".to_owned());
        }
        let chunk = format!(": {comment}\n\n").into_bytes();
        if self.total_bytes.saturating_add(chunk.len()) > self.limits.max_total_bytes {
            return Err("total stream would exceed the byte cap".to_owned());
        }
        self.total_bytes += chunk.len();
        Ok(chunk)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn cursor_parse_and_check() {
        let cursor = EventCursor::parse("jobs:42", "jobs").expect("cursor");
        assert_eq!(cursor.to_header(), "jobs:42");
        assert_eq!(check_cursor(&cursor, 41), CursorStatus::Ahead);
        assert_eq!(check_cursor(&cursor, 42), CursorStatus::Valid);
        assert_eq!(check_cursor(&cursor, 43), CursorStatus::Stale);
        assert!(EventCursor::parse("jobs:-1", "jobs").is_err());
        assert!(EventCursor::parse("jobs:1:2", "jobs").is_err());
        assert!(EventCursor::parse("a\nb", "jobs").is_err());
    }

    #[test]
    fn encoder_emits_canonical_terminal_event() {
        let mut encoder = SSEEncoder::new(StreamLimits::default());
        let event = StreamEvent {
            name: EventKind::JobProgress,
            data: serde_json::json!({"job_id": 1, "progress_0_1": 0.5}),
            id: Some("jobs:1".to_owned()),
            retry_ms: Some(1000),
            terminal: false,
            error: None,
        };
        let chunk = encoder.encode(&event).expect("encodes");
        let text = String::from_utf8(chunk).expect("utf8");
        assert!(text.contains("event: job_progress\n"));
        assert!(text.contains("id: jobs:1\n"));
        assert!(text.contains("data: {\"job_id\":1,\"progress_0_1\":0.5}\n"));
        assert!(text.ends_with("\n\n"));
        assert!(encoder
            .encode_keepalive("ok")
            .expect("keepalive")
            .starts_with(b": ok"));
    }

    #[test]
    fn encoder_rejects_oversized_data_and_total() {
        let mut encoder = SSEEncoder::new(StreamLimits {
            max_data_bytes: 16,
            ..StreamLimits::default()
        });
        let event = StreamEvent {
            name: EventKind::JobStatus,
            data: serde_json::json!({"x": "y".repeat(100)}),
            id: None,
            retry_ms: None,
            terminal: false,
            error: None,
        };
        assert!(encoder.encode(&event).is_err());

        let mut encoder = SSEEncoder::new(StreamLimits {
            max_total_bytes: 32,
            ..StreamLimits::default()
        });
        assert!(encoder.encode_keepalive(&"x".repeat(64)).is_err());
    }
}
