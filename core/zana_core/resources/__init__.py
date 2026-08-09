"""Strict, lightweight resource admission governance for ZANA Core.

This package is dependency-light and thread-free: it captures cheap snapshots,
admits bounded operations, and accounts for active leases synchronously.
Nothing here starts background threads, polls, or touches model runtimes.
"""

from zana_core.resources.batching import (
    BatchLimitError,
    BatchPlan,
    iter_batches,
    plan_batch,
    validate_batch_limits,
)
from zana_core.resources.governor import (
    ResourceAdmissionError,
    ResourceGovernor,
    ResourceLeaseError,
)
from zana_core.resources.guards import (
    BuildResourceGuard,
    EmbeddingIndexResourceGuard,
    InferenceTrainingResourceGuard,
    PortabilityResourceGuard,
    ResourceGuard,
)
from zana_core.resources.models import (
    AdmissionDecision,
    AdmissionOutcome,
    CategoryLimit,
    DenialReason,
    OperationCategory,
    OperationRequest,
    PlatformLabel,
    RecoveryAction,
    ResourceLease,
    ResourcePolicy,
    ResourceSnapshot,
    UsageRecord,
)
from zana_core.resources.snapshot import (
    DefaultSnapshotProvider,
    SnapshotProvider,
)

__all__ = [
    "AdmissionDecision",
    "AdmissionOutcome",
    "BatchLimitError",
    "BatchPlan",
    "BuildResourceGuard",
    "CategoryLimit",
    "DefaultSnapshotProvider",
    "DenialReason",
    "EmbeddingIndexResourceGuard",
    "InferenceTrainingResourceGuard",
    "OperationCategory",
    "OperationRequest",
    "PlatformLabel",
    "PortabilityResourceGuard",
    "RecoveryAction",
    "ResourceAdmissionError",
    "ResourceGovernor",
    "ResourceGuard",
    "ResourceLease",
    "ResourceLeaseError",
    "ResourcePolicy",
    "ResourceSnapshot",
    "SnapshotProvider",
    "UsageRecord",
    "iter_batches",
    "plan_batch",
    "validate_batch_limits",
]
