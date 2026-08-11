//! Bounded native model acquisition planning, execution, and persistence.

use std::collections::VecDeque;
use std::path::PathBuf;
use std::sync::atomic::{AtomicUsize, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};

use rusqlite::Connection;

use crate::domain::{
    AcquisitionKind, AcquisitionPolicy, AcquisitionState, JobKind, JobStatus, RuntimeKind,
    RuntimeStatus,
};
use crate::jobs::{JobError, JobService};
use crate::repositories::{JobRow, Jobs, Runtimes};
use crate::runtimes::validate_origin;

pub const MAX_MODEL_REFERENCE_BYTES: usize = 200;
pub const MAX_LINE_BYTES: usize = 8 * 1024;
pub const MAX_TOTAL_EVENT_BYTES: usize = 64 * 1024;
pub const MAX_EVENT_COUNT: usize = 2_000;
pub const MAX_RETAINED_EVENTS: usize = 50;
pub const MAX_CONCURRENT_ACQUISITIONS: usize = 1;
pub const MAX_DEADLINE_SECONDS: f64 = 3600.0;
pub const MAX_PROGRESS_VALUE: i64 = 1 << 40;
pub const MAX_ADMISSION_HEADROOM: i64 = 1 << 40;
pub const MAX_ERROR_CODE_LENGTH: usize = 64;
pub const MAX_QUEUE: usize = 8;

#[derive(Debug, Clone)]
pub struct AcquisitionRequest {
    pub kind: AcquisitionKind,
    pub endpoint: String,
    pub model_reference: String,
    pub policy: AcquisitionPolicy,
    pub expected_size_bytes: Option<i64>,
    pub user_approved: bool,
    pub deadline_seconds: f64,
}

impl AcquisitionRequest {
    pub fn new(
        endpoint: &str,
        model_reference: &str,
        expected_size_bytes: Option<i64>,
        user_approved: bool,
        deadline_seconds: f64,
    ) -> Result<Self, String> {
        let reference = sanitize_model_reference(model_reference)?;
        let endpoint = validate_origin(endpoint, true)?;
        if expected_size_bytes.is_some_and(|value| !(1..=MAX_PROGRESS_VALUE).contains(&value)) {
            return Err("expected_size_bytes is out of range".to_owned());
        }
        if !deadline_seconds.is_finite()
            || !(0.0..=MAX_DEADLINE_SECONDS).contains(&deadline_seconds)
            || deadline_seconds <= 0.0
        {
            return Err("deadline_seconds is out of range".to_owned());
        }
        Ok(Self {
            kind: AcquisitionKind::OllamaPull,
            endpoint,
            model_reference: reference,
            policy: AcquisitionPolicy::LocalOnly,
            expected_size_bytes,
            user_approved,
            deadline_seconds,
        })
    }
}

#[derive(Debug, Clone)]
pub struct AdmissionResult {
    pub allowed: bool,
    pub reason: String,
    pub conservative_reserve_bytes: i64,
    pub explicit_user_approval: bool,
}

pub trait AdmissionProvider: Send + Sync {
    fn admit(&self, request: &AcquisitionRequest) -> AdmissionResult;
}

pub struct ConfigurableAdmission {
    pub reserve_bytes: i64,
    pub headroom_unknown: bool,
    pub headroom_bytes: Option<i64>,
}

