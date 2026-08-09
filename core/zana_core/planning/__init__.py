"""Deterministic, non-executing build planning for ZANA.

The planner turns immutable model/capability/evaluation/provider/hardware
facts and a validated BuildPolicy into an immutable BuildPlan with a stable
SHA-256 digest. It never starts subprocesses, downloads, trains, embeds,
queries models, or writes artifacts.
"""

from zana_core.planning.estimates import ResourceCheck, ResourceEstimate
from zana_core.planning.lifecycle import (
    CancellationCheckpoint,
    LifecyclePlan,
    PhasePlan,
)
from zana_core.planning.models import (
    AcquisitionMode,
    ApprovalKind,
    ApprovalRequirement,
    ApprovalSet,
    BuildPlan,
    BuildPolicy,
    CapabilityFacts,
    DownloadMode,
    EvaluationFacts,
    HardwareFacts,
    ModelFacts,
    StrategyComponent,
    StrategyDecision,
    StrategyMode,
    TrainingProviderCompatibility,
)
from zana_core.planning.planner import BuildPlanner
from zana_core.planning.strategy import compose_strategy

__all__ = [
    "AcquisitionMode",
    "ApprovalKind",
    "ApprovalRequirement",
    "ApprovalSet",
    "BuildPlan",
    "BuildPlanner",
    "BuildPolicy",
    "CancellationCheckpoint",
    "CapabilityFacts",
    "DownloadMode",
    "EvaluationFacts",
    "HardwareFacts",
    "LifecyclePlan",
    "ModelFacts",
    "PhasePlan",
    "ResourceCheck",
    "ResourceEstimate",
    "StrategyComponent",
    "StrategyDecision",
    "StrategyMode",
    "TrainingProviderCompatibility",
    "compose_strategy",
]
