"""Real-filesystem atomic export/import over canonical image primitives.

This package owns the atomic service orchestration: approved-root confinement,
disk preflight, guards, fsync/replace, cleanup evidence, and idempotency.
Archive codecs, OCI validation, secret/mutable-state exclusion, runnability,
and registration planning are delegated to the canonical ``zana_core.images``
stack so there is exactly one implementation of each integrity boundary.
"""

from zana_core.portability.export import ExportResult, ExportService
from zana_core.portability.guards import ConcurrentOperationError, OperationGuard
from zana_core.portability.import_ import ImportResult, ImportService
from zana_core.portability.models import (
    BlobRegistration,
    CleanupEvidence,
    CodecKind,
    DiskPreflight,
    ExportRequest,
    ImportRequest,
    LimitExceededError,
    OperationStage,
    PathPolicyError,
    PortabilityError,
    PortabilityLimits,
    PreconditionError,
    RecoveryAction,
    RegistrationPlan,
    RunnableState,
)

__all__ = [
    "BlobRegistration",
    "CleanupEvidence",
    "CodecKind",
    "ConcurrentOperationError",
    "DiskPreflight",
    "ExportRequest",
    "ExportResult",
    "ExportService",
    "ImportRequest",
    "ImportResult",
    "ImportService",
    "LimitExceededError",
    "OperationGuard",
    "OperationStage",
    "PathPolicyError",
    "PortabilityError",
    "PortabilityLimits",
    "PreconditionError",
    "RecoveryAction",
    "RegistrationPlan",
    "RunnableState",
]
