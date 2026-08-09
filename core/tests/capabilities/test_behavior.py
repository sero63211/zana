"""Behavior file loading and hashing tests (content is never executed)."""

from __future__ import annotations

import hashlib

import pytest

from tests.capabilities.helpers import (
    MINIMAL_EVAL_JSONL,
    POLICY_BEHAVIOR,
    POLICY_MANIFEST,
    build_policy_example,
    make_validator,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def test_behavior_loaded_and_hashed(tmp_path):
    root = build_policy_example(tmp_path)
    result = make_validator().validate(root)
    assert result.behavior is not None
    assert result.behavior.relative_path == "behavior/system.md"
    expected = hashlib.sha256(POLICY_BEHAVIOR.encode("utf-8")).hexdigest()
    assert result.behavior.sha256 == expected
    assert result.behavior.line_count == 1
    manifest_prov = next(item for item in result.provenance if item.role.value == "manifest")
    behavior_prov = next(item for item in result.provenance if item.role.value == "behavior")
    assert behavior_prov.sha256 == expected
    assert behavior_prov.sha256 != manifest_prov.sha256


def test_behavior_content_never_executed(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    write(
        root,
        "zana.yaml",
        POLICY_MANIFEST,
    )
    write(
        root,
        "behavior/system.md",
        "import os\nraise SystemExit(1)\n# this file would fail if executed\n",
    )
    write(root, "knowledge/sources/doc.md", "knowledge doc\n")
    write(root, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(root, "permissions/policy.yaml", "network:\n  outbound: false\n")
    result = make_validator().validate(root)
    assert result.behavior is not None
    assert result.behavior.size_bytes > 0


def test_invalid_utf8_behavior_rejected(tmp_path):
    root = tmp_path / "pkg"
    root.mkdir()
    write(root, "zana.yaml", POLICY_MANIFEST)
    write(root, "behavior/system.md", b"\xff\xfe\x00")
    write(root, "evals/domain.jsonl", MINIMAL_EVAL_JSONL)
    write(root, "permissions/policy.yaml", "network:\n  outbound: false\n")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert any(issue.code == "BEHAVIOR_UTF8" for issue in exc_info.value.issues)
