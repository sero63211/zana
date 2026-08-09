"""Bounded local structured observability for ZANA.

Observability is local-only, synchronous, redacted, strictly bounded,
dependency-free, and cross-platform. Telemetry remains disabled and no remote
transport exists.
"""

from zana_core.observability.events import (
    Event,
    EventContext,
    EventKind,
    Severity,
)
from zana_core.observability.redact import (
    RedactedValue,
    redact_event,
    redact_object,
)
from zana_core.observability.serialization import (
    JsonLinesCodec,
    SerializationFailureError,
    serialize_event,
)
from zana_core.observability.sinks import (
    BoundedMemorySink,
    CompositeSink,
    LocalJsonlSink,
    RedactedRecord,
    SinkStats,
    TelemetryDisabledSink,
    WriteResult,
)

__all__ = [
    "BoundedMemorySink",
    "CompositeSink",
    "Event",
    "EventContext",
    "EventKind",
    "JsonLinesCodec",
    "LocalJsonlSink",
    "RedactedRecord",
    "RedactedValue",
    "SerializationFailureError",
    "Severity",
    "SinkStats",
    "TelemetryDisabledSink",
    "WriteResult",
    "redact_event",
    "redact_object",
    "serialize_event",
]
