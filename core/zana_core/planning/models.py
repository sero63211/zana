"""Immutable typed inputs and outputs for deterministic build planning."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zana_core.domain.enums import BuildJobStatus, ModelIdentityStrength
from zana_core.permissions.models import BUILTIN_TOOL_IDS

ADAPTER_ELIGIBLE_TRAINING_GOALS: frozenset[str] = frozenset(
    {
        "behavior",
        "classification",
        "domain_language",
        "reasoning_pattern",
        "structured_output",
        # The canonical capability example declares this goal with explicit
        # task-oriented examples; it is the documented extension to the set
        # listed in spec 07.
        "structured_reasoning",
    }
)

# Knowledge corpora at or above this size trigger the "large source corpus"
# RAG preference heuristic (1 MiB).
LARGE_KNOWLEDGE_BYTES = 1 << 20


class StrategyMode(str, Enum):
    """Auto strategy selection or an explicit, non-fallback user override."""

    AUTO = "auto"
    RAG = "rag"
    TOOLS = "tools"
    ADAPTER = "adapter"
    RAG_TOOLS = "rag+tools"
    RAG_ADAPTER = "rag+adapter"
    TOOLS_ADAPTER = "tools+adapter"
    RAG_TOOLS_ADAPTER = "rag+tools+adapter"


class AcquisitionMode(str, Enum):
    """Build-time network/acquisition behavior."""

    OFFLINE = "offline"
    ASK = "ask"
    DENY_AFTER_ACQUISITION = "deny_after_acquisition"


class DownloadMode(str, Enum):
    """Policy for external artifact downloads."""

    OFFLINE = "offline"
    ASK = "ask"
    ALLOWED = "allowed"


class StrategyComponent(str, Enum):
    """One build strategy component; adapter appears at most once."""

    RAG = "rag"
    TOOLS = "tools"
    ADAPTER = "adapter"


STRATEGY_COMPONENT_MAP: dict[StrategyMode, tuple[StrategyComponent, ...]] = {
    StrategyMode.RAG: (StrategyComponent.RAG,),
    StrategyMode.TOOLS: (StrategyComponent.TOOLS,),
    StrategyMode.ADAPTER: (StrategyComponent.ADAPTER,),
    StrategyMode.RAG_TOOLS: (StrategyComponent.RAG, StrategyComponent.TOOLS),
    StrategyMode.RAG_ADAPTER: (StrategyComponent.RAG, StrategyComponent.ADAPTER),
    StrategyMode.TOOLS_ADAPTER: (StrategyComponent.TOOLS, StrategyComponent.ADAPTER),
    StrategyMode.RAG_TOOLS_ADAPTER: (
        StrategyComponent.RAG,
        StrategyComponent.TOOLS,
        StrategyComponent.ADAPTER,
    ),
}


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BuildPolicy(_Frozen):
    """Validated user build policy; contradictory settings fail closed."""

    strategy: StrategyMode = StrategyMode.AUTO
    acquisition: AcquisitionMode = AcquisitionMode.DENY_AFTER_ACQUISITION
    prefer_training: bool = False
    max_disk_gb: float | None = None
    max_memory_fraction: float = Field(default=0.75, gt=0, le=1)
    require_verification: bool = True
    allow_adapter_training: bool = True
    allow_external_artifact_downloads: DownloadMode = DownloadMode.ASK
    safety_reserve_fraction: float = Field(default=0.15, ge=0, lt=1)

    @model_validator(mode="after")
    def _reject_contradictions(self) -> BuildPolicy:
        if self.prefer_training and not self.allow_adapter_training:
            raise ValueError(
                "prefer_training is contradictory when allow_adapter_training is false"
            )
        if self.max_disk_gb is not None and self.max_disk_gb <= 0:
            raise ValueError("max_disk_gb must be positive when set")
        if (
            self.acquisition == AcquisitionMode.OFFLINE
            and self.allow_external_artifact_downloads != DownloadMode.OFFLINE
        ):
            raise ValueError("offline acquisition contradicts non-offline external download policy")
        return self


class ModelFacts(_Frozen):
    """Immutable model identity and compatibility facts for planning."""

    model_id: str = Field(min_length=1, max_length=255)
    display_name: str = Field(min_length=1, max_length=300)
    digest: str | None = None
    family: str | None = None
    parameter_count: int | None = Field(default=None, ge=0)
    format: str | None = None
    quantization: str | None = None
    size_bytes: int | None = Field(default=None, ge=0)
    context_length: int | None = Field(default=None, ge=0)
    capabilities: tuple[str, ...] = ()
    identity_strength: ModelIdentityStrength = ModelIdentityStrength.UNKNOWN
    runtime_identity: str | None = None
    training_source_identity: str | None = None
    adapter_base_identity: str | None = None
    training_source_available: bool = False
    runtime_online: bool = True

    @model_validator(mode="after")
    def _reject_empty_identifiers(self) -> ModelFacts:
        for name in (
            "digest",
            "runtime_identity",
            "training_source_identity",
            "adapter_base_identity",
        ):
            value = getattr(self, name)
            if value is not None and not value.strip():
                raise ValueError(f"{name} must be non-empty when present")
        return self


class CapabilityFacts(_Frozen):
    """Immutable facts about one validated capability source."""

    manifest_id: str = Field(min_length=1, max_length=255)
    name: str = Field(min_length=1, max_length=300)
    version: str = Field(min_length=1, max_length=100)
    has_behavior: bool = False
    has_knowledge: bool = False
    knowledge_citation_required: bool = False
    knowledge_source_count: int = Field(default=0, ge=0)
    knowledge_bytes: int | None = Field(default=None, ge=0)
    has_tools: bool = False
    tool_ids: tuple[str, ...] = ()
    has_training: bool = False
    training_goal: str | None = None
    training_optional: bool = False
    train_record_count: int | None = Field(default=None, ge=0)
    validation_record_count: int | None = Field(default=None, ge=0)
    minimum_examples: int | None = Field(default=None, ge=0)
    training_files_present: bool = False
    has_evaluation: bool = False
    evaluation_domain_records: int | None = Field(default=None, ge=0)
    evaluation_regression_records: int | None = Field(default=None, ge=0)
    leakage_ok: bool = True
    required_context_tokens: int | None = Field(default=None, ge=0)
    required_model_capabilities: tuple[str, ...] = ()

    @classmethod
    def from_capability_validation(cls, result: Any) -> CapabilityFacts:
        """Map a validated capability package into immutable planner facts."""
        from zana_core.capabilities.provenance import SourceRole

        manifest = result.manifest
        knowledge = manifest.knowledge
        training = manifest.training
        evaluation = manifest.evaluation
        compatibility = manifest.compatibility
        knowledge_bytes = sum(
            item.size_bytes
            for item in result.provenance
            if item.role == SourceRole.KNOWLEDGE and item.size_bytes is not None
        )
        train_count = len(result.training.train.records) if result.training.train else None
        validation_count = (
            len(result.training.validation.records) if result.training.validation else None
        )
        domain_count = len(result.evaluation.domain.records) if result.evaluation.domain else None
        regression_count = (
            len(result.evaluation.regression.records) if result.evaluation.regression else None
        )
        return cls(
            manifest_id=manifest.id,
            name=manifest.name,
            version=manifest.version,
            has_behavior=manifest.behavior is not None,
            has_knowledge=knowledge is not None and bool(knowledge.sources),
            knowledge_citation_required=bool(knowledge and knowledge.citationRequired),
            knowledge_source_count=len(knowledge.sources) if knowledge and knowledge.sources else 0,
            knowledge_bytes=knowledge_bytes if knowledge_bytes > 0 else None,
            has_tools=manifest.tools is not None,
            tool_ids=(),
            has_training=training is not None,
            training_goal=training.goal if training else None,
            training_optional=bool(training and training.optional),
            train_record_count=train_count,
            validation_record_count=validation_count,
            minimum_examples=training.minimumExamples if training else None,
            training_files_present=result.training.train is not None
            or result.training.validation is not None,
            has_evaluation=evaluation is not None,
            evaluation_domain_records=domain_count,
            evaluation_regression_records=regression_count,
            leakage_ok=result.leakage.ok,
            required_context_tokens=compatibility.minimumContextTokens if compatibility else None,
            required_model_capabilities=tuple(compatibility.requiredModelCapabilities or ())
            if compatibility
            else (),
        )


class EvaluationFacts(_Frozen):
    """Immutable evaluation-suite facts used by the verification gate."""

    has_domain: bool = False
    has_regression: bool = False
    domain_records: int | None = Field(default=None, ge=0)
    regression_records: int | None = Field(default=None, ge=0)
    held_out_ok: bool = True

    @classmethod
    def from_capability_validation(cls, result: Any) -> EvaluationFacts:
        domain_count = len(result.evaluation.domain.records) if result.evaluation.domain else None
        regression_count = (
            len(result.evaluation.regression.records) if result.evaluation.regression else None
        )
        return cls(
            has_domain=result.evaluation.domain is not None,
            has_regression=result.evaluation.regression is not None,
            domain_records=domain_count,
            regression_records=regression_count,
            held_out_ok=result.leakage.ok,
        )


class TrainingProviderCompatibility(_Frozen):
    """Provider compatibility facts; never inferred from display names."""

    provider_id: str = Field(min_length=1, max_length=100)
    supported: bool = False
    installed: bool = False
    compatible_arch: bool | None = None
    peak_memory_estimate_bytes: int | None = Field(default=None, ge=0)
    reasons: tuple[str, ...] = ()


class HardwareFacts(_Frozen):
    """Immutable hardware facts relevant to resource planning."""

    os: str = ""
    arch: str = ""
    memory_total_bytes: int | None = Field(default=None, ge=0)
    memory_available_bytes: int | None = Field(default=None, ge=0)
    disk_free_bytes: int | None = Field(default=None, ge=0)
    accelerator_kinds: tuple[str, ...] = ()
    training_backends: tuple[str, ...] = ()
    runtime_backends: tuple[str, ...] = ()
    notes: tuple[str, ...] = ()

    @classmethod
    def from_hardware_profile(cls, profile: Any) -> HardwareFacts:
        """Map a HardwareProfile snapshot into immutable planner facts."""
        from zana_core.hardware.models import BackendRole

        training_backends = tuple(
            backend.backend.value
            for backend in profile.training_backends
            if backend.role == BackendRole.TRAINING and backend.installed
        )
        runtime_backends = tuple(
            backend.backend.value
            for backend in profile.runtime_backends
            if backend.role == BackendRole.RUNTIME and backend.installed
        )
        return cls(
            os=profile.os.value,
            arch=profile.arch,
            memory_total_bytes=profile.memory.total_bytes,
            memory_available_bytes=profile.memory.available_bytes,
            disk_free_bytes=profile.disk.free_bytes,
            accelerator_kinds=tuple(item.kind.value for item in profile.accelerators),
            training_backends=training_backends,
            runtime_backends=runtime_backends,
            notes=tuple(profile.notes),
        )


class StrategyDecision(_Frozen):
    """Deterministic strategy composition with explicit reasons."""

    components: tuple[StrategyComponent, ...] = ()
    strategy_id: str = "none"
    reasons: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    blockers: tuple[str, ...] = ()


class ApprovalKind(str, Enum):
    """Approval categories required before an executable plan is approved."""

    DOWNLOAD = "download"
    TRAINING = "training"
    PERMISSIONS = "permissions"
    DISK = "disk"


class ApprovalRequirement(_Frozen):
    """One explicit approval with a stable grant state."""

    kind: ApprovalKind
    description: str = Field(min_length=1)
    required: bool = True
    granted: bool = False
    reason: str = ""


class ApprovalSet(_Frozen):
    """Immutable set of approval requirements."""

    requirements: tuple[ApprovalRequirement, ...] = ()

    @classmethod
    def none_granted(cls, requirements: tuple[ApprovalRequirement, ...]) -> ApprovalSet:
        return cls(requirements=requirements)

    @classmethod
    def with_grants(
        cls,
        requirements: tuple[ApprovalRequirement, ...],
        granted: set[ApprovalKind],
    ) -> ApprovalSet:
        updated = tuple(
            requirement.model_copy(update={"granted": requirement.kind in granted})
            for requirement in requirements
        )
        return cls(requirements=updated)

    @property
    def all_granted(self) -> bool:
        return all(
            not requirement.required or requirement.granted for requirement in self.requirements
        )

    @property
    def missing(self) -> tuple[ApprovalKind, ...]:
        return tuple(
            requirement.kind
            for requirement in self.requirements
            if requirement.required and not requirement.granted
        )


class ResourceEstimate(_Frozen):
    """Conservative resource ranges with explicit assumptions."""

    disk_bytes_min: int | None = None
    disk_bytes_max: int | None = None
    memory_bytes_min: int | None = None
    memory_bytes_max: int | None = None
    disk_bytes_min_with_reserve: int | None = None
    disk_bytes_max_with_reserve: int | None = None
    memory_bytes_min_with_reserve: int | None = None
    memory_bytes_max_with_reserve: int | None = None
    assumptions: tuple[str, ...] = ()
    safety_reserve_fraction: float = 0.15
    duration_estimate: str = "unknown"


class ResourceCheck(_Frozen):
    """Resource comparison results; unknown state is never claimed safe."""

    disk_within_policy: bool | None = None
    disk_within_available: bool | None = None
    memory_within_policy: bool | None = None
    memory_within_available: bool | None = None
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    reasons: tuple[str, ...] = ()


class CancellationCheckpoint(_Frozen):
    """Deterministic cancellation metadata for one lifecycle phase."""

    safe_cancellation_supported: bool
    subprocess_termination_required: bool
    transaction_rollback_required: bool
    temp_workspace_cleanup_required: bool


class PhasePlan(_Frozen):
    """One ordered lifecycle phase with its cancellation checkpoint."""

    phase: BuildJobStatus
    required: bool
    reason: str
    checkpoint: CancellationCheckpoint


class LifecyclePlan(_Frozen):
    """Ordered phase plan; optional phases are skipped, never reordered."""

    phases: tuple[PhasePlan, ...] = ()

    @property
    def phase_names(self) -> tuple[str, ...]:
        return tuple(phase.phase.value for phase in self.phases)


class BuildPlan(_Frozen):
    """Immutable canonical build plan with a stable SHA-256 digest."""

    policy: BuildPolicy
    model: ModelFacts
    capability: CapabilityFacts
    evaluation: EvaluationFacts
    provider: TrainingProviderCompatibility
    hardware: HardwareFacts
    strategy: StrategyDecision
    resource_estimate: ResourceEstimate
    resource_check: ResourceCheck
    approvals: ApprovalSet
    lifecycle: LifecyclePlan
    blockers: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    approvable: bool = False
    digest: str = ""

    @property
    def approvals_satisfied(self) -> bool:
        return self.approvals.all_granted


def canonical_json(plan: BuildPlan) -> str:
    """Serialize a plan without its digest for deterministic hashing."""
    payload = plan.model_dump(mode="json", exclude={"digest"})
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )


def build_digest(plan: BuildPlan) -> str:
    """Return the lowercase SHA-256 hex digest of the canonical plan JSON."""
    return hashlib.sha256(canonical_json(plan).encode("utf-8")).hexdigest()


def trusted_tool_ids(capability: CapabilityFacts) -> tuple[str, ...]:
    return tuple(tool_id for tool_id in capability.tool_ids if tool_id in BUILTIN_TOOL_IDS)


def external_tool_ids(capability: CapabilityFacts) -> tuple[str, ...]:
    return tuple(tool_id for tool_id in capability.tool_ids if tool_id not in BUILTIN_TOOL_IDS)
