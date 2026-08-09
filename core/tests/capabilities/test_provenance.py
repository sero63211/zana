"""Deterministic hashes and immutable provenance structure tests."""

from __future__ import annotations

import hashlib
from dataclasses import FrozenInstanceError

import pytest
from pydantic import ValidationError

from tests.capabilities.helpers import (
    FIXED_INGESTED_AT,
    MATH_EVAL_JSONL,
    MATH_MANIFEST,
    build_math_example,
    build_policy_example,
    make_validator,
)


def test_hashes_are_deterministic(tmp_path):
    root = build_math_example(tmp_path)
    first = make_validator().validate(root)
    second = make_validator().validate(root)
    assert first.provenance == second.provenance
    manifest_prov = next(item for item in first.provenance if item.role.value == "manifest")
    assert manifest_prov.sha256 == hashlib.sha256(MATH_MANIFEST.encode("utf-8")).hexdigest()
    eval_prov = next(item for item in first.provenance if item.role.value == "evaluation")
    assert eval_prov.sha256 == hashlib.sha256(MATH_EVAL_JSONL.encode("utf-8")).hexdigest()


def test_provenance_captures_declared_metadata_without_rights_inference(tmp_path):
    root = build_policy_example(tmp_path)
    result = make_validator().validate(root)
    manifest_prov = next(item for item in result.provenance if item.role.value == "manifest")
    assert manifest_prov.title == "ZANA Internal Policy Demo"
    assert manifest_prov.title_origin == "manifest"
    assert manifest_prov.declared_license == "MIT"
    assert manifest_prov.rights_inferred is False
    assert manifest_prov.ingested_at == FIXED_INGESTED_AT
    assert manifest_prov.usage_metadata["citation_required"] is True
    knowledge_prov = next(item for item in result.provenance if item.role.value == "knowledge")
    assert knowledge_prov.title == "Remote Work Policy"
    assert knowledge_prov.title_origin == "file_stem"
    assert knowledge_prov.declared_license == "MIT"


def test_provenance_covers_every_package_file(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    expected = {
        "zana.yaml",
        "tools/tools.yaml",
        "permissions/policy.yaml",
        "evals/domain.jsonl",
    }
    assert {item.relative_path for item in result.provenance} == expected


def test_roles_assigned_by_declaration(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    roles = {item.relative_path: item.role.value for item in result.provenance}
    assert roles["tools/tools.yaml"] == "tools"
    assert roles["permissions/policy.yaml"] == "permissions"
    assert roles["evals/domain.jsonl"] == "evaluation"


def test_result_is_immutable(tmp_path):
    result = make_validator().validate(build_math_example(tmp_path))
    with pytest.raises(FrozenInstanceError):
        result.manifest = None  # type: ignore[misc]


def test_manifest_is_immutable(tmp_path):
    result = make_validator().validate(build_math_example(tmp_path))
    with pytest.raises(ValidationError):
        result.manifest.name = "mutated"


def test_provenance_is_immutable(tmp_path):
    result = make_validator().validate(build_math_example(tmp_path))
    provenance = result.provenance[0]
    with pytest.raises(FrozenInstanceError):
        provenance.sha256 = "deadbeef"  # type: ignore[misc]
    with pytest.raises(TypeError):
        provenance.usage_metadata["new_key"] = "value"


def test_ingestion_time_is_honored(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    assert all(item.ingested_at == FIXED_INGESTED_AT for item in result.provenance)
