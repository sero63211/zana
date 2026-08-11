//! Bounded runtime probe contracts, limits, executables, and loopback HTTP.

use std::io::{self, Read, Write};
use std::net::TcpStream;
use std::sync::Arc;
use std::time::{Duration, Instant};

use serde_json::Value;

use crate::domain::{ModelIdentityStrength, RuntimeKind, RuntimeSource, RuntimeStatus};
use crate::time::now_iso;

pub const OLLAMA_DEFAULT_ENDPOINT: &str = "http://127.0.0.1:11434";
pub const LM_STUDIO_DEFAULT_ENDPOINT: &str = "http://127.0.0.1:1234";
pub const LLAMA_CPP_DEFAULT_ENDPOINT: &str = "http://127.0.0.1:8080";
pub const MLX_LM_DEFAULT_ENDPOINT: &str = "http://127.0.0.1:8080";

pub const MAX_TARGETS: usize = 16;
pub const MAX_WORKERS: usize = 4;
pub const MAX_TIMEOUT_SECONDS: f64 = 10.0;
pub const MAX_ENDPOINT_LENGTH: usize = 2000;
pub const MAX_ENDPOINT_BYTES: usize = 4096;
pub const MAX_EVIDENCE_ITEMS: usize = 64;
pub const MAX_ERROR_CHARS: usize = 512;
pub const MAX_MODELS: usize = 128;
pub const MAX_MODEL_FIELD_BYTES: usize = 256;
pub const MAX_MODEL_CAPABILITIES: usize = 16;
pub const MAX_MODELS_TOTAL_BYTES: usize = 262_144;
pub const MAX_RESPONSE_BYTES: usize = 2 * 1024 * 1024;
pub const MAX_REQUEST_HEADERS: usize = 16;
pub const MAX_REQUEST_BODY_BYTES: usize = 4096;
pub const MAX_RUNTIME_ID_BYTES: usize = 128;
pub const MAX_MODEL_ID_BYTES: usize = 256;
pub const MAX_IDENTITY_FIELD_BYTES: usize = 256;

#[derive(Debug, Clone)]
pub struct RuntimeProbeLimits {
    pub max_targets: usize,
    pub max_timeout_seconds: f64,
    pub max_endpoint_length: usize,
    pub max_endpoint_bytes: usize,
    pub max_evidence_items: usize,
    pub max_error_chars: usize,
    pub max_models: usize,
    pub max_model_field_bytes: usize,
    pub max_model_capabilities: usize,
    pub max_models_total_bytes: usize,
}

impl Default for RuntimeProbeLimits {
    fn default() -> Self {
        Self {
            max_targets: MAX_TARGETS,
            max_timeout_seconds: MAX_TIMEOUT_SECONDS,
            max_endpoint_length: MAX_ENDPOINT_LENGTH,
            max_endpoint_bytes: MAX_ENDPOINT_BYTES,
            max_evidence_items: MAX_EVIDENCE_ITEMS,
            max_error_chars: MAX_ERROR_CHARS,
            max_models: MAX_MODELS,
            max_model_field_bytes: MAX_MODEL_FIELD_BYTES,
            max_model_capabilities: MAX_MODEL_CAPABILITIES,
            max_models_total_bytes: MAX_MODELS_TOTAL_BYTES,
        }
    }
}

impl RuntimeProbeLimits {
    pub fn validate(&self) -> Result<(), String> {
        if !(1..=MAX_TARGETS).contains(&self.max_targets) {
            return Err("max_targets is out of range".to_owned());
        }
        if !self.max_timeout_seconds.is_finite()
            || self.max_timeout_seconds <= 0.0
            || self.max_timeout_seconds > MAX_TIMEOUT_SECONDS
        {
            return Err("max_timeout_seconds is out of range".to_owned());
        }
        if !(1..=MAX_ENDPOINT_LENGTH).contains(&self.max_endpoint_length)
            || !(1..=MAX_ENDPOINT_BYTES).contains(&self.max_endpoint_bytes)
            || !(1..=MAX_EVIDENCE_ITEMS).contains(&self.max_evidence_items)
            || !(1..=MAX_ERROR_CHARS).contains(&self.max_error_chars)
            || !(1..=MAX_MODELS).contains(&self.max_models)
            || !(1..=MAX_MODEL_FIELD_BYTES).contains(&self.max_model_field_bytes)
            || !(1..=MAX_MODEL_CAPABILITIES).contains(&self.max_model_capabilities)
            || !(1..=MAX_MODELS_TOTAL_BYTES).contains(&self.max_models_total_bytes)
        {
            return Err("a probe limit is out of range".to_owned());
        }
        Ok(())
    }
}

#[derive(Debug, Clone)]
pub struct HttpResponse {
    pub status: u16,
    pub text: String,
    pub content_type: Option<String>,
}

#[derive(Debug, Clone)]
pub enum TransportError {
    Protocol(String),
    Timeout,
    Network,
    Oversized,
}

impl TransportError {
    pub fn sanitized(&self) -> String {
        match self {
            Self::Protocol(message) => message.clone(),
            Self::Timeout => "runtime did not answer within the bounded timeout".to_owned(),
            Self::Network => "runtime transport failed".to_owned(),
            Self::Oversized => "runtime response exceeded the bounded size limit".to_owned(),
        }
    }
}

pub trait HttpTransport: Send + Sync {
    fn request(
        &self,
        method: &str,
        url: &str,
        headers: &[(&str, &str)],
        body: Option<&[u8]>,
        timeout: Duration,
    ) -> Result<HttpResponse, TransportError>;
}

// Cooperative timeout contract: `HttpTransport::request` MUST honor the
// caller-provided `timeout` as an absolute cap for connect/write/read and
// return `TransportError::Timeout` on expiry. The registry uses scoped
// workers and joins them before returning, so a transport that blocks past
// its timeout can delay the caller but can never outlive the batch call.

pub type SharedTransport = Arc<dyn HttpTransport>;

#[derive(Debug, Clone)]
pub struct ModelDescriptor {
    pub runtime_id: String,
    pub model_id: String,
    pub display_name: String,
    pub digest: Option<String>,
    pub family: Option<String>,
    pub parameter_count: Option<i64>,
    pub parameter_label: Option<String>,
    pub format: Option<String>,
    pub quantization: Option<String>,
    pub size_bytes: Option<i64>,
    pub context_length: Option<i64>,
    pub capabilities: Vec<String>,
    pub trainability: Option<String>,
    pub metadata_source: String,
    pub last_seen_at: String,
    pub identity_strength: ModelIdentityStrength,
}

#[derive(Debug, Clone)]
pub struct RuntimeDescriptor {
    pub runtime_id: String,
    pub kind: RuntimeKind,
    pub endpoint: String,
    pub source: RuntimeSource,
    pub status: RuntimeStatus,
    pub registered: bool,
    pub server_running: bool,
    pub installed: bool,
    pub installed_not_running: bool,
    pub identified_vendor: Option<String>,
    pub evidence: Vec<String>,
    pub warnings: Vec<String>,
    pub error: Option<String>,
    pub models: Vec<ModelDescriptor>,
    pub last_seen_at: String,
}

