"""Mapping from validated capability packages and hardware profiles."""

from __future__ import annotations

from pathlib import Path

from tests.capabilities.helpers import (
    MATH_MANIFEST,
    TRAIN_JSONL,
    VALID_JSONL,
    build_math_example,
    write,
)
from zana_core.capabilities.validator import CapabilitySourceValidator
from zana_core.hardware.models import (
    AcceleratorInfo,
    AcceleratorKind,
    BackendAvailability,
    BackendKind,
    BackendRole,
    CpuInfo,
    DiskInfo,
    HardwareProfile,
    MemoryInfo,
    OSType,
)
from zana_core.planning.models import (
    CapabilityFacts,
    EvaluationFacts,
    HardwareFacts,
)


def test_capability_facts_from_validation(tmp_path):
    root = Path(tmp_path)
    write(
        root,
        "zana.yaml",
        MATH_MANIFEST.replace("minimumExamples: 100", "minimumExamples: 2"),
    )
    write(
        root,
        "evals/domain.jsonl",
        '{"id":"e","prompt":"p","scorer":{"type":"numeric_exact","expected":1}}\n',
    )
    write(root, "training/train.jsonl", TRAIN_JSONL)
    write(root, "training/valid.jsonl", VALID_JSONL)
    write(
        root,
        "tools/tools.yaml",
        "tools:\n  - id: calculator\n    provider: zana.builtin\n    version: 1\n",
    )
    write(
        root,
        "permissions/policy.yaml",
        "network:\n  outbound: false\n",
    )
    result = CapabilitySourceValidator().validate(root)
    facts = CapabilityFacts.from_capability_validation(result)
    assert facts.manifest_id == "io.zana.demo.math"
    assert facts.has_tools is True
    assert facts.has_training is True
    assert facts.train_record_count == 2
    assert facts.validation_record_count == 1
    assert facts.training_goal == "structured_reasoning"
    assert facts.leakage_ok is True
    assert facts.required_context_tokens is None


def test_evaluation_facts_from_validation(tmp_path):
    root = build_math_example(Path(tmp_path))
    result = CapabilitySourceValidator().validate(root)
    facts = EvaluationFacts.from_capability_validation(result)
    assert facts.has_domain is True
    assert facts.has_regression is False
    assert facts.domain_records == 2
    assert facts.held_out_ok is True


def test_hardware_facts_from_profile():
    profile = HardwareProfile(
        os=OSType.MACOS,
        arch="arm64",
        cpu=CpuInfo(name="Apple M2 Pro", logical_cores=12),
        memory=MemoryInfo(total_bytes=16_000_000_000, available_bytes=9_000_000_000),
        disk=DiskInfo(path="/", free_bytes=200_000_000_000),
        accelerators=[AcceleratorInfo(kind=AcceleratorKind.APPLE_METAL, shared_memory=True)],
        training_backends=[
            BackendAvailability(
                backend=BackendKind.MLX_LM,
                role=BackendRole.TRAINING,
                installed=True,
            )
        ],
        runtime_backends=[
            BackendAvailability(
                backend=BackendKind.OLLAMA,
                role=BackendRole.RUNTIME,
                installed=True,
            )
        ],
        collected_at="2026-08-09T00:00:00Z",
    )
    facts = HardwareFacts.from_hardware_profile(profile)
    assert facts.arch == "arm64"
    assert facts.training_backends == ("mlx-lm",)
    assert facts.runtime_backends == ("ollama",)
    assert facts.accelerator_kinds == ("apple_metal",)
