"""Bounded ZANA training foundation with a real, injectable MLX-LM execution path.

The package never imports ML frameworks, downloads artifacts, or runs remote
code. Execution is confined to the injected ``ProcessBoundary`` used by
``TrainingExecutor``.
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
    ExecutionResult,
    ExecutionStatus,
    InferenceIdentity,
    InvocationSpec,
    LocalTrainingSource,
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
from zana_core.training.execution import (
    ProcessBoundary,
    ProcessResult,
    SubprocessBoundary,
    TrainingExecutor,
)
from zana_core.training.identity import enforce_exact_base_identity
from zana_core.training.invocations import (
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
from zana_core.training.workspaces import (
    StagedTrainingData,
    cleanup_training_workspace,
    prepare_training_workspace,
    sha256_file,
    stage_training_data,
)

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
    "ExecutionResult",
    "ExecutionStatus",
    "HfPeftProviderProbe",
    "InferenceIdentity",
    "InvocationSpec",
    "InvalidCancellationTransitionError",
    "LocalTrainingSource",
    "MLXLMProviderProbe",
    "ProcessBoundary",
    "ProcessResult",
    "ProviderEnvironment",
    "ProviderProbe",
    "ProviderProbeStatus",
    "ProviderRegistry",
    "ResourceGuard",
    "ResourceGuardDecision",
    "ResourceGuards",
    "RunRecord",
    "StagedTrainingData",
    "SubprocessBoundary",
    "SyntheticDataset",
    "TrainingExecutor",
    "TrainingRequestConfig",
    "TrainingSourceIdentity",
    "TrainingState",
    "TrainingTargetPolicy",
    "build_mlx_lm_invocation",
    "check_split_isolation",
    "cleanup_training_workspace",
    "decide_materialization",
    "enforce_exact_base_identity",
    "held_out_range_disjoint",
    "mark_cancelled",
    "prepare_training_workspace",
    "promote_adapter",
    "reject_raw_documents_as_training_targets",
    "require_dataset_hashes",
    "sha256_file",
    "stage_training_data",
    "synthetic_dataset_contract",
    "transition_run",
    "validate_adapter",
]