#[derive(Debug, Clone)]
pub struct ProbeTarget {
    pub runtime_id: String,
    pub kind: RuntimeKind,
    pub endpoint: String,
    pub source: RuntimeSource,
    pub adapter_type: AdapterType,
    pub timeout: Option<Duration>,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AdapterType {
    Auto,
    Ollama,
    OpenAiCompatible,
    LmStudio,
    LlamaCpp,
    MlxLm,
}

impl AdapterType {
    pub fn for_kind(kind: RuntimeKind) -> Self {
        match kind {
            RuntimeKind::Ollama => Self::Ollama,
            RuntimeKind::LmStudio => Self::LmStudio,
            RuntimeKind::LlamaCpp => Self::LlamaCpp,
            RuntimeKind::MlxLm => Self::MlxLm,
            RuntimeKind::OpenAiCompatible | RuntimeKind::Unknown => Self::OpenAiCompatible,
        }
    }
}

#[allow(clippy::too_many_arguments)]
pub fn build_runtime_descriptor(
    runtime_id: &str,
    kind: RuntimeKind,
    endpoint: &str,
    source: RuntimeSource,
    installed: bool,
    server_running: bool,
    registered: bool,
    status: RuntimeStatus,
    evidence: Vec<String>,
    warnings: Vec<String>,
    error: Option<String>,
    models: Vec<ModelDescriptor>,
    identified_vendor: Option<String>,
) -> RuntimeDescriptor {
    RuntimeDescriptor {
        runtime_id: runtime_id.to_owned(),
        kind,
        endpoint: endpoint.to_owned(),
        source,
        status,
        registered,
        server_running,
        installed,
        installed_not_running: installed && !server_running,
        identified_vendor,
        evidence,
        warnings,
        error,
        models,
        last_seen_at: now_iso(),
    }
}

pub fn parse_json_object(response: &HttpResponse, label: &str) -> Result<Value, String> {
    let parsed: Value = serde_json::from_str(&response.text)
        .map_err(|_| format!("{label} returned invalid JSON"))?;
    if !parsed.is_object() {
        return Err(format!("{label} returned a non-object payload"));
    }
    Ok(parsed)
}

pub fn require_http_ok(response: &HttpResponse, label: &str) -> Result<(), String> {
    if !(200..300).contains(&response.status) {
        return Err(format!("{label} returned HTTP {}", response.status));
    }
    Ok(())
}

pub fn parse_parameter_label(label: Option<&str>) -> Option<i64> {
    let label = label?.trim().to_ascii_lowercase();
    let normalized = label.strip_suffix('b')?;
    let number: f64 = normalized.trim().parse().ok()?;
    if !number.is_finite() || number <= 0.0 {
        return None;
    }
    let scaled = number * 1_000_000_000.0;
    if !scaled.is_finite() || scaled >= (i64::MAX as f64) || scaled < 1.0 {
        return None;
    }
    Some(scaled as i64)
}

pub struct ExecutableDiscovery {
    pub which: Arc<ExecutableWhich>,
}

pub type ExecutableWhich = Box<dyn Fn(&str) -> Option<String> + Send + Sync>;

impl Clone for ExecutableDiscovery {
    fn clone(&self) -> Self {
        Self {
            which: Arc::clone(&self.which),
        }
    }
}

fn parse_int(value: Option<&Value>) -> Option<i64> {
    match value {
        Some(Value::Number(number)) => number.as_i64(),
        _ => None,
    }
}

fn parse_text(value: Option<&Value>) -> Option<String> {
    match value {
        Some(Value::String(text)) if !text.is_empty() => Some(text.clone()),
        _ => None,
    }
}

fn bounded_evidence(values: Vec<String>, limit: usize) -> Vec<String> {
    values
        .into_iter()
        .take(limit)
        .map(|value| bounded_text(&value, 512))
        .collect()
}

fn bounded_text(value: &str, max_bytes: usize) -> String {
    if value.len() <= max_bytes {
        return value.to_owned();
    }
    let marker = "...[truncated]";
    if max_bytes == 0 {
        return String::new();
    }
    let marker_prefix_len = marker.len().min(max_bytes);
    if max_bytes <= marker.len() {
        return marker[..marker_prefix_len].to_owned();
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

pub struct OllamaAdapter {
    pub endpoint: String,
    pub source: RuntimeSource,
    pub transport: SharedTransport,
    pub deadline: Instant,
    pub installed: bool,
    pub max_models: usize,
}

impl OllamaAdapter {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        endpoint: &str,
        source: RuntimeSource,
        transport: SharedTransport,
        deadline: Instant,
        installed: bool,
        max_models: usize,
    ) -> Self {
        Self {
            endpoint: endpoint.trim_end_matches('/').to_owned(),
            source,
            transport,
            deadline,
            installed,
            max_models,
        }
    }

    fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }

    pub fn probe(&self) -> RuntimeDescriptor {
        let mut evidence = Vec::new();
        let mut warnings = Vec::new();
        if self.installed {
            evidence.push("ollama executable present on PATH".to_owned());
        }
        let tags_url = format!("{}/api/tags", self.endpoint);
        let tags = self
            .transport
            .request("GET", &tags_url, &[], None, self.remaining());
        let tags = match tags {
            Ok(response) if response.text.len() <= MAX_RESPONSE_BYTES => response,
            Ok(_) => {
                return build_runtime_descriptor(
                    "ollama-local",
                    RuntimeKind::Ollama,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some("runtime response exceeded the bounded size limit".to_owned()),
                    Vec::new(),
                    None,
                );
            }
            Err(error) => {
                let status = if matches!(error, TransportError::Timeout | TransportError::Network) {
                    RuntimeStatus::Offline
                } else {
                    RuntimeStatus::Error
                };
                return build_runtime_descriptor(
                    "ollama-local",
                    RuntimeKind::Ollama,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    status,
                    evidence,
                    warnings,
                    Some(error.sanitized()),
                    Vec::new(),
                    None,
                );
            }
        };
        let payload = match require_http_ok(&tags, "Ollama /api/tags")
            .and_then(|_| parse_json_object(&tags, "Ollama /api/tags"))
        {
            Ok(payload) => payload,
            Err(message) => {
                return build_runtime_descriptor(
                    "ollama-local",
                    RuntimeKind::Ollama,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some(message),
                    Vec::new(),
                    None,
                );
            }
        };
        let Some(entries) = payload.get("models").and_then(Value::as_array) else {
            return build_runtime_descriptor(
                "ollama-local",
                RuntimeKind::Ollama,
                &self.endpoint,
                self.source,
                self.installed,
                false,
                false,
                RuntimeStatus::Error,
                evidence,
                warnings,
                Some("Ollama /api/tags did not match the expected shape.".to_owned()),
                Vec::new(),
                None,
            );
        };
        evidence.push("Ollama /api/tags matched expected shape".to_owned());
        let models = match self.parse_models(entries, &mut evidence, &mut warnings) {
            Ok(models) => models,
            Err(message) => {
                return build_runtime_descriptor(
                    "ollama-local",
                    RuntimeKind::Ollama,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some(message),
                    Vec::new(),
                    None,
                );
            }
        };
        build_runtime_descriptor(
            "ollama-local",
            RuntimeKind::Ollama,
            &self.endpoint,
            self.source,
            self.installed,
            true,
            true,
            RuntimeStatus::Online,
            evidence,
            warnings,
            None,
            models,
            None,
        )
    }

    fn parse_models(
        &self,
        entries: &[Value],
        evidence: &mut Vec<String>,
        warnings: &mut Vec<String>,
    ) -> Result<Vec<ModelDescriptor>, String> {
        if entries.len() > self.max_models {
            return Err("Ollama model count exceeds the bounded policy limit.".to_owned());
        }
        let mut models = Vec::new();
        for entry in entries.iter() {
            let Some(object) = entry.as_object() else {
                return Err("Ollama model list contains a non-object entry.".to_owned());
            };
            let Some(name) = parse_text(object.get("name")) else {
                return Err("Ollama model list contains an entry without a name.".to_owned());
            };
            if name.is_empty() || name.len() > MAX_MODEL_ID_BYTES {
                return Err("Ollama model identity exceeds the bounded limit.".to_owned());
            }
            let Some(descriptor) = self.build_model(&name, object, evidence, warnings) else {
                return Err("Ollama model descriptor could not be built safely.".to_owned());
            };
            models.push(descriptor);
        }
        Ok(models)
    }

    fn build_model(
        &self,
        name: &str,
        tag: &serde_json::Map<String, Value>,
        evidence: &mut Vec<String>,
        warnings: &mut Vec<String>,
    ) -> Option<ModelDescriptor> {
        let details = tag.get("details").and_then(Value::as_object);
        let digest = parse_text(tag.get("digest"));
        let identity = if digest.is_some() {
            ModelIdentityStrength::ExactDigest
        } else {
            ModelIdentityStrength::RuntimeModelId
        };
        let mut descriptor = ModelDescriptor {
            runtime_id: "ollama-local".to_owned(),
            model_id: name.to_owned(),
            display_name: name.to_owned(),
            digest,
            family: details.and_then(|d| parse_text(d.get("family"))),
            parameter_count: None,
            parameter_label: details.and_then(|d| parse_text(d.get("parameter_size"))),
            format: details.and_then(|d| parse_text(d.get("format"))),
            quantization: details.and_then(|d| parse_text(d.get("quantization_level"))),
            size_bytes: parse_int(tag.get("size")),
            context_length: None,
            capabilities: Vec::new(),
            trainability: None,
            metadata_source: "runtime".to_owned(),
            last_seen_at: now_iso(),
            identity_strength: identity,
        };
        let show = if self.remaining().is_zero() {
            warnings.push(format!(
                "/api/show enrichment skipped for {name}; deadline exhausted."
            ));
            None
        } else {
            let show_url = format!("{}/api/show", self.endpoint);
            let body = serde_json::json!({ "model": name });
            match self.transport.request(
                "POST",
                &show_url,
                &[("Content-Type", "application/json")],
                Some(&serde_json::to_vec(&body).unwrap_or_default()),
                self.remaining(),
            ) {
                Ok(response) if response.text.len() <= MAX_RESPONSE_BYTES => Some(response),
                _ => {
                    warnings.push(format!(
                        "/api/show enrichment failed for {name}; tags metadata only."
                    ));
                    None
                }
            }
        };
        if let Some(response) = show {
            if require_http_ok(&response, "Ollama /api/show").is_ok() {
                match parse_json_object(&response, "Ollama /api/show") {
                    Ok(payload) => {
                        self.apply_show_metadata(&mut descriptor, &payload, evidence);
                    }
                    Err(_) => warnings.push(format!(
                        "/api/show enrichment failed for {name}; tags metadata only."
                    )),
                }
            } else {
                warnings.push(format!(
                    "/api/show enrichment failed for {name}; tags metadata only."
                ));
            }
        }
        Some(descriptor)
    }

    fn apply_show_metadata(
        &self,
        descriptor: &mut ModelDescriptor,
        show: &Value,
        evidence: &mut Vec<String>,
    ) {
        let details = show.get("details").and_then(Value::as_object);
        let model_info = show.get("model_info").and_then(Value::as_object);
        if descriptor.digest.is_none() {
            descriptor.digest = parse_text(show.get("digest"));
            if descriptor.digest.is_some() {
                descriptor.identity_strength = ModelIdentityStrength::ExactDigest;
            }
        }
        if let Some(details) = details {
            if descriptor.family.is_none() {
                descriptor.family = parse_text(details.get("family"));
            }
            if descriptor.parameter_label.is_none() {
                descriptor.parameter_label = parse_text(details.get("parameter_size"));
            }
            if descriptor.format.is_none() {
                descriptor.format = parse_text(details.get("format"));
            }
            if descriptor.quantization.is_none() {
                descriptor.quantization = parse_text(details.get("quantization_level"));
            }
        }
        if let Some(model_info) = model_info {
            if let Some(count) = parse_int(model_info.get("general.parameter_count")) {
                descriptor.parameter_count = Some(count);
            } else if descriptor.parameter_count.is_none() {
                descriptor.parameter_count =
                    parse_parameter_label(descriptor.parameter_label.as_deref());
            }
            if let Some(size) = parse_int(model_info.get("general.size")) {
                descriptor.size_bytes = Some(size);
            }
            if let Some(context) = parse_int(model_info.get("llama.context_length")) {
                descriptor.context_length = Some(context);
            }
        }
        if let Some(capabilities) = show.get("capabilities").and_then(Value::as_array) {
            descriptor.capabilities = capabilities
                .iter()
                .filter_map(|item| item.as_str().map(str::to_owned))
                .collect();
        }
        evidence.push(format!("Ollama /api/show enriched {}", descriptor.model_id));
    }
}

pub struct OpenAiCompatAdapter {
    pub runtime_id: String,
    pub kind: RuntimeKind,
    pub endpoint: String,
    pub source: RuntimeSource,
    pub transport: SharedTransport,
    pub deadline: Instant,
    pub installed: bool,
    pub identify: Option<VendorIdentifier>,
    pub max_models: usize,
}

pub type VendorIdentifier =
    fn(&dyn HttpTransport, &str, Duration) -> Result<(Option<String>, RuntimeKind), String>;

impl OpenAiCompatAdapter {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        runtime_id: &str,
        kind: RuntimeKind,
        endpoint: &str,
        source: RuntimeSource,
        transport: SharedTransport,
        deadline: Instant,
        installed: bool,
        identify: Option<VendorIdentifier>,
        max_models: usize,
    ) -> Self {
        Self {
            runtime_id: runtime_id.to_owned(),
            kind,
            endpoint: endpoint.trim_end_matches('/').to_owned(),
            source,
            transport,
            deadline,
            installed,
            identify,
            max_models,
        }
    }

    fn remaining(&self) -> Duration {
        self.deadline.saturating_duration_since(Instant::now())
    }

    pub fn models_url(&self) -> String {
        if self.endpoint.ends_with("/models") {
            return self.endpoint.clone();
        }
        if self.endpoint.ends_with("/v1") {
            return format!("{}/models", self.endpoint);
        }
        format!("{}/v1/models", self.endpoint)
    }

    pub fn probe(&self) -> RuntimeDescriptor {
        let mut evidence = Vec::new();
        let mut warnings = Vec::new();
        let response =
            self.transport
                .request("GET", &self.models_url(), &[], None, self.remaining());
        let response = match response {
            Ok(response) if response.text.len() <= MAX_RESPONSE_BYTES => response,
            Ok(_) => {
                return build_runtime_descriptor(
                    &self.runtime_id,
                    self.kind,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some("runtime response exceeded the bounded size limit".to_owned()),
                    Vec::new(),
                    None,
                );
            }
            Err(error) => {
                let status = if matches!(error, TransportError::Timeout | TransportError::Network) {
                    RuntimeStatus::Offline
                } else {
                    RuntimeStatus::Error
                };
                return build_runtime_descriptor(
                    &self.runtime_id,
                    self.kind,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    status,
                    evidence,
                    warnings,
                    Some(error.sanitized()),
                    Vec::new(),
                    None,
                );
            }
        };
        let payload = match require_http_ok(&response, "OpenAI-compatible /v1/models")
            .and_then(|_| parse_json_object(&response, "OpenAI-compatible /v1/models"))
        {
            Ok(payload) => payload,
            Err(message) => {
                return build_runtime_descriptor(
                    &self.runtime_id,
                    self.kind,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some(message),
                    Vec::new(),
                    None,
                );
            }
        };
        let Some(entries) = payload.get("data").and_then(Value::as_array) else {
            return build_runtime_descriptor(
                &self.runtime_id,
                self.kind,
                &self.endpoint,
                self.source,
                self.installed,
                false,
                false,
                RuntimeStatus::Error,
                evidence,
                warnings,
                Some("OpenAI-compatible /v1/models did not match the expected shape.".to_owned()),
                Vec::new(),
                None,
            );
        };
        let models = match self.parse_models(entries, &mut evidence, &mut warnings) {
            Ok(models) => models,
            Err(message) => {
                return build_runtime_descriptor(
                    &self.runtime_id,
                    self.kind,
                    &self.endpoint,
                    self.source,
                    self.installed,
                    false,
                    false,
                    RuntimeStatus::Error,
                    evidence,
                    warnings,
                    Some(message),
                    Vec::new(),
                    None,
                );
            }
        };
        evidence.push("OpenAI-compatible /v1/models matched expected shape".to_owned());
        let (vendor, vendor_evidence, vendor_warnings, kind) = match self.identify {
            Some(identifier) => match identifier(self.transport.as_ref(), &self.endpoint, self.remaining())
            {
                Ok((vendor, kind)) => {
                    let evidence = if vendor.is_some() {
                        vec![format!(
                            "{} metadata matched server identity",
                            vendor.as_deref().unwrap_or("vendor")
                        )]
                    } else {
                        Vec::new()
                    };
                    (vendor, evidence, Vec::new(), kind)
                }
                Err(_) => (
                    None,
                    Vec::new(),
                    vec![
                        "Server answered /v1/models but no vendor metadata; identified as generic OpenAI-compatible, not by port.".to_owned()
                    ],
                    RuntimeKind::OpenAiCompatible,
                ),
            },
            None => (None, Vec::new(), Vec::new(), self.kind),
        };
        evidence.extend(vendor_evidence);
        warnings.extend(vendor_warnings);
        build_runtime_descriptor(
            &self.runtime_id,
            kind,
            &self.endpoint,
            self.source,
            self.installed,
            true,
            true,
            RuntimeStatus::Online,
            evidence,
            warnings,
            None,
            models,
            vendor,
        )
    }

