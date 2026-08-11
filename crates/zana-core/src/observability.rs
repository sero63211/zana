//! Bounded local observability events, redaction, serialization, and audit.

use std::collections::VecDeque;
use std::io::{self, Write};
use std::sync::Mutex;

use serde_json::Value;

use crate::repositories::AuditEvents;
use crate::time::now_iso;

pub const MAX_STRING_LENGTH: usize = 512;
pub const MAX_STRING_BYTES: usize = 1024;
pub const MAX_MAP_SIZE: usize = 64;
pub const MAX_LIST_SIZE: usize = 128;
pub const MAX_DEPTH: usize = 8;
pub const MAX_AGGREGATE_BYTES: usize = 6144;
pub const MAX_IDENTIFIER_LENGTH: usize = 128;
pub const MAX_ENCODED_LINE_BYTES: usize = 8192;
pub const MAX_RETAINED_EVENTS: usize = 500;
pub const MAX_RETAINED_EVENTS_HARD_CAP: usize = 1000;
pub const MAX_RETAINED_BYTES_DEFAULT: usize = 2 * 1024 * 1024;
pub const MAX_RETAINED_BYTES_HARD_CAP: usize = 16 * 1024 * 1024;
pub const MAX_EVENT_PAGE_LIMIT: usize = 200;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Severity {
    Debug,
    Info,
    Warning,
    Error,
}

impl Severity {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Debug => "debug",
            Self::Info => "info",
            Self::Warning => "warning",
            Self::Error => "error",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum EventKind {
    System,
    Job,
    Runtime,
    Build,
    Tool,
    Instance,
    Evaluation,
}

impl EventKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::System => "system",
            Self::Job => "job",
            Self::Runtime => "runtime",
            Self::Build => "build",
            Self::Tool => "tool",
            Self::Instance => "instance",
            Self::Evaluation => "evaluation",
        }
    }
}

#[derive(Debug, Clone)]
pub struct EventContext {
    pub operation_id: String,
    pub job_id: String,
    pub phase: String,
    pub instance_id: Option<String>,
    pub image_digest: Option<String>,
}

#[derive(Debug, Clone)]
pub struct Event {
    pub schema_version: i64,
    pub kind: EventKind,
    pub severity: Severity,
    pub message: String,
    pub timestamp: String,
    pub context: EventContext,
    pub operation_id: String,
    pub job_id: String,
    pub phase: String,
    pub progress_0_1: Option<f64>,
    pub duration_ms: Option<i64>,
    pub recovery_code: Option<String>,
    pub payload: Value,
}

impl Event {
    pub fn new(
        kind: EventKind,
        severity: Severity,
        message: impl Into<String>,
        operation_id: impl Into<String>,
    ) -> Result<Self, String> {
        let message = bounded_text(&message.into(), MAX_STRING_LENGTH, MAX_STRING_BYTES)?;
        let operation_id = bounded_identifier(&operation_id.into(), MAX_IDENTIFIER_LENGTH)?;
        Ok(Self {
            schema_version: 1,
            kind,
            severity,
            message,
            timestamp: now_iso(),
            context: EventContext {
                operation_id: operation_id.clone(),
                job_id: String::new(),
                phase: String::new(),
                instance_id: None,
                image_digest: None,
            },
            operation_id,
            job_id: String::new(),
            phase: String::new(),
            progress_0_1: None,
            duration_ms: None,
            recovery_code: None,
            payload: Value::Object(Default::default()),
        })
    }
}

#[derive(Debug, Clone)]
pub struct RedactionLimits {
    pub max_depth: usize,
    pub max_items: usize,
    pub max_container_items: usize,
    pub max_string_length: usize,
    pub max_string_bytes: usize,
    pub max_key_length: usize,
    pub max_key_bytes: usize,
    pub max_output_bytes: usize,
}

