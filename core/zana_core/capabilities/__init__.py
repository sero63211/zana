"""Canonical validation for editable ZANA Capability Sources.

This package validates an editable capability package (zana.yaml plus
declared behavior, knowledge, training, evaluation, tools, and permissions
files) without executing any of its content.  Validation is deterministic,
fails closed on unsupported or unsafe input, and never touches databases,
APIs, or ingestion pipelines.
"""

from zana_core.capabilities.behavior import BehaviorSource
from zana_core.capabilities.errors import (
    CapabilityIssue,
    CapabilitySourceValidationError,
)
from zana_core.capabilities.evaluation import EvaluationRecord, EvaluationSet
from zana_core.capabilities.leakage import LeakageReport
from zana_core.capabilities.manifest import CapabilityManifest
from zana_core.capabilities.provenance import SourceProvenance, SourceRole
from zana_core.capabilities.training import TrainingRecord, TrainingSet
from zana_core.capabilities.validator import (
    CapabilitySourceValidation,
    CapabilitySourceValidator,
    EvaluationCollections,
    TrainingCollections,
)

__all__ = [
    "BehaviorSource",
    "CapabilityIssue",
    "CapabilityManifest",
    "CapabilitySourceValidation",
    "CapabilitySourceValidationError",
    "CapabilitySourceValidator",
    "EvaluationCollections",
    "EvaluationRecord",
    "EvaluationSet",
    "LeakageReport",
    "SourceProvenance",
    "SourceRole",
    "TrainingCollections",
    "TrainingRecord",
    "TrainingSet",
]