    fn parse_models(
        &self,
        entries: &[Value],
        evidence: &mut Vec<String>,
        _warnings: &mut Vec<String>,
    ) -> Result<Vec<ModelDescriptor>, String> {
        if entries.len() > self.max_models {
            return Err(
                "OpenAI-compatible model count exceeds the bounded policy limit.".to_owned(),
            );
        }
        let mut models = Vec::new();
        for entry in entries {
            let Some(object) = entry.as_object() else {
                return Err("OpenAI-compatible model list contains a non-object entry.".to_owned());
            };
            let Some(model_id) = parse_text(object.get("id")) else {
                return Err(
                    "OpenAI-compatible model list contains an entry without an id.".to_owned(),
                );
            };
            if model_id.is_empty() || model_id.len() > MAX_MODEL_ID_BYTES {
                return Err(
                    "OpenAI-compatible model identity exceeds the bounded limit.".to_owned(),
                );
            }
            models.push(ModelDescriptor {
                runtime_id: self.runtime_id.clone(),
                model_id: model_id.clone(),
                display_name: model_id,
                digest: None,
                family: None,
                parameter_count: None,
                parameter_label: None,
                format: None,
                quantization: None,
                size_bytes: None,
                context_length: None,
                capabilities: Vec::new(),
                trainability: None,
                metadata_source: "runtime".to_owned(),
                last_seen_at: now_iso(),
                identity_strength: ModelIdentityStrength::RuntimeModelId,
            });
        }
        evidence.push(format!(
            "OpenAI-compatible list returned {} model(s)",
            models.len()
        ));
        Ok(models)
    }
}

pub fn identify_lm_studio(
    transport: &dyn HttpTransport,
    endpoint: &str,
    timeout: Duration,
) -> Result<(Option<String>, RuntimeKind), String> {
    let response = transport
        .request(
            "GET",
            &format!("{endpoint}/api/v0/models"),
            &[],
            None,
            timeout,
        )
        .map_err(|_| "LM Studio metadata request failed".to_owned())?;
    if response.text.len() > MAX_RESPONSE_BYTES {
        return Err("LM Studio metadata response exceeded the bounded size limit".to_owned());
    }
    require_http_ok(&response, "LM Studio /api/v0/models")?;
    let payload: Value = serde_json::from_str(&response.text)
        .map_err(|_| "LM Studio metadata returned invalid JSON".to_owned())?;
    if is_lmstudio_payload(&payload) {
        Ok((Some("LM Studio".to_owned()), RuntimeKind::LmStudio))
    } else {
        Err("LM Studio v0 payload did not match expected shape".to_owned())
    }
}

fn is_lmstudio_payload(payload: &Value) -> bool {
    if let Some(list) = payload.as_array() {
        return !list.is_empty()
            && list.iter().all(|item| {
                item.as_object()
                    .is_some_and(|object| object.contains_key("id"))
            });
    }
    payload.as_object().is_some_and(|object| {
        ["data", "models"].iter().any(|key| {
            object
                .get(*key)
                .and_then(Value::as_array)
                .is_some_and(|list| {
                    !list.is_empty()
                        && list
                            .iter()
                            .all(|item| item.as_object().is_some_and(|o| o.contains_key("id")))
                })
        })
    })
}

pub fn identify_llama_cpp(
    transport: &dyn HttpTransport,
    endpoint: &str,
    timeout: Duration,
) -> Result<(Option<String>, RuntimeKind), String> {
    let response = transport
        .request("GET", &format!("{endpoint}/props"), &[], None, timeout)
        .map_err(|_| "llama.cpp metadata request failed".to_owned())?;
    if response.text.len() > MAX_RESPONSE_BYTES {
        return Err("llama.cpp metadata response exceeded the bounded size limit".to_owned());
    }
    require_http_ok(&response, "llama.cpp /props")?;
    let payload = parse_json_object(&response, "llama.cpp /props")?;
    if contains_marker(&payload, &["llama.cpp", "llama_cpp", "llama-cpp"]) {
        Ok((Some("llama.cpp".to_owned()), RuntimeKind::LlamaCpp))
    } else {
        Err("llama.cpp /props did not contain identity markers".to_owned())
    }
}

fn contains_marker(payload: &Value, markers: &[&str]) -> bool {
    let Some(object) = payload.as_object() else {
        return false;
    };
    object.values().any(|value| {
        value
            .as_str()
            .map(|text| {
                let lowered = text.to_ascii_lowercase();
                markers.iter().any(|marker| lowered.contains(marker))
            })
            .unwrap_or(false)
    })
}

pub fn identify_mlx_lm(
    transport: &dyn HttpTransport,
    endpoint: &str,
    timeout: Duration,
) -> Result<(Option<String>, RuntimeKind), String> {
    let response = transport
        .request("GET", &format!("{endpoint}/version"), &[], None, timeout)
        .map_err(|_| "MLX metadata request failed".to_owned())?;
    if response.text.len() > MAX_RESPONSE_BYTES {
        return Err("MLX metadata response exceeded the bounded size limit".to_owned());
    }
    require_http_ok(&response, "MLX /version")?;
    let payload = parse_json_object(&response, "MLX /version")?;
    if contains_marker(&payload, &["mlx"]) {
        Ok((Some("MLX-LM".to_owned()), RuntimeKind::MlxLm))
    } else {
        Err("MLX /version did not contain MLX markers".to_owned())
    }
}

pub struct RuntimeProbeRegistry {
    transport: SharedTransport,
    timeout: Duration,
    max_workers: usize,
    executables: ExecutableDiscovery,
    limits: RuntimeProbeLimits,
}

impl RuntimeProbeRegistry {
    pub fn new(
        transport: SharedTransport,
        timeout: Duration,
        max_workers: usize,
    ) -> Result<Self, String> {
        let limits = RuntimeProbeLimits::default();
        limits.validate()?;
        if timeout.is_zero() || timeout.as_secs_f64() > limits.max_timeout_seconds {
            return Err("probe timeout is out of range".to_owned());
        }
        if !(1..=MAX_WORKERS).contains(&max_workers) {
            return Err("max_workers is out of range".to_owned());
        }
        Ok(Self {
            transport,
            timeout,
            max_workers,
            executables: ExecutableDiscovery::default(),
            limits,
        })
    }

    pub fn default_targets(&self) -> Vec<ProbeTarget> {
        vec![
            ProbeTarget {
                runtime_id: "ollama-local".to_owned(),
                kind: RuntimeKind::Ollama,
                endpoint: OLLAMA_DEFAULT_ENDPOINT.to_owned(),
                source: RuntimeSource::Auto,
                adapter_type: AdapterType::Ollama,
                timeout: None,
            },
            ProbeTarget {
                runtime_id: "lm-studio-local".to_owned(),
                kind: RuntimeKind::LmStudio,
                endpoint: LM_STUDIO_DEFAULT_ENDPOINT.to_owned(),
                source: RuntimeSource::Auto,
                adapter_type: AdapterType::LmStudio,
                timeout: None,
            },
            ProbeTarget {
                runtime_id: "llamacpp-local".to_owned(),
                kind: RuntimeKind::LlamaCpp,
                endpoint: LLAMA_CPP_DEFAULT_ENDPOINT.to_owned(),
                source: RuntimeSource::Auto,
                adapter_type: AdapterType::LlamaCpp,
                timeout: None,
            },
            ProbeTarget {
                runtime_id: "mlx-lm-local".to_owned(),
                kind: RuntimeKind::MlxLm,
                endpoint: MLX_LM_DEFAULT_ENDPOINT.to_owned(),
                source: RuntimeSource::Auto,
                adapter_type: AdapterType::MlxLm,
                timeout: None,
            },
        ]
    }

