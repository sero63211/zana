"""Stable evaluation JSONL validation with precise file/line recovery errors."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any

from zana_core.capabilities.errors import (
    CapabilityIssue,
    CapabilitySourceValidationError,
    relative_label,
)
from zana_core.capabilities.provenance import sha256_of

SCORER_TYPES: dict[str, tuple[str, ...]] = {
    "exact_string": ("expected",),
    "case_normalized_exact": ("expected",),
    "numeric_exact": ("expected",),
    "numeric_tolerance": ("expected", "tolerance"),
    "regex": ("expected",),
    "contains_all": ("expected",),
    "json_schema_valid": ("schema",),
    "classification_label": ("expected",),
    "citation_required": (),
    "source_grounding": ("expected",),
}

ALLOWED_SCORER_KEYS = frozenset({"type", "expected", "tolerance", "schema"})


class EvalKind(str, Enum):
    """Whether an evaluation JSONL file is the domain or regression suite."""

    DOMAIN = "domain"
    REGRESSION = "regression"


@dataclass(frozen=True, slots=True)
class EvaluationRecord:
    id: str
    prompt: str
    scorer: Mapping[str, Any]
    tags: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class EvaluationSet:
    kind: EvalKind
    path: Path
    file: str
    sha256: str
    size_bytes: int
    records: tuple[EvaluationRecord, ...]


def load_evaluation_set(root: Path, path: Path, kind: EvalKind) -> EvaluationSet:
    """Validate one evaluation JSONL file and return its immutable records."""
    issues: list[CapabilityIssue] = []
    label = relative_label(root, path)
    try:
        digest, size = sha256_of(path)
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("EVALUATION_UTF8", "evaluation file is not valid UTF-8", label)]
        ) from None
    except OSError as exc:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("EVALUATION_READ", f"cannot read evaluation file: {exc}", label)]
        ) from exc

    records: list[EvaluationRecord] = []
    seen_ids: dict[str, int] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            issues.append(
                CapabilityIssue(
                    "EVALUATION_LINE_EMPTY",
                    "empty line in evaluation JSONL",
                    label,
                    line_number,
                )
            )
            continue
        try:
            data = json.loads(stripped)
        except json.JSONDecodeError as exc:
            issues.append(
                CapabilityIssue(
                    "EVALUATION_JSON",
                    f"invalid JSON: {exc.msg}",
                    label,
                    line_number,
                )
            )
            continue
        record = _parse_record(data, label, line_number, issues)
        if record is None:
            continue
        if record.id in seen_ids:
            issues.append(
                CapabilityIssue(
                    "EVALUATION_DUPLICATE_ID",
                    f"duplicate record id {record.id!r}; first seen on line {seen_ids[record.id]}",
                    label,
                    line_number,
                )
            )
            continue
        seen_ids[record.id] = line_number
        records.append(record)

    if not records and not any(issue.code == "EVALUATION_JSON" for issue in issues):
        issues.append(
            CapabilityIssue("EVALUATION_EMPTY", "evaluation file contains no records", label)
        )
    if issues:
        raise CapabilitySourceValidationError(issues)
    return EvaluationSet(
        kind=kind,
        path=path,
        file=label,
        sha256=digest,
        size_bytes=size,
        records=tuple(records),
    )


def _parse_record(
    data: Any, label: str, line: int, issues: list[CapabilityIssue]
) -> EvaluationRecord | None:
    if not isinstance(data, dict):
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                "evaluation record must be a JSON object",
                label,
                line,
            )
        )
        return None
    extra = set(data) - {"id", "prompt", "scorer", "tags"}
    if extra:
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                f"unsupported evaluation record keys: {', '.join(sorted(extra))}",
                label,
                line,
            )
        )
    record_id = data.get("id")
    if not isinstance(record_id, str) or not record_id.strip():
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                "record id must be a non-empty string",
                label,
                line,
            )
        )
    prompt = data.get("prompt")
    if not isinstance(prompt, str) or not prompt.strip():
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                "prompt must be a non-empty string",
                label,
                line,
            )
        )
    scorer = _parse_scorer(data.get("scorer"), label, line, issues)
    tags: tuple[str, ...] = ()
    raw_tags = data.get("tags")
    if raw_tags is not None:
        if not isinstance(raw_tags, list) or not all(
            isinstance(tag, str) and tag.strip() for tag in raw_tags
        ):
            issues.append(
                CapabilityIssue(
                    "EVALUATION_RECORD",
                    "tags must be a list of non-empty strings",
                    label,
                    line,
                )
            )
        else:
            tags = tuple(raw_tags)
    if not isinstance(record_id, str) or not isinstance(prompt, str) or scorer is None:
        return None
    return EvaluationRecord(id=record_id, prompt=prompt, scorer=scorer, tags=tags)


def _parse_scorer(
    raw: Any, label: str, line: int, issues: list[CapabilityIssue]
) -> Mapping[str, Any] | None:
    if not isinstance(raw, dict):
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                "scorer must be an object",
                label,
                line,
            )
        )
        return None
    extra = set(raw) - ALLOWED_SCORER_KEYS
    if extra:
        issues.append(
            CapabilityIssue(
                "EVALUATION_RECORD",
                f"scorer has unsupported keys: {', '.join(sorted(extra))}",
                label,
                line,
            )
        )
    scorer_type = raw.get("type")
    if not isinstance(scorer_type, str) or scorer_type not in SCORER_TYPES:
        issues.append(
            CapabilityIssue(
                "EVALUATION_SCORER_UNSUPPORTED",
                f"unsupported scorer type {scorer_type!r}; supported: "
                f"{', '.join(sorted(SCORER_TYPES))}",
                label,
                line,
            )
        )
        return None
    required_params = SCORER_TYPES[scorer_type]
    for param in required_params:
        if param not in raw:
            issues.append(
                CapabilityIssue(
                    "EVALUATION_SCORER_PARAM",
                    f"scorer {scorer_type!r} requires {param!r}",
                    label,
                    line,
                )
            )
    _validate_scorer_params(raw, scorer_type, label, line, issues)
    return MappingProxyType(dict(sorted(raw.items())))


def _validate_scorer_params(
    raw: dict[Any, Any],
    scorer_type: str,
    label: str,
    line: int,
    issues: list[CapabilityIssue],
) -> None:
    expected = raw.get("expected")
    string_scorers = {"exact_string", "case_normalized_exact", "regex", "classification_label"}
    number_scorers = {"numeric_exact"}
    list_scorers = {"contains_all", "source_grounding"}
    if scorer_type in string_scorers and expected is not None:
        if not isinstance(expected, str) or not expected.strip():
            issues.append(
                CapabilityIssue(
                    "EVALUATION_SCORER_PARAM",
                    f"scorer {scorer_type!r} expected must be a non-empty string",
                    label,
                    line,
                )
            )
    elif scorer_type in number_scorers and expected is not None:
        if isinstance(expected, bool) or not isinstance(expected, int | float):
            issues.append(
                CapabilityIssue(
                    "EVALUATION_SCORER_PARAM",
                    f"scorer {scorer_type!r} expected must be a number",
                    label,
                    line,
                )
            )
    elif (
        scorer_type in list_scorers
        and expected is not None
        and (
            not isinstance(expected, list)
            or not expected
            or not all(isinstance(item, str) and item.strip() for item in expected)
        )
    ):
        issues.append(
            CapabilityIssue(
                "EVALUATION_SCORER_PARAM",
                f"scorer {scorer_type!r} expected must be a non-empty list of strings",
                label,
                line,
            )
        )
    tolerance = raw.get("tolerance")
    if tolerance is not None and (
        isinstance(tolerance, bool) or not isinstance(tolerance, int | float) or tolerance < 0
    ):
        issues.append(
            CapabilityIssue(
                "EVALUATION_SCORER_PARAM",
                "tolerance must be a non-negative number",
                label,
                line,
            )
        )
    schema = raw.get("schema")
    if schema is not None and not isinstance(schema, dict):
        issues.append(
            CapabilityIssue(
                "EVALUATION_SCORER_PARAM",
                "schema must be a JSON object",
                label,
                line,
            )
        )
