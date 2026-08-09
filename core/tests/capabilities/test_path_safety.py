"""Traversal, absolute path, symlink, and missing path rejection tests."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    MINIMAL_EVAL_JSONL,
    POLICY_MANIFEST,
    TRAIN_JSONL,
    build_math_example,
    make_validator,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def expect_failure(root, *codes_expected: str) -> None:
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    for code in codes_expected:
        assert code in codes(exc_info.value), str(exc_info.value)


def test_traversal_rejected_with_manifest_key(tmp_path):
    # Replace the declared train path with a traversal.
    write(
        tmp_path,
        "zana.yaml",
        POLICY_MANIFEST.replace(
            "training:\n  optional: false",
            "training:\n  optional: false\n  train: ../outside.jsonl",
        ),
    )
    outside = tmp_path.parent / "outside.jsonl"
    outside.write_text(TRAIN_JSONL)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(tmp_path)
    assert "PATH_TRAVERSAL" in codes(exc_info.value)
    assert any(issue.file == "training.train" for issue in exc_info.value.issues)


def test_absolute_path_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        POLICY_MANIFEST.replace("behavior/system.md", "/etc/hosts"),
    )
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(tmp_path)
    assert "MANIFEST_INVALID" in codes(exc_info.value)
    assert any("project-root-relative" in issue.message for issue in exc_info.value.issues)


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path.parent / "outside-secret.md"
    outside.write_text("secret\n")
    root = tmp_path / "pkg"
    root.mkdir()
    write(root, "zana.yaml", POLICY_MANIFEST)
    write(root, "behavior/system.md", "placeholder\n")
    (root / "behavior" / "system.md").unlink()
    (root / "behavior" / "system.md").symlink_to(outside)
    expect_failure(root, "PATH_ESCAPE")


def test_directory_symlink_rejected(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    write(root, "zana.yaml", POLICY_MANIFEST)
    write(root, "real-knowledge/sources/doc.md", "data\n")
    write(root, "behavior/system.md", "prompt data\n")
    write(root, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(root, "permissions/policy.yaml", "network:\n  outbound: false\n")
    (root / "knowledge").symlink_to(root / "real-knowledge", target_is_directory=True)
    expect_failure(root, "PATH_SYMLINK_DIR")


def test_declared_missing_file_rejected(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        POLICY_MANIFEST.replace(
            "training:\n  optional: false",
            "training:\n  optional: false\n  train: training/train.jsonl",
        ),
    )
    write(tmp_path, "behavior/system.md", "prompt data\n")
    write(tmp_path, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(tmp_path, "permissions/policy.yaml", "network:\n  outbound: false\n")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(tmp_path)
    assert "PATH_NOT_FOUND" in codes(exc_info.value)
    assert any(issue.file == "training.train" for issue in exc_info.value.issues)


def test_optional_training_missing_declared_files_ok(tmp_path):
    root = build_math_example(tmp_path)
    result = make_validator().validate(root)
    assert result.training.train is None
    assert result.training.validation is None


def test_knowledge_source_can_be_single_file(tmp_path):
    write(tmp_path, "zana.yaml", POLICY_MANIFEST)
    write(
        tmp_path,
        "zana.yaml",
        POLICY_MANIFEST.replace("path: knowledge/sources", "path: knowledge/one.md"),
    )
    write(tmp_path, "behavior/system.md", "prompt data\n")
    write(tmp_path, "knowledge/one.md", "single source doc\n")
    write(tmp_path, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(tmp_path, "permissions/policy.yaml", "network:\n  outbound: false\n")
    result = make_validator().validate(tmp_path)
    assert any(item.role.value == "knowledge" for item in result.provenance)


def test_declared_file_is_directory_rejected(tmp_path):
    write(tmp_path, "zana.yaml", POLICY_MANIFEST)
    write(tmp_path, "knowledge/sources/doc.md", "doc\n")
    write(tmp_path, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(tmp_path, "permissions/policy.yaml", "network:\n  outbound: false\n")
    # behavior.system points at the knowledge directory.
    write(tmp_path, "zana.yaml", POLICY_MANIFEST.replace("behavior/system.md", "knowledge/sources"))
    expect_failure(tmp_path, "PATH_TYPE")