    pub fn probe(&self, targets: Vec<ProbeTarget>) -> Result<Vec<RuntimeDescriptor>, String> {
        self.revalidate_config()?;
        let validated = self.validate_targets(targets)?;
        if validated.is_empty() {
            return Ok(Vec::new());
        }
        let timeout = self.trusted_timeout()?;
        let deadline = Instant::now() + timeout;
        if validated.len() == 1 || self.max_workers == 1 {
            let result = validated
                .iter()
                .map(|target| self.probe_one_sanitized(target, deadline))
                .collect::<Vec<_>>();
            return Ok(sort_descriptors(result));
        }
        let results = std::thread::scope(|scope| {
            let target_count = validated.len();
            let expected_targets = validated.clone();
            let queue = std::sync::Arc::new(std::sync::Mutex::new(validated));
            let results = std::sync::Arc::new(std::sync::Mutex::new(Vec::new()));
            let worker_count = self.max_workers.min(target_count);
            let mut handles = Vec::new();
            for _ in 0..worker_count {
                let queue = Arc::clone(&queue);
                let results = Arc::clone(&results);
                let registry = Arc::clone(&self.transport);
                let limits = self.limits.clone();
                let executables = self.executables.clone();
                let handle = std::thread::Builder::new()
                    .name("zana-runtime-probe".to_owned())
                    .spawn_scoped(scope, move || {
                        let registry_handle = RuntimeProbeRegistry {
                            transport: registry,
                            timeout,
                            max_workers: 1,
                            executables,
                            limits,
                        };
                        loop {
                            let target = {
                                let mut guard = match queue.lock() {
                                    Ok(guard) => guard,
                                    Err(poisoned) => poisoned.into_inner(),
                                };
                                guard.pop()
                            };
                            let Some(target) = target else {
                                break;
                            };
                            let descriptor =
                                std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
                                    let remaining =
                                        deadline.saturating_duration_since(Instant::now());
                                    if remaining.is_zero() {
                                        registry_handle.bare_error_with(
                                            &target,
                                            "probe deadline expired before the target was probed",
                                        )
                                    } else {
                                        registry_handle.probe_one_sanitized(&target, deadline)
                                    }
                                }))
                                .unwrap_or_else(|_| {
                                    registry_handle.bare_error_with(
                                        &target,
                                        "Unexpected probe failure; details are not exposed.",
                                    )
                                });
                            match results.lock() {
                                Ok(mut guard) => guard.push(descriptor),
                                Err(poisoned) => poisoned.into_inner().push(descriptor),
                            }
                        }
                    })
                    .map_err(|_| ());
                match handle {
                    Ok(handle) => handles.push(handle),
                    Err(()) => {
                        // Spawn failure leaves the queue untouched; final
                        // reconciliation drains every exact leftover target.
                    }
                }
            }
            for handle in handles {
                // Per-target work is catch_unwind guarded; a join panic can
                // only be outer infrastructure failure, and the exact target
                // is reconciled below.
                let _ = handle.join();
            }
            let mut descriptors = results
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .clone();
            let mut seen = std::collections::HashSet::new();
            let mut reconciled = Vec::new();
            for descriptor in descriptors.drain(..) {
                if seen.insert(descriptor.runtime_id.clone()) {
                    reconciled.push(descriptor);
                }
            }
            let _remaining = queue
                .lock()
                .unwrap_or_else(|poisoned| poisoned.into_inner())
                .drain(..)
                .collect::<Vec<_>>();
            for expected in expected_targets {
                if !seen.contains(&expected.runtime_id) {
                    reconciled.push(self.bare_error_with(
                        &expected,
                        "runtime probe worker could not complete the target",
                    ));
                    seen.insert(expected.runtime_id);
                }
            }
            reconciled
        });
        Ok(sort_descriptors(results))
    }

    fn validate_targets(&self, targets: Vec<ProbeTarget>) -> Result<Vec<ProbeTarget>, String> {
        if targets.len() > self.limits.max_targets {
            return Err(format!(
                "target count exceeds limit {}",
                self.limits.max_targets
            ));
        }
        let mut seen_runtime_ids = std::collections::HashSet::new();
        let mut seen_identities = std::collections::HashSet::new();
        let mut validated = Vec::new();
        for mut target in targets {
            if target.runtime_id.is_empty()
                || target.runtime_id.len() > MAX_RUNTIME_ID_BYTES
                || !target
                    .runtime_id
                    .bytes()
                    .all(|byte| byte.is_ascii_alphanumeric() || b"_.-:".contains(&byte))
            {
                return Err("runtime_id is invalid or too long".to_owned());
            }
            if !seen_runtime_ids.insert(target.runtime_id.clone()) {
                return Err("duplicate runtime_id in probe targets".to_owned());
            }
            if target.endpoint.len() > self.limits.max_endpoint_length
                || target.endpoint.len() > self.limits.max_endpoint_bytes
            {
                return Err("probe endpoint exceeds the byte limit".to_owned());
            }
            if target.timeout.is_some_and(|timeout| {
                timeout.is_zero() || timeout.as_secs_f64() > self.limits.max_timeout_seconds
            }) {
                return Err("probe target timeout is out of range".to_owned());
            }
            if !adapter_matches_kind(target.adapter_type, target.kind) {
                return Err("probe adapter and runtime kind do not match".to_owned());
            }
            if !matches!(target.endpoint.as_str(), endpoint if endpoint_path_is_origin(endpoint)) {
                return Err("probe endpoint must be an origin without a path".to_owned());
            }
            let canonical = canonical_endpoint(&target.endpoint)?;
            target.endpoint = canonical.clone();
            let identity = format!(
                "{}|{}|{}",
                target.kind.as_str(),
                canonical,
                target.source.as_str()
            );
            if !seen_identities.insert(identity) {
                return Err("duplicate canonical runtime identity in probe targets".to_owned());
            }
            validated.push(target);
        }
        Ok(validated)
    }

    fn probe_one_sanitized(&self, target: &ProbeTarget, deadline: Instant) -> RuntimeDescriptor {
        let remaining = deadline.saturating_duration_since(Instant::now());
        if remaining.is_zero() {
            return self.bare_error(target);
        }
        let configured = match self.trusted_timeout() {
            Ok(timeout) => timeout,
            Err(_) => return self.bare_error(target),
        };
        let timeout = target.timeout.unwrap_or(configured).min(remaining);
        let adapter = self.make_adapter(target, timeout);
        let descriptor = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| adapter.probe()))
            .unwrap_or_else(|_| {
                self.bare_error_with(target, "Unexpected probe failure; details are not exposed.")
            });
        self.bound_descriptor(descriptor, target)
    }

    fn make_adapter(&self, target: &ProbeTarget, timeout: Duration) -> Box<dyn ProbeAdapter> {
        let installed = self.executables.installed(target.kind);
        let deadline = Instant::now() + timeout;
        match target.adapter_type {
            AdapterType::Ollama => Box::new(OllamaAdapter::new(
                &target.endpoint,
                target.source,
                Arc::clone(&self.transport),
                deadline,
                installed,
                self.limits.max_models,
            )),
            AdapterType::LmStudio => Box::new(OpenAiCompatAdapter::new(
                "lm-studio-local",
                RuntimeKind::LmStudio,
                &target.endpoint,
                target.source,
                Arc::clone(&self.transport),
                deadline,
                installed,
                Some(identify_lm_studio),
                self.limits.max_models,
            )),
            AdapterType::LlamaCpp => Box::new(OpenAiCompatAdapter::new(
                "llamacpp-local",
                RuntimeKind::LlamaCpp,
                &target.endpoint,
                target.source,
                Arc::clone(&self.transport),
                deadline,
                installed,
                Some(identify_llama_cpp),
                self.limits.max_models,
            )),
            AdapterType::MlxLm => Box::new(OpenAiCompatAdapter::new(
                "mlx-lm-local",
                RuntimeKind::MlxLm,
                &target.endpoint,
                target.source,
                Arc::clone(&self.transport),
                deadline,
                installed,
                Some(identify_mlx_lm),
                self.limits.max_models,
            )),
            AdapterType::OpenAiCompatible | AdapterType::Auto => {
                Box::new(OpenAiCompatAdapter::new(
                    "openai-compatible",
                    RuntimeKind::OpenAiCompatible,
                    &target.endpoint,
                    target.source,
                    Arc::clone(&self.transport),
                    deadline,
                    installed,
                    None,
                    self.limits.max_models,
                ))
            }
        }
    }

    fn revalidate_config(&self) -> Result<(), String> {
        self.limits.validate()?;
        if self.timeout.is_zero() || self.timeout.as_secs_f64() > self.limits.max_timeout_seconds {
            return Err("probe timeout is out of range".to_owned());
        }
        if !(1..=MAX_WORKERS).contains(&self.max_workers) {
            return Err("max_workers is out of range".to_owned());
        }
        Ok(())
    }

    fn trusted_timeout(&self) -> Result<Duration, String> {
        self.revalidate_config()?;
        Ok(self.timeout)
    }

    fn bound_descriptor(
        &self,
        descriptor: RuntimeDescriptor,
        target: &ProbeTarget,
    ) -> RuntimeDescriptor {
        let mut descriptor = descriptor;
        descriptor.runtime_id = target.runtime_id.clone();
        descriptor.endpoint = target.endpoint.clone();
        descriptor.source = target.source;
        descriptor.evidence = bounded_evidence(descriptor.evidence, self.limits.max_evidence_items);
        descriptor.warnings = bounded_evidence(descriptor.warnings, self.limits.max_evidence_items);
        descriptor.error = descriptor
            .error
            .map(|error| bounded_text(&error, self.limits.max_error_chars));
        descriptor.identified_vendor = descriptor.identified_vendor.inspect(|vendor| {
            if vendor.len() > self.limits.max_model_field_bytes
                || vendor.bytes().any(|b| b < 0x20 || b == 0x7f)
            {
                descriptor.error =
                    Some("runtime vendor metadata exceeds the bounded limit".to_owned());
                descriptor.status = RuntimeStatus::Error;
                descriptor.registered = false;
                descriptor.server_running = false;
                descriptor.installed_not_running =
                    descriptor.installed && !descriptor.server_running;
                descriptor.models = Vec::new();
            }
        });
        if descriptor.status == RuntimeStatus::Error {
            descriptor.identified_vendor = None;
        }
        match self.bound_models(descriptor.models, target) {
            Ok(models) => descriptor.models = models,
            Err(message) => {
                descriptor.status = RuntimeStatus::Error;
                descriptor.registered = false;
                descriptor.server_running = false;
                descriptor.installed_not_running =
                    descriptor.installed && !descriptor.server_running;
                descriptor.models = Vec::new();
                descriptor.error = Some(message);
            }
        }
        descriptor
    }

    fn bound_models(
        &self,
        models: Vec<ModelDescriptor>,
        target: &ProbeTarget,
    ) -> Result<Vec<ModelDescriptor>, String> {
        if models.len() > self.limits.max_models {
            return Err("model count exceeds the bounded policy limit".to_owned());
        }
        let mut projected = Vec::new();
        let mut total_bytes = 0usize;
        for mut model in models {
            if model.model_id.is_empty()
                || model.model_id.len() > self.limits.max_model_field_bytes
                || model.model_id.bytes().any(|b| b < 0x20 || b == 0x7f)
                || model.digest.as_deref().is_some_and(|value| {
                    value.is_empty()
                        || value.len() > self.limits.max_model_field_bytes
                        || value.bytes().any(|b| b < 0x20 || b == 0x7f)
                })
            {
                return Err("model identity field exceeds the bounded limit".to_owned());
            }
            for value in [
                model.parameter_count,
                model.size_bytes,
                model.context_length,
            ]
            .into_iter()
            .flatten()
            {
                if !(0..=(1 << 62)).contains(&value) {
                    return Err("model numeric metadata is out of range".to_owned());
                }
            }
            model.runtime_id = target.runtime_id.clone();
            for field in [
                model.display_name.as_str(),
                model.family.as_deref().unwrap_or(""),
                model.format.as_deref().unwrap_or(""),
                model.quantization.as_deref().unwrap_or(""),
                model.parameter_label.as_deref().unwrap_or(""),
                model.trainability.as_deref().unwrap_or(""),
            ] {
                if !field.is_empty()
                    && (field.len() > self.limits.max_model_field_bytes
                        || field.bytes().any(|b| b < 0x20 || b == 0x7f))
                {
                    return Err("model metadata field exceeds the bounded limit".to_owned());
                }
            }
            if model.capabilities.len() > self.limits.max_model_capabilities {
                return Err("model capability count exceeds the bounded limit".to_owned());
            }
            if model.capabilities.iter().any(|value| {
                value.len() > self.limits.max_model_field_bytes
                    || value.bytes().any(|b| b < 0x20 || b == 0x7f)
            }) {
                return Err("model capability exceeds the bounded limit".to_owned());
            }
            if model.metadata_source.len() > self.limits.max_model_field_bytes
                || model.metadata_source.bytes().any(|b| b < 0x20 || b == 0x7f)
            {
                return Err("model metadata field exceeds the bounded limit".to_owned());
            }
            model.last_seen_at = now_iso();
            total_bytes = total_bytes
                .saturating_add(model.runtime_id.len())
                .saturating_add(model.last_seen_at.len())
                .saturating_add(model.model_id.len())
                .saturating_add(model.display_name.len())
                .saturating_add(model.family.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.format.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.quantization.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.parameter_label.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.trainability.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.digest.as_deref().map(str::len).unwrap_or(0))
                .saturating_add(model.metadata_source.len())
                .saturating_add(model.capabilities.iter().map(String::len).sum::<usize>());
            if total_bytes > self.limits.max_models_total_bytes {
                return Err("model output exceeds the total byte limit".to_owned());
            }
            projected.push(model);
        }
        Ok(projected)
    }

    fn bare_error(&self, target: &ProbeTarget) -> RuntimeDescriptor {
        self.bare_error_with(
            target,
            "probe deadline expired before the target was probed",
        )
    }

    fn bare_error_with(&self, target: &ProbeTarget, message: &str) -> RuntimeDescriptor {
        build_runtime_descriptor(
            &target.runtime_id,
            target.kind,
            &target.endpoint,
            target.source,
            self.executables.installed(target.kind),
            false,
            false,
            RuntimeStatus::Error,
            Vec::new(),
            Vec::new(),
            Some(message.to_owned()),
            Vec::new(),
            None,
        )
    }
}

pub trait ProbeAdapter {
    fn probe(&self) -> RuntimeDescriptor;
}

impl ProbeAdapter for OllamaAdapter {
    fn probe(&self) -> RuntimeDescriptor {
        OllamaAdapter::probe(self)
    }
}

impl ProbeAdapter for OpenAiCompatAdapter {
    fn probe(&self) -> RuntimeDescriptor {
        OpenAiCompatAdapter::probe(self)
    }
}

fn sort_descriptors(mut descriptors: Vec<RuntimeDescriptor>) -> Vec<RuntimeDescriptor> {
    descriptors.sort_by(|left, right| left.runtime_id.cmp(&right.runtime_id));
    descriptors
}

impl Default for ExecutableDiscovery {
    fn default() -> Self {
        Self {
            which: Arc::new(Box::new(which_on_path)),
        }
    }
}

