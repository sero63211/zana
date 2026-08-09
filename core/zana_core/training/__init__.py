"""Safe, non-executing ZANA training foundation.

This package builds typed contracts and command specifications as data only.
It never imports ML frameworks, starts providers, downloads artifacts, or
executes training commands.
"""

from zana_core.training.adapters import AdapterValidation, validate_adapter
from zana_core.training.cancellation import (
    InvalidCancellationTransitionError,
    mark_cancelled,
    promote_adapter,
    transition_run,
)
from zana_core.training.contracts import (
    AdapterBaseIdentity,
    AdapterMaterializationCompatibility,
    AdapterMetadata,
    AdapterState,
    CancellationState,
    CompatibilityDecision,
    DatasetSplitManifest,
    InferenceIdentity,
    InvocationSpec,
    ProviderProbe,
    ProviderProbeStatus,
    ResourceGuard,
    ResourceGuardDecision,
    RunRecord,
    SyntheticDataset,
    TrainingRequestConfig,
    TrainingSourceIdentity,
    TrainingState,
)
from zana_core.training.datasets import (
    DatasetIsolation,
    TrainingTargetPolicy,
    check_split_isolation,
    held_out_range_disjoint,
    reject_raw_documents_as_training_targets,
    synthetic_dataset_contract,
)
from zana_core.training.identity import enforce_exact_base_identity
from zana_core.training.invocations import (
    build_hf_peft_invocation,
    build_mlx_lm_invocation,
    require_dataset_hashes,
)
from zana_core.training.materialization import decide_materialization
from zana_core.training.providers import (
    HfPeftProviderProbe,
    MLXLMProviderProbe,
    ProviderEnvironment,
    ProviderRegistry,
)
from zana_core.training.resources import ResourceGuards

__all__ = [
    "AdapterBaseIdentity",
    "AdapterMaterializationCompatibility",
    "AdapterMetadata",
    "AdapterState",
    "AdapterValidation",
    "CancellationState",
    "CompatibilityDecision",
    "DatasetIsolation",
    "DatasetSplitManifest",
    "HfPeftProviderProbe",
    "InferenceIdentity",
    "InvocationSpec",
    "InvalidCancellationTransitionError",
    "MLXLMProviderProbe",
    "ProviderEnvironment",
    "ProviderProbe",
    "ProviderProbeStatus",
    "ProviderRegistry",
    "ResourceGuard",
    "ResourceGuardDecision",
    "ResourceGuards",
    "RunRecord",
    "SyntheticDataset",
    "TrainingRequestConfig",
    "TrainingSourceIdentity",
    "TrainingState",
    "TrainingTargetPolicy",
    "build_hf_peft_invocation",
    "build_mlx_lm_invocation",
    "check_split_isolation",
    "decide_materialization",
    "enforce_exact_base_identity",
    "held_out_range_disjoint",
    "mark_cancelled",
    "promote_adapter",
    "reject_raw_documents_as_training_targets",
    "require_dataset_hashes",
    "synthetic_dataset_contract",
    "transition_run",
    "validate_adapter",
]
