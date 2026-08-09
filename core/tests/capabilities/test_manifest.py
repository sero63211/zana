"""zana.yaml parsing and manifest model validation tests."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    MATH_MANIFEST,
    build_math_example,
    make_validator,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def expect_issues(
    root, *codes_expected: str, line: int | None = None
) -> CapabilitySourceValidationError:
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert codes(exc_info.value) == list(codes_expected), str(exc_info.value)
    if line is not None:
        assert any(issue.line == line for issue in exc_info.value.issues), str(exc_info.value)
    return exc_info.value


def test_math_example_manifest_fields(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    manifest = result.manifest
    assert manifest.schemaVersion == 1
    assert manifest.kind == "ZanaCapability"
    assert manifest.id == "io.zana.demo.math"
    assert manifest.name == "ZANA Math Demo"
    assert manifest.version == "0.1.0"
    assert manifest.license == "MIT"
    assert manifest.compatibility is None
    assert manifest.goal is not None
    assert manifest.goal.type == "domain-assistant"
    assert manifest.goal.primaryMetrics == ["math_exact_accuracy"]
    assert manifest.behavior is None
    assert manifest.training is not None
    assert manifest.training.optional is True
    assert manifest.training.train == "training/train.jsonl"
    assert manifest.training.validation == "training/valid.jsonl"
    assert manifest.training.minimumExamples == 100
    assert manifest.tools is not None
    assert manifest.tools.manifest == "tools/tools.yaml"
    assert manifest.permissions is not None
    assert manifest.permissions.policy == "permissions/policy.yaml"
    assert manifest.evaluation is not None
    assert manifest.evaluation.domain == "evals/domain.jsonl"
    assert manifest.evaluation.regression is None
    assert manifest.verification is not None
    assert manifest.verification.gates is not None
    assert manifest.verification.gates.domain is not None
    assert manifest.verification.gates.domain.minimumAbsolute == 0.70
    assert manifest.verification.gates.domain.minimumImprovement == 0.05
    assert manifest.verification.gates.regression is None


def test_minimal_valid_manifest(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        """\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.minimal
name: Minimal Demo
version: 1.2.3-rc.1+build.7
""",
    )
    result = make_validator().validate(tmp_path)
    assert result.manifest.version == "1.2.3-rc.1+build.7"


def test_missing_required_fields_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        """\
schemaVersion: 1
kind: ZanaCapability
id: io.zana.demo.x
name: Missing Version
""",
    )
    expect_issues(tmp_path, "MANIFEST_INVALID")


def test_unsupported_schema_version_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST.replace("schemaVersion: 1", "schemaVersion: 2"),
    )
    expect_issues(tmp_path, "UNSUPPORTED_SCHEMA")


def test_unsupported_kind_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST.replace("kind: ZanaCapability", "kind: OtherThing"),
    )
    expect_issues(tmp_path, "UNSUPPORTED_KIND")


def test_malformed_yaml_rejected(tmp_path):
    write(tmp_path, "zana.yaml", "schemaVersion: [unclosed\n")
    expect_issues(tmp_path, "MANIFEST_PARSE")


def test_duplicate_yaml_keys_rejected_with_line(tmp_path):
    write(tmp_path, "zana.yaml", "schemaVersion: 1\nschemaVersion: 1\n")
    expect_issues(tmp_path, "MANIFEST_DUPLICATE_KEY", line=2)


def test_multiple_yaml_documents_rejected(tmp_path):
    write(tmp_path, "zana.yaml", "---\nschemaVersion: 1\n---\nschemaVersion: 1\n")
    expect_issues(tmp_path, "MANIFEST_PARSE")


def test_invalid_semver_rejected(tmp_path):
    for version in ("0.1", "1.2.3.4", "01.2.3", "1.2.3-alpha..1"):
        write(
            tmp_path,
            "zana.yaml",
            MATH_MANIFEST.replace("version: 0.1.0", f"version: {version}"),
        )
        expect_issues(tmp_path, "MANIFEST_INVALID")


def test_invalid_manifest_id_rejected(tmp_path):
    write(tmp_path, "zana.yaml", MATH_MANIFEST.replace("io.zana.demo.math", "io zana demo math"))
    expect_issues(tmp_path, "MANIFEST_INVALID")


def test_hook_manifest_keys_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST + "\ninstall:\n  command: true\n",
    )
    expect_issues(tmp_path, "MANIFEST_INVALID")


def test_nested_extra_keys_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST.replace(
            "training:\n  optional: true",
            "training:\n  optional: true\n  postInstall: true",
        ),
    )
    expect_issues(tmp_path, "MANIFEST_INVALID")


def test_gate_out_of_range_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        MATH_MANIFEST.replace("minimumAbsolute: 0.70", "minimumAbsolute: 1.5"),
    )
    expect_issues(tmp_path, "MANIFEST_INVALID")


def test_missing_zana_yaml_rejected(tmp_path):
    expect_issues(tmp_path, "MANIFEST_MISSING")