impl ExecutableDiscovery {
    pub fn find(&self, kind: RuntimeKind) -> Option<String> {
        let names: &[&str] = match kind {
            RuntimeKind::Ollama => &["ollama"],
            RuntimeKind::LmStudio => &["lms"],
            RuntimeKind::LlamaCpp => &["llama-server"],
            RuntimeKind::MlxLm => &["mlx_lm"],
            RuntimeKind::OpenAiCompatible | RuntimeKind::Unknown => &[],
        };
        names.iter().find_map(|name| (self.which)(name))
    }

    pub fn installed(&self, kind: RuntimeKind) -> bool {
        self.find(kind).is_some()
    }
}

pub fn which_on_path(name: &str) -> Option<String> {
    if name.is_empty() || name.contains('/') || name.contains('\\') {
        return None;
    }
    let path = std::env::var_os("PATH")?;
    for directory in std::env::split_paths(&path) {
        let candidate = directory.join(name);
        let Ok(metadata) = std::fs::metadata(&candidate) else {
            continue;
        };
        if !metadata.is_file() {
            continue;
        }
        #[cfg(unix)]
        {
            use std::os::unix::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                continue;
            }
        }
        return Some(candidate.to_string_lossy().into_owned());
    }
    None
}

/// Bounded loopback-only HTTP client used by discovery and acquisition.
pub struct LoopbackHttpTransport;

/// Monotonic clock boundary so transport deadlines are testable without real
/// sleeps.
pub trait TransportClock {
    fn now(&self) -> Instant;
}

pub struct SystemClock;

impl TransportClock for SystemClock {
    fn now(&self) -> Instant {
        Instant::now()
    }
}

/// Reader boundary that can limit each blocking read to the remaining total
/// budget. The production socket applies the timeout before every read.
pub trait ReadWithRemaining {
    fn read_with_remaining(&mut self, buffer: &mut [u8], remaining: Duration) -> io::Result<usize>;
}

impl ReadWithRemaining for TcpStream {
    fn read_with_remaining(&mut self, buffer: &mut [u8], remaining: Duration) -> io::Result<usize> {
        self.set_read_timeout(Some(remaining))?;
        self.read(buffer)
    }
}

impl LoopbackHttpTransport {
    fn connect(url: &UrlParts, remaining: Duration) -> Result<TcpStream, TransportError> {
        if remaining.is_zero() {
            return Err(TransportError::Timeout);
        }
        let host = dial_host(&url.host);
        let address: std::net::SocketAddr = match host.parse::<std::net::IpAddr>() {
            Ok(ip) => std::net::SocketAddr::new(ip, url.port),
            Err(_) => {
                return Err(TransportError::Protocol(
                    "runtime endpoint host is not a loopback IP".to_owned(),
                ));
            }
        };
        TcpStream::connect_timeout(&address, remaining).map_err(|error| {
            if error.kind() == io::ErrorKind::WouldBlock || error.kind() == io::ErrorKind::TimedOut
            {
                TransportError::Timeout
            } else {
                TransportError::Network
            }
        })
    }
}

fn dial_host(host: &str) -> &str {
    if host.eq_ignore_ascii_case("localhost") {
        "127.0.0.1"
    } else {
        host
    }
}

impl HttpTransport for LoopbackHttpTransport {
    fn request(
        &self,
        method: &str,
        url: &str,
        headers: &[(&str, &str)],
        body: Option<&[u8]>,
        timeout: Duration,
    ) -> Result<HttpResponse, TransportError> {
        self.request_with_clock(&SystemClock, method, url, headers, body, timeout)
    }
}

impl LoopbackHttpTransport {
    fn request_with_clock<C: TransportClock>(
        &self,
        clock: &C,
        method: &str,
        url: &str,
        headers: &[(&str, &str)],
        body: Option<&[u8]>,
        timeout: Duration,
    ) -> Result<HttpResponse, TransportError> {
        if timeout.is_zero() || timeout.as_secs_f64() > MAX_TIMEOUT_SECONDS {
            return Err(TransportError::Protocol(
                "runtime request timeout is out of range".to_owned(),
            ));
        }
        let deadline = clock.now() + timeout;
        let parsed = validate_url(url)?;
        validate_request_components(method, &parsed.path, headers)?;
        if body
            .map(|value| value.len() > MAX_REQUEST_BODY_BYTES)
            .unwrap_or(false)
        {
            return Err(TransportError::Protocol(
                "runtime request body exceeds the byte limit".to_owned(),
            ));
        }
        let mut stream = Self::connect(&parsed, remaining_budget(clock, deadline)?)?;
        let body = body.unwrap_or_default();
        let mut request = format!(
            "{method} {path} HTTP/1.1\r\nHost: {host_header}\r\nUser-Agent: zana-core/0.1.0\r\nAccept: application/json\r\nConnection: close\r\nContent-Length: {}\r\n",
            body.len(),
            host_header = host_header(&parsed),
            path = parsed.path,
        );
        for (name, value) in headers {
            request.push_str(name);
            request.push_str(": ");
            request.push_str(value);
            request.push_str("\r\n");
        }
        request.push_str("\r\n");
        stream
            .set_write_timeout(Some(remaining_budget(clock, deadline)?))
            .map_err(|error| {
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) {
                    TransportError::Timeout
                } else {
                    TransportError::Network
                }
            })?;
        stream.write_all(request.as_bytes()).map_err(|error| {
            if matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
            ) {
                TransportError::Timeout
            } else {
                TransportError::Network
            }
        })?;
        remaining_budget(clock, deadline)?;
        stream
            .set_write_timeout(Some(remaining_budget(clock, deadline)?))
            .map_err(|error| {
                if matches!(
                    error.kind(),
                    io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
                ) {
                    TransportError::Timeout
                } else {
                    TransportError::Network
                }
            })?;
        stream.write_all(body).map_err(|error| {
            if matches!(
                error.kind(),
                io::ErrorKind::WouldBlock | io::ErrorKind::TimedOut
            ) {
                TransportError::Timeout
            } else {
                TransportError::Network
            }
        })?;
        remaining_budget(clock, deadline)?;

        let parts = read_response_with_clock(&mut stream, clock, deadline)?;
        let content_length = parts.content_length;
        let mut text = parts.body_prefix;
        if let Some(length) = content_length {
            if length > MAX_RESPONSE_BYTES as u64 {
                return Err(TransportError::Oversized);
            }
            if text.len() as u64 > length {
                return Err(TransportError::Protocol(
                    "runtime response content-length contradicts the body".to_owned(),
                ));
            }
            let mut missing = length.saturating_sub(text.len() as u64);
            let mut chunk = [0u8; 8192];
            while missing > 0 {
                let remaining = remaining_budget(clock, deadline)?;
                let wanted = usize::min(
                    chunk.len(),
                    usize::try_from(missing).map_err(|_| TransportError::Oversized)?,
                );
                match stream.read_with_remaining(&mut chunk[..wanted], remaining) {
                    Ok(0) => {
                        return Err(TransportError::Protocol(
                            "runtime response was truncated".to_owned(),
                        ));
                    }
                    Ok(count) => {
                        if clock.now() >= deadline {
                            return Err(TransportError::Timeout);
                        }
                        text.extend_from_slice(&chunk[..count]);
                        missing = length.saturating_sub(text.len() as u64);
                    }
                    Err(error)
                        if error.kind() == io::ErrorKind::WouldBlock
                            || error.kind() == io::ErrorKind::TimedOut =>
                    {
                        return Err(TransportError::Timeout);
                    }
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => return Err(TransportError::Network),
                }
            }
        } else {
            let mut chunk = [0u8; 8192];
            loop {
                let remaining = remaining_budget(clock, deadline)?;
                match stream.read_with_remaining(&mut chunk, remaining) {
                    Ok(0) => break,
                    Ok(count) => {
                        if clock.now() >= deadline {
                            return Err(TransportError::Timeout);
                        }
                        let new_len = text.len().saturating_add(count);
                        if new_len > MAX_RESPONSE_BYTES {
                            return Err(TransportError::Oversized);
                        }
                        text.extend_from_slice(&chunk[..count]);
                    }
                    Err(error)
                        if error.kind() == io::ErrorKind::WouldBlock
                            || error.kind() == io::ErrorKind::TimedOut =>
                    {
                        return Err(TransportError::Timeout);
                    }
                    Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
                    Err(_) => return Err(TransportError::Network),
                }
            }
        }
        if text.len() > MAX_RESPONSE_BYTES {
            return Err(TransportError::Oversized);
        }
        let content_type = parts
            .headers
            .iter()
            .find(|(name, _)| name == "content-type")
            .map(|(_, value)| value.clone());
        let text = String::from_utf8(text).map_err(|_| {
            TransportError::Protocol("runtime response body is not valid UTF-8".to_owned())
        })?;
        Ok(HttpResponse {
            status: parts.status,
            text,
            content_type,
        })
    }
}

struct UrlParts {
    host: String,
    port: u16,
    path: String,
}

fn host_header(url: &UrlParts) -> String {
    if url.host.contains(':') {
        format!("[{}]:{}", url.host, url.port)
    } else {
        format!("{}:{}", url.host, url.port)
    }
}

fn validate_url(url: &str) -> Result<UrlParts, TransportError> {
    if url.is_empty() || url.len() > MAX_ENDPOINT_BYTES {
        return Err(TransportError::Protocol(
            "runtime endpoint is invalid".to_owned(),
        ));
    }
    let (scheme, rest) = url
        .split_once("://")
        .ok_or_else(|| TransportError::Protocol("runtime endpoint must be http(s)".to_owned()))?;
    // The loopback client speaks plaintext TCP only; HTTPS is rejected until a
    // real TLS boundary exists so the transport never pretends to be secure.
    if scheme != "http" {
        return Err(TransportError::Protocol(
            "runtime endpoint must use plaintext HTTP on a loopback host".to_owned(),
        ));
    }
    if rest.contains('@') || rest.contains('#') || rest.contains('?') {
        return Err(TransportError::Protocol(
            "runtime endpoint must not contain credentials, fragments, or queries".to_owned(),
        ));
    }
    let (authority, path) = match rest.split_once('/') {
        Some((authority, path)) => (authority, format!("/{path}")),
        None => (rest, "/".to_owned()),
    };
    if path.len() > 2000 || path.bytes().any(is_http_control) || path.contains(' ') {
        return Err(TransportError::Protocol(
            "runtime endpoint path is invalid or too long".to_owned(),
        ));
    }
    let (host, port) = match parse_ipv6_authority(authority) {
        Some((host, port)) => (host, port.unwrap_or(80)),
        None => match authority.split_once(':') {
            Some((host, port)) => {
                let port: u16 = port.parse().map_err(|_| {
                    TransportError::Protocol("runtime endpoint port is invalid".to_owned())
                })?;
                if port == 0 {
                    return Err(TransportError::Protocol(
                        "runtime endpoint port is invalid".to_owned(),
                    ));
                }
                (host.to_owned(), port)
            }
            None => (authority.to_owned(), 80),
        },
    };
    if host.is_empty() || host.contains(' ') || host.bytes().any(is_http_control) {
        return Err(TransportError::Protocol(
            "runtime endpoint host is invalid".to_owned(),
        ));
    }
    if port == 0 {
        return Err(TransportError::Protocol(
            "runtime endpoint port is invalid".to_owned(),
        ));
    }
    let normalized_host = host.trim_end_matches('.').to_ascii_lowercase();
    let host = if normalized_host == "localhost" {
        "localhost".to_owned()
    } else {
        normalized_host
    };
    if !is_loopback_host(&host) {
        return Err(TransportError::Protocol(
            "runtime endpoint must target a loopback host".to_owned(),
        ));
    }
    Ok(UrlParts { host, port, path })
}