impl Default for RedactionLimits {
    fn default() -> Self {
        Self {
            max_depth: 12,
            max_items: 256,
            max_container_items: 128,
            max_string_length: 512,
            max_string_bytes: 1024,
            max_key_length: 128,
            max_key_bytes: 256,
            max_output_bytes: 8192,
        }
    }
}

const REDACTED: &str = "***";
const TRUNCATED: &str = "...[truncated]";
const REDACTED_KEY: &str = "<redacted-key>";

const SENSITIVE_NORMALIZED: &[&str] = &[
    "authorization",
    "authtoken",
    "accesstoken",
    "apitoken",
    "apikey",
    "xapikey",
    "cookie",
    "credential",
    "credentials",
    "password",
    "passwd",
    "secret",
    "secrets",
    "privatekey",
    "accesskey",
    "sessionkey",
    "clientsecret",
    "refreshtoken",
    "bearer",
    "token",
    "tokens",
];

const CONTENT_NORMALIZED: &[&str] = &[
    "prompt",
    "response",
    "completion",
    "document",
    "documentcontent",
    "content",
    "raw",
    "rawbody",
    "requestbody",
    "responsebody",
    "body",
    "environment",
    "env",
];

const SAFE_OPERATIONAL: &[&str] = &[
    "status",
    "message",
    "recoverycode",
    "errorcode",
    "operationid",
    "jobid",
    "phase",
    "progress01",
    "durationms",
    "count",
    "digest",
    "basename",
    "ok",
];

const PATH_KEYS: &[&str] = &[
    "path",
    "file",
    "filename",
    "filepath",
    "directory",
    "root",
    "source",
    "destination",
    "sourcepath",
    "destinationpath",
    "localpath",
    "logroot",
    "logrootpath",
    "workspace",
    "workspacepath",
];

fn normalize_key(key: &str) -> String {
    key.chars()
        .filter(|ch| ch.is_ascii_alphanumeric())
        .flat_map(char::to_lowercase)
        .collect()
}

fn is_sensitive_key(key: &str) -> bool {
    let normalized = normalize_key(key);
    SENSITIVE_NORMALIZED.contains(&normalized.as_str())
        || (CONTENT_NORMALIZED.contains(&normalized.as_str())
            && !SAFE_OPERATIONAL.contains(&normalized.as_str()))
}

fn is_path_key(key: &str) -> bool {
    let normalized = normalize_key(key);
    PATH_KEYS.contains(&normalized.as_str()) && !SAFE_OPERATIONAL.contains(&normalized.as_str())
}

fn path_basename(value: &str) -> String {
    let normalized = value.replace('\\', "/");
    let basename = normalized.rsplit('/').next().unwrap_or(value);
    if basename.is_empty() || basename == "." || basename == ".." || basename.len() > 128 {
        let digest = crate::sha256::sha256_hex(value.as_bytes());
        format!("path-{}-len{}", &digest[..16], value.len())
    } else {
        basename.to_owned()
    }
}

struct LimitWriter {
    remaining: usize,
}

