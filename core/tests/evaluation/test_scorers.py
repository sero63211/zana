"""Tests for every built-in scorer and edge cases."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.evaluation.models import ScorerConfig, ScorerType
from zana_core.evaluation.scorers import score_case


def _score(
    scorer_type: ScorerType,
    raw_output: str,
    *,
    expected=None,
    tolerance=None,
    schema=None,
    citations=None,
    source_ids=None,
):
    return score_case(
        "case-1",
        raw_output,
        ScorerConfig(
            type=scorer_type,
            expected=expected,
            tolerance=tolerance,
            schema=schema,
        ),
        citations=citations,
        source_ids=source_ids,
    )


class TestStringScorers:
    def test_exact_string(self) -> None:
        assert _score(ScorerType.EXACT_STRING, "hello", expected="hello").passed is True
        assert _score(ScorerType.EXACT_STRING, "Hello", expected="hello").passed is False

    def test_case_normalized_exact(self) -> None:
        result = _score(ScorerType.CASE_NORMALIZED_EXACT, "Hello, World", expected="hello world")
        assert result.passed is True
        assert _score(ScorerType.CASE_NORMALIZED_EXACT, "hello", expected="goodbye").passed is False

    def test_classification_label(self) -> None:
        assert _score(ScorerType.CLASSIFICATION_LABEL, "positive", expected="positive").passed
        assert _score(ScorerType.CLASSIFICATION_LABEL, " Positive ", expected="positive").passed
        assert not _score(ScorerType.CLASSIFICATION_LABEL, "negative", expected="positive").passed


class TestNumericScorers:
    def test_numeric_exact(self) -> None:
        assert _score(ScorerType.NUMERIC_EXACT, "391", expected=391).passed is True
        assert _score(ScorerType.NUMERIC_EXACT, "391.0000001", expected=391).passed is False
        assert _score(ScorerType.NUMERIC_EXACT, "abc", expected=391).passed is False

    def test_numeric_tolerance(self) -> None:
        assert _score(ScorerType.NUMERIC_TOLERANCE, "391.05", expected=391, tolerance=0.1).passed
        assert not _score(ScorerType.NUMERIC_TOLERANCE, "391.2", expected=391, tolerance=0.1).passed
        assert _score(ScorerType.NUMERIC_TOLERANCE, "391", expected=391, tolerance=0).passed

    def test_invalid_numeric_configuration(self) -> None:
        assert _score(ScorerType.NUMERIC_EXACT, "391", expected="391").passed is False
        assert (
            _score(ScorerType.NUMERIC_TOLERANCE, "391", expected=391, tolerance=None).passed
            is False
        )
        with pytest.raises(ValidationError):
            _score(ScorerType.NUMERIC_TOLERANCE, "391", expected=391, tolerance=-1)


class TestPatternScorers:
    def test_regex(self) -> None:
        assert _score(ScorerType.REGEX, "the answer is 42", expected=r"\d+").passed is True
        assert _score(ScorerType.REGEX, "no numbers", expected=r"\d+").passed is False
        assert _score(ScorerType.REGEX, "x", expected="[").passed is False

    def test_contains_all(self) -> None:
        assert (
            _score(
                ScorerType.CONTAINS_ALL,
                "two facts about the Remote Work Policy",
                expected=["two", "Remote Work Policy"],
            ).passed
            is True
        )
        result = _score(
            ScorerType.CONTAINS_ALL,
            "one fact",
            expected=["two", "Remote Work Policy"],
        )
        assert result.passed is False
        assert "Remote Work Policy" in (result.failure_reason or "")


class TestJsonSchemaScorer:
    def test_valid_json_against_schema(self) -> None:
        result = _score(
            ScorerType.JSON_SCHEMA_VALID,
            '{"name":"ZANA","count":3}',
            schema={
                "type": "object",
                "required": ["name"],
                "properties": {"name": {"type": "string"}},
            },
        )
        assert result.passed is True

    def test_invalid_json_fails(self) -> None:
        result = _score(
            ScorerType.JSON_SCHEMA_VALID,
            "{not-json",
            schema={"type": "object"},
        )
        assert result.passed is False
        assert "not valid JSON" in (result.failure_reason or "")

    def test_schema_violation_fails(self) -> None:
        result = _score(
            ScorerType.JSON_SCHEMA_VALID,
            '{"count":"3"}',
            schema={
                "type": "object",
                "properties": {"count": {"type": "integer"}},
                "required": ["count"],
            },
        )
        assert result.passed is False
        assert "validation failed" in (result.failure_reason or "")

    def test_additional_properties_rejected(self) -> None:
        result = _score(
            ScorerType.JSON_SCHEMA_VALID,
            '{"name":"ZANA","extra":true}',
            schema={
                "type": "object",
                "properties": {"name": {"type": "string"}},
                "additionalProperties": False,
            },
        )
        assert result.passed is False

    def test_enum_and_array_items(self) -> None:
        schema = {
            "type": "object",
            "properties": {
                "role": {"enum": ["user", "assistant"]},
                "tags": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["role", "tags"],
        }
        assert (
            _score(
                ScorerType.JSON_SCHEMA_VALID,
                '{"role":"user","tags":["a"]}',
                schema=schema,
            ).passed
            is True
        )
        assert (
            _score(
                ScorerType.JSON_SCHEMA_VALID,
                '{"role":"admin","tags":["a"]}',
                schema=schema,
            ).passed
            is False
        )

    def test_unsupported_schema_keyword_fails_cleanly(self) -> None:
        result = _score(
            ScorerType.JSON_SCHEMA_VALID,
            "{}",
            schema={"type": "object", "notARealKeyword": True},
        )
        assert result.passed is False
        assert "Unsupported JSON Schema" in (result.failure_reason or "")


class TestGroundingScorers:
    def test_citation_required(self) -> None:
        assert (
            _score(
                ScorerType.CITATION_REQUIRED,
                "answer",
                citations=["src-1"],
            ).passed
            is True
        )
        result = _score(ScorerType.CITATION_REQUIRED, "answer", citations=[])
        assert result.passed is False
        assert "citation is required" in (result.failure_reason or "")

    def test_source_grounding_known_ids(self) -> None:
        result = _score(
            ScorerType.SOURCE_GROUNDING,
            "answer",
            expected=["src-1", "src-2"],
            citations=["src-1", "src-2"],
            source_ids=["src-1", "src-2"],
        )
        assert result.passed is True

    def test_source_grounding_missing_expected_id(self) -> None:
        result = _score(
            ScorerType.SOURCE_GROUNDING,
            "answer",
            expected=["src-1", "src-3"],
            citations=["src-1"],
            source_ids=["src-1", "src-2"],
        )
        assert result.passed is False
        assert "src-3" in (result.failure_reason or "")

    def test_source_grounding_rejects_unknown_citation(self) -> None:
        result = _score(
            ScorerType.SOURCE_GROUNDING,
            "answer",
            expected=["src-1"],
            citations=["src-1", "src-99"],
            source_ids=["src-1"],
        )
        assert result.passed is False
        assert "unknown source id" in (result.failure_reason or "")

    def test_source_grounding_requires_citation(self) -> None:
        result = _score(
            ScorerType.SOURCE_GROUNDING,
            "answer",
            expected=["src-1"],
            citations=[],
            source_ids=["src-1"],
        )
        assert result.passed is False
        assert "at least one citation" in (result.failure_reason or "")


class TestRawOutputPreservation:
    def test_raw_output_and_failure_reason_preserved(self) -> None:
        result = _score(ScorerType.EXACT_STRING, "raw answer", expected="different")
        assert result.raw_output == "raw answer"
        assert result.failure_reason is not None
        assert result.score == 0.0