fn validate_request_components(
    method: &str,
    path: &str,
    headers: &[(&str, &str)],
) -> Result<(), TransportError> {
    if headers.len() > MAX_REQUEST_HEADERS {
        return Err(TransportError::Protocol(
            "runtime request header count exceeds the limit".to_owned(),
        ));
    }
    if method.is_empty()
        || !method
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
    {
        return Err(TransportError::Protocol(
            "runtime request method is invalid".to_owned(),
        ));
    }
    if path.is_empty()
        || path.len() > 2000
        || path.contains(' ')
        || path
            .bytes()
            .any(|byte| byte >= 0x80 || is_http_control(byte))
    {
        return Err(TransportError::Protocol(
            "runtime request path contains forbidden characters".to_owned(),
        ));
    }
    for (name, value) in headers {
        if matches!(
            name.to_ascii_lowercase().as_str(),
            "host" | "content-length" | "connection" | "transfer-encoding"
        ) {
            return Err(TransportError::Protocol(
                "runtime request header is reserved and cannot be overridden".to_owned(),
            ));
        }
        if name.is_empty()
            || name.contains(':')
            || name.contains(' ')
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&byte))
            || name.bytes().any(is_http_control)
            || value.len() > 1024
            || value.bytes().any(is_http_control)
        {
            return Err(TransportError::Protocol(
                "runtime request header contains forbidden characters".to_owned(),
            ));
        }
    }
    Ok(())
}

fn is_http_control(byte: u8) -> bool {
    byte == 0x7f || byte < 0x20
}

fn is_loopback_host(host: &str) -> bool {
    if host == "localhost" || host == "127.0.0.1" || host == "::1" {
        return true;
    }
    host.parse::<std::net::IpAddr>()
        .map(|ip| ip.is_loopback())
        .unwrap_or(false)
}

fn parse_ipv6_authority(authority: &str) -> Option<(String, Option<u16>)> {
    if let Some(rest) = authority.strip_prefix('[') {
        let end = rest.find(']')?;
        let host = &rest[..end];
        if host.parse::<std::net::Ipv6Addr>().is_err() {
            return None;
        }
        let after = &rest[end + 1..];
        if after.is_empty() {
            return Some((host.to_owned(), None));
        }
        let port = after.strip_prefix(':')?.parse().ok()?;
        return Some((host.to_owned(), Some(port)));
    }
    None
}

fn remaining_budget<C: TransportClock>(
    clock: &C,
    deadline: Instant,
) -> Result<Duration, TransportError> {
    if clock.now() >= deadline {
        return Err(TransportError::Timeout);
    }
    let remaining = deadline.saturating_duration_since(clock.now());
    if remaining.is_zero() {
        return Err(TransportError::Timeout);
    }
    Ok(remaining)
}

#[derive(Debug)]
struct ResponseParts {
    status: u16,
    headers: Vec<(String, String)>,
    content_length: Option<u64>,
    body_prefix: Vec<u8>,
}

#[cfg(test)]
struct SplitReader {
    data: Vec<u8>,
    offset: usize,
    clock: FakeClock,
    step: Duration,
}

#[cfg(test)]
impl SplitReader {
    fn new(data: Vec<u8>, clock: FakeClock, step: Duration) -> Self {
        Self {
            data,
            offset: 0,
            clock,
            step,
        }
    }
}

#[cfg(test)]
impl ReadWithRemaining for SplitReader {
    fn read_with_remaining(&mut self, output: &mut [u8], remaining: Duration) -> io::Result<usize> {
        // Honor the provided per-read remaining budget like the production
        // socket: advance by the step and fail with a timeout when the fake
        // read would cross the total deadline.
        self.clock.advance(self.step.min(remaining));
        let available = self.data.len().saturating_sub(self.offset);
        if available == 0 || output.is_empty() {
            return Ok(0);
        }
        let count = available.min(output.len()).min(1);
        output[..count].copy_from_slice(&self.data[self.offset..self.offset + count]);
        self.offset += count;
        Ok(count)
    }
}

#[cfg(test)]
#[derive(Clone)]
struct FakeClock {
    now: std::rc::Rc<std::cell::Cell<Instant>>,
}

#[cfg(test)]
impl FakeClock {
    fn new(start: Instant) -> Self {
        Self {
            now: std::rc::Rc::new(std::cell::Cell::new(start)),
        }
    }

    fn advance(&self, duration: Duration) {
        self.now.set(self.now.get() + duration);
    }
}

#[cfg(test)]
impl TransportClock for FakeClock {
    fn now(&self) -> Instant {
        self.now.get()
    }
}

#[cfg(test)]
impl ReadWithRemaining for &[u8] {
    fn read_with_remaining(
        &mut self,
        output: &mut [u8],
        _remaining: Duration,
    ) -> io::Result<usize> {
        Read::read(self, output)
    }
}

#[cfg(test)]
#[allow(clippy::items_after_test_module)]
mod tests {
    use super::*;

    struct CountingTransport {
        concurrent: std::sync::Arc<std::sync::atomic::AtomicUsize>,
        max_concurrent: std::sync::Arc<std::sync::atomic::AtomicUsize>,
        delay: Duration,
    }

    struct NetworkTransport;

    struct OversizedTransport;

    struct CountingUrlTransport {
        inner: CountingTransport,
        calls: std::sync::Arc<std::sync::atomic::AtomicUsize>,
    }

    impl HttpTransport for CountingUrlTransport {
        fn request(
            &self,
            _method: &str,
            url: &str,
            headers: &[(&str, &str)],
            body: Option<&[u8]>,
            timeout: Duration,
        ) -> Result<HttpResponse, TransportError> {
            if url.contains("/api/show") {
                self.calls.fetch_add(1, std::sync::atomic::Ordering::SeqCst);
            }
            self.inner.request(_method, url, headers, body, timeout)
        }
    }

    impl HttpTransport for OversizedTransport {
        fn request(
            &self,
            _method: &str,
            _url: &str,
            _headers: &[(&str, &str)],
            _body: Option<&[u8]>,
            _timeout: Duration,
        ) -> Result<HttpResponse, TransportError> {
            Ok(HttpResponse {
                status: 200,
                text: "x".repeat(MAX_RESPONSE_BYTES + 1),
                content_type: None,
            })
        }
    }

    impl HttpTransport for NetworkTransport {
        fn request(
            &self,
            _method: &str,
            _url: &str,
            _headers: &[(&str, &str)],
            _body: Option<&[u8]>,
            _timeout: Duration,
        ) -> Result<HttpResponse, TransportError> {
            Err(TransportError::Network)
        }
    }

    impl HttpTransport for CountingTransport {
        fn request(
            &self,
            _method: &str,
            _url: &str,
            _headers: &[(&str, &str)],
            _body: Option<&[u8]>,
            timeout: Duration,
        ) -> Result<HttpResponse, TransportError> {
            if timeout.is_zero() || self.delay > timeout {
                if !timeout.is_zero() {
                    std::thread::sleep(timeout);
                }
                return Err(TransportError::Timeout);
            }
            let current = self
                .concurrent
                .fetch_add(1, std::sync::atomic::Ordering::SeqCst)
                + 1;
            let mut max = self
                .max_concurrent
                .load(std::sync::atomic::Ordering::SeqCst);
            while current > max {
                match self.max_concurrent.compare_exchange(
                    max,
                    current,
                    std::sync::atomic::Ordering::SeqCst,
                    std::sync::atomic::Ordering::SeqCst,
                ) {
                    Ok(_) => break,
                    Err(observed) => max = observed,
                }
            }
            std::thread::sleep(self.delay);
            self.concurrent
                .fetch_sub(1, std::sync::atomic::Ordering::SeqCst);
            Ok(HttpResponse {
                status: 200,
                text: r#"{"models":[],"data":[]}"#.to_owned(),
                content_type: Some("application/json".to_owned()),
            })
        }
    }

    fn target(id: &str, kind: RuntimeKind, adapter: AdapterType) -> ProbeTarget {
        ProbeTarget {
            runtime_id: id.to_owned(),
            kind,
            endpoint: "http://127.0.0.1:11434".to_owned(),
            source: RuntimeSource::Auto,
            adapter_type: adapter,
            timeout: None,
        }
    }

