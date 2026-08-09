"""Stable training JSONL validation with precise file/line recovery errors."""

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

ALLOWED_ROLES = frozenset({"system", "user", "assistant", "tool"})
ALLOWED_RECORD_KEYS = frozenset({"id", "messages", "provenance"})
ALLOWED_MESSAGE_KEYS = frozenset({"role", "content"})
ALLOWED_PROVENANCE_KEYS = frozenset({"type", "generator"})


class TrainingRole(str, Enum):
    """Whether a JSONL set is the training or held-out validation split."""

    TRAIN = "train"
    VALIDATION = "validation"


@dataclass(frozen=True, slots=True)
class TrainingMessage:
    role: str
    content: str


@dataclass(frozen=True, slots=True)
class TrainingRecord:
    id: str
    messages: tuple[TrainingMessage, ...]
    provenance: Mapping[str, Any] | None


@dataclass(frozen=True, slots=True)
class TrainingSet:
    role: TrainingRole
    path: Path
    file: str
    sha256: str
    size_bytes: int
    records: tuple[TrainingRecord, ...]


def load_training_set(root: Path, path: Path, role: TrainingRole) -> TrainingSet:
    """Validate one training JSONL file and return its immutable records."""
    issues: list[CapabilityIssue] = []
    label = relative_label(root, path)
    try:
        digest, size = sha256_of(path)
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("TRAINING_UTF8", "training file is not valid UTF-8", label)]
        ) from None
    except OSError as exc:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("TRAINING_READ", f"cannot read training file: {exc}", label)]
        ) from exc

    records: list[TrainingRecord] = []
    seen_ids: dict[str, int] = {}
    for line_number, raw_line in enumerate(text.splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            issues.append(
                CapabilityIssue(
                    "TRAINING_LINE_EMPTY",
                    "empty line in training JSONL",
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
                    "TRAINING_JSON",
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
                    "TRAINING_DUPLICATE_ID",
                    f"duplicate record id {record.id!r}; first seen on line {seen_ids[record.id]}",
                    label,
                    line_number,
                )
            )
            continue
        seen_ids[record.id] = line_number
        records.append(record)

    if not records and not any(issue.code == "TRAINING_JSON" for issue in issues):
        issues.append(CapabilityIssue("TRAINING_EMPTY", "training file contains no records", label))
    if issues:
        raise CapabilitySourceValidationError(issues)
    return TrainingSet(
        role=role,
        path=path,
        file=label,
        sha256=digest,
        size_bytes=size,
        records=tuple(records),
    )


def _parse_record(
    data: Any, label: str, line: int, issues: list[CapabilityIssue]
) -> TrainingRecord | None:
    valid = True
    if not isinstance(data, dict):
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "training record must be a JSON object",
                label,
                line,
            )
        )
        return None
    extra = set(data) - ALLOWED_RECORD_KEYS
    if extra:
        valid = False
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"unsupported training record keys: {', '.join(sorted(extra))}",
                label,
                line,
            )
        )
    record_id = data.get("id")
    if not isinstance(record_id, str) or not record_id.strip():
        valid = False
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "record id must be a non-empty string",
                label,
                line,
            )
        )
    messages = data.get("messages")
    parsed_messages: list[TrainingMessage] = []
    if not isinstance(messages, list) or not messages:
        valid = False
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "record messages must be a non-empty list",
                label,
                line,
            )
        )
    else:
        for index, raw_message in enumerate(messages):
            parsed = _parse_message(raw_message, index, label, line, issues)
            if parsed is None:
                valid = False
            else:
                parsed_messages.append(parsed)
    provenance = _parse_provenance(data.get("provenance"), label, line, issues)
    if provenance is None and "provenance" in data:
        valid = False
    if not valid or not isinstance(record_id, str):
        return None
    return TrainingRecord(id=record_id, messages=tuple(parsed_messages), provenance=provenance)


def _parse_message(
    raw: Any, index: int, label: str, line: int, issues: list[CapabilityIssue]
) -> TrainingMessage | None:
    if not isinstance(raw, dict):
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"message {index} must be an object",
                label,
                line,
            )
        )
        return None
    extra = set(raw) - ALLOWED_MESSAGE_KEYS
    if extra:
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"message {index} has unsupported keys: {', '.join(sorted(extra))}",
                label,
                line,
            )
        )
    role = raw.get("role")
    content = raw.get("content")
    if role not in ALLOWED_ROLES:
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"message {index} role must be one of {', '.join(sorted(ALLOWED_ROLES))}",
                label,
                line,
            )
        )
    if not isinstance(content, str) or not content.strip():
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"message {index} content must be a non-empty string",
                label,
                line,
            )
        )
    if role in ALLOWED_ROLES and isinstance(content, str) and content.strip():
        return TrainingMessage(role=role, content=content)
    return None


def _parse_provenance(
    raw: Any, label: str, line: int, issues: list[CapabilityIssue]
) -> Mapping[str, Any] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "provenance must be an object",
                label,
                line,
            )
        )
        return None
    extra = set(raw) - ALLOWED_PROVENANCE_KEYS
    if extra:
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                f"provenance has unsupported keys: {', '.join(sorted(extra))}",
                label,
                line,
            )
        )
    provenance_type = raw.get("type")
    if not isinstance(provenance_type, str) or not provenance_type.strip():
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "provenance.type must be a non-empty string",
                label,
                line,
            )
        )
    generator = raw.get("generator")
    if generator is not None and (not isinstance(generator, str) or not generator.strip()):
        issues.append(
            CapabilityIssue(
                "TRAINING_RECORD",
                "provenance.generator must be a non-empty string when present",
                label,
                line,
            )
        )
    return MappingProxyType(dict(sorted(raw.items())))
