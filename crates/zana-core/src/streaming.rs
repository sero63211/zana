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
    source_id: String,
    sequence: i64,
}

impl EventCursor {
    pub fn new(source_id: impl Into<String>, sequence: i64) -> Result<Self, InvalidCursorError> {
        let source_id = source_id.into();
        if !valid_source(&source_id) || !(0..=i64::MAX).contains(&sequence) {
            return Err(InvalidCursorError);
        }
        Ok(Self {
            source_id,
            sequence,
        })
    }

    pub fn source_id(&self) -> &str {
        &self.source_id
    }

    pub fn sequence(&self) -> i64 {
        self.sequence
    }
}

fn valid_source(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
}

impl EventCursor {
    pub fn parse(value: &str, default_source: &str) -> Result<Self, InvalidCursorError> {
        if value.is_empty() || value.bytes().any(|b| matches!(b, b'\r' | b'\n' | 0)) {
            return Err(InvalidCursorError);
        }
        if value.len() > 256 || value.chars().count() > 128 {
            return Err(InvalidCursorError);
        }
        if !default_source.is_empty() && !valid_source(default_source) {
            return Err(InvalidCursorError);
        }
        let (source, sequence) = match value.split_once(':') {
            Some((source, sequence)) if !source.is_empty() && !sequence.contains(':') => {
                (source, sequence)
            }
            Some(_) => return Err(InvalidCursorError),
            None => (default_source, value),
        };
        if !valid_source(source) {
            return Err(InvalidCursorError);
        }
        let sequence: i64 = sequence.parse().map_err(|_| InvalidCursorError)?;
        if sequence < 0 {
            return Err(InvalidCursorError);
        }
        Self::new(source, sequence)
    }

    pub fn to_header(&self) -> String {
        format!("{}:{}", self.source_id, self.sequence)
    }
}

pub fn check_cursor(cursor: &EventCursor, expected: i64) -> CursorStatus {
    check_cursor_with(cursor, expected, true)
}

pub fn check_cursor_with(cursor: &EventCursor, expected: i64, allow_ahead: bool) -> CursorStatus {
    if expected < 0 {
        return CursorStatus::Invalid;
    }
    if cursor.sequence < expected {
        CursorStatus::Stale
    } else if cursor.sequence > expected {
        if allow_ahead {
            CursorStatus::Ahead
        } else {
            CursorStatus::Invalid
        }
    } else {
        CursorStatus::Valid
    }
}

