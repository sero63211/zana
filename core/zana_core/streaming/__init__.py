"""Canonical bounded SSE/event-stream primitives."""

from zana_core.streaming.encoder import (
    SSEEncoder,
    StreamEncodeError,
    StreamLimitError,
    encode_keepalive_comment,
)
from zana_core.streaming.models import (
    CursorCheck,
    CursorStatus,
    ErrorMetadata,
    EventBatch,
    EventCursor,
    EventKind,
    InvalidCursorError,
    ResumeDecision,
    StreamEvent,
    StreamLimits,
)
from zana_core.streaming.redaction import (
    DEFAULT_REDACTION_LIMITS,
    RedactionLimits,
    RedactionProvider,
    Redactor,
    redact_value,
)
from zana_core.streaming.source import (
    DrainCancelledError,
    DrainResult,
    DrainTimeoutError,
    EventSource,
    StreamOrderError,
    drain,
)

__all__ = [
    "CursorCheck",
    "CursorStatus",
    "DEFAULT_REDACTION_LIMITS",
    "DrainCancelledError",
    "DrainResult",
    "DrainTimeoutError",
    "ErrorMetadata",
    "EventBatch",
    "EventCursor",
    "EventKind",
    "EventSource",
    "InvalidCursorError",
    "RedactionLimits",
    "RedactionProvider",
    "Redactor",
    "ResumeDecision",
    "SSEEncoder",
    "StreamEncodeError",
    "StreamEvent",
    "StreamLimitError",
    "StreamLimits",
    "StreamOrderError",
    "drain",
    "encode_keepalive_comment",
    "redact_value",
]
