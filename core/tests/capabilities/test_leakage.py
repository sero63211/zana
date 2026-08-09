"""Train/validation/evaluation separation (held-out leakage) tests."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    MATH_EVAL_JSONL,
    TRAIN_JSONL,
    VALID_JSONL,
    build_training_package,
    make_validator,
    training_manifest,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def test_shared_train_validation_file_rejected(tmp_path):
    root = build_training_package(
        tmp_path,
        train="training/shared.jsonl",
        validation="training/shared.jsonl",
        train_jsonl=TRAIN_JSONL,
        valid_jsonl=TRAIN_JSONL,
    )
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "LEAKAGE_SHARED_FILE" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "LEAKAGE_SHARED_FILE")
    assert issue.file == "training/shared.jsonl"


def test_shared_train_eval_file_rejected_even_with_content_errors(tmp_path):
    write(
        tmp_path,
        "zana.yaml",
        training_manifest(train="evals/domain.jsonl", validation="training/valid.jsonl"),
    )
    write(tmp_path, "evals/domain.jsonl", MATH_EVAL_JSONL)
    write(tmp_path, "training/valid.jsonl", VALID_JSONL)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(tmp_path)
    assert "LEAKAGE_SHARED_FILE" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "LEAKAGE_SHARED_FILE")
    assert "evals/domain.jsonl" in issue.file


def test_cross_split_duplicate_ids_rejected(tmp_path):
    eval_with_train_id = MATH_EVAL_JSONL.replace('{"id":"math-001"', '{"id":"train-001"')
    root = build_training_package(tmp_path, eval_jsonl=eval_with_train_id)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "LEAKAGE_DUPLICATE_ID" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "LEAKAGE_DUPLICATE_ID")
    assert "train-001" in issue.message


def test_test_override_relaxes_shared_file_but_never_duplicate_ids(tmp_path):
    root = build_training_package(
        tmp_path,
        train="training/shared.jsonl",
        validation="training/shared.jsonl",
        train_jsonl=TRAIN_JSONL,
        valid_jsonl=TRAIN_JSONL,
    )
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator(allow_test_overrides=True).validate(root)
    assert "LEAKAGE_SHARED_FILE" not in codes(exc_info.value)
    assert "LEAKAGE_DUPLICATE_ID" in codes(exc_info.value)


def test_distinct_splits_report_ok(tmp_path):
    result = make_validator().validate(build_training_package(tmp_path))
    assert result.leakage.ok is True
    assert result.leakage.shared_files == ()
    assert result.leakage.duplicate_ids == ()
