"""Conservative, assumption-driven disk and memory estimation."""

from __future__ import annotations

from zana_core.planning.models import (
    BuildPolicy,
    CapabilityFacts,
    HardwareFacts,
    ModelFacts,
    ResourceCheck,
    ResourceEstimate,
    StrategyComponent,
    StrategyDecision,
    TrainingProviderCompatibility,
)

MEBIBYTE = 1024 * 1024
GIBIBYTE = 1024**3

# Deterministic multiplier assumptions, documented in the estimate reasons.
KNOWLEDGE_WORKSPACE_MIN_FACTOR = 2.0
KNOWLEDGE_WORKSPACE_MAX_FACTOR = 4.0
KNOWLEDGE_INDEX_MIN_FACTOR = 0.2
KNOWLEDGE_INDEX_MAX_FACTOR = 0.5
ADAPTER_WORKSPACE_MIN_FACTOR = 1.5
ADAPTER_WORKSPACE_MAX_FACTOR = 3.0
ADAPTER_CACHE_MIN_FACTOR = 1.0
ADAPTER_CACHE_MAX_FACTOR = 1.1
CHECKPOINT_DOWNLOAD_MAX_FACTOR = 1.2
INFERENCE_MEMORY_MIN_OVERHEAD = 512 * MEBIBYTE
INFERENCE_MEMORY_MAX_OVERHEAD = GIBIBYTE
INFERENCE_MEMORY_MIN_FACTOR = 1.0
INFERENCE_MEMORY_MAX_FACTOR = 1.5
TRAINING_MEMORY_MIN_FACTOR = 2.0
TRAINING_MEMORY_MAX_FACTOR = 3.5


def model_weight_bytes(model: ModelFacts) -> int | None:
    """Best available model weight size; parameter-count fallback is documented."""
    if model.size_bytes is not None:
        return model.size_bytes
    if model.parameter_count is not None:
        return model.parameter_count * 2
    return None


def estimate_resources(
    *,
    policy: BuildPolicy,
    model: ModelFacts,
    capability: CapabilityFacts,
    strategy: StrategyDecision,
    provider: TrainingProviderCompatibility,
    hardware: HardwareFacts,
) -> ResourceEstimate:
    """Compute conservative min/max ranges with explicit assumptions."""
    assumptions: list[str] = []
    disk_min = 0
    disk_max = 0
    weight = model_weight_bytes(model)
    has_adapter = StrategyComponent.ADAPTER in strategy.components

    if capability.knowledge_bytes is not None:
        knowledge = float(capability.knowledge_bytes)
        disk_min += int(knowledge * KNOWLEDGE_WORKSPACE_MIN_FACTOR) + int(
            knowledge * KNOWLEDGE_INDEX_MIN_FACTOR
        )
        disk_max += int(knowledge * KNOWLEDGE_WORKSPACE_MAX_FACTOR) + int(
            knowledge * KNOWLEDGE_INDEX_MAX_FACTOR
        )
        assumptions.append("knowledge workspace retains 2x-4x source bytes and index 0.2x-0.5x")
    else:
        assumptions.append("knowledge source bytes unknown; RAG disk use left open")

    if has_adapter:
        if weight is None:
            disk_min = None
            disk_max = None
            assumptions.append("adapter workspace needs model weight size, which is unknown")
        else:
            disk_min += int(weight * ADAPTER_WORKSPACE_MIN_FACTOR)
            disk_max += int(weight * ADAPTER_WORKSPACE_MAX_FACTOR)
            assumptions.append(
                "adapter training workspace estimated at 1.5x-3.0x model weights "
                "for LoRA/QLoRA plus optimizer state"
            )
            if not model.training_source_available:
                disk_min += int(weight)
                disk_max += int(weight * CHECKPOINT_DOWNLOAD_MAX_FACTOR)
                assumptions.append(
                    "trainable checkpoint download estimated at 1.0x-1.2x model weights"
                )
            else:
                assumptions.append("trainable checkpoint is already available locally")
            disk_min += int(weight * ADAPTER_CACHE_MIN_FACTOR)
            disk_max += int(weight * ADAPTER_CACHE_MAX_FACTOR)
            assumptions.append(
                "runtime adapter materialization cache estimated at 1.0x-1.1x model weights"
            )

    if disk_min is None or disk_max is None:
        disk_min, disk_max = None, None
    assumptions.append("disk estimate is conservative; exact usage is measured only during build")

    memory_min = 0
    memory_max = 0
    if weight is not None:
        memory_min = int(weight * INFERENCE_MEMORY_MIN_FACTOR) + INFERENCE_MEMORY_MIN_OVERHEAD
        memory_max = int(weight * INFERENCE_MEMORY_MAX_FACTOR) + INFERENCE_MEMORY_MAX_OVERHEAD
        assumptions.append(
            "inference memory estimated at 1.0x-1.5x model weights plus 512 MiB-1 GiB"
        )
        if has_adapter:
            if provider.peak_memory_estimate_bytes is not None:
                memory_min = max(memory_min, provider.peak_memory_estimate_bytes)
                memory_max = max(memory_max, provider.peak_memory_estimate_bytes)
                assumptions.append(
                    "training peak memory from provider estimate is used when available"
                )
            else:
                training_min = int(weight * TRAINING_MEMORY_MIN_FACTOR)
                training_max = int(weight * TRAINING_MEMORY_MAX_FACTOR)
                memory_min = max(memory_min, training_min)
                memory_max = max(memory_max, training_max)
                assumptions.append(
                    "training peak memory estimated at 2.0x-3.5x model weights "
                    "when the provider exposes no estimate"
                )
    else:
        memory_min = None
        memory_max = None
        assumptions.append("model weight size unknown; memory ranges left open")
    assumptions.append(
        "duration is not estimated before measurement; exact timing is never promised"
    )

    reserve = policy.safety_reserve_fraction
    disk_min_reserved = int(disk_min * (1.0 + reserve)) if disk_min is not None else None
    disk_max_reserved = int(disk_max * (1.0 + reserve)) if disk_max is not None else None
    memory_min_reserved = int(memory_min * (1.0 + reserve)) if memory_min is not None else None
    memory_max_reserved = int(memory_max * (1.0 + reserve)) if memory_max is not None else None
    return ResourceEstimate(
        disk_bytes_min=disk_min,
        disk_bytes_max=disk_max,
        memory_bytes_min=memory_min,
        memory_bytes_max=memory_max,
        disk_bytes_min_with_reserve=disk_min_reserved,
        disk_bytes_max_with_reserve=disk_max_reserved,
        memory_bytes_min_with_reserve=memory_min_reserved,
        memory_bytes_max_with_reserve=memory_max_reserved,
        assumptions=tuple(assumptions),
        safety_reserve_fraction=reserve,
        duration_estimate="unknown",
    )


