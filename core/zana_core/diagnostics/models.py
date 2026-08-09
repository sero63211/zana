"""Strict immutable diagnostic models with redacted evidence."""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class Severity(str, Enum):
    """Severity of a diagnostic finding."""

    INFO = "info"
    WARN = "warn"
    ERROR = "error"


class CheckStatus(str, Enum):
    """Actual diagnostic check outcome."""

    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    UNAVAILABLE = "unavailable"
    SKIPPED = "skipped"


class RecoveryAction(BaseModel):
    """Safe, actionable recovery instruction; never auto-executes."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    optional: bool = False


class Evidence(BaseModel):
    """Redacted evidence; never full paths, secrets, or document contents."""

    model_config = ConfigDict(frozen=True)

    observed_source: str
    value: str | int | float | bool | None = None
    basename: str | None = None
    digest_prefix: str | None = None
    boolean_presence: bool | None = None
    notes: list[str] = Field(default_factory=list, max_length=8)


class FeatureReadiness(BaseModel):
    """Narrow readiness mapping for optional features."""

    model_config = ConfigDict(frozen=True)

    feature: str
    ready: bool
    blocks_core_start: bool = False
    blocks_feature_only: bool = False
    missing_reason: str = ""


class ProbeBudget(BaseModel):
    """Deterministic bounded probe budget."""

    model_config = ConfigDict(frozen=True)

    max_checks: int = Field(default=64, gt=0)
    per_check_timeout_seconds: float = Field(default=1.0, gt=0)
    total_budget_seconds: float = Field(default=8.0, gt=0)
    max_output_chars: int = Field(default=2_000, gt=0)
    max_path_count: int = Field(default=32, gt=0)
    max_error_count: int = Field(default=64, gt=0)


class DiagnosticCheck(BaseModel):
    """One immutable check result."""

    model_config = ConfigDict(frozen=True)

    check_id: str
    name: str
    status: CheckStatus
    severity: Severity
    duration_seconds: float = Field(ge=0)
    observed_source: str
    observed_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    evidence: Evidence
    issues: list[DiagnosticIssue] = Field(default_factory=list, max_length=8)
    feature_readiness: list[FeatureReadiness] = Field(default_factory=list, max_length=8)


class DiagnosticIssue(BaseModel):
    """One immutable issue with stable code and safe recovery actions."""

    model_config = ConfigDict(frozen=True)

    code: str
    severity: Severity
    message: str
    recovery_actions: list[RecoveryAction] = Field(default_factory=list, max_length=8)


class AggregateHealth(str, Enum):
    """Deterministic aggregate health."""

    HEALTHY = "healthy"
    PASS_WITH_LIMITED_FEATURES = "pass_with_limited_features"
    FAILED = "failed"


class DiagnosticReport(BaseModel):
    """Full bounded doctor report."""

    model_config = ConfigDict(frozen=True)

    generated_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    budget: ProbeBudget
    checks: list[DiagnosticCheck] = Field(default_factory=list)
    aggregate_health: AggregateHealth
    total_duration_seconds: float = Field(ge=0)
    skipped_or_unavailable_count: int = Field(ge=0)
    error_count: int = Field(ge=0)
    details: dict[str, Any] = Field(default_factory=dict)