    #[test]
    fn registry_probe_respects_max_workers_and_returns_every_target() {
        let concurrent = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let max_concurrent = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let transport = CountingTransport {
            concurrent: std::sync::Arc::clone(&concurrent),
            max_concurrent: std::sync::Arc::clone(&max_concurrent),
            delay: Duration::from_millis(40),
        };
        let registry = RuntimeProbeRegistry::new(Arc::new(transport), Duration::from_secs(2), 2)
            .expect("registry builds");
        let targets = vec![
            ProbeTarget {
                endpoint: "http://127.0.0.1:11434".to_owned(),
                ..target(
                    "a",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://127.0.0.1:11435".to_owned(),
                ..target(
                    "b",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://127.0.0.1:11436".to_owned(),
                ..target(
                    "c",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://127.0.0.1:11437".to_owned(),
                ..target(
                    "d",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
        ];
        let descriptors = registry.probe(targets).expect("probes");
        assert_eq!(descriptors.len(), 4);
        let mut ids: Vec<_> = descriptors.iter().map(|d| d.runtime_id.clone()).collect();
        ids.sort();
        assert_eq!(ids, vec!["a", "b", "c", "d"]);
        assert!(max_concurrent.load(std::sync::atomic::Ordering::SeqCst) <= 2);
    }

    #[test]
    fn registry_deadline_marks_all_targets_without_extra_work() {
        let concurrent = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let max_concurrent = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let transport = CountingTransport {
            concurrent: std::sync::Arc::clone(&concurrent),
            max_concurrent: std::sync::Arc::clone(&max_concurrent),
            delay: Duration::from_millis(300),
        };
        let registry = RuntimeProbeRegistry::new(Arc::new(transport), Duration::from_millis(80), 2)
            .expect("registry builds");
        let targets = vec![
            ProbeTarget {
                endpoint: "http://127.0.0.1:11434".to_owned(),
                ..target(
                    "a",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://127.0.0.1:11435".to_owned(),
                ..target(
                    "b",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
        ];
        let descriptors = registry.probe(targets).expect("probes");
        assert_eq!(descriptors.len(), 2);
        for descriptor in &descriptors {
            assert!(
                matches!(
                    descriptor.status,
                    RuntimeStatus::Offline | RuntimeStatus::Error
                ),
                "deadline exhaustion is honest non-success"
            );
            assert!(descriptor.error.is_some());
        }
    }

    #[test]
    fn validate_targets_rejects_remote_duplicate_and_mismatched_adapter() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let remote = ProbeTarget {
            runtime_id: "remote".to_owned(),
            kind: RuntimeKind::OpenAiCompatible,
            endpoint: "http://192.168.1.1:8080".to_owned(),
            source: RuntimeSource::Auto,
            adapter_type: AdapterType::OpenAiCompatible,
            timeout: None,
        };
        assert!(registry.validate_targets(vec![remote]).is_err());
        let mismatched = ProbeTarget {
            adapter_type: AdapterType::Ollama,
            ..target("m", RuntimeKind::OpenAiCompatible, AdapterType::Ollama)
        };
        assert!(registry.validate_targets(vec![mismatched]).is_err());
        let duplicate = vec![
            target(
                "x",
                RuntimeKind::OpenAiCompatible,
                AdapterType::OpenAiCompatible,
            ),
            target(
                "x",
                RuntimeKind::OpenAiCompatible,
                AdapterType::OpenAiCompatible,
            ),
        ];
        assert!(registry.validate_targets(duplicate).is_err());
    }

    #[test]
    fn bound_descriptor_preserves_verified_evidence_kind() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let descriptor = build_runtime_descriptor(
            "ollama-local",
            RuntimeKind::OpenAiCompatible,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            false,
            true,
            true,
            RuntimeStatus::Online,
            Vec::new(),
            Vec::new(),
            None,
            Vec::new(),
            None,
        );
        let target = target("t", RuntimeKind::Ollama, AdapterType::Ollama);
        let bounded = registry.bound_descriptor(descriptor, &target);
        assert_eq!(bounded.kind, RuntimeKind::OpenAiCompatible);
    }

    #[test]
    fn bound_models_rejects_oversized_identity_and_vendor() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let target = target(
            "t",
            RuntimeKind::OpenAiCompatible,
            AdapterType::OpenAiCompatible,
        );
        let model = ModelDescriptor {
            runtime_id: "t".to_owned(),
            model_id: "x".repeat(MAX_MODEL_FIELD_BYTES + 1),
            display_name: "x".to_owned(),
            digest: None,
            family: None,
            parameter_count: None,
            parameter_label: None,
            format: None,
            quantization: None,
            size_bytes: None,
            context_length: None,
            capabilities: Vec::new(),
            trainability: None,
            metadata_source: "runtime".to_owned(),
            last_seen_at: now_iso(),
            identity_strength: ModelIdentityStrength::RuntimeModelId,
        };
        assert!(registry.bound_models(vec![model], &target).is_err());

        let descriptor = build_runtime_descriptor(
            "t",
            RuntimeKind::OpenAiCompatible,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            false,
            true,
            true,
            RuntimeStatus::Online,
            Vec::new(),
            Vec::new(),
            None,
            Vec::new(),
            Some("v".repeat(MAX_MODEL_FIELD_BYTES + 1)),
        );
        let bounded = registry.bound_descriptor(descriptor, &target);
        assert_eq!(bounded.status, RuntimeStatus::Error);
    }

    #[test]
    fn model_overflow_marks_descriptor_non_success() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let model = ModelDescriptor {
            runtime_id: "t".to_owned(),
            model_id: "m".to_owned(),
            display_name: "m".to_owned(),
            digest: None,
            family: None,
            parameter_count: Some(-1),
            parameter_label: None,
            format: None,
            quantization: None,
            size_bytes: None,
            context_length: None,
            capabilities: Vec::new(),
            trainability: None,
            metadata_source: "runtime".to_owned(),
            last_seen_at: now_iso(),
            identity_strength: ModelIdentityStrength::RuntimeModelId,
        };
        let target = target(
            "t",
            RuntimeKind::OpenAiCompatible,
            AdapterType::OpenAiCompatible,
        );
        assert!(registry.bound_models(vec![model], &target).is_err());
    }

    #[test]
    fn parse_parameter_label_rejects_unsafe_values() {
        assert_eq!(parse_parameter_label(Some("4b")), Some(4_000_000_000));
        assert_eq!(parse_parameter_label(Some("1.5B")), Some(1_500_000_000));
        assert_eq!(parse_parameter_label(Some("0b")), None);
        assert_eq!(parse_parameter_label(Some("-2b")), None);
        assert_eq!(parse_parameter_label(Some("nan")), None);
        assert_eq!(parse_parameter_label(Some("inf")), None);
        assert_eq!(parse_parameter_label(Some("9999999999999999999b")), None);
    }

    #[test]
    fn ipv6_loopback_endpoint_is_canonicalized() {
        let parts = validate_url("http://[::1]:11434/").expect("parses ipv6");
        assert_eq!(parts.host, "::1");
        assert_eq!(parts.port, 11434);
        let canonical = canonical_endpoint("http://[::1]:11434").expect("canonical");
        assert_eq!(canonical, "http://[::1]:11434");
        assert!(is_loopback_endpoint("http://[::1]:11434"));
        assert!(validate_url("http://[::1]:0").is_err());
    }

    #[test]
    fn registry_config_revalidation_blocks_mutation() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_millis(500),
            2,
        )
        .expect("registry builds");
        assert!(registry.revalidate_config().is_ok());
        // RuntimeProbeRegistry fields are private; the only public mutation
        // path would be through a fabricated config, which cannot be built
        // after `new`. Revalidation is proven by invalid target timeouts.
        let bad_timeout = ProbeTarget {
            timeout: Some(Duration::from_secs(999)),
            ..target(
                "t",
                RuntimeKind::OpenAiCompatible,
                AdapterType::OpenAiCompatible,
            )
        };
        assert!(registry.validate_targets(vec![bad_timeout]).is_err());
    }

    #[test]
    fn validate_targets_rejects_same_id_different_endpoint() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let targets = vec![
            ProbeTarget {
                endpoint: "http://127.0.0.1:11434".to_owned(),
                ..target(
                    "x",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://127.0.0.1:11435".to_owned(),
                ..target(
                    "x",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
        ];
        assert!(registry.validate_targets(targets).is_err());
    }

    #[test]
    fn validate_targets_rejects_different_id_same_canonical_target() {
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let targets = vec![
            ProbeTarget {
                endpoint: "http://127.0.0.1:11434".to_owned(),
                ..target(
                    "x",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
            ProbeTarget {
                endpoint: "http://localhost:11434".to_owned(),
                ..target(
                    "y",
                    RuntimeKind::OpenAiCompatible,
                    AdapterType::OpenAiCompatible,
                )
            },
        ];
        assert!(registry.validate_targets(targets).is_err());
    }

    #[test]
    fn host_header_emits_bracketed_ipv6() {
        let parts = validate_url("http://[::1]:11434/").expect("parses");
        assert_eq!(host_header(&parts), "[::1]:11434");
        let parts = validate_url("http://127.0.0.1:11434").expect("parses");
        assert_eq!(host_header(&parts), "127.0.0.1:11434");
    }

    #[test]
    fn validate_origin_exact_and_hostile() {
        assert_eq!(
            validate_origin("http://127.0.0.1:11434", true).expect("local"),
            "http://127.0.0.1:11434"
        );
        assert_eq!(
            validate_origin("http://[::1]:11434", true).expect("ipv6"),
            "http://[::1]:11434"
        );
        assert!(validate_origin("http://localhost:11434", true).is_ok());
        assert!(validate_origin("http://192.168.1.1:8080", true).is_err());
        assert!(validate_origin("http://example.com", true).is_err());
        assert!(validate_origin("http://127.0.0.1:0", true).is_err());
        assert!(validate_origin("http://127.0.0.1:8080/path", true).is_err());
        assert!(validate_origin("http://user:pass@127.0.0.1", true).is_err());
        assert!(validate_origin("http://127.0.0.1:8080?x=1", true).is_err());
        assert!(validate_origin("http://[::1]", true).is_ok());
        assert!(validate_origin("http://[::1]:0", true).is_err());
    }

    #[test]
    fn bounded_output_is_exact_utf8_bytes() {
        let value = "😀".repeat(200);
        let bounded = bounded_text(&value, 64);
        assert!(bounded.len() <= 64);
        let registry = RuntimeProbeRegistry::new(
            Arc::new(CountingTransport {
                concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
                delay: Duration::ZERO,
            }),
            Duration::from_secs(1),
            2,
        )
        .expect("registry builds");
        let target = target(
            "t",
            RuntimeKind::OpenAiCompatible,
            AdapterType::OpenAiCompatible,
        );
        let model = ModelDescriptor {
            runtime_id: "t".to_owned(),
            model_id: "m".to_owned(),
            display_name: "😀".repeat(200),
            digest: None,
            family: None,
            parameter_count: None,
            parameter_label: None,
            format: None,
            quantization: None,
            size_bytes: None,
            context_length: None,
            capabilities: Vec::new(),
            trainability: None,
            metadata_source: "runtime".to_owned(),
            last_seen_at: now_iso(),
            identity_strength: ModelIdentityStrength::RuntimeModelId,
        };
        assert!(registry.bound_models(vec![model], &target).is_err());
    }

    #[test]
    fn bounded_text_is_exact_for_every_budget() {
        for max in 0..=20 {
            let bounded = bounded_text(&"😀".repeat(100), max);
            assert!(bounded.len() <= max, "max={max} got {}", bounded.len());
        }
        assert_eq!(bounded_text("abc", 0), "");
        assert_eq!(bounded_text("abcdef", 3), "...");
    }

    #[test]
    fn dial_host_normalizes_localhost() {
        assert_eq!(dial_host("localhost"), "127.0.0.1");
        assert_eq!(dial_host("127.0.0.1"), "127.0.0.1");
        assert_eq!(dial_host("[::1]"), "[::1]");
    }

    #[test]
    fn validate_origin_rejects_hostile_remote_and_allows_https_remote() {
        assert!(validate_origin("https://example.com:443", false).is_ok());
        assert!(validate_origin("http://example.com", false).is_ok());
        assert!(validate_origin("http://[not-v6]:8080", false).is_err());
        assert!(validate_origin("http://foo::bar", false).is_err());
        assert!(validate_origin("http://", false).is_err());
        assert!(validate_origin("http://a..b", false).is_err());
        assert!(validate_origin("http://-bad.example", false).is_err());
        assert!(validate_origin("http://bad-.example", false).is_err());
        assert!(validate_origin("http://bad_label.example", false).is_err());
        assert!(validate_origin("http://[::1]:0", false).is_err());
        assert!(validate_origin("http://[::1]:443", false).is_ok());
    }

    #[test]
    fn adapter_rejects_oversized_injected_responses() {
        let transport: SharedTransport = Arc::new(OversizedTransport);
        let ollama = OllamaAdapter::new(
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            Arc::clone(&transport),
            Instant::now() + Duration::from_secs(1),
            false,
            10,
        );
        let descriptor = ollama.probe();
        assert_eq!(descriptor.status, RuntimeStatus::Error);
        assert!(descriptor.models.is_empty());
        let openai = OpenAiCompatAdapter::new(
            "t",
            RuntimeKind::OpenAiCompatible,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            transport,
            Instant::now() + Duration::from_secs(1),
            false,
            None,
            10,
        );
        let descriptor = openai.probe();
        assert_eq!(descriptor.status, RuntimeStatus::Error);
        assert!(descriptor.models.is_empty());
    }

    #[test]
    fn ollama_show_is_skipped_after_deadline() {
        let calls = std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0));
        let transport = CountingTransport {
            concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            max_concurrent: std::sync::Arc::new(std::sync::atomic::AtomicUsize::new(0)),
            delay: Duration::ZERO,
        };
        let transport = CountingUrlTransport {
            inner: transport,
            calls: std::sync::Arc::clone(&calls),
        };
        let adapter = OllamaAdapter::new(
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            Arc::new(transport),
            Instant::now(),
            false,
            10,
        );
        let descriptor = adapter.probe();
        assert_eq!(descriptor.status, RuntimeStatus::Offline);
        let show_calls = calls.load(std::sync::atomic::Ordering::SeqCst);
        assert_eq!(show_calls, 0, "no /api/show calls after deadline");
    }

    #[test]
    fn network_error_maps_to_offline_for_both_adapters() {
        let network: SharedTransport = Arc::new(NetworkTransport);
        let ollama = OllamaAdapter::new(
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            Arc::clone(&network),
            Instant::now() + Duration::from_secs(1),
            false,
            10,
        );
        assert_eq!(ollama.probe().status, RuntimeStatus::Offline);
        let openai = OpenAiCompatAdapter::new(
            "t",
            RuntimeKind::OpenAiCompatible,
            "http://127.0.0.1:11434",
            RuntimeSource::Auto,
            network,
            Instant::now() + Duration::from_secs(1),
            false,
            None,
            10,
        );
        assert_eq!(openai.probe().status, RuntimeStatus::Offline);
    }

    #[test]
    fn transport_rejects_https_and_crlf_injection() {
        assert!(matches!(
            LoopbackHttpTransport
                .request_with_clock(
                    &SystemClock,
                    "GET",
                    "https://127.0.0.1:11434/api/tags",
                    &[],
                    None,
                    Duration::from_secs(1),
                )
                .expect_err("rejects https"),
            TransportError::Protocol(_)
        ));
        assert!(matches!(
            LoopbackHttpTransport
                .request_with_clock(
                    &SystemClock,
                    "GET",
                    "http://127.0.0.1:11434/api/tags",
                    &[("X-Test", "a\r\nInjected: yes")],
                    None,
                    Duration::from_secs(1),
                )
                .expect_err("rejects crlf header"),
            TransportError::Protocol(_)
        ));
        assert!(matches!(
            LoopbackHttpTransport
                .request_with_clock(
                    &SystemClock,
                    "GET",
                    "http://127.0.0.1:11434/api/tags\r\nX: y",
                    &[],
                    None,
                    Duration::from_secs(1),
                )
                .expect_err("rejects crlf path"),
            TransportError::Protocol(_)
        ));
    }

    #[test]
    fn response_reader_preserves_body_prefix_from_single_read() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let data =
            b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\nContent-Type: application/json\r\n\r\n{\"status\":\"ok\"}".to_vec();
        let mut reader = &data[..];
        let parts = read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(1))
            .expect("parses");
        assert_eq!(parts.status, 200);
        assert_eq!(parts.content_length, Some(13));
        assert_eq!(parts.body_prefix, b"{\"status\":\"ok\"}");
    }

    #[test]
    fn response_reader_rejects_invalid_content_length_without_underflow() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let data = b"HTTP/1.1 200 OK\r\nContent-Length: 18446744073709551615\r\n\r\n";
        let mut reader = &data[..];
        let error = read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(1))
            .expect_err("rejects oversized length");
        assert!(matches!(error, TransportError::Protocol(_)));
    }

    #[test]
    fn response_reader_slow_drip_cannot_extend_total_budget() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let data = b"HTTP/1.1 200 OK\r\nContent-Length: 13\r\n\r\n{\"status\":\"ok\"}";
        let mut reader = SplitReader::new(data.to_vec(), clock.clone(), Duration::from_millis(400));
        let error = read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(1))
            .expect_err("slow drip times out");
        assert!(matches!(error, TransportError::Timeout));
    }

    #[test]
    fn response_reader_rejects_raw_header_controls_and_bad_status() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let data = b"HTTP/1.1 200 OK\r\nX-Bad: \tvalue\r\nContent-Length: 0\r\n\r\n";
        let mut reader = &data[..];
        assert!(
            read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(1)).is_err()
        );

        let data = b"HTTP/2.0 200 OK\r\nContent-Length: 0\r\n\r\n";
        let mut reader = &data[..];
        assert!(
            read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(1)).is_err()
        );
    }

    #[test]
    fn read_response_headers_and_body_prefix_are_bounded() {
        let start = Instant::now();
        let clock = FakeClock::new(start);
        let data = b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\nab";
        let mut reader = &data[..];
        let parts = read_response_with_clock(&mut reader, &clock, start + Duration::from_secs(5))
            .expect("headers parse");
        assert_eq!(parts.body_prefix, b"ab");
    }
}

