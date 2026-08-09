"""Deterministic Markdown and UTF-8 text normalizers with hard bounds."""

from __future__ import annotations

import re
from collections.abc import Iterator

from zana_core.knowledge.limits import (
    KnowledgeLimits,
    ResourceLimitError,
    check_deadline,
    check_utf8_bytes,
    make_deadline,
    resolve_limits,
    validate_deadline_value,
)
from zana_core.knowledge.models import (
    DocumentKind,
    NormalizedDocument,
    NormalizedSection,
    ParserWarning,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCED_CODE_RE = re.compile(r"^```")


class NormalizationLimitError(ResourceLimitError):
    """Raised when normalized content exceeds configured knowledge limits."""


def _iter_lines(text: str, max_lines: int) -> Iterator[str]:
    """Yield lines without materializing a split list; stop at max_lines + 1."""
    start = 0
    count = 0
    length = len(text)
    while start < length:
        end = text.find("\n", start)
        if end == -1:
            end = length
        count += 1
        if count > max_lines:
            raise NormalizationLimitError(f"Document exceeds the {max_lines}-line limit.")
        yield text[start:end]
        start = end + 1


def normalize_text(
    text: str,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
    _deadline: float | None = None,
) -> str:
    """Normalize UTF-8 text deterministically without altering meaning."""
    active = resolve_limits(limits)
    deadline = (
        _deadline
        if _deadline is not None
        else make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    )
    validate_deadline_value(deadline, label="Normalization deadline")
    _check_bounded_text(text, max_bytes=active.max_text_bytes, label="Document text")
    check_deadline(deadline, label="text normalization")
    decoded = text.replace("\r\n", "\n").replace("\r", "\n")
    decoded = decoded.replace("\ufeff", "")
    lines: list[str] = []
    for line in _iter_lines(decoded, active.max_lines):
        check_deadline(deadline, label="text normalization")
        lines.append(line.rstrip())
    start = 0
    while start < len(lines) and lines[start] == "":
        start += 1
    lines = lines[start:]
    while lines and lines[-1] == "":
        lines.pop()
    return "\n".join(lines) + ("\n" if lines else "")


def normalize_markdown(
    text: str,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
    _deadline: float | None = None,
) -> tuple[str, list[ParserWarning]]:
    """Normalize Markdown, preserving heading structure and offsets."""
    active = resolve_limits(limits)
    deadline = (
        _deadline
        if _deadline is not None
        else make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    )
    validate_deadline_value(deadline, label="Markdown normalization deadline")
    normalized = normalize_text(text, limits=active, _deadline=deadline)
    warnings: list[ParserWarning] = []
    in_fence = False
    for index, line in enumerate(_iter_lines(normalized, active.max_lines), start=1):
        check_deadline(deadline, label="markdown normalization")
        if len(warnings) >= active.max_warnings:
            raise NormalizationLimitError(
                f"Document exceeds the {active.max_warnings}-warning limit."
            )
        if FENCED_CODE_RE.match(line):
            in_fence = not in_fence
        elif not in_fence and re.search(r"\[[^\]]*\]\(https?://", line):
            warnings.append(
                ParserWarning(
                    code="EXTERNAL_LINK_NOT_INGESTED",
                    message="External links are not fetched or followed during intake.",
                    line=index,
                )
            )
    return normalized, warnings


def normalize_source(
    text: str,
    *,
    kind: DocumentKind,
    title: str,
    document_id: str,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
) -> NormalizedDocument:
    """Produce a normalized document for Markdown or UTF-8 text."""
    active = resolve_limits(limits)
    if kind not in {DocumentKind.MARKDOWN, DocumentKind.TEXT}:
        raise NormalizationLimitError(f"Normalization does not support {kind.value} sources.")
    deadline = make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    _check_bounded_text(text, max_bytes=active.max_text_bytes, label="Document text")
    _check_bounded_text(title, max_bytes=active.max_string_bytes, label="Document title")
    _check_bounded_text(document_id, max_bytes=active.max_string_bytes, label="Document id")
    if kind == DocumentKind.MARKDOWN:
        normalized, warnings = normalize_markdown(text, limits=active, _deadline=deadline)
    else:
        normalized, warnings = normalize_text(text, limits=active, _deadline=deadline), []

    sections: list[NormalizedSection] = []
    heading_path: list[str] = []
    current_text: list[str] = []
    current_start: int | None = None
    current_end = 0
    current_bytes = 0
    offset = 0
    for line in _iter_lines(normalized, active.max_lines):
        check_deadline(deadline, label="source normalization")
        match = HEADING_RE.match(line)
        if match:
            if current_text:
                _append_section(
                    sections,
                    document_id=document_id,
                    heading_path=heading_path,
                    text="\n".join(current_text),
                    start_offset=current_start or 0,
                    end_offset=current_end,
                    limits=active,
                )
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            heading_path = heading_path[: level - 1] + [heading_text]
            if len(heading_path) > active.max_heading_depth:
                raise NormalizationLimitError(
                    f"Heading path exceeds the {active.max_heading_depth}-level limit."
                )
            _check_bounded_text(
                heading_text,
                max_bytes=active.max_string_bytes,
                label="Heading text",
            )
            current_text = []
            current_start = None
            current_end = 0
            current_bytes = 0
        else:
            if line.strip() == "":
                pass
            elif current_start is None:
                current_start = offset
                current_text.append(line)
                current_end = offset + len(line)
                current_bytes = len(line.encode("utf-8"))
            else:
                current_text.append(line)
                current_end = offset + len(line)
                current_bytes += len(line.encode("utf-8")) + 1
            if current_bytes > active.max_text_bytes:
                raise NormalizationLimitError(
                    f"Section text exceeds the {active.max_text_bytes}-byte limit."
                )
        offset += len(line) + 1
    if current_text:
        _append_section(
            sections,
            document_id=document_id,
            heading_path=heading_path,
            text="\n".join(current_text),
            start_offset=current_start or 0,
            end_offset=current_end,
            limits=active,
        )
    return NormalizedDocument(
        document_id=document_id,
        title=title,
        sections=tuple(sections),
        warnings=tuple(warnings),
    )


def _append_section(
    sections: list[NormalizedSection],
    *,
    document_id: str,
    heading_path: list[str],
    text: str,
    start_offset: int,
    end_offset: int,
    limits: KnowledgeLimits,
) -> None:
    if len(sections) >= limits.max_section_count:
        raise NormalizationLimitError(
            f"Document exceeds the {limits.max_section_count}-section limit."
        )
    _check_bounded_text(text, max_bytes=limits.max_text_bytes, label="Section text")
    sections.append(
        NormalizedSection(
            section_id=_section_id(document_id, len(sections), heading_path),
            heading_path=tuple(heading_path),
            page_start=None,
            page_end=None,
            text=text,
            start_offset=start_offset,
            end_offset=end_offset,
        )
    )


def _section_id(document_id: str, index: int, heading_path: list[str]) -> str:
    import hashlib

    raw = f"{document_id}:{index}:{':'.join(heading_path)}"
    return f"{document_id}:s{index}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _check_bounded_text(value: str, *, max_bytes: int, label: str) -> None:
    try:
        check_utf8_bytes(value, max_bytes=max_bytes, label=label)
    except ResourceLimitError:
        raise NormalizationLimitError(f"{label} exceeds the configured UTF-8 byte limit.") from None