impl Write for LimitWriter {
    fn write(&mut self, buffer: &[u8]) -> io::Result<usize> {
        if buffer.len() > self.remaining {
            return Err(io::Error::new(
                io::ErrorKind::WriteZero,
                "output limit exceeded",
            ));
        }
        self.remaining -= buffer.len();
        Ok(buffer.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

/// Redact an exact JSON value with bounded depth/items/bytes, never allocating
/// a hostile tree beyond the output limit.
pub fn redact_value(value: &Value, limits: &RedactionLimits) -> Value {
    let mut budget = Budget {
        items: limits.max_items,
        output: limits.max_output_bytes,
    };
    redact_node(value, 0, limits, &mut budget, None)
}

struct Budget {
    items: usize,
    output: usize,
}

impl Budget {
    fn spend(&mut self) -> bool {
        if self.items == 0 || self.output == 0 {
            return false;
        }
        self.items -= 1;
        self.output -= 1;
        true
    }

    fn charge(&mut self, amount: usize) -> bool {
        if self.output < amount {
            false
        } else {
            self.output -= amount;
            true
        }
    }
}

fn redact_node(
    value: &Value,
    depth: usize,
    limits: &RedactionLimits,
    budget: &mut Budget,
    key: Option<&str>,
) -> Value {
    if depth >= limits.max_depth || !budget.spend() {
        return Value::String(REDACTED.to_owned());
    }
    match value {
        Value::Null | Value::Bool(_) => value.clone(),
        Value::Number(number) if number.is_i64() || number.is_u64() => value.clone(),
        Value::Number(number) if number.is_f64() => {
            if number.as_f64().is_some_and(f64::is_finite) {
                value.clone()
            } else {
                Value::String(REDACTED.to_owned())
            }
        }
        Value::String(text) => {
            if key.is_some_and(is_sensitive_key) {
                Value::String(REDACTED.to_owned())
            } else if key.is_some_and(is_path_key) {
                Value::String(path_basename(text))
            } else {
                Value::String(truncate_text(text, limits))
            }
        }
        Value::Array(items) => {
            let mut result = Vec::new();
            for item in items {
                if result.len() >= limits.max_container_items {
                    result.push(Value::String(REDACTED.to_owned()));
                    break;
                }
                let child = redact_node(item, depth + 1, limits, budget, None);
                if !budget.charge(1) {
                    result.push(Value::String(REDACTED.to_owned()));
                    break;
                }
                result.push(child);
            }
            Value::Array(result)
        }
        Value::Object(map) => {
            let mut result = serde_json::Map::new();
            for (item_key, item_value) in map {
                if result.len() >= limits.max_container_items {
                    result.insert(REDACTED_KEY.to_owned(), Value::String(REDACTED.to_owned()));
                    break;
                }
                if item_key.len() > limits.max_key_length || item_key.len() > limits.max_key_bytes {
                    result.insert(REDACTED_KEY.to_owned(), Value::String(REDACTED.to_owned()));
                    continue;
                }
                let child = redact_node(item_value, depth + 1, limits, budget, Some(item_key));
                if !budget.charge(item_key.len() + 1) {
                    result.insert(REDACTED_KEY.to_owned(), Value::String(REDACTED.to_owned()));
                    break;
                }
                result.insert(item_key.clone(), child);
            }
            Value::Object(result)
        }
        _ => Value::String(REDACTED.to_owned()),
    }
}

fn truncate_text(value: &str, limits: &RedactionLimits) -> String {
    bounded_text(value, limits.max_string_length, limits.max_string_bytes)
        .unwrap_or_else(|_| REDACTED.to_owned())
}

pub fn bounded_text(value: &str, max_chars: usize, max_bytes: usize) -> Result<String, String> {
    if value.is_empty() {
        return Ok(String::new());
    }
    if value.len() <= max_bytes && value.chars().count() <= max_chars {
        return Ok(value.to_owned());
    }
    let suffix = TRUNCATED;
    let budget_bytes = max_bytes.saturating_sub(suffix.len());
    let budget_chars = max_chars.saturating_sub(suffix.chars().count());
    if budget_bytes == 0 || budget_chars == 0 {
        return Ok(suffix.to_owned());
    }
    let mut retained = String::new();
    for (count, ch) in value.chars().enumerate() {
        if count >= budget_chars || retained.len() + ch.len_utf8() > budget_bytes {
            break;
        }
        retained.push(ch);
    }
    retained.push_str(suffix);
    Ok(retained)
}

fn bounded_identifier(value: &str, max: usize) -> Result<String, String> {
    if value.is_empty() || value.len() > max || value.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err("identifier is invalid".to_owned());
    }
    Ok(value.to_owned())
}

/// Canonical compact JSON plus a trailing newline, bounded by the encoded cap.
pub fn serialize_event(event: &Event, limits: &RedactionLimits) -> Result<String, String> {
    let raw = serde_json::json!({
        "schema_version": event.schema_version,
        "kind": event.kind.as_str(),
        "severity": event.severity.as_str(),
        "message": event.message,
        "timestamp": event.timestamp,
        "context": {
            "operation_id": event.context.operation_id,
            "job_id": event.context.job_id,
            "phase": event.context.phase,
            "instance_id": event.context.instance_id,
            "image_digest": event.context.image_digest,
        },
        "operation_id": event.operation_id,
        "job_id": event.job_id,
        "phase": event.phase,
        "progress_0_1": event.progress_0_1,
        "duration_ms": event.duration_ms,
        "recovery_code": event.recovery_code,
        "payload": event.payload,
    });
    let redacted = redact_value(&raw, limits);
    let mut writer = LimitWriter {
        remaining: MAX_ENCODED_LINE_BYTES,
    };
    serde_json::to_writer(&mut writer, &redacted)
        .map_err(|_| "event could not be serialized within bounds".to_owned())?;
    writer
        .write_all(b"\n")
        .map_err(|_| "event could not be serialized within bounds".to_owned())?;
    let mut line = Vec::new();
    serde_json::to_writer(&mut line, &redacted)
        .map_err(|_| "event could not be serialized".to_owned())?;
    line.push(b'\n');
    String::from_utf8(line).map_err(|_| "event line is not UTF-8".to_owned())
}

#[derive(Debug, Clone, Default)]
pub struct SinkStats {
    pub events_written: i64,
    pub events_dropped: i64,
    pub bytes_written: i64,
    pub failures: i64,
}

#[derive(Debug, Clone)]
pub struct WriteResult {
    pub ok: bool,
    pub event_id: String,
    pub dropped: bool,
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct RetainedEvent {
    pub sequence: i64,
    pub event_id: String,
    pub line: String,
    pub bytes: usize,
    pub received_at: String,
}

#[derive(Debug, Clone)]
pub struct EventPage {
    pub items: Vec<RetainedEvent>,
    pub count: usize,
    pub limit: usize,
    pub next_cursor: Option<i64>,
    pub truncated: bool,
    pub total_available: usize,
    pub retention_dropped: i64,
    pub retention_dropped_bytes: i64,
    pub max_retained_bytes: usize,
    pub retained_bytes: usize,
}

#[derive(Debug, Clone)]
pub struct ObservabilityHealth {
    pub telemetry_enabled: bool,
    pub remote_transport: String,
    pub mode: String,
    pub max_retained_events: usize,
    pub max_retained_bytes: usize,
    pub retained_events: usize,
    pub retained_bytes: usize,
    pub retention_dropped: i64,
    pub retention_dropped_bytes: i64,
    pub failures: i64,
    pub closed: bool,
}

pub struct ObservabilityRegistry {
    limits: RedactionLimits,
    max_retained_events: usize,
    max_retained_bytes: usize,
    state: Mutex<RegistryState>,
}

struct RegistryState {
    records: VecDeque<RetainedEvent>,
    retained_bytes: usize,
    sequence: i64,
    retention_dropped: i64,
    retention_dropped_bytes: i64,
    failures: i64,
    closed: bool,
}

impl ObservabilityRegistry {
    pub fn new(
        max_retained_events: usize,
        max_retained_bytes: usize,
        limits: RedactionLimits,
    ) -> Result<Self, String> {
        if !(1..=MAX_RETAINED_EVENTS_HARD_CAP).contains(&max_retained_events)
            || !(1..=MAX_RETAINED_BYTES_HARD_CAP).contains(&max_retained_bytes)
        {
            return Err("observability retention bounds are out of range".to_owned());
        }
        Ok(Self {
            limits,
            max_retained_events,
            max_retained_bytes,
            state: Mutex::new(RegistryState {
                records: VecDeque::new(),
                retained_bytes: 0,
                sequence: 0,
                retention_dropped: 0,
                retention_dropped_bytes: 0,
                failures: 0,
                closed: false,
            }),
        })
    }

    pub fn write(&self, event: &Event) -> WriteResult {
        let line = match serialize_event(event, &self.limits) {
            Ok(line) => line,
            Err(_) => {
                let mut state = lock(&self.state);
                state.failures += 1;
                return WriteResult {
                    ok: false,
                    event_id: String::new(),
                    dropped: false,
                    error: Some("WRITE_FAILED".to_owned()),
                };
            }
        };
        let event_id = safe_public_identifier(&event.operation_id);
        let bytes = line.len();
        let received_at = now_iso();
        let mut state = lock(&self.state);
        if state.closed {
            state.failures += 1;
            return WriteResult {
                ok: false,
                event_id,
                dropped: false,
                error: Some("REGISTRY_CLOSED".to_owned()),
            };
        }
        state.sequence += 1;
        let sequence = state.sequence;
        state.records.push_back(RetainedEvent {
            sequence,
            event_id: event_id.clone(),
            line: line.clone(),
            bytes,
            received_at,
        });
        state.retained_bytes += bytes;
        while state.records.len() > self.max_retained_events
            || state.retained_bytes > self.max_retained_bytes
        {
            if let Some(removed) = state.records.pop_front() {
                state.retained_bytes = state.retained_bytes.saturating_sub(removed.bytes);
                state.retention_dropped += 1;
                state.retention_dropped_bytes += removed.bytes as i64;
            } else {
                break;
            }
        }
        WriteResult {
            ok: true,
            event_id,
            dropped: false,
            error: None,
        }
    }

    pub fn events(&self, limit: usize, before_sequence: Option<i64>) -> Result<EventPage, String> {
        if !(1..=MAX_EVENT_PAGE_LIMIT).contains(&limit) {
            return Err("limit must be within the event page cap".to_owned());
        }
        if before_sequence.is_some_and(|value| value < 0) {
            return Err("before_sequence must be non-negative".to_owned());
        }
        let state = lock(&self.state);
        let mut records: Vec<RetainedEvent> = state.records.iter().cloned().collect();
        if let Some(before) = before_sequence {
            records.retain(|record| record.sequence < before);
        }
        let total = records.len();
        let newest: Vec<RetainedEvent> = records.iter().rev().take(limit).cloned().collect();
        let truncated = total > newest.len();
        let next_cursor = if truncated && !newest.is_empty() {
            Some(newest.last().expect("non-empty").sequence)
        } else {
            None
        };
        let count = newest.len();
        Ok(EventPage {
            items: newest,
            count,
            limit,
            next_cursor,
            truncated,
            total_available: total,
            retention_dropped: state.retention_dropped,
            retention_dropped_bytes: state.retention_dropped_bytes,
            max_retained_bytes: self.max_retained_bytes,
            retained_bytes: state.retained_bytes,
        })
    }

    pub fn health(&self) -> ObservabilityHealth {
        let state = lock(&self.state);
        ObservabilityHealth {
            telemetry_enabled: false,
            remote_transport: "none".to_owned(),
            mode: "local_memory".to_owned(),
            max_retained_events: self.max_retained_events,
            max_retained_bytes: self.max_retained_bytes,
            retained_events: state.records.len(),
            retained_bytes: state.retained_bytes,
            retention_dropped: state.retention_dropped,
            retention_dropped_bytes: state.retention_dropped_bytes,
            failures: state.failures,
            closed: state.closed,
        }
    }

    pub fn close(&self) {
        let mut state = lock(&self.state);
        state.closed = true;
    }
}

fn safe_public_identifier(value: &str) -> String {
    if value.is_empty()
        || value.len() > MAX_IDENTIFIER_LENGTH
        || value.bytes().any(|b| b < 0x20 || b == 0x7f)
    {
        let digest =
            crate::sha256::sha256_hex(format!("zana-event-identifier-v1{value}").as_bytes());
        format!("redacted-{}", &digest[..16])
    } else {
        value.to_owned()
    }
}

fn lock<T>(mutex: &Mutex<T>) -> std::sync::MutexGuard<'_, T> {
    match mutex.lock() {
        Ok(guard) => guard,
        Err(poisoned) => poisoned.into_inner(),
    }
}

/// Bounded SQLite audit persistence for observability events.
pub struct AuditService;

impl AuditService {
    pub fn write(conn: &rusqlite::Connection, event: &Event) -> Result<String, String> {
        let limits = RedactionLimits::default();
        let line = serialize_event(event, &limits)?;
        let event_id = safe_public_identifier(&event.operation_id);
        AuditEvents::insert(conn, &event_id, &line, line.len() as i64)
            .map_err(|_| "audit event could not be persisted".to_owned())?;
        Ok(event_id)
    }

    pub fn page(
        conn: &rusqlite::Connection,
        limit: usize,
        before_sequence: Option<i64>,
    ) -> Result<Vec<crate::repositories::AuditEventRow>, String> {
        if !(1..=MAX_EVENT_PAGE_LIMIT).contains(&limit) {
            return Err("limit must be within the audit page cap".to_owned());
        }
        AuditEvents::page(conn, limit, before_sequence)
            .map_err(|_| "audit events could not be read".to_owned())
    }

    pub fn trim(conn: &rusqlite::Connection, retain_count: i64) -> Result<(), String> {
        if retain_count < 0 {
            return Err("retain_count must be non-negative".to_owned());
        }
        AuditEvents::trim_oldest(conn, retain_count)
            .map_err(|_| "audit events could not be trimmed".to_owned())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn event() -> Event {
        Event::new(EventKind::Job, Severity::Info, "hello", "op-1").expect("event")
    }

    #[test]
    fn redaction_hides_secrets_and_paths() {
        let limits = RedactionLimits::default();
        let value = serde_json::json!({
            "token": "secret",
            "path": "/Users/zana/secret/file.txt",
            "message": "safe",
        });
        let redacted = redact_value(&value, &limits);
        assert_eq!(redacted["token"], "***");
        assert_eq!(redacted["message"], "safe");
        assert!(redacted["path"]
            .as_str()
            .is_some_and(|s| !s.contains("Users")));
    }

    #[test]
    fn registry_bounds_retention_and_pages() {
        let registry =
            ObservabilityRegistry::new(5, 1 << 20, RedactionLimits::default()).expect("registry");
        for index in 0..10 {
            let mut event = event();
            event.operation_id = format!("op-{index}");
            assert!(registry.write(&event).ok);
        }
        assert_eq!(registry.health().retained_events, 5);
        let page = registry.events(3, None).expect("pages");
        assert_eq!(page.items.len(), 3);
        assert_eq!(page.items[0].sequence, 10);
        assert_eq!(page.next_cursor, Some(8));
        let older = registry.events(10, page.next_cursor).expect("pages");
        assert_eq!(older.items.len(), 2);
        assert_eq!(older.items[0].sequence, 7);
    }

    #[test]
    fn audit_persists_and_pages() {
        let dir = std::env::temp_dir().join(format!("zana-audit-test-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("db").join("zana.sqlite3");
        let database = crate::db::Database::open(path.clone()).expect("opens");
        database.migrate().expect("migrates");
        let conn = database.connect().expect("connects");
        AuditService::write(&conn, &event()).expect("writes");
        let page = AuditService::page(&conn, 10, None).expect("pages");
        assert_eq!(page.len(), 1);
        AuditService::trim(&conn, 0).expect("trims");
        assert!(AuditService::page(&conn, 10, None)
            .expect("pages")
            .is_empty());
        drop(conn);
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }
}
