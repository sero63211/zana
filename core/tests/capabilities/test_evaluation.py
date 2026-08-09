"""Evaluation JSONL validation tests with precise line/file recovery."""

from __future__ import annotations

import pytest

from tests.capabilities.helpers import (
    MATH_EVAL_JSONL,
    POLICY_EVAL_JSONL,
    build_eval_package,
    make_validator,
)
from zana_core.capabilities.errors import CapabilitySourceValidationError


def codes(exc: CapabilitySourceValidationError) -> list[str]:
    return [issue.code for issue in exc.issues]


def test_valid_domain_records(tmp_path):
    result = make_validator().validate(build_eval_package(tmp_path))
    assert result.evaluation.domain is not None
    assert len(result.evaluation.domain.records) == 2
    assert result.evaluation.domain.records[0].scorer["type"] == "numeric_exact"
    assert result.evaluation.domain.records[0].scorer["expected"] == 391
    assert result.evaluation.regression is None


def test_contains_all_scorer_valid(tmp_path):
    result = make_validator().validate(build_eval_package(tmp_path, domain_jsonl=POLICY_EVAL_JSONL))
    assert len(result.evaluation.domain.records) == 3
    assert result.evaluation.domain.records[0].scorer["expected"] == ["two", "Remote Work Policy"]


def test_unknown_scorer_rejected(tmp_path):
    data = MATH_EVAL_JSONL.replace('"type":"numeric_exact"', '"type":"cloud_llm_judge"')
    root = build_eval_package(tmp_path, domain_jsonl=data)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_SCORER_UNSUPPORTED" in codes(exc_info.value)


def test_missing_expected_rejected(tmp_path):
    data = MATH_EVAL_JSONL.replace(',"expected":391}', "}")
    root = build_eval_package(tmp_path, domain_jsonl=data)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_SCORER_PARAM" in codes(exc_info.value)


def test_numeric_expected_wrong_type_rejected(tmp_path):
    data = MATH_EVAL_JSONL.replace('"expected":391', '"expected":"391"')
    root = build_eval_package(tmp_path, domain_jsonl=data)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_SCORER_PARAM" in codes(exc_info.value)


def test_duplicate_record_id_rejected(tmp_path):
    duplicated = MATH_EVAL_JSONL.replace("math-002", "math-001")
    root = build_eval_package(tmp_path, domain_jsonl=duplicated)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_DUPLICATE_ID" in codes(exc_info.value)
    issue = next(item for item in exc_info.value.issues if item.code == "EVALUATION_DUPLICATE_ID")
    assert issue.file == "evals/domain.jsonl"
    assert issue.line == 2


def test_malformed_json_reports_line(tmp_path):
    malformed = MATH_EVAL_JSONL + "not-json\n"
    root = build_eval_package(tmp_path, domain_jsonl=malformed)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    issue = next(item for item in exc_info.value.issues if item.code == "EVALUATION_JSON")
    assert issue.file == "evals/domain.jsonl"
    assert issue.line == 3


def test_unsupported_record_key_rejected(tmp_path):
    data = MATH_EVAL_JSONL.replace('"prompt"', '"answer":"x","prompt"')
    root = build_eval_package(tmp_path, domain_jsonl=data)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_RECORD" in codes(exc_info.value)


def test_empty_eval_file_rejected(tmp_path):
    root = build_eval_package(tmp_path, domain_jsonl="")
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_EMPTY" in codes(exc_info.value)


def test_numeric_tolerance_and_tags_ok(tmp_path):
    data = (
        '{"id":"t-001","prompt":"p","tags":["numeric"],'
        '"scorer":{"type":"numeric_tolerance","expected":391,"tolerance":0.1}}\n'
    )
    result = make_validator().validate(build_eval_package(tmp_path, domain_jsonl=data))
    assert result.evaluation.domain.records[0].tags == ("numeric",)


def test_invalid_tags_rejected(tmp_path):
    data = MATH_EVAL_JSONL.replace('"prompt"', '"tags":[""],"prompt"')
    root = build_eval_package(tmp_path, domain_jsonl=data)
    with pytest.raises(CapabilitySourceValidationError) as exc_info:
        make_validator().validate(root)
    assert "EVALUATION_RECORD" in codes(exc_info.value)
