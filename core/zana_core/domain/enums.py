"""Canonical enums grounded in the ZANA domain and build specifications."""

from enum import Enum


class RuntimeKind(str, Enum):
    """Supported local runtime families named by the ZANA specification."""

    OLLAMA = "ollama"
    LM_STUDIO = "lm-studio"
    LLAMA_CPP = "llama.cpp"
    MLX_LM = "mlx-lm"
    OPENAI_COMPATIBLE = "openai-compatible"
    UNKNOWN = "unknown"


class RuntimeSource(str, Enum):
    """Whether a runtime record was auto-discovered or added manually."""

    AUTO = "auto"
    MANUAL = "manual"


class RuntimeStatus(str, Enum):
    """Last known runtime reachability state."""

    UNKNOWN = "unknown"
    ONLINE = "online"
    OFFLINE = "offline"
    ERROR = "error"


class ModelIdentityStrength(str, Enum):
    """How confidently a model record identifies the exact base model."""

    UNKNOWN = "unknown"
    EXACT_DIGEST = "exact_digest"
    RUNTIME_MODEL_ID = "runtime_model_id"
    DISPLAY_NAME_ONLY = "display_name_only"


class VerificationStatus(str, Enum):
    """Immutable image verification statuses from the ZANA Image spec."""

    UNVERIFIED = "unverified"
    VERIFIED_LOCAL = "verified-local"
    VERIFIED_REPRODUCIBLE = "verified-reproducible"
    VERIFICATION_FAILED = "verification-failed"


class BuildJobStatus(str, Enum):
    """Build lifecycle state machine from the ZANA build spec."""

    DRAFT = "DRAFT"
    ANALYZING = "ANALYZING"
    BASELINE_RUNNING = "BASELINE_RUNNING"
    PLANNED = "PLANNED"
    ACQUIRING_APPROVED_ARTIFACTS = "ACQUIRING_APPROVED_ARTIFACTS"
    BUILDING_KNOWLEDGE = "BUILDING_KNOWLEDGE"
    TRAINING_ADAPTER = "TRAINING_ADAPTER"
    MATERIALIZING = "MATERIALIZING"
    EVALUATING = "EVALUATING"
    PACKING = "PACKING"
    VERIFIED = "VERIFIED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    VERIFICATION_FAILED = "VERIFICATION_FAILED"


class JobKind(str, Enum):
    """Kinds of generic asynchronous work recorded by the Core."""

    RUNTIME_REFRESH = "runtime_refresh"
    MODEL_PULL = "model_pull"
    BUILD_ANALYSIS = "build_analysis"
    BUILD = "build"
    IMAGE_EXPORT = "image_export"
    IMAGE_IMPORT = "image_import"


class JobStatus(str, Enum):
    """Generic job lifecycle statuses."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class JobEventKind(str, Enum):
    """Kinds of persistent job events."""

    CREATED = "CREATED"
    STATUS_CHANGED = "STATUS_CHANGED"
    PROGRESS = "PROGRESS"
    ERROR = "ERROR"
    CANCELLED = "CANCELLED"


class MessageRole(str, Enum):
    """Message roles used by conversations and training records."""

    SYSTEM = "system"
    USER = "user"
    ASSISTANT = "assistant"
    TOOL = "tool"


class InstanceStatus(str, Enum):
    """Mutable ZANA instance lifecycle."""

    UNKNOWN = "unknown"
    STOPPED = "stopped"
    STARTING = "starting"
    RUNNING = "running"
    STOPPING = "stopping"
    ERROR = "error"


class MemoryStatus(str, Enum):
    """Approval lifecycle for instance memories."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
