"""Deterministic Markdown and UTF-8 text normalizers."""

from __future__ import annotations

import re

from zana_core.knowledge.models import (
    DocumentKind,
    NormalizedDocument,
    NormalizedSection,
    ParserWarning,
)

HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCED_CODE_RE = re.compile(r"^```")


def normalize_text(text: str) -> str:
    """Normalize UTF-8 text deterministically without altering meaning."""
    decoded = text.replace("\r\n", "\n").replace("\r", "\n")
    decoded = decoded.replace("\ufeff", "")
    lines = decoded.split("\n")
    normalized_lines = [line.rstrip() for line in lines]
    while normalized_lines and normalized_lines[0] == "":
        normalized_lines.pop(0)
    while normalized_lines and normalized_lines[-1] == "":
        normalized_lines.pop()
    return "\n".join(normalized_lines) + ("\n" if normalized_lines else "")


def normalize_markdown(text: str) -> tuple[str, list[ParserWarning]]:
    """Normalize Markdown, preserving heading structure and offsets."""
    normalized = normalize_text(text)
    warnings: list[ParserWarning] = []
    in_fence = False
    for index, line in enumerate(normalized.split("\n"), start=1):
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
) -> NormalizedDocument:
    """Produce a normalized document for Markdown or UTF-8 text."""
    if kind == DocumentKind.MARKDOWN:
        normalized, warnings = normalize_markdown(text)
    else:
        normalized, warnings = normalize_text(text), []
    sections: list[NormalizedSection] = []
    heading_path: list[str] = []
    current_text: list[str] = []
    current_start: int | None = None
    current_end = 0
    offset = 0
    lines = normalized.split("\n")
    for line in lines:
        match = HEADING_RE.match(line)
        if match:
            if current_text:
                sections.append(
                    NormalizedSection(
                        section_id=_section_id(document_id, len(sections), heading_path),
                        heading_path=list(heading_path),
                        page_start=None,
                        page_end=None,
                        text="\n".join(current_text),
                        start_offset=current_start or 0,
                        end_offset=current_end,
                    )
                )
            level = len(match.group(1))
            heading_text = match.group(2).strip()
            heading_path = heading_path[: level - 1] + [heading_text]
            current_text = []
            current_start = None
            current_end = 0
        else:
            if line.strip() == "":
                pass
            elif current_start is None:
                current_start = offset
                current_text.append(line)
                current_end = offset + len(line)
            else:
                current_text.append(line)
                current_end = offset + len(line)
        offset += len(line) + 1
    if current_text:
        sections.append(
            NormalizedSection(
                section_id=_section_id(document_id, len(sections), heading_path),
                heading_path=list(heading_path),
                page_start=None,
                page_end=None,
                text="\n".join(current_text),
                start_offset=current_start or 0,
                end_offset=current_end,
            )
        )
    return NormalizedDocument(
        document_id=document_id,
        title=title,
        sections=sections,
        warnings=warnings,
    )


def _section_id(document_id: str, index: int, heading_path: list[str]) -> str:
    import hashlib

    raw = f"{document_id}:{index}:{':'.join(heading_path)}"
    return f"{document_id}:s{index}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
