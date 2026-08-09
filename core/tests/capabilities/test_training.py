"""Training JSONL validation tests with precise line/file recovery."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    TRAIN_JSONL,
    build_training_package,
    make_validator,
    write,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def test_valid_train_and_validation_sets(tmp_path):
    result = make_validator().validate(build_training_package(tmp_path))
    assert result.training.train is not None
    assert result.training.validation is not None
    assert len(result.training.train.records) == 2
    assert len(result.training.validation.records) == 1
    assert result.training.train.records[0].id == "train-001"
    assert result.training.train.records[0].provenance is not None
    assert result.training.train.records[0].provenance["type"] == "deterministic-generator"
    assert result.training.validation.records[0].id == "valid-001"


def test_malformed_json_line_reports_line(tmp_path):
    malformed = TRAIN_JSONL + "{not-json\n"
    root = build_training_package(tmp_path, train_jsonl=malformed)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_JSON" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "TRAINING_JSON")
    assert issue.file == "training/train.jsonl"
    assert issue.line == 3


def test_duplicate_record_id_in_file(tmp_path):
    duplicated = TRAIN_JSONL.replace("train-002", "train-001")
    root = build_training_package(tmp_path, train_jsonl=duplicated)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_DUPLICATE_ID" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "TRAINING_DUPLICATE_ID")
    assert issue.file == "training/train.jsonl"
    assert issue.line == 2


def test_invalid_message_role_rejected(tmp_path):
    invalid = TRAIN_JSONL.replace('"role":"assistant"', '"role":"admin"')
    root = build_training_package(tmp_path, train_jsonl=invalid)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_RECORD" in codes(exc_info.value)


def test_unsupported_record_key_rejected(tmp_path):
    invalid = TRAIN_JSONL.replace('"provenance"', '"prompt":"x","provenance"')
    root = build_training_package(tmp_path, train_jsonl=invalid)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_RECORD" in codes(exc_info.value)


def test_empty_training_file_rejected(tmp_path):
    root = build_training_package(tmp_path, train_jsonl="")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_EMPTY" in codes(exc_info.value)


def test_blank_line_reported(tmp_path):
    blank = TRAIN_JSONL.replace("\n", "\n\n", 1)
    root = build_training_package(tmp_path, train_jsonl=blank)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_LINE_EMPTY" in codes(exc_info.value)


def test_minimum_examples_enforced(tmp_path):
    root = build_training_package(tmp_path, minimum_examples=10)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_MIN_EXAMPLES" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "TRAINING_MIN_EXAMPLES")
    assert issue.file == "training/train.jsonl"


def test_invalid_utf8_training_rejected(tmp_path):
    root = build_training_package(tmp_path, train_jsonl="")
    write(root, "training/train.jsonl", b'{"id":"x"}\xff\n')
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "TRAINING_UTF8" in codes(exc_info.value)