impl AdmissionProvider for ConfigurableAdmission {
    fn admit(&self, request: &AcquisitionRequest) -> AdmissionResult {
        if !(0..=MAX_ADMISSION_HEADROOM).contains(&self.reserve_bytes)
            || self
                .headroom_bytes
                .is_some_and(|value| !(0..=MAX_ADMISSION_HEADROOM).contains(&value))
        {
            return AdmissionResult {
                allowed: false,
                reason: "INVALID_ADMISSION_CONFIG".to_owned(),
                conservative_reserve_bytes: 0,
                explicit_user_approval: request.user_approved,
            };
        }
        if self.headroom_unknown {
            return AdmissionResult {
                allowed: false,
                reason: "UNKNOWN_HEADROOM".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        let Some(headroom) = self.headroom_bytes else {
            return AdmissionResult {
                allowed: false,
                reason: "HEADROOM_UNAVAILABLE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        };
        let expected = request.expected_size_bytes;
        if expected.is_none() {
            if request.user_approved && headroom >= self.reserve_bytes {
                return AdmissionResult {
                    allowed: true,
                    reason: "UNKNOWN_SIZE_APPROVED_WITH_RESERVE".to_owned(),
                    conservative_reserve_bytes: self.reserve_bytes,
                    explicit_user_approval: true,
                };
            }
            return AdmissionResult {
                allowed: false,
                reason: "UNKNOWN_SIZE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        let Some(requirement) = expected.unwrap_or(0).checked_add(self.reserve_bytes) else {
            return AdmissionResult {
                allowed: false,
                reason: "ADMISSION_OVERFLOW".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        };
        if requirement > headroom {
            return AdmissionResult {
                allowed: false,
                reason: "DISK_INSUFFICIENT".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        AdmissionResult {
            allowed: true,
            reason: "ADMITTED".to_owned(),
            conservative_reserve_bytes: self.reserve_bytes,
            explicit_user_approval: request.user_approved,
        }
    }
}

/// Conservative live filesystem admission using the shared resource snapshot.
pub struct FilesystemAdmission {
    pub root: String,
    pub reserve_bytes: i64,
    pub active_bytes: Arc<dyn Fn() -> i64 + Send + Sync>,
    pub lease_conflict: Arc<dyn Fn() -> bool + Send + Sync>,
}

impl AdmissionProvider for FilesystemAdmission {
    fn admit(&self, request: &AcquisitionRequest) -> AdmissionResult {
        if !(0..=MAX_ADMISSION_HEADROOM).contains(&self.reserve_bytes) || self.root.is_empty() {
            return AdmissionResult {
                allowed: false,
                reason: "INVALID_ADMISSION_CONFIG".to_owned(),
                conservative_reserve_bytes: 0,
                explicit_user_approval: request.user_approved,
            };
        }
        if (self.lease_conflict)() {
            return AdmissionResult {
                allowed: false,
                reason: "LEASE_CONFLICT".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        let active = (self.active_bytes)();
        if !(0..=MAX_ADMISSION_HEADROOM).contains(&active) {
            return AdmissionResult {
                allowed: false,
                reason: "HEADROOM_UNAVAILABLE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        let Some(headroom) = crate::resources::capture_host_snapshot(&self.root, 0).disk_free_bytes
        else {
            return AdmissionResult {
                allowed: false,
                reason: "HEADROOM_UNAVAILABLE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        };
        let Some(expected) = request.expected_size_bytes else {
            return AdmissionResult {
                allowed: false,
                reason: "UNKNOWN_SIZE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        };
        if expected <= 0 {
            return AdmissionResult {
                allowed: false,
                reason: "INVALID_SIZE".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        let Some(requirement) = expected
            .checked_add(self.reserve_bytes)
            .and_then(|value| value.checked_add(active))
        else {
            return AdmissionResult {
                allowed: false,
                reason: "ADMISSION_OVERFLOW".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        };
        if requirement > headroom {
            return AdmissionResult {
                allowed: false,
                reason: "DISK_INSUFFICIENT".to_owned(),
                conservative_reserve_bytes: self.reserve_bytes,
                explicit_user_approval: request.user_approved,
            };
        }
        AdmissionResult {
            allowed: true,
            reason: "ADMITTED".to_owned(),
            conservative_reserve_bytes: self.reserve_bytes,
            explicit_user_approval: request.user_approved,
        }
    }
}

/// Conservative ASCII Ollama/HF-compatible model reference.
pub fn sanitize_model_reference(value: &str) -> Result<String, String> {
    if value != value.trim()
        || value.is_empty()
        || value.len() > MAX_MODEL_REFERENCE_BYTES
        || value.bytes().any(|byte| byte < 0x20 || byte == 0x7f)
        || value.contains('\0')
    {
        return Err("model_reference is invalid".to_owned());
    }
    let part = |segment: &str| {
        !segment.is_empty()
            && segment.len() <= MAX_MODEL_REFERENCE_BYTES
            && segment
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"._-".contains(&byte))
            && !segment.starts_with('.')
            && !segment.ends_with('.')
    };
    let (path, tag) = match value.split_once(':') {
        Some((path, tag)) => {
            if tag.is_empty() || !part(tag) {
                return Err("model_reference is invalid".to_owned());
            }
            (path, Some(tag))
        }
        None => (value, None),
    };
    if path.is_empty()
        || path
            .split('/')
            .any(|segment| !part(segment) || segment == "." || segment == "..")
    {
        return Err("model_reference is invalid".to_owned());
    }
    Ok(match tag {
        Some(tag) => format!("{path}:{tag}"),
        None => path.to_owned(),
    })
}

#[derive(Debug, Clone)]
pub struct AcquisitionPlan {
    pub kind: AcquisitionKind,
    pub endpoint: String,
    pub model_reference: String,
    pub body: String,
    pub approved: bool,
}

/// Build a native plan only when explicit user approval exists; never
/// performs HTTP or proxies model bytes.
pub fn plan_ollama_pull(request: &AcquisitionRequest) -> Result<AcquisitionPlan, String> {
    if !request.user_approved {
        return Err("Ollama pull requires explicit user approval.".to_owned());
    }
    let body = serde_json::json!({
        "model": request.model_reference,
        "stream": true,
    });
    Ok(AcquisitionPlan {
        kind: AcquisitionKind::OllamaPull,
        endpoint: request.endpoint.clone(),
        model_reference: request.model_reference.clone(),
        body: serde_json::to_string(&body)
            .map_err(|_| "pull body could not be built".to_owned())?,
        approved: true,
    })
}

#[derive(Debug, Clone)]
pub struct NativeProgress {
    pub sequence: i64,
    pub status: String,
    pub digest: Option<String>,
    pub total: Option<i64>,
    pub completed: Option<i64>,
    pub progress_0_1: Option<f64>,
    pub error: Option<String>,
}

#[derive(Debug, Clone)]
pub struct AcquisitionResult {
    pub state: AcquisitionState,
    pub events_consumed: i64,
    pub retained_events: Vec<NativeProgress>,
    pub error_code: Option<String>,
    pub error_message: Option<String>,
}

pub trait NativeStreamTransport: Send + Sync {
    fn open(
        &self,
        plan: &AcquisitionPlan,
        timeout: Duration,
    ) -> Result<Box<dyn LineSource>, String>;
    fn close(&self) -> Result<(), String>;
}

pub trait LineSource: Send {
    fn next_chunk(&mut self, remaining: Duration) -> Result<Option<Vec<u8>>, String>;
}

/// Bounded JSONL framer that lazily assembles lines from arbitrary chunks.
pub struct JsonlFramer {
    max_line_bytes: usize,
    max_total_bytes: usize,
    buffer: Vec<u8>,
    total_bytes: usize,
}

impl JsonlFramer {
    pub fn new(max_line_bytes: usize, max_total_bytes: usize) -> Self {
        Self {
            max_line_bytes,
            max_total_bytes,
            buffer: Vec::new(),
            total_bytes: 0,
        }
    }

    pub fn feed(&mut self, chunk: &[u8]) -> Result<Vec<String>, String> {
        if self.total_bytes.saturating_add(chunk.len()) > self.max_total_bytes {
            return Err("native stream exceeded the total raw event byte budget".to_owned());
        }
        self.total_bytes += chunk.len();
        self.buffer.extend_from_slice(chunk);
        let mut lines = Vec::new();
        while let Some(position) = self.buffer.iter().position(|byte| *byte == b'\n') {
            let mut raw = self.buffer.drain(..=position).collect::<Vec<_>>();
            raw.pop();
            if raw.ends_with(b"\r") {
                raw.pop();
            }
            if !raw.is_empty() && !raw.iter().all(u8::is_ascii_whitespace) {
                if raw.len() > self.max_line_bytes {
                    return Err("native stream line exceeds the byte cap".to_owned());
                }
                let text = String::from_utf8(raw)
                    .map_err(|_| "native stream line is not UTF-8".to_owned())?;
                lines.push(text);
            }
        }
        if self.buffer.len() > self.max_line_bytes {
            return Err("native stream unfinished tail exceeds the byte cap".to_owned());
        }
        Ok(lines)
    }

    pub fn finish(&mut self) -> Result<Vec<String>, String> {
        let mut raw = std::mem::take(&mut self.buffer);
        if raw.ends_with(b"\r") {
            raw.pop();
        }
        if raw.is_empty() || raw.iter().all(u8::is_ascii_whitespace) {
            return Ok(Vec::new());
        }
        if raw.len() > self.max_line_bytes {
            return Err("native stream unfinished tail exceeds the byte cap".to_owned());
        }
        let text =
            String::from_utf8(raw).map_err(|_| "native stream line is not UTF-8".to_owned())?;
        Ok(vec![text])
    }
}

pub struct NativeAcquisitionAdapter {
    pub max_event_count: usize,
    pub max_retained_events: usize,
    pub max_line_bytes: usize,
    pub max_total_event_bytes: usize,
}

impl NativeAcquisitionAdapter {
    pub fn run(
        &self,
        request: &AcquisitionRequest,
        transport: &dyn NativeStreamTransport,
        admitted: &AdmissionResult,
        cancel: &Arc<AtomicUsize>,
        deadline: Instant,
    ) -> AcquisitionResult {
        if !admitted.allowed {
            return AcquisitionResult {
                state: AcquisitionState::Failed,
                events_consumed: 0,
                retained_events: Vec::new(),
                error_code: Some("ADMISSION_DENIED".to_owned()),
                error_message: None,
            };
        }
        let plan = match plan_ollama_pull(request) {
            Ok(plan) => plan,
            Err(_message) => {
                return AcquisitionResult {
                    state: AcquisitionState::Failed,
                    events_consumed: 0,
                    retained_events: Vec::new(),
                    error_code: Some("PLAN_FAILED".to_owned()),
                    error_message: Some("Ollama pull planning failed.".to_owned()),
                };
            }
        };
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return AcquisitionResult {
                state: AcquisitionState::Failed,
                events_consumed: 0,
                retained_events: Vec::new(),
                error_code: Some("DEADLINE_EXCEEDED".to_owned()),
                error_message: Some("Acquisition deadline exceeded.".to_owned()),
            };
        }
        let mut source = match transport.open(&plan, remaining) {
            Ok(source) => source,
            Err(_message) => {
                return AcquisitionResult {
                    state: AcquisitionState::Failed,
                    events_consumed: 0,
                    retained_events: Vec::new(),
                    error_code: Some("TRANSPORT_FAILED".to_owned()),
                    error_message: Some("Native acquisition transport failed.".to_owned()),
                };
            }
        };
        let mut framer = JsonlFramer::new(self.max_line_bytes, self.max_total_event_bytes);
        let mut retained = Vec::new();
        let mut consumed = 0i64;
        let mut lines_seen = 0usize;
        let mut result: Option<AcquisitionResult> = loop {
            if cancel.load(Ordering::SeqCst) != 0 {
                break Some(AcquisitionResult {
                    state: AcquisitionState::Cancelled,
                    events_consumed: consumed,
                    retained_events: std::mem::take(&mut retained),
                    error_code: Some("CANCELLED".to_owned()),
                    error_message: None,
                });
            }
            let remaining = deadline.saturating_duration_since(Instant::now());
            if remaining.is_zero() {
                break Some(AcquisitionResult {
                    state: AcquisitionState::Failed,
                    events_consumed: consumed,
                    retained_events: std::mem::take(&mut retained),
                    error_code: Some("DEADLINE_EXCEEDED".to_owned()),
                    error_message: Some("Acquisition deadline exceeded.".to_owned()),
                });
            }
            let chunk = match source.next_chunk(remaining) {
                Ok(Some(chunk)) => chunk,
                Ok(None) => break None,
                Err(_) => {
                    break Some(AcquisitionResult {
                        state: AcquisitionState::Failed,
                        events_consumed: consumed,
                        retained_events: std::mem::take(&mut retained),
                        error_code: Some("TRANSPORT_FAILED".to_owned()),
                        error_message: Some("Native acquisition transport failed.".to_owned()),
                    });
                }
            };
            let lines = match framer.feed(&chunk) {
                Ok(lines) => lines,
                Err(_) => {
                    break Some(AcquisitionResult {
                        state: AcquisitionState::Failed,
                        events_consumed: consumed,
                        retained_events: std::mem::take(&mut retained),
                        error_code: Some("STREAM_OVER_BUDGET".to_owned()),
                        error_message: Some(
                            "Native stream exceeded bounded size limits.".to_owned(),
                        ),
                    });
                }
            };
            match self.process_lines(lines, &mut lines_seen, &mut consumed, &mut retained) {
                Ok(Some(result)) => break Some(result),
                Ok(None) => {}
                Err(code) => {
                    break Some(AcquisitionResult {
                        state: AcquisitionState::Failed,
                        events_consumed: consumed,
                        retained_events: std::mem::take(&mut retained),
                        error_code: Some(code),
                        error_message: Some(
                            "Native stream exceeded bounded event limits.".to_owned(),
                        ),
                    });
                }
            }
        };
        if result.is_none() {
            match framer.finish() {
                Ok(lines) => {
                    match self.process_lines(lines, &mut lines_seen, &mut consumed, &mut retained) {
                        Ok(Some(terminal)) => result = Some(terminal),
                        Ok(None) => {}
                        Err(code) => {
                            result = Some(AcquisitionResult {
                                state: AcquisitionState::Failed,
                                events_consumed: consumed,
                                retained_events: std::mem::take(&mut retained),
                                error_code: Some(code),
                                error_message: Some(
                                    "Native stream exceeded bounded event limits.".to_owned(),
                                ),
                            });
                        }
                    }
                }
                Err(_) => {
                    result = Some(AcquisitionResult {
                        state: AcquisitionState::Failed,
                        events_consumed: consumed,
                        retained_events: std::mem::take(&mut retained),
                        error_code: Some("STREAM_OVER_BUDGET".to_owned()),
                        error_message: Some(
                            "Native stream exceeded bounded size limits.".to_owned(),
                        ),
                    });
                }
            };
        }
        let mut result = result.unwrap_or(AcquisitionResult {
            state: AcquisitionState::Failed,
            events_consumed: consumed,
            retained_events: std::mem::take(&mut retained),
            error_code: Some("STREAM_ENDED_WITHOUT_SUCCESS".to_owned()),
            error_message: Some("Native stream ended without a success event.".to_owned()),
        });
        // Close must be attempted on every path after open; a close failure is
        // never silently converted into success.
        if transport.close().is_err() {
            result = AcquisitionResult {
                state: AcquisitionState::Failed,
                events_consumed: result.events_consumed,
                retained_events: result.retained_events,
                error_code: Some("TRANSPORT_CLOSE_FAILED".to_owned()),
                error_message: Some("Native transport cleanup failed.".to_owned()),
            };
        }
        result
    }

    fn process_lines(
        &self,
        lines: Vec<String>,
        lines_seen: &mut usize,
        consumed: &mut i64,
        retained: &mut Vec<NativeProgress>,
    ) -> Result<Option<AcquisitionResult>, String> {
        for line in lines {
            *lines_seen += 1;
            if *lines_seen > self.max_event_count {
                return Err("STREAM_EVENT_COUNT_EXCEEDED".to_owned());
            }
            match parse_progress_line(&line, *consumed + 1) {
                Ok(Some(progress)) => {
                    *consumed += 1;
                    retained.push(progress.clone());
                    if retained.len() > self.max_retained_events {
                        retained.remove(0);
                    }
                    if progress.status == "success" {
                        return Ok(Some(AcquisitionResult {
                            state: AcquisitionState::Succeeded,
                            events_consumed: *consumed,
                            retained_events: std::mem::take(retained),
                            error_code: None,
                            error_message: None,
                        }));
                    }
                }
                Ok(None) => {}
                Err(_) => {
                    return Err("STREAM_MALFORMED".to_owned());
                }
            }
        }
        Ok(None)
    }
}

fn parse_progress_line(line: &str, sequence: i64) -> Result<Option<NativeProgress>, String> {
    if sequence > MAX_EVENT_COUNT as i64 {
        return Err("native stream exceeded the event count cap".to_owned());
    }
    let payload: serde_json::Value = serde_json::from_str(line)
        .map_err(|_| "native stream line is not valid JSON".to_owned())?;
    let Some(object) = payload.as_object() else {
        return Err("native stream event is not an object".to_owned());
    };
    let Some(status) = object.get("status").and_then(serde_json::Value::as_str) else {
        return Ok(None);
    };
    let status = bounded_text(status, 256);
    let total = object.get("total").and_then(serde_json::Value::as_i64);
    let completed = object.get("completed").and_then(serde_json::Value::as_i64);
    if total.is_some_and(|value| !(0..=MAX_PROGRESS_VALUE).contains(&value))
        || completed.is_some_and(|value| !(0..=MAX_PROGRESS_VALUE).contains(&value))
        || (total.is_some() && completed.is_some() && completed > total)
    {
        return Err("native progress values are out of range".to_owned());
    }
    let progress_0_1 = match (total, completed) {
        (Some(total), Some(completed)) if total > 0 => {
            Some(((completed as f64) / (total as f64)).clamp(0.0, 1.0))
        }
        _ => None,
    };
    Ok(Some(NativeProgress {
        sequence,
        status,
        digest: object
            .get("digest")
            .and_then(serde_json::Value::as_str)
            .map(|value| bounded_text(value, 256)),
        total,
        completed,
        progress_0_1,
        error: object
            .get("error")
            .and_then(serde_json::Value::as_str)
            .map(|value| bounded_text(value, 256)),
    }))
}

pub fn bounded_text(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_owned();
    }
    let marker = "...[truncated]";
    if max_bytes == 0 {
        return String::new();
    }
    if max_bytes <= marker.len() {
        return marker[..max_bytes].to_owned();
    }
    let budget = max_bytes - marker.len();
    let bytes = value.as_bytes();
    let mut end = budget.min(bytes.len());
    while end > 0 && (bytes[end] & 0xC0) == 0x80 {
        end -= 1;
    }
    let mut result = String::with_capacity(end + marker.len());
    result.push_str(&value[..end]);
    result.push_str(marker);
    result
}

pub struct NonBlockingAcquisitionLock {
    permits: AtomicUsize,
}

impl NonBlockingAcquisitionLock {
    pub fn new(max_concurrent: usize) -> Self {
        Self {
            permits: AtomicUsize::new(max_concurrent),
        }
    }

    pub fn try_acquire(&self) -> bool {
        let mut current = self.permits.load(Ordering::SeqCst);
        loop {
            if current == 0 {
                return false;
            }
            match self.permits.compare_exchange(
                current,
                current - 1,
                Ordering::SeqCst,
                Ordering::SeqCst,
            ) {
                Ok(_) => return true,
                Err(observed) => current = observed,
            }
        }
    }

    pub fn release(&self) {
        self.permits.fetch_add(1, Ordering::SeqCst);
    }
}

pub struct AcquisitionService {
    pub lock: Arc<NonBlockingAcquisitionLock>,
    pub clock: Arc<dyn Fn() -> Instant + Send + Sync>,
}

impl AcquisitionService {
    pub fn run(
        &self,
        request: &AcquisitionRequest,
        transport: &dyn NativeStreamTransport,
        admission: &dyn AdmissionProvider,
        cancel: &Arc<AtomicUsize>,
    ) -> Result<AcquisitionResult, String> {
        if !self.lock.try_acquire() {
            return Err("Concurrent acquisition cap reached.".to_owned());
        }
        let result = {
            let admitted = admission.admit(request);
            let deadline = (self.clock)() + Duration::from_secs_f64(request.deadline_seconds);
            NativeAcquisitionAdapter {
                max_event_count: MAX_EVENT_COUNT,
                max_retained_events: MAX_RETAINED_EVENTS,
                max_line_bytes: MAX_LINE_BYTES,
                max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
            }
            .run(request, transport, &admitted, cancel, deadline)
        };
        self.lock.release();
        Ok(result)
    }
}

/// Persisted bounded supervisor for queued model-pull jobs.
///
/// The supervisor is intentionally synchronous: callers invoke `drain_once`
/// explicitly and no background worker thread exists. This preserves the
/// bounded no-background-thread policy of the operational core while still
/// providing a bounded FIFO, per-job cancellation tokens, and deterministic
/// shutdown/restart truth.
pub struct AcquisitionSupervisor {
    db_path: PathBuf,
    transport: Arc<dyn NativeStreamTransport>,
    admission: Arc<dyn AdmissionProvider>,
    queue: Mutex<VecDeque<i64>>,
    tokens: Mutex<std::collections::HashMap<i64, Arc<AtomicUsize>>>,
    max_queue: usize,
    stop: std::sync::atomic::AtomicBool,
}

impl AcquisitionSupervisor {
    pub fn new(
        db_path: PathBuf,
        transport: Arc<dyn NativeStreamTransport>,
        admission: Arc<dyn AdmissionProvider>,
        max_queue: usize,
    ) -> Result<Self, String> {
        if !(1..=MAX_QUEUE).contains(&max_queue) {
            return Err("max_queue is out of range".to_owned());
        }
        Ok(Self {
            db_path,
            transport,
            admission,
            queue: Mutex::new(VecDeque::new()),
            tokens: Mutex::new(std::collections::HashMap::new()),
            max_queue,
            stop: std::sync::atomic::AtomicBool::new(false),
        })
    }

    pub fn dispatch(&self, job_id: i64) -> Result<(), String> {
        if self.stop.load(Ordering::SeqCst) {
            return Err("acquisition supervisor is shutting down".to_owned());
        }
        let mut queue = self
            .queue
            .lock()
            .map_err(|_| "acquisition queue is unavailable".to_owned())?;
        let mut tokens = self
            .tokens
            .lock()
            .map_err(|_| "acquisition tokens are unavailable".to_owned())?;
        if tokens.contains_key(&job_id) {
            return Err("job is already queued or running".to_owned());
        }
        if tokens.len() >= self.max_queue {
            return Err("acquisition queue is full".to_owned());
        }
        queue.push_back(job_id);
        tokens.insert(job_id, Arc::new(AtomicUsize::new(0)));
        Ok(())
    }

    pub fn cancel(&self, job_id: i64) -> bool {
        let Ok(tokens) = self.tokens.lock() else {
            return false;
        };
        match tokens.get(&job_id) {
            Some(token) => {
                token.store(1, Ordering::SeqCst);
                true
            }
            None => false,
        }
    }

    pub fn drain_once(&self) -> Result<(), String> {
        let job_id = {
            let mut queue = self
                .queue
                .lock()
                .map_err(|_| "acquisition queue is unavailable".to_owned())?;
            queue.pop_front()
        };
        let Some(job_id) = job_id else {
            return Ok(());
        };
        let token = self
            .tokens
            .lock()
            .map_err(|_| "acquisition tokens are unavailable".to_owned())?
            .get(&job_id)
            .cloned()
            .ok_or_else(|| "queued job has no cancellation token".to_owned())?;
        let mut worker_conn = crate::db::open_connection(&self.db_path)
            .map_err(|_| "acquisition database could not be opened".to_owned())?;
        let result = execute_persisted_pull(
            &mut worker_conn,
            job_id,
            self.transport.as_ref(),
            self.admission.as_ref(),
            &token,
        );
        if let Err(code) = result {
            mark_pull_failed(
                &mut worker_conn,
                job_id,
                &code,
                "Model acquisition could not be executed.",
            )
            .map_err(|_| "acquisition failure could not be persisted".to_owned())?;
        }
        if let Ok(mut tokens) = self.tokens.lock() {
            tokens.remove(&job_id);
        }
        Ok(())
    }

    pub fn pending_count(&self) -> usize {
        self.queue.lock().map(|queue| queue.len()).unwrap_or(0)
    }

    pub fn shutdown(&self) -> Result<(), String> {
        self.stop.store(true, Ordering::SeqCst);
        let queued = self
            .queue
            .lock()
            .map_err(|_| "acquisition queue is unavailable".to_owned())?
            .drain(..)
            .collect::<Vec<_>>();
        let mut tokens = self
            .tokens
            .lock()
            .map_err(|_| "acquisition tokens are unavailable".to_owned())?;
        for token in tokens.values() {
            token.store(1, Ordering::SeqCst);
        }
        let mut conn = crate::db::open_connection(&self.db_path)
            .map_err(|_| "acquisition database could not be opened".to_owned())?;
        for job_id in queued {
            let error = serde_json::json!({
                "code": "INTERRUPTED_ON_SHUTDOWN",
                "message": "Model acquisition was interrupted by shutdown.",
                "recoverable": true,
                "actions": ["retry_pull"],
            });
            JobService::transition_job(
                &mut conn,
                job_id,
                JobStatus::Failed,
                Some("interrupted"),
                Some("Model acquisition was interrupted by shutdown."),
                None,
                Some(&error),
            )
            .map_err(|_| "interrupted-job persistence failed".to_owned())?;
            tokens.remove(&job_id);
        }
        Ok(())
    }
}

/// Load, validate, run, and persist one queued model-pull job.
pub fn execute_persisted_pull(
    conn: &mut Connection,
    job_id: i64,
    transport: &dyn NativeStreamTransport,
    admission: &dyn AdmissionProvider,
    cancel: &Arc<AtomicUsize>,
) -> Result<(), String> {
    let job = Jobs::get(conn, job_id)
        .map_err(|_| "job could not be loaded".to_owned())?
        .ok_or_else(|| "job not found".to_owned())?;
    if job.kind != JobKind::ModelPull {
        return Err("job is not a model pull".to_owned());
    }
    if job.status.is_terminal() {
        return Ok(());
    }
    let request = persisted_request(conn, &job)?;
    if !request.user_approved {
        return Err("USER_APPROVAL_REQUIRED".to_owned());
    }
    JobService::transition_job(
        conn,
        job_id,
        JobStatus::Running,
        Some("downloading"),
        Some("Model acquisition started."),
        Some(0.0),
        None,
    )
    .map_err(|_| "job could not be started".to_owned())?;
    let admitted = admission.admit(&request);
    let deadline = Instant::now() + Duration::from_secs_f64(request.deadline_seconds);
    let result = NativeAcquisitionAdapter {
        max_event_count: MAX_EVENT_COUNT,
        max_retained_events: MAX_RETAINED_EVENTS,
        max_line_bytes: MAX_LINE_BYTES,
        max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
    }
    .run(&request, transport, &admitted, cancel, deadline);
    for progress in &result.retained_events {
        let value = progress
            .progress_0_1
            .map(|value| value.min(0.99))
            .unwrap_or(0.0);
        if JobService::record_progress(conn, job_id, value, "downloading", &progress.status)
            .is_err()
        {
            let error = serde_json::json!({
                "code": "PROGRESS_PERSISTENCE_FAILED",
                "message": "Native progress could not be persisted.",
                "recoverable": true,
                "actions": ["retry_pull"],
            });
            let _ = JobService::transition_job(
                conn,
                job_id,
                JobStatus::Failed,
                Some("failed"),
                Some("Native progress could not be persisted."),
                None,
                Some(&error),
            );
            return Err("PROGRESS_PERSISTENCE_FAILED".to_owned());
        }
    }
    match result.state {
        AcquisitionState::Succeeded => {
            let message = serde_json::json!({
                "code": "ACQUISITION_COMPLETED",
                "message": "Model acquisition reported completion by the runtime.",
            });
            JobService::transition_job(
                conn,
                job_id,
                JobStatus::Succeeded,
                Some("complete"),
                Some("Model acquisition complete."),
                Some(1.0),
                Some(&message),
            )
            .map_err(|_| "success could not be persisted".to_owned())?;
            Ok(())
        }
        AcquisitionState::Cancelled => {
            let message = serde_json::json!({
                "code": "CANCELLED",
                "message": "Model acquisition cancelled.",
            });
            JobService::transition_job(
                conn,
                job_id,
                JobStatus::Cancelled,
                Some("cancelled"),
                Some("Model acquisition cancelled."),
                None,
                Some(&message),
            )
            .map_err(|_| "cancellation could not be persisted".to_owned())?;
            Ok(())
        }
        _ => Err(result
            .error_code
            .unwrap_or_else(|| "ACQUISITION_FAILED".to_owned())),
    }
}

fn persisted_request(conn: &Connection, job: &JobRow) -> Result<AcquisitionRequest, String> {
    let raw = job
        .error_json
        .as_ref()
        .ok_or_else(|| "persisted request is missing".to_owned())?;
    let runtime_id = raw.get("runtime_id").and_then(serde_json::Value::as_i64);
    let model_reference = raw
        .get("model_reference")
        .and_then(serde_json::Value::as_str);
    let expected_size_bytes = raw
        .get("expected_size_bytes")
        .and_then(serde_json::Value::as_i64);
    let user_approved = raw
        .get("user_approved")
        .and_then(serde_json::Value::as_bool);
    let deadline_seconds = raw
        .get("deadline_seconds")
        .and_then(serde_json::Value::as_f64);
    let stored_identity = raw
        .get("runtime_identity")
        .and_then(serde_json::Value::as_str);
    if runtime_id.is_none()
        || model_reference.is_none()
        || user_approved.is_none()
        || deadline_seconds.is_none()
        || stored_identity.is_none()
    {
        return Err("INVALID_PERSISTED_REQUEST".to_owned());
    }
    let runtime = Runtimes::get(conn, runtime_id.unwrap_or_default())
        .map_err(|_| "INVALID_PERSISTED_REQUEST".to_owned())?
        .ok_or_else(|| "RUNTIME_CHANGED".to_owned())?;
    let identity = crate::sha256::sha256_hex(
        format!(
            "{}|{}|{}",
            runtime.kind.as_str(),
            runtime.endpoint,
            runtime.source.as_str()
        )
        .as_bytes(),
    );
    if runtime.kind != RuntimeKind::Ollama
        || runtime.status != RuntimeStatus::Online
        || identity != stored_identity.unwrap_or_default()
    {
        return Err("RUNTIME_CHANGED".to_owned());
    }
    AcquisitionRequest::new(
        &runtime.endpoint,
        model_reference.unwrap_or_default(),
        expected_size_bytes,
        user_approved.unwrap_or(false),
        deadline_seconds.unwrap_or(30.0),
    )
}

fn mark_pull_failed(
    conn: &mut Connection,
    job_id: i64,
    code: &str,
    message: &str,
) -> Result<(), JobError> {
    let error = serde_json::json!({
        "code": bounded_text(code, MAX_ERROR_CODE_LENGTH),
        "message": bounded_text(message, 256),
        "recoverable": true,
        "actions": ["retry_pull"],
    });
    JobService::transition_job(
        conn,
        job_id,
        JobStatus::Failed,
        Some("failed"),
        Some(message),
        None,
        Some(&error),
    )
    .map(|_| ())
}

/// Mark stale PENDING/RUNNING model pulls interrupted on restart; never
/// auto-resumes.
pub fn recover_interrupted_pulls(conn: &mut Connection) -> Result<i64, JobError> {
    let active = Jobs::list_active(conn).map_err(|_| JobError::Storage)?;
    let mut recovered = 0;
    for job in active {
        if job.kind != JobKind::ModelPull {
            continue;
        }
        let error = serde_json::json!({
            "code": "INTERRUPTED_ON_RESTART",
            "message": "Model acquisition was interrupted by a restart.",
            "recoverable": true,
            "actions": ["retry_pull"],
        });
        match JobService::transition_job(
            conn,
            job.id,
            JobStatus::Failed,
            Some("interrupted"),
            Some("Model acquisition was interrupted by a restart."),
            None,
            Some(&error),
        ) {
            Ok(_) => recovered += 1,
            Err(JobError::InvalidTransition(_)) => {}
            Err(_) => return Err(JobError::Storage),
        }
    }
    Ok(recovered)
}

/// Persist a bounded acquisition-queue payload without endpoints/secrets.
#[allow(clippy::too_many_arguments)]
pub fn sanitize_job_payload(
    runtime_id: i64,
    model_reference: &str,
    expected_size_bytes: Option<i64>,
    user_approved: bool,
    deadline_seconds: f64,
    runtime_kind: &str,
    runtime_source: &str,
    runtime_status: &str,
    runtime_identity: &str,
) -> Result<serde_json::Value, String> {
    if runtime_id <= 0 {
        return Err("runtime_id must be a positive int".to_owned());
    }
    if expected_size_bytes.is_some_and(|value| !(1..=MAX_PROGRESS_VALUE).contains(&value)) {
        return Err("expected_size_bytes is out of range".to_owned());
    }
    if !deadline_seconds.is_finite()
        || !(0.0..=MAX_DEADLINE_SECONDS).contains(&deadline_seconds)
        || deadline_seconds <= 0.0
    {
        return Err("deadline_seconds is out of range".to_owned());
    }
    if !is_hex64(runtime_identity) {
        return Err("runtime_identity must be a 64-character hexadecimal digest".to_owned());
    }
    let reference = sanitize_model_reference(model_reference)?;
    Ok(serde_json::json!({
        "code": "ACQUISITION_QUEUED",
        "message": "Native model acquisition queued.",
        "runtime_id": runtime_id,
        "runtime_kind": bounded_text(runtime_kind, 24),
        "runtime_source": bounded_text(runtime_source, 16),
        "runtime_status": bounded_text(runtime_status, 16),
        "runtime_identity": bounded_text(runtime_identity, 64),
        "model_reference": reference,
        "expected_size_bytes": expected_size_bytes,
        "user_approved": user_approved,
        "deadline_seconds": deadline_seconds,
    }))
}

fn is_hex64(value: &str) -> bool {
    value.len() == 64 && value.bytes().all(|byte| byte.is_ascii_hexdigit())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::domain::RuntimeSource;
    use crate::repositories::JobEvents;

    struct FakeChunks {
        chunks: Vec<Vec<u8>>,
        index: usize,
    }

    impl LineSource for FakeChunks {
        fn next_chunk(&mut self, _remaining: Duration) -> Result<Option<Vec<u8>>, String> {
            if self.index >= self.chunks.len() {
                return Ok(None);
            }
            let chunk = self.chunks[self.index].clone();
            self.index += 1;
            Ok(Some(chunk))
        }
    }

    struct FakeTransport {
        chunks: Vec<Vec<u8>>,
        close_calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
        close_error: bool,
    }

    impl NativeStreamTransport for FakeTransport {
        fn open(
            &self,
            _plan: &AcquisitionPlan,
            _timeout: Duration,
        ) -> Result<Box<dyn LineSource>, String> {
            Ok(Box::new(FakeChunks {
                chunks: self.chunks.clone(),
                index: 0,
            }))
        }

        fn close(&self) -> Result<(), String> {
            self.close_calls
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            if self.close_error {
                Err("close failed".to_owned())
            } else {
                Ok(())
            }
        }
    }

    fn request(user_approved: bool) -> AcquisitionRequest {
        AcquisitionRequest::new(
            "http://127.0.0.1:11434",
            "zephyr:7b",
            Some(1 << 20),
            user_approved,
            30.0,
        )
        .expect("request builds")
    }

    fn db(name: &str) -> (crate::db::Database, std::path::PathBuf) {
        let dir = std::env::temp_dir().join(format!(
            "zana-acquisition-test-{}-{}",
            std::process::id(),
            name
        ));
        let _ = std::fs::remove_dir_all(&dir);
        let path = dir.join("db").join("zana.sqlite3");
        let database = crate::db::Database::open(path.clone()).expect("opens");
        database.migrate().expect("migrates");
        (database, dir)
    }

    #[test]
    fn model_reference_validation_rejects_traversal_and_controls() {
        assert_eq!(
            sanitize_model_reference("zephyr:7b").expect("valid"),
            "zephyr:7b"
        );
        assert!(sanitize_model_reference("../zephyr").is_err());
        assert!(sanitize_model_reference("a b").is_err());
        assert!(sanitize_model_reference("a\nb").is_err());
        assert!(sanitize_model_reference(&"x".repeat(201)).is_err());
    }

    #[test]
    fn approval_and_admission_gate_plan() {
        let unapproved = request(false);
        assert!(plan_ollama_pull(&unapproved).is_err());
        let approved = request(true);
        let plan = plan_ollama_pull(&approved).expect("plans");
        assert_eq!(plan.endpoint, "http://127.0.0.1:11434");
        assert_eq!(plan.model_reference, "zephyr:7b");
    }

    #[test]
    fn framer_bounds_lines_and_total_bytes() {
        let mut framer = JsonlFramer::new(16, 64);
        let lines = framer
            .feed(
                br#"{"status":"one"}
{"status":"two"}"#,
            )
            .expect("feeds");
        assert_eq!(lines.len(), 1);
        assert_eq!(framer.finish().expect("finishes").len(), 1);
        assert!(JsonlFramer::new(4, 64)
            .feed(br#"{"status":"toolong"}"#)
            .is_err());
    }

    #[test]
    fn adapter_succeeds_on_terminal_and_cancels() {
        let transport = FakeTransport {
            chunks: vec![br#"{"status":"downloading","total":100,"completed":50}
{"status":"success","digest":"sha256:abc"}"#
                .to_vec()],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: false,
        };
        let admitted = AdmissionResult {
            allowed: true,
            reason: "ADMITTED".to_owned(),
            conservative_reserve_bytes: 0,
            explicit_user_approval: true,
        };
        let cancel = Arc::new(AtomicUsize::new(0));
        let result = NativeAcquisitionAdapter {
            max_event_count: MAX_EVENT_COUNT,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &transport,
            &admitted,
            &cancel,
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Succeeded);
        assert_eq!(result.events_consumed, 2);
        assert_eq!(result.retained_events[0].progress_0_1, Some(0.5));
        assert_eq!(
            transport
                .close_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            1
        );

        cancel.store(1, Ordering::SeqCst);
        let result = NativeAcquisitionAdapter {
            max_event_count: MAX_EVENT_COUNT,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &transport,
            &admitted,
            &cancel,
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Cancelled);
    }

    #[test]
    fn lock_is_non_blocking_and_bounded() {
        let lock = NonBlockingAcquisitionLock::new(1);
        assert!(lock.try_acquire());
        assert!(!lock.try_acquire());
        lock.release();
        assert!(lock.try_acquire());
    }

    #[test]
    fn persisted_pull_honors_identity_approval_and_terminal() {
        let (database, dir) = db("persisted");
        let mut conn = database.connect().expect("connects");
        let runtime_id = Runtimes::insert(
            &conn,
            RuntimeKind::Ollama,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            RuntimeStatus::Online,
            &serde_json::json!({}),
            &crate::time::now_iso(),
        )
        .expect("runtime");
        let identity = crate::sha256::sha256_hex(
            format!(
                "{}|{}|{}",
                RuntimeKind::Ollama.as_str(),
                "http://127.0.0.1:11434",
                RuntimeSource::Auto.as_str()
            )
            .as_bytes(),
        );
        let payload = sanitize_job_payload(
            runtime_id,
            "zephyr:7b",
            Some(1 << 20),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .expect("payload");
        let job = JobService::create_job(&mut conn, JobKind::ModelPull, "queued", "zephyr:7b")
            .expect("creates");
        let _ = JobEvents::insert(
            &conn,
            job.id,
            "STATUS_CHANGED",
            "queued",
            "",
            0.0,
            Some(&payload),
        );
        Jobs::update(
            &conn,
            job.id,
            JobStatus::Pending,
            JobStatus::Pending,
            "queued",
            "zephyr:7b",
            0.0,
            Some(&payload),
        )
        .expect("stores payload");
        let transport = FakeTransport {
            chunks: vec![br#"{"status":"success"}"#.to_vec()],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: false,
        };
        let admission = ConfigurableAdmission {
            reserve_bytes: 0,
            headroom_unknown: false,
            headroom_bytes: Some(1 << 30),
        };
        let cancel = Arc::new(AtomicUsize::new(0));
        execute_persisted_pull(&mut conn, job.id, &transport, &admission, &cancel)
            .expect("pull succeeds");
        let done = Jobs::get(&conn, job.id).expect("reads").expect("exists");
        assert_eq!(done.status, JobStatus::Succeeded);
        drop(conn);
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn recovery_marks_interrupted_pulls_only() {
        let (database, dir) = db("recovery");
        let mut conn = database.connect().expect("connects");
        let pull =
            JobService::create_job(&mut conn, JobKind::ModelPull, "queued", "").expect("pull");
        let refresh =
            JobService::create_job(&mut conn, JobKind::RuntimeRefresh, "", "").expect("refresh");
        let recovered = recover_interrupted_pulls(&mut conn).expect("recovers");
        assert_eq!(recovered, 1);
        assert_eq!(
            Jobs::get(&conn, pull.id)
                .expect("reads")
                .expect("exists")
                .status,
            JobStatus::Failed
        );
        assert_eq!(
            Jobs::get(&conn, refresh.id)
                .expect("reads")
                .expect("exists")
                .status,
            JobStatus::Pending
        );
        drop(conn);
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn supervisor_dispatch_drain_and_cancel() {
        let (database, dir) = db("supervisor");
        let mut setup = database.connect().expect("connects");
        let runtime_id = Runtimes::insert(
            &setup,
            RuntimeKind::Ollama,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            RuntimeStatus::Online,
            &serde_json::json!({}),
            &crate::time::now_iso(),
        )
        .expect("runtime");
        let identity = crate::sha256::sha256_hex(
            format!(
                "{}|{}|{}",
                RuntimeKind::Ollama.as_str(),
                "http://127.0.0.1:11434",
                RuntimeSource::Auto.as_str()
            )
            .as_bytes(),
        );
        let payload = sanitize_job_payload(
            runtime_id,
            "zephyr:7b",
            Some(1 << 20),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .expect("payload");
        let job = JobService::create_job(&mut setup, JobKind::ModelPull, "queued", "zephyr:7b")
            .expect("creates");
        Jobs::update(
            &setup,
            job.id,
            JobStatus::Pending,
            JobStatus::Pending,
            "queued",
            "zephyr:7b",
            0.0,
            Some(&payload),
        )
        .expect("stores payload");
        drop(setup);
        let supervisor = AcquisitionSupervisor::new(
            database.path.clone(),
            Arc::new(FakeTransport {
                chunks: vec![br#"{"status":"success"}"#.to_vec()],
                close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                close_error: false,
            }),
            Arc::new(ConfigurableAdmission {
                reserve_bytes: 0,
                headroom_unknown: false,
                headroom_bytes: Some(1 << 30),
            }),
            2,
        )
        .expect("supervisor");
        supervisor.dispatch(job.id).expect("dispatches");
        supervisor.drain_once().expect("drains");
        let conn = database.connect().expect("connects");
        assert_eq!(
            Jobs::get(&conn, job.id)
                .expect("reads")
                .expect("exists")
                .status,
            JobStatus::Succeeded
        );
        assert!(!supervisor.cancel(job.id));
        drop(conn);
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn supervisor_shutdown_marks_queued_interrupted() {
        let (database, dir) = db("shutdown");
        let mut setup = database.connect().expect("connects");
        let runtime_id = Runtimes::insert(
            &setup,
            RuntimeKind::Ollama,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            RuntimeStatus::Online,
            &serde_json::json!({}),
            &crate::time::now_iso(),
        )
        .expect("runtime");
        let identity = crate::sha256::sha256_hex(
            format!(
                "{}|{}|{}",
                RuntimeKind::Ollama.as_str(),
                "http://127.0.0.1:11434",
                RuntimeSource::Auto.as_str()
            )
            .as_bytes(),
        );
        let payload = sanitize_job_payload(
            runtime_id,
            "zephyr:7b",
            Some(1 << 20),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .expect("payload");
        let job = JobService::create_job(&mut setup, JobKind::ModelPull, "queued", "zephyr:7b")
            .expect("creates");
        Jobs::update(
            &setup,
            job.id,
            JobStatus::Pending,
            JobStatus::Pending,
            "queued",
            "zephyr:7b",
            0.0,
            Some(&payload),
        )
        .expect("stores payload");
        drop(setup);
        let supervisor = AcquisitionSupervisor::new(
            database.path.clone(),
            Arc::new(FakeTransport {
                chunks: vec![],
                close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                close_error: false,
            }),
            Arc::new(ConfigurableAdmission {
                reserve_bytes: 0,
                headroom_unknown: false,
                headroom_bytes: Some(1 << 30),
            }),
            2,
        )
        .expect("supervisor");
        supervisor.dispatch(job.id).expect("dispatches");
        supervisor.shutdown().expect("shuts down");
        let conn = database.connect().expect("connects");
        assert_eq!(
            Jobs::get(&conn, job.id)
                .expect("reads")
                .expect("exists")
                .status,
            JobStatus::Failed
        );
        drop(conn);
        database.close();
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn adapter_enforces_line_total_and_event_bounds() {
        let admitted = AdmissionResult {
            allowed: true,
            reason: "ADMITTED".to_owned(),
            conservative_reserve_bytes: 0,
            explicit_user_approval: true,
        };
        let cancel = Arc::new(AtomicUsize::new(0));

        let oversized = FakeTransport {
            chunks: vec![vec![b'x'; MAX_LINE_BYTES + 1]],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: false,
        };
        let result = NativeAcquisitionAdapter {
            max_event_count: MAX_EVENT_COUNT,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &oversized,
            &admitted,
            &cancel,
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Failed);
        assert_eq!(result.error_code.as_deref(), Some("STREAM_OVER_BUDGET"));
        assert_eq!(
            oversized
                .close_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            1
        );

        let cumulative = FakeTransport {
            chunks: vec![
                vec![b'{'; MAX_TOTAL_EVENT_BYTES / 2],
                vec![b'}'; MAX_TOTAL_EVENT_BYTES / 2 + 1],
            ],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: false,
        };
        let result = NativeAcquisitionAdapter {
            max_event_count: MAX_EVENT_COUNT,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &cumulative,
            &admitted,
            &cancel,
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Failed);
        assert_eq!(result.error_code.as_deref(), Some("STREAM_OVER_BUDGET"));

        let many_statusless = FakeTransport {
            chunks: vec![(0..4000)
                .map(|_| "{\"status\":null}\n")
                .collect::<String>()
                .into_bytes()],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: false,
        };
        let result = NativeAcquisitionAdapter {
            max_event_count: 3,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &many_statusless,
            &admitted,
            &cancel,
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Failed);
        assert_eq!(
            result.error_code.as_deref(),
            Some("STREAM_EVENT_COUNT_EXCEEDED")
        );
    }

    #[test]
    fn admission_rejects_invalid_config_and_overflow() {
        assert!(AcquisitionRequest::new(
            "http://127.0.0.1:11434",
            "zephyr:7b",
            Some(0),
            true,
            30.0,
        )
        .is_err());
        assert!(
            AcquisitionRequest::new("http://127.0.0.1:11434", "zephyr:7b", Some(1), true, 0.0,)
                .is_err()
        );

        let config = ConfigurableAdmission {
            reserve_bytes: -1,
            headroom_unknown: false,
            headroom_bytes: Some(1 << 30),
        };
        assert_eq!(
            config.admit(&request(true)).reason,
            "INVALID_ADMISSION_CONFIG"
        );
        let overflow = ConfigurableAdmission {
            reserve_bytes: MAX_ADMISSION_HEADROOM,
            headroom_unknown: false,
            headroom_bytes: Some(MAX_ADMISSION_HEADROOM),
        };
        let mut overflow_request = request(true);
        overflow_request.expected_size_bytes = Some(i64::MAX);
        assert_eq!(
            overflow.admit(&overflow_request).reason,
            "ADMISSION_OVERFLOW"
        );

        let filesystem = FilesystemAdmission {
            root: std::env::temp_dir().to_string_lossy().into_owned(),
            reserve_bytes: -1,
            active_bytes: Arc::new(|| 0),
            lease_conflict: Arc::new(|| false),
        };
        assert_eq!(
            filesystem.admit(&request(true)).reason,
            "INVALID_ADMISSION_CONFIG"
        );
        let filesystem_overflow = FilesystemAdmission {
            root: std::env::temp_dir().to_string_lossy().into_owned(),
            reserve_bytes: MAX_ADMISSION_HEADROOM,
            active_bytes: Arc::new(|| 0),
            lease_conflict: Arc::new(|| false),
        };
        assert_eq!(
            filesystem_overflow.admit(&overflow_request).reason,
            "ADMISSION_OVERFLOW"
        );
    }

    #[test]
    fn close_failure_never_converts_to_success() {
        let transport = FakeTransport {
            chunks: vec![br#"{"status":"success"}"#.to_vec()],
            close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            close_error: true,
        };
        let admitted = AdmissionResult {
            allowed: true,
            reason: "ADMITTED".to_owned(),
            conservative_reserve_bytes: 0,
            explicit_user_approval: true,
        };
        let result = NativeAcquisitionAdapter {
            max_event_count: MAX_EVENT_COUNT,
            max_retained_events: MAX_RETAINED_EVENTS,
            max_line_bytes: MAX_LINE_BYTES,
            max_total_event_bytes: MAX_TOTAL_EVENT_BYTES,
        }
        .run(
            &request(true),
            &transport,
            &admitted,
            &Arc::new(AtomicUsize::new(0)),
            Instant::now() + Duration::from_secs(30),
        );
        assert_eq!(result.state, AcquisitionState::Failed);
        assert_eq!(result.error_code.as_deref(), Some("TRANSPORT_CLOSE_FAILED"));
        assert_eq!(
            transport
                .close_calls
                .load(std::sync::atomic::Ordering::SeqCst),
            1
        );
    }

    #[test]
    fn supervisor_queue_is_fifo() {
        let (database, dir) = db("fifo");
        let mut setup = database.connect().expect("connects");
        let runtime_id = Runtimes::insert(
            &setup,
            RuntimeKind::Ollama,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            RuntimeStatus::Online,
            &serde_json::json!({}),
            &crate::time::now_iso(),
        )
        .expect("runtime");
        let identity = crate::sha256::sha256_hex(
            format!(
                "{}|{}|{}",
                RuntimeKind::Ollama.as_str(),
                "http://127.0.0.1:11434",
                RuntimeSource::Auto.as_str()
            )
            .as_bytes(),
        );
        let mut ids = Vec::new();
        for index in 0..2 {
            let payload = sanitize_job_payload(
                runtime_id,
                &format!("zephyr:{index}b"),
                Some(1 << 20),
                true,
                30.0,
                "ollama",
                "auto",
                "online",
                &identity,
            )
            .expect("payload");
            let job = JobService::create_job(
                &mut setup,
                JobKind::ModelPull,
                "queued",
                &format!("zephyr:{index}b"),
            )
            .expect("creates");
            Jobs::update(
                &setup,
                job.id,
                JobStatus::Pending,
                JobStatus::Pending,
                "queued",
                &format!("zephyr:{index}b"),
                0.0,
                Some(&payload),
            )
            .expect("stores payload");
            ids.push(job.id);
        }
        drop(setup);
        let supervisor = AcquisitionSupervisor::new(
            database.path.clone(),
            Arc::new(FakeTransport {
                chunks: vec![br#"{"status":"success"}"#.to_vec()],
                close_calls: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                close_error: false,
            }),
            Arc::new(ConfigurableAdmission {
                reserve_bytes: 0,
                headroom_unknown: false,
                headroom_bytes: Some(1 << 30),
            }),
            2,
        )
        .expect("supervisor");
        supervisor.dispatch(ids[0]).expect("first");
        supervisor.dispatch(ids[1]).expect("second");
        supervisor.drain_once().expect("first drains");
        assert_eq!(supervisor.pending_count(), 1, "second job remains queued");
        supervisor.drain_once().expect("second drains");
        assert_eq!(supervisor.pending_count(), 0);
        drop(database);
        let _ = std::fs::remove_dir_all(&dir);
    }

    #[test]
    fn sanitize_payload_validates_identity_and_bounds() {
        let identity = "a".repeat(64);
        assert!(sanitize_job_payload(
            0,
            "zephyr:7b",
            Some(1),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .is_err());
        assert!(sanitize_job_payload(
            1,
            "zephyr:7b",
            Some(0),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .is_err());
        assert!(sanitize_job_payload(
            1,
            "zephyr:7b",
            Some(1),
            true,
            0.0,
            "ollama",
            "auto",
            "online",
            &identity,
        )
        .is_err());
        assert!(sanitize_job_payload(
            1,
            "zephyr:7b",
            Some(1),
            true,
            30.0,
            "ollama",
            "auto",
            "online",
            "not-hex",
        )
        .is_err());
    }
}
