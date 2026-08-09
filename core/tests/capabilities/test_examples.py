"""End-to-end validation of both authoritative example capability packages."""

from __future__ import annotations

from tests.capabilities.helpers import (
    build_math_example,
    build_policy_example,
    make_validator,
)


def test_math_example_validates(tmp_path):
    result = make_validator().validate(build_math_example(tmp_path))
    assert result.manifest.id == "io.zana.demo.math"
    assert result.leakage.ok is True
    assert result.training.train is None
    assert result.training.validation is None
    assert result.behavior is None
    assert result.evaluation.domain is not None
    assert len(result.evaluation.domain.records) == 2
    assert len(result.provenance) == 4


def test_policy_example_validates(tmp_path):
    result = make_validator().validate(build_policy_example(tmp_path))
    assert result.manifest.id == "io.zana.demo.policy"
    assert result.leakage.ok is True
    assert result.behavior is not None
    assert result.training.train is None
    assert result.evaluation.domain is not None
    assert len(result.evaluation.domain.records) == 3
    knowledge = [item for item in result.provenance if item.role.value == "knowledge"]
    assert len(knowledge) == 2
    assert result.manifest.knowledge is not None
    assert result.manifest.knowledge.citationRequired is True


def test_examples_are_deterministic(tmp_path):
    math_root = build_math_example(tmp_path / "math")
    policy_root = build_policy_example(tmp_path / "policy")
    assert make_validator().validate(math_root) == make_validator().validate(math_root)
    assert make_validator().validate(policy_root) == make_validator().validate(policy_root)