fn read_response_with_clock<R: ReadWithRemaining, C: TransportClock>(
    reader: &mut R,
    clock: &C,
    deadline: Instant,
) -> Result<ResponseParts, TransportError> {
    const MAX_HEADER_BYTES: usize = 16 * 1024;
    let mut buffer = Vec::new();
    let mut chunk = [0u8; 8192];
    let header_end;
    loop {
        let remaining = remaining_budget(clock, deadline)?;
        match reader.read_with_remaining(&mut chunk, remaining) {
            Ok(0) => return Err(TransportError::Network),
            Ok(count) => {
                if clock.now() >= deadline {
                    return Err(TransportError::Timeout);
                }
                buffer.extend_from_slice(&chunk[..count]);
                if let Some(position) = find_double_crlf(&buffer) {
                    header_end = position;
                    if header_end > MAX_HEADER_BYTES {
                        return Err(TransportError::Protocol(
                            "runtime response headers are too large".to_owned(),
                        ));
                    }
                    break;
                }
                if buffer.len() > MAX_HEADER_BYTES {
                    return Err(TransportError::Protocol(
                        "runtime response headers are too large".to_owned(),
                    ));
                }
            }
            Err(error)
                if error.kind() == io::ErrorKind::WouldBlock
                    || error.kind() == io::ErrorKind::TimedOut =>
            {
                return Err(TransportError::Timeout);
            }
            Err(error) if error.kind() == io::ErrorKind::Interrupted => continue,
            Err(_) => return Err(TransportError::Network),
        }
    }
    let body_prefix = buffer[header_end..].to_vec();
    let text = String::from_utf8_lossy(&buffer[..header_end]).into_owned();
    let mut lines = text.split("\r\n");
    let status_line = lines.next().unwrap_or_default();
    let mut parts = status_line.split(' ');
    let _version = parts.next();
    if !status_line.starts_with("HTTP/1.0 ") && !status_line.starts_with("HTTP/1.1 ") {
        return Err(TransportError::Protocol(
            "runtime response status line is not HTTP/1.x".to_owned(),
        ));
    }
    let status: u16 = parts
        .next()
        .and_then(|value| value.parse().ok())
        .ok_or_else(|| TransportError::Protocol("runtime response status is invalid".to_owned()))?;
    let mut headers = Vec::new();
    let mut content_length = None;
    let mut transfer_encoding: Option<String> = None;
    for line in lines {
        if line.is_empty() {
            continue;
        }
        if line.bytes().any(is_http_control) {
            return Err(TransportError::Protocol(
                "runtime response header contains control characters".to_owned(),
            ));
        }
        let Some((name, value)) = line.split_once(':') else {
            return Err(TransportError::Protocol(
                "runtime response contains a malformed header line".to_owned(),
            ));
        };
        let name = name.trim().to_ascii_lowercase();
        if line.as_bytes().starts_with(b" ") || line.as_bytes().starts_with(b"\t") {
            return Err(TransportError::Protocol(
                "runtime response header contains obs-fold or leading whitespace".to_owned(),
            ));
        }
        if name.is_empty()
            || !name
                .bytes()
                .all(|byte| byte.is_ascii_alphanumeric() || b"!#$%&'*+-.^_`|~".contains(&byte))
        {
            return Err(TransportError::Protocol(
                "runtime response contains an invalid header name".to_owned(),
            ));
        }
        let value = value.trim().to_owned();
        if value.bytes().any(is_http_control) {
            return Err(TransportError::Protocol(
                "runtime response contains control characters in a header value".to_owned(),
            ));
        }
        if name == "content-length" {
            if content_length.is_some() {
                return Err(TransportError::Protocol(
                    "runtime response contains duplicate content-length".to_owned(),
                ));
            }
            content_length = value
                .parse()
                .ok()
                .filter(|length: &u64| *length <= MAX_RESPONSE_BYTES as u64);
            if content_length.is_none() {
                return Err(TransportError::Protocol(
                    "runtime response content-length is invalid or oversized".to_owned(),
                ));
            }
        }
        if name == "transfer-encoding" {
            if transfer_encoding.is_some() || !value.eq_ignore_ascii_case("identity") {
                return Err(TransportError::Protocol(
                    "runtime response transfer-encoding is not supported".to_owned(),
                ));
            }
            transfer_encoding = Some(value.clone());
        }
        headers.push((name, value));
    }
    Ok(ResponseParts {
        status,
        headers,
        content_length,
        body_prefix,
    })
}

fn find_double_crlf(buffer: &[u8]) -> Option<usize> {
    buffer
        .windows(4)
        .position(|window| window == b"\r\n\r\n")
        .map(|position| position + 4)
}

/// Validate an explicit origin for acquisition: http(s), loopback only for
/// local policy, no credentials/fragments/query, exact port bounds.
pub fn validate_origin(endpoint: &str, local_only: bool) -> Result<String, String> {
    if endpoint.is_empty() || endpoint.len() > MAX_ENDPOINT_BYTES {
        return Err("acquisition endpoint is invalid".to_owned());
    }
    let (scheme, rest) = endpoint
        .split_once("://")
        .ok_or_else(|| "acquisition endpoint must be http(s)".to_owned())?;
    if scheme != "http" && scheme != "https" {
        return Err("acquisition endpoint must be http(s)".to_owned());
    }
    if rest.contains('@') || rest.contains('#') || rest.contains('?') {
        return Err(
            "acquisition endpoint must not contain credentials, fragments, or queries".to_owned(),
        );
    }
    let (authority, _path) = match rest.split_once('/') {
        Some((_authority, path)) if !path.is_empty() && path != "/" => {
            return Err("acquisition endpoint must be an origin without a path".to_owned());
        }
        Some((authority, _)) => (authority, ""),
        None => (rest, ""),
    };
    if authority.is_empty()
        || authority.contains(' ')
        || authority.bytes().any(|b| b < 0x20 || b == 0x7f)
    {
        return Err("acquisition endpoint host is invalid".to_owned());
    }
    let (host, port) = match parse_ipv6_authority(authority) {
        Some((host, port)) => (host, port),
        None => match authority.rsplit_once(':') {
            Some((host, port)) => {
                if port.is_empty() {
                    return Err("acquisition endpoint port is incomplete".to_owned());
                }
                let port: u16 = port
                    .parse()
                    .map_err(|_| "acquisition endpoint port is invalid".to_owned())?;
                if port == 0 {
                    return Err("acquisition endpoint port is out of range".to_owned());
                }
                (host.to_owned(), Some(port))
            }
            None => (authority.to_owned(), None),
        },
    };
    let host = host.trim_end_matches('.').to_ascii_lowercase();
    if host.is_empty() || host.contains(' ') || host.bytes().any(|b| b < 0x20 || b == 0x7f) {
        return Err("acquisition endpoint host is invalid".to_owned());
    }
    if !valid_origin_host(&host) {
        return Err("acquisition endpoint host is invalid".to_owned());
    }
    if local_only && !is_loopback_host(&host) {
        return Err(
            "remote acquisition is denied by local-only policy; explicit remote approval is required"
                .to_owned(),
        );
    }
    if port == Some(0) {
        return Err("acquisition endpoint port is out of range".to_owned());
    }
    let display_host = if host.contains(':') {
        format!("[{host}]")
    } else {
        host
    };
    Ok(match port {
        Some(port) => format!("{scheme}://{display_host}:{port}"),
        None => format!("{scheme}://{display_host}"),
    })
}

fn valid_origin_host(host: &str) -> bool {
    if host.parse::<std::net::IpAddr>().is_ok() {
        return true;
    }
    if host.len() > 253 || host.is_empty() {
        return false;
    }
    host.split('.').all(valid_dns_label)
}

fn valid_dns_label(label: &str) -> bool {
    !label.is_empty()
        && label.len() <= 63
        && label
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || byte == b'-')
        && !label.starts_with('-')
        && !label.ends_with('-')
}

/// Loopback-only endpoint check used when building discovery targets.
pub fn is_loopback_endpoint(endpoint: &str) -> bool {
    validate_url(endpoint).is_ok()
}

fn adapter_matches_kind(adapter: AdapterType, kind: RuntimeKind) -> bool {
    match adapter {
        AdapterType::Ollama => kind == RuntimeKind::Ollama,
        AdapterType::LmStudio => kind == RuntimeKind::LmStudio,
        AdapterType::LlamaCpp => kind == RuntimeKind::LlamaCpp,
        AdapterType::MlxLm => kind == RuntimeKind::MlxLm,
        AdapterType::OpenAiCompatible | AdapterType::Auto => {
            matches!(kind, RuntimeKind::OpenAiCompatible | RuntimeKind::Unknown)
        }
    }
}

fn canonical_endpoint(endpoint: &str) -> Result<String, String> {
    let parts = validate_url(endpoint).map_err(|_| "probe endpoint is invalid".to_owned())?;
    let dial_host = if parts.host == "localhost" {
        "127.0.0.1".to_owned()
    } else {
        parts.host.clone()
    };
    let host = if dial_host.contains(':') {
        format!("[{dial_host}]")
    } else {
        dial_host
    };
    Ok(format!("http://{host}:{}", parts.port))
}

fn endpoint_path_is_origin(endpoint: &str) -> bool {
    let Some((_scheme, rest)) = endpoint.split_once("://") else {
        return false;
    };
    let path = rest.split_once('/').map(|(_, path)| path).unwrap_or("");
    path.is_empty() || path == "/"
}
