"""Typed evaluation case, result, settings, and verification models."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class ScorerType(str, Enum):
    """Built-in scorer types aligned with the ZANA evaluation spec."""

    EXACT_STRING = "exact_string"
    CASE_NORMALIZED_EXACT = "case_normalized_exact"
    NUMERIC_EXACT = "numeric_exact"
    NUMERIC_TOLERANCE = "numeric_tolerance"
    REGEX = "regex"
    CONTAINS_ALL = "contains_all"
    JSON_SCHEMA_VALID = "json_schema_valid"
    CLASSIFICATION_LABEL = "classification_label"
    CITATION_REQUIRED = "citation_required"
    SOURCE_GROUNDING = "source_grounding"


class ScorerConfig(BaseModel):
    """Configuration for one scorer, preserving raw expected values."""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())

    type: ScorerType
    expected: Any = None
    tolerance: float | None = Field(default=None, ge=0)
    json_schema: dict[str, Any] | None = Field(default=None, alias="schema")


class EvaluationCase(BaseModel):
    """One immutable evaluation case."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    prompt: str = Field(min_length=1)
    scorer: ScorerConfig
    tags: list[str] = Field(default_factory=list)


class ScorerResult(BaseModel):
    """Raw scoring output with failure reasons preserved."""

    model_config = ConfigDict(extra="forbid")

    case_id: str
    scorer_type: ScorerType
    passed: bool
    score: float = Field(ge=0, le=1)
    raw_output: str = ""
    failure_reason: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


class ReproducibilitySettings(BaseModel):
    """Captured decoding/runtime settings, never invoked by this package."""

    model_config = ConfigDict(extra="forbid")

    temperature_expectation: float = Field(default=0.0, ge=0)
    seed: int | None = None
    max_tokens: int | None = Field(default=None, gt=0)
    runtime_identity: str | None = None
    model_identity: str | None = None
    model_version: str | None = None
    context_policy: str | None = None


class AggregateMetrics(BaseModel):
    """Deterministic aggregate metrics over real raw results."""

    model_config = ConfigDict(extra="forbid")

    cases: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0, le=1)
    invalid: int = Field(ge=0, default=0)


class EvaluationSuiteResult(BaseModel):
    """Full deterministic suite result with raw outputs preserved."""

    model_config = ConfigDict(extra="forbid")

    suite_id: str = Field(min_length=1)
    reproducibility: ReproducibilitySettings
    results: list[ScorerResult] = Field(default_factory=list)
    metrics: AggregateMetrics
    ran_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class BaselineCandidateComparison(BaseModel):
    """Baseline/candidate comparison with delta and gate outcomes."""

    model_config = ConfigDict(extra="forbid")

    baseline: AggregateMetrics
    candidate: AggregateMetrics
    delta: float
    gate_outcomes: dict[str, bool]
    status: str


class VerificationStatus(str, Enum):
    """Verification lifecycle statuses from the ZANA image spec."""

    UNVERIFIED = "unverified"
    VERIFIED_LOCAL = "verified-local"
    VERIFIED_REPRODUCIBLE = "verified-reproducible"
    VERIFICATION_FAILED = "verification-failed"


class GateDecision(str, Enum):
    """Outcome of a declared verification gate."""

    PASS = "PASS"
    FAIL = "FAIL"


class GateResult(BaseModel):
    """One declared gate's deterministic outcome."""

    model_config = ConfigDict(extra="forbid")

    name: str
    decision: GateDecision
    observed: float
    threshold: float | None = None
    message: str


def utc_now() -> datetime:
    return datetime.now(UTC)