def check_resources(
    *,
    policy: BuildPolicy,
    hardware: HardwareFacts,
    estimate: ResourceEstimate,
) -> ResourceCheck:
    """Compare estimates against policy and hardware; unknown state is honest."""
    blockers: list[str] = []
    warnings: list[str] = []
    reasons: list[str] = []
    disk_within_policy: bool | None = None
    disk_within_available: bool | None = None
    memory_within_policy: bool | None = None
    memory_within_available: bool | None = None

    policy_disk = int(policy.max_disk_gb * GIBIBYTE) if policy.max_disk_gb is not None else None
    if estimate.disk_bytes_max_with_reserve is None:
        warnings.append("disk requirement is unknown and cannot be bounded before acquisition")
        reasons.append("unknown source or model sizes prevent a disk range")
    else:
        if policy_disk is not None:
            if estimate.disk_bytes_max_with_reserve <= policy_disk:
                disk_within_policy = True
                reasons.append("disk estimate with reserve is within the policy limit")
            else:
                disk_within_policy = False
                blockers.append("DISK_OVER_POLICY_LIMIT: estimated disk use exceeds max_disk_gb")
        if hardware.disk_free_bytes is not None:
            if estimate.disk_bytes_max_with_reserve <= hardware.disk_free_bytes:
                disk_within_available = True
                reasons.append("disk estimate is within currently free disk space")
            else:
                disk_within_available = False
                blockers.append("DISK_INSUFFICIENT: estimated disk use exceeds free disk space")
        else:
            warnings.append("free disk space is unknown; disk feasibility is unconfirmed")

    policy_memory_limit = (
        int(policy.max_memory_fraction * hardware.memory_total_bytes)
        if hardware.memory_total_bytes is not None
        else None
    )
    if estimate.memory_bytes_max_with_reserve is None:
        warnings.append("memory requirement is unknown and cannot be bounded before acquisition")
        reasons.append("model weight size is unknown")
    else:
        if policy_memory_limit is not None:
            if estimate.memory_bytes_max_with_reserve <= policy_memory_limit:
                memory_within_policy = True
                reasons.append("memory estimate with reserve is within the policy fraction")
            else:
                memory_within_policy = False
                blockers.append(
                    "MEMORY_OVER_POLICY_LIMIT: estimated peak memory exceeds "
                    "max_memory_fraction of total memory"
                )
        else:
            warnings.append("total memory is unknown; policy memory limit cannot be checked")
        if hardware.memory_available_bytes is not None:
            if estimate.memory_bytes_max_with_reserve <= hardware.memory_available_bytes:
                memory_within_available = True
                reasons.append("memory estimate fits currently available memory")
            else:
                memory_within_available = False
                blockers.append(
                    "MEMORY_INSUFFICIENT: estimated peak memory exceeds available memory"
                )
        else:
            warnings.append("available memory is unknown; feasibility is unconfirmed")

    return ResourceCheck(
        disk_within_policy=disk_within_policy,
        disk_within_available=disk_within_available,
        memory_within_policy=memory_within_policy,
        memory_within_available=memory_within_available,
        blockers=tuple(blockers),
        warnings=tuple(warnings),
        reasons=tuple(reasons),
    )