/// Explicit source binding check so API consumers can reject a cursor whose
/// source does not match the requested stream.
pub fn source_matches(cursor: &EventCursor, expected: &str) -> bool {
    cursor.source_id == expected && valid_source(&cursor.source_id)
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
        let mut output = Vec::new();
        let name = event.name.as_str();
        if name.len() > self.limits.max_name_chars {
            return Err("event name exceeds the cap".to_owned());
        }
        if let Some(id) = &event.id {
            if id.len() > self.limits.max_identifier_chars || id.bytes().any(control_byte) {
                return Err("event id is invalid".to_owned());
            }
        }
        let data = serde_json::to_string(&event.data)
            .map_err(|_| "event data is not JSON serializable".to_owned())?;
        if data.len() > self.limits.max_data_bytes || contains_control(&data) {
            return Err("event data exceeds the cap".to_owned());
        }
        let block = self.encode_block(
            event.name.as_str(),
            event.id.as_deref(),
            event.retry_ms,
            &data,
        )?;
        let block_len = block.len();
        output.extend_from_slice(&block);

        // A terminal/error event emits each canonical JSON value in its own
        // complete SSE block, then [DONE] in its own block; standard
        // fetch/EventSource parsers never see concatenated JSON values.
        if let Some(error) = &event.error {
            validate_error(error)?;
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
            let error_block = self.encode_block(
                event.name.as_str(),
                event.id.as_deref(),
                event.retry_ms,
                &error_text,
            )?;
            if error_text.len() > self.limits.max_data_bytes {
                return Err("error data exceeds the cap".to_owned());
            }
            if error_block.len() > self.limits.max_event_bytes {
                return Err("event exceeds the byte cap".to_owned());
            }
            output.extend_from_slice(&error_block);
        }
        if event.terminal {
            let done = self.encode_block(
                event.name.as_str(),
                event.id.as_deref(),
                event.retry_ms,
                "[DONE]",
            )?;
            if done.len() > self.limits.max_data_bytes || done.len() > self.limits.max_event_bytes {
                return Err("event exceeds the byte cap".to_owned());
            }
            output.extend_from_slice(&done);
        }
        if output.len() > self.limits.max_event_bytes || block_len > self.limits.max_event_bytes {
            return Err("event exceeds the byte cap".to_owned());
        }
        if self.total_bytes.saturating_add(output.len()) > self.limits.max_total_bytes {
            return Err("total stream would exceed the byte cap".to_owned());
        }
        self.total_bytes += output.len();
        Ok(output)
    }

    fn encode_block(
        &self,
        name: &str,
        id: Option<&str>,
        retry_ms: Option<i64>,
        data: &str,
    ) -> Result<Vec<u8>, String> {
        let mut block = Vec::new();
        if let Some(id) = id {
            block.extend_from_slice(format!("id: {id}\n").as_bytes());
        }
        block.extend_from_slice(format!("event: {name}\n").as_bytes());
        if let Some(retry) = retry_ms {
            if retry < 0 || retry > self.limits.max_retry_ms {
                return Err("retry exceeds the cap".to_owned());
            }
            block.extend_from_slice(format!("retry: {retry}\n").as_bytes());
        }
        for line in data.split('\n') {
            block.extend_from_slice(format!("data: {line}\n").as_bytes());
        }
        block.push(b'\n');
        if block.len() > self.limits.max_event_bytes {
            return Err("event exceeds the byte cap".to_owned());
        }
        Ok(block)
    }

    pub fn encode_keepalive(&mut self, comment: &str) -> Result<Vec<u8>, String> {
        if comment.is_empty()
            || comment.len() > self.limits.max_identifier_chars
            || comment.bytes().any(control_byte)
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

fn control_byte(byte: u8) -> bool {
    byte == 0x7f || byte < 0x20
}

fn contains_control(value: &str) -> bool {
    value.bytes().any(control_byte)
}

fn validate_error(error: &ErrorMetadata) -> Result<(), String> {
    if error.code.len() > 64
        || error.message.len() > 500
        || error.recovery_action.len() > 300
        || contains_control(&error.code)
        || contains_control(&error.message)
        || contains_control(&error.recovery_action)
    {
        return Err("error metadata exceeds the bounded limits".to_owned());
    }
    Ok(())
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
    fn cursor_source_bounds_and_ahead_disallow() {
        let wrong = EventCursor::parse("other:42", "jobs").expect("cursor");
        assert!(!source_matches(&wrong, "jobs"));
        let right = EventCursor::parse("jobs:42", "jobs").expect("cursor");
        assert!(source_matches(&right, "jobs"));

        assert!(EventCursor::parse(&"x".repeat(257), "jobs").is_err());
        assert!(EventCursor::parse(&"😀".repeat(70), "jobs").is_err());
        assert!(EventCursor::parse("bad/source:1", "jobs").is_err());

        let ahead = EventCursor::parse("jobs:42", "jobs").expect("cursor");
        assert_eq!(check_cursor_with(&ahead, 41, false), CursorStatus::Invalid);
        assert_eq!(check_cursor_with(&ahead, 42, false), CursorStatus::Valid);
        assert_eq!(check_cursor_with(&ahead, 43, false), CursorStatus::Stale);
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

    #[test]
    fn encoder_emits_one_json_value_per_sse_block() {
        let mut encoder = SSEEncoder::new(StreamLimits::default());
        let event = StreamEvent {
            name: EventKind::JobError,
            data: serde_json::json!({"job_id": 1}),
            id: Some("jobs:1".to_owned()),
            retry_ms: None,
            terminal: true,
            error: Some(ErrorMetadata {
                code: "ERR".to_owned(),
                message: "boom".to_owned(),
                recoverable: true,
                recovery_action: "retry".to_owned(),
                terminal: true,
            }),
        };
        let chunk = encoder.encode(&event).expect("encodes");
        let text = String::from_utf8(chunk).expect("utf8");
        let blocks: Vec<&str> = text
            .split("\n\n")
            .filter(|block| !block.is_empty())
            .collect();
        assert_eq!(
            blocks.len(),
            3,
            "primary, error, and [DONE] are separate blocks"
        );

        let primary_data = data_lines(blocks[0]);
        let primary: serde_json::Value =
            serde_json::from_str(&primary_data).expect("primary block is one JSON value");
        assert_eq!(primary["job_id"], 1);
        assert!(primary.get("error").is_none());

        let error_data = data_lines(blocks[1]);
        let error: serde_json::Value =
            serde_json::from_str(&error_data).expect("error block is one JSON value");
        assert_eq!(error["error"]["code"], "ERR");

        assert_eq!(data_lines(blocks[2]), "[DONE]");
    }

    fn data_lines(block: &str) -> String {
        block
            .lines()
            .filter_map(|line| line.strip_prefix("data: "))
            .collect::<Vec<_>>()
            .join("\n")
    }

    #[test]
    fn encoder_total_cap_covers_all_emitted_blocks() {
        let mut encoder = SSEEncoder::new(StreamLimits {
            max_total_bytes: 64,
            max_event_bytes: 4096,
            ..StreamLimits::default()
        });
        let event = StreamEvent {
            name: EventKind::JobError,
            data: serde_json::json!({"x": "y".repeat(200)}),
            id: Some("jobs:1".to_owned()),
            retry_ms: None,
            terminal: true,
            error: Some(ErrorMetadata {
                code: "ERR".to_owned(),
                message: "boom".to_owned(),
                recoverable: true,
                recovery_action: "retry".to_owned(),
                terminal: true,
            }),
        };
        assert!(encoder.encode(&event).is_err());
    }

    #[test]
    fn hostile_error_and_control_ids_fail_without_counter_mutation() {
        let mut encoder = SSEEncoder::new(StreamLimits::default());
        let hostile_error = StreamEvent {
            name: EventKind::JobError,
            data: serde_json::json!({}),
            id: None,
            retry_ms: None,
            terminal: false,
            error: Some(ErrorMetadata {
                code: "x".repeat(65),
                message: "m".to_owned(),
                recoverable: true,
                recovery_action: "r".to_owned(),
                terminal: true,
            }),
        };
        assert!(encoder.encode(&hostile_error).is_err());

        let control_id = StreamEvent {
            name: EventKind::JobStatus,
            data: serde_json::json!({}),
            id: Some("id\x01x".to_owned()),
            retry_ms: None,
            terminal: false,
            error: None,
        };
        assert!(encoder.encode(&control_id).is_err());

        let valid = StreamEvent {
            name: EventKind::JobStatus,
            data: serde_json::json!({"ok": true}),
            id: None,
            retry_ms: None,
            terminal: false,
            error: None,
        };
        assert!(encoder.encode(&valid).is_ok());
    }

    #[test]
    fn cursor_is_structurally_valid_and_handles_i64_max() {
        assert!(EventCursor::new("jobs", -1).is_err());
        let max = EventCursor::new("jobs", i64::MAX).expect("max cursor");
        assert_eq!(max.to_header(), format!("jobs:{}", i64::MAX));
        let parsed = EventCursor::parse(&format!("jobs:{}", i64::MAX), "jobs").expect("parses");
        assert_eq!(parsed.to_header(), format!("jobs:{}", i64::MAX));
        assert_eq!(
            check_cursor_with(&parsed, i64::MAX, false),
            CursorStatus::Valid
        );
        assert_eq!(check_cursor_with(&parsed, -1, false), CursorStatus::Invalid);
    }
}
