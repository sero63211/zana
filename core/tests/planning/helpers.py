"""Shared immutable facts for build planner tests."""

from __future__ import annotations

from zana_core.domain.enums import ModelIdentityStrength
from zana_core.planning.models import (
    AcquisitionMode,
    BuildPolicy,
    CapabilityFacts,
    DownloadMode,
    EvaluationFacts,
    HardwareFacts,
    ModelFacts,
    TrainingProviderCompatibility,
)


def policy(**overrides) -> BuildPolicy:
    defaults = {
        "strategy": "auto",
        "acquisition": AcquisitionMode.DENY_AFTER_ACQUISITION,
        "prefer_training": False,
        "max_disk_gb": 40.0,
        "max_memory_fraction": 0.75,
        "require_verification": True,
        "allow_adapter_training": True,
        "allow_external_artifact_downloads": DownloadMode.ASK,
        "safety_reserve_fraction": 0.15,
    }
    defaults.update(overrides)
    return BuildPolicy(**defaults)


def model_facts(**overrides) -> ModelFacts:
    defaults = {
        "model_id": "ollama:llama3.1:8b",
        "display_name": "llama3.1:8b",
        "digest": "a" * 64,
        "parameter_count": 8_000_000_000,
        "size_bytes": 5_000_000_000,
        "context_length": 8192,
        "capabilities": ("completion",),
        "identity_strength": ModelIdentityStrength.EXACT_DIGEST,
        "runtime_identity": "a" * 64,
        "training_source_identity": "a" * 64,
        "adapter_base_identity": "a" * 64,
        "training_source_available": True,
        "runtime_online": True,
    }
    defaults.update(overrides)
    return ModelFacts(**defaults)


def capability_facts(**overrides) -> CapabilityFacts:
    defaults = {
        "manifest_id": "io.zana.demo.math",
        "name": "Math Demo",
        "version": "0.1.0",
        "has_behavior": False,
        "has_knowledge": False,
        "knowledge_citation_required": False,
        "knowledge_source_count": 0,
        "knowledge_bytes": None,
        "has_tools": True,
        "tool_ids": ("zana.calculator",),
        "has_training": True,
        "training_goal": "structured_reasoning",
        "training_optional": False,
        "train_record_count": 120,
        "validation_record_count": 40,
        "minimum_examples": 100,
        "training_files_present": True,
        "has_evaluation": True,
        "evaluation_domain_records": 50,
        "evaluation_regression_records": 20,
        "leakage_ok": True,
        "required_context_tokens": 4096,
        "required_model_capabilities": ("completion",),
    }
    defaults.update(overrides)
    return CapabilityFacts(**defaults)


def evaluation_facts(**overrides) -> EvaluationFacts:
    defaults = {
        "has_domain": True,
        "has_regression": True,
        "domain_records": 50,
        "regression_records": 20,
        "held_out_ok": True,
    }
    defaults.update(overrides)
    return EvaluationFacts(**defaults)


def provider_facts(**overrides) -> TrainingProviderCompatibility:
    defaults = {
        "provider_id": "mlx-lm",
        "supported": True,
        "installed": True,
        "compatible_arch": True,
        "peak_memory_estimate_bytes": None,
        "reasons": ("mlx_lm supports LoRA on Apple Silicon",),
    }
    defaults.update(overrides)
    return TrainingProviderCompatibility(**defaults)


def hardware_facts(**overrides) -> HardwareFacts:
    defaults = {
        "os": "macos",
        "arch": "arm64",
        "memory_total_bytes": 34_359_738_368,
        "memory_available_bytes": 25_000_000_000,
        "disk_free_bytes": 200_000_000_000,
        "accelerator_kinds": ("apple_metal",),
        "training_backends": ("mlx-lm",),
        "runtime_backends": ("ollama",),
        "notes": (),
    }
    defaults.update(overrides)
    return HardwareFacts(**defaults)


def plan_inputs(**overrides):
    """Return keyword arguments for BuildPlanner.plan with sane defaults."""
    values = {
        "policy": policy(),
        "model": model_facts(),
        "capability": capability_facts(),
        "evaluation": evaluation_facts(),
        "provider": provider_facts(),
        "hardware": hardware_facts(),
    }
    values.update(overrides)
    return values
