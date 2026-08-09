"""Deterministic evaluation scorers, aggregation, and verification gates.

This package is pure: it never invokes a runtime or model. It scores raw
outputs, aggregates real measurements, and decides verification gates.
"""

from zana_core.evaluation.aggregate import AggregateMetrics, aggregate
from zana_core.evaluation.gates import (
    GateDecision,
    GateResult,
    VerificationGateEngine,
)
from zana_core.evaluation.heldout import HeldOutIsolation, check_held_out_isolation
from zana_core.evaluation.models import (
    BaselineCandidateComparison,
    EvaluationCase,
    EvaluationSuiteResult,
    ReproducibilitySettings,
    ScorerConfig,
    ScorerResult,
    ScorerType,
    VerificationStatus,
)
from zana_core.evaluation.scorers import (
    ScorerInput,
    ScorerRegistry,
    score_case,
)

__all__ = [
    "AggregateMetrics",
    "BaselineCandidateComparison",
    "EvaluationCase",
    "EvaluationSuiteResult",
    "GateDecision",
    "GateResult",
    "HeldOutIsolation",
    "ReproducibilitySettings",
    "ScorerConfig",
    "ScorerInput",
    "ScorerRegistry",
    "ScorerResult",
    "ScorerType",
    "VerificationGateEngine",
    "VerificationStatus",
    "aggregate",
    "check_held_out_isolation",
    "score_case",
]
