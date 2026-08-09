"""JSON Schema validity and runtime/schema parity tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import yaml

from tests.capabilities.helpers import (
    MATH_MANIFEST,
    MINIMAL_EVAL_JSONL,
    build_eval_package,
    make_validator,
    write,
)
from tests.capabilities.schema_check import validate_schema
from zana_core.capabilities.errors import CapabilitySourceValidationError
from zana_core.capabilities.evaluation import SCORER_TYPES
from zana_core.capabilities.manifest import CapabilityManifest

_REPO_ROOT = Path(__file__).resolve().parents[3]
CAPABILITY_SCHEMA = json.loads((_REPO_ROOT / "schemas/capability.schema.json").read_text())
EVALUATION_SCHEMA = json.loads((_REPO_ROOT / "schemas/evaluation.schema.json").read_text())


def load_schema(name: str) -> dict:
    path = _REPO_ROOT / "schemas" / f"{name}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_schemas_parse_as_standard_draft_2020_12():
    for schema in (CAPABILITY_SCHEMA, EVALUATION_SCHEMA):
        assert schema["$schema"] == "https://json-schema.org/draft/2020-12/schema"
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False


def test_capability_schema_matches_runtime_model_surface():
    schema = load_schema("capability")
    assert schema["required"] == ["schemaVersion", "kind", "id", "name", "version"]
    assert schema["properties"]["schemaVersion"]["const"] == 1
    assert schema["properties"]["kind"]["const"] == "ZanaCapability"
    model_fields = set(CapabilityManifest.model_fields)
    assert set(schema["properties"]) == model_fields


def test_evaluation_schema_matches_runtime_scorer_registry():
    schema = load_schema("evaluation")
    assert schema["required"] == ["id", "prompt", "scorer"]
    scorer_type_enum = schema["properties"]["scorer"]["properties"]["type"]["enum"]
    assert sorted(scorer_type_enum) == sorted(SCORER_TYPES)
    constrained = {
        block["if"]["properties"]["type"]["const"]
        for block in schema["properties"]["scorer"]["allOf"]
    }
    assert constrained == set(SCORER_TYPES) - {"citation_required"}


def test_safe_path_schema_rejects_traversal_and_absolute(tmp_path):
    manifest = {
        "schemaVersion": 1,
        "kind": "ZanaCapability",
        "id": "io.zana.demo.x",
        "name": "X",
        "version": "0.1.0",
        "evaluation": {"domain": "../outside.jsonl"},
    }
    assert validate_schema(CAPABILITY_SCHEMA, manifest)
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST.replace("evals/domain.jsonl", "../outside.jsonl"),
    )
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(tmp_path)
    assert any(issue.code == "PATH_TRAVERSAL" for issue in exc_info.value.issues)


def test_manifest_schema_runtime_parity(tmp_path):
    cases: list[tuple[str, bool]] = [
        (MATH_MANIFEST, True),
        (MATH_MANIFEST.replace("version: 0.1.0", "version: 1.2"), False),
        (MATH_MANIFEST + "\ninstall:\n  command: true\n", False),
        (MATH_MANIFEST.replace("schemaVersion: 1", "schemaVersion: 2"), False),
        (MATH_MANIFEST.replace("kind: ZanaCapability", "kind: Other"), False),
        (MATH_MANIFEST.replace("minimumAbsolute: 0.70", "minimumAbsolute: 1.5"), False),
        (MATH_MANIFEST.replace("minimumExamples: 100", "minimumExamples: -1"), False),
        (MATH_MANIFEST.replace("io.zana.demo.math", "io zana demo math"), False),
    ]
    for index, (manifest_yaml, valid) in enumerate(cases):
        root = tmp_path / f"case-{index}"
        write(root, "zana.yaml", manifest_yaml)
        write(root, "tools/tools.yaml", "tools:\n  - id: calculator\n")
        write(root, "permissions/policy.yaml", "network:\n  outbound: false\n")
        write(root, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
        data = _yaml_to_dict(manifest_yaml)
        schema_valid = not validate_schema(CAPABILITY_SCHEMA, data)
        runtime_valid = _runtime_valid(root)
        assert schema_valid == valid, f"case {index}: schema={schema_valid} runtime={runtime_valid}"
        assert runtime_valid == valid, (
            f"case {index}: schema={schema_valid} runtime={runtime_valid}"
        )


def test_evaluation_schema_runtime_parity(tmp_path):
    records = [
        ('{"id":"a","prompt":"p","scorer":{"type":"numeric_exact","expected":1}}', True),
        ('{"id":"a","prompt":"p","scorer":{"type":"contains_all","expected":["x"]}}', True),
        (
            '{"id":"a","prompt":"p","scorer":{"type":"numeric_tolerance",'
            '"expected":1,"tolerance":0.1}}',
            True,
        ),
        ('{"id":"a","prompt":"p","scorer":{"type":"citation_required"}}', True),
        (
            '{"id":"a","prompt":"p","scorer":{"type":"json_schema_valid","schema":{}}}',
            True,
        ),
        ('{"id":"a","prompt":"p","scorer":{"type":"cloud_llm_judge"}}', False),
        ('{"id":"a","prompt":"p","scorer":{"type":"numeric_exact"}}', False),
        (
            '{"id":"a","prompt":"p","scorer":{"type":"numeric_exact","expected":"1"}}',
            False,
        ),
        (
            '{"id":"a","prompt":"p","scorer":{"type":"contains_all","expected":["x",""]}}',
            False,
        ),
        (
            '{"id":"a","prompt":"p","scorer":{"type":"numeric_exact","expected":1},"answer":"x"}',
            False,
        ),
        ('{"id":"a","scorer":{"type":"numeric_exact","expected":1}}', False),
        ('{"id":"a","prompt":"p","scorer":{"type":"json_schema_valid"}}', False),
        ('{"id":"a","prompt":"p","scorer":{"type":"numeric_tolerance","expected":1}}', False),
    ]
    for index, (record, valid) in enumerate(records):
        root = tmp_path / f"eval-{index}"
        build_eval_package(root, domain_jsonl=record + "\n")
        schema_valid = not validate_schema(EVALUATION_SCHEMA, json.loads(record))
        runtime_valid = _runtime_valid(root)
        assert schema_valid == valid, f"case {index}: schema={schema_valid} runtime={runtime_valid}"
        assert runtime_valid == valid, (
            f"case {index}: schema={schema_valid} runtime={runtime_valid}"
        )


def _yaml_to_dict(text: str) -> dict:
    return yaml.safe_load(text)


def _runtime_valid(root: Path) -> bool:
    try:
        make_validator().validate(root)
        return True
    except CapabilitySourceValidationError:
        return False
