//! Canonical ZANA operational enums and bounded parsing.

use serde::Serialize;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RuntimeKind {
    Ollama,
    LmStudio,
    LlamaCpp,
    MlxLm,
    OpenAiCompatible,
    Unknown,
}

impl RuntimeKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Ollama => "ollama",
            Self::LmStudio => "lm-studio",
            Self::LlamaCpp => "llama.cpp",
            Self::MlxLm => "mlx-lm",
            Self::OpenAiCompatible => "openai-compatible",
            Self::Unknown => "unknown",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "ollama" => Some(Self::Ollama),
            "lm-studio" => Some(Self::LmStudio),
            "llama.cpp" => Some(Self::LlamaCpp),
            "mlx-lm" => Some(Self::MlxLm),
            "openai-compatible" => Some(Self::OpenAiCompatible),
            "unknown" => Some(Self::Unknown),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RuntimeSource {
    Auto,
    Manual,
}

impl RuntimeSource {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Auto => "auto",
            Self::Manual => "manual",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "auto" => Some(Self::Auto),
            "manual" => Some(Self::Manual),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum RuntimeStatus {
    Unknown,
    Online,
    Offline,
    Error,
}

impl RuntimeStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::Online => "online",
            Self::Offline => "offline",
            Self::Error => "error",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "unknown" => Some(Self::Unknown),
            "online" => Some(Self::Online),
            "offline" => Some(Self::Offline),
            "error" => Some(Self::Error),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum ModelIdentityStrength {
    Unknown,
    ExactDigest,
    RuntimeModelId,
    DisplayNameOnly,
}

impl ModelIdentityStrength {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Unknown => "unknown",
            Self::ExactDigest => "exact_digest",
            Self::RuntimeModelId => "runtime_model_id",
            Self::DisplayNameOnly => "display_name_only",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "unknown" => Some(Self::Unknown),
            "exact_digest" => Some(Self::ExactDigest),
            "runtime_model_id" => Some(Self::RuntimeModelId),
            "display_name_only" => Some(Self::DisplayNameOnly),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum JobKind {
    RuntimeRefresh,
    ModelPull,
    BuildAnalysis,
    Build,
    ImageExport,
    ImageImport,
}

impl JobKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::RuntimeRefresh => "runtime_refresh",
            Self::ModelPull => "model_pull",
            Self::BuildAnalysis => "build_analysis",
            Self::Build => "build",
            Self::ImageExport => "image_export",
            Self::ImageImport => "image_import",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "runtime_refresh" => Some(Self::RuntimeRefresh),
            "model_pull" => Some(Self::ModelPull),
            "build_analysis" => Some(Self::BuildAnalysis),
            "build" => Some(Self::Build),
            "image_export" => Some(Self::ImageExport),
            "image_import" => Some(Self::ImageImport),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum JobStatus {
    Pending,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

impl JobStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Pending => "PENDING",
            Self::Running => "RUNNING",
            Self::Succeeded => "SUCCEEDED",
            Self::Failed => "FAILED",
            Self::Cancelled => "CANCELLED",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "PENDING" => Some(Self::Pending),
            "RUNNING" => Some(Self::Running),
            "SUCCEEDED" => Some(Self::Succeeded),
            "FAILED" => Some(Self::Failed),
            "CANCELLED" => Some(Self::Cancelled),
            _ => None,
        }
    }

    pub fn is_terminal(self) -> bool {
        matches!(self, Self::Succeeded | Self::Failed | Self::Cancelled)
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum JobEventKind {
    Created,
    StatusChanged,
    Progress,
    Error,
    Cancelled,
}

impl JobEventKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Created => "CREATED",
            Self::StatusChanged => "STATUS_CHANGED",
            Self::Progress => "PROGRESS",
            Self::Error => "ERROR",
            Self::Cancelled => "CANCELLED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum PlatformLabel {
    Macos,
    Linux,
    Windows,
    Unknown,
}

impl PlatformLabel {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Macos => "macos",
            Self::Linux => "linux",
            Self::Windows => "windows",
            Self::Unknown => "unknown",
        }
    }

    pub fn parse(value: &str) -> Self {
        match value {
            "macos" => Self::Macos,
            "linux" => Self::Linux,
            "windows" => Self::Windows,
            _ => Self::Unknown,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Serialize)]
pub enum OperationCategory {
    Tiny,
    Metadata,
    ReadOnly,
    Build,
    EmbeddingIndex,
    Inference,
    Training,
    Export,
    Portability,
}

impl OperationCategory {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Tiny => "tiny",
            Self::Metadata => "metadata",
            Self::ReadOnly => "read_only",
            Self::Build => "build",
            Self::EmbeddingIndex => "embedding_index",
            Self::Inference => "inference",
            Self::Training => "training",
            Self::Export => "export",
            Self::Portability => "portability",
        }
    }

    pub fn parse(value: &str) -> Option<Self> {
        match value {
            "tiny" => Some(Self::Tiny),
            "metadata" => Some(Self::Metadata),
            "read_only" => Some(Self::ReadOnly),
            "build" => Some(Self::Build),
            "embedding_index" => Some(Self::EmbeddingIndex),
            "inference" => Some(Self::Inference),
            "training" => Some(Self::Training),
            "export" => Some(Self::Export),
            "portability" => Some(Self::Portability),
            _ => None,
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum AcquisitionKind {
    OllamaPull,
    Unsupported,
}

impl AcquisitionKind {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::OllamaPull => "ollama_pull",
            Self::Unsupported => "unsupported",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum AcquisitionState {
    Preflight,
    Running,
    Succeeded,
    Failed,
    Cancelled,
}

impl AcquisitionState {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Preflight => "PREFLIGHT",
            Self::Running => "RUNNING",
            Self::Succeeded => "SUCCEEDED",
            Self::Failed => "FAILED",
            Self::Cancelled => "CANCELLED",
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize)]
pub enum AcquisitionPolicy {
    LocalOnly,
    ExplicitRemoteAllowed,
}

impl AcquisitionPolicy {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::LocalOnly => "local_only",
            Self::ExplicitRemoteAllowed => "explicit_remote_allowed",
        }
    }
}
