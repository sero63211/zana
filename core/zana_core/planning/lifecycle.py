"""Build lifecycle phase ordering and cancellation-checkpoint metadata."""

from __future__ import annotations

from zana_core.domain.enums import BuildJobStatus
from zana_core.planning.models import (
    BuildPolicy,
    CancellationCheckpoint,
    CapabilityFacts,
    EvaluationFacts,
    LifecyclePlan,
    ModelFacts,
    PhasePlan,
    StrategyComponent,
    StrategyDecision,
)


def _checkpoint(
    *,
    safe: bool,
    subprocess: bool,
    rollback: bool,
    cleanup: bool,
) -> CancellationCheckpoint:
    return CancellationCheckpoint(
        safe_cancellation_supported=safe,
        subprocess_termination_required=subprocess,
        transaction_rollback_required=rollback,
        temp_workspace_cleanup_required=cleanup,
    )


def build_lifecycle(
    *,
    policy: BuildPolicy,
    capability: CapabilityFacts,
    evaluation: EvaluationFacts,
    strategy: StrategyDecision,
    model: ModelFacts,
) -> LifecyclePlan:
    """Return the canonical ordered phase plan for a composed strategy."""
    phases: list[PhasePlan] = []
    has_adapter = StrategyComponent.ADAPTER in strategy.components
    has_rag = StrategyComponent.RAG in strategy.components
    has_evaluation = evaluation.has_domain or evaluation.has_regression

    phases.append(
        PhasePlan(
            phase=BuildJobStatus.ANALYZING,
            required=True,
            reason="hash inputs, compute data statistics, and produce compatibility facts",
            checkpoint=_checkpoint(safe=True, subprocess=False, rollback=True, cleanup=True),
        )
    )
    phases.append(
        PhasePlan(
            phase=BuildJobStatus.BASELINE_RUNNING,
            required=True,
            reason="measure the base model before any candidate changes",
            checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
        )
    )
    phases.append(
        PhasePlan(
            phase=BuildJobStatus.PLANNED,
            required=True,
            reason="immutable build plan is approved before execution",
            checkpoint=_checkpoint(safe=True, subprocess=False, rollback=True, cleanup=True),
        )
    )

    downloads_needed = has_adapter and not model.training_source_available
    if downloads_needed:
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.ACQUIRING_APPROVED_ARTIFACTS,
                required=True,
                reason="adapter training requires the approved trainable checkpoint",
                checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
            )
        )
    if has_rag:
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.BUILDING_KNOWLEDGE,
                required=True,
                reason="parse, chunk, embed, and index the approved knowledge sources",
                checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
            )
        )
    if has_adapter:
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.TRAINING_ADAPTER,
                required=True,
                reason="train the single approved adapter against the exact base identity",
                checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
            )
        )
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.MATERIALIZING,
                required=True,
                reason="materialize the adapter for the selected runtime",
                checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
            )
        )
    if has_evaluation or policy.require_verification:
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.EVALUATING,
                required=True,
                reason="run held-out domain/regression suites against the candidate",
                checkpoint=_checkpoint(safe=True, subprocess=True, rollback=True, cleanup=True),
            )
        )
    phases.append(
        PhasePlan(
            phase=BuildJobStatus.PACKING,
            required=True,
            reason="pack immutable content-addressed artifacts",
            checkpoint=_checkpoint(safe=True, subprocess=False, rollback=True, cleanup=True),
        )
    )
    if policy.require_verification:
        phases.append(
            PhasePlan(
                phase=BuildJobStatus.VERIFIED,
                required=True,
                reason="verification gate passes before the image is labeled verified",
                checkpoint=_checkpoint(safe=False, subprocess=False, rollback=False, cleanup=False),
            )
        )
    return LifecyclePlan(phases=tuple(phases))
