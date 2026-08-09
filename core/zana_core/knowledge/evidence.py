"""Structured evidence rendering and deterministic context budgeting."""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from zana_core.knowledge.chunker import TextEstimator
from zana_core.knowledge.limits import (
    HARD_MAX_STRING_BYTES,
    KnowledgeLimits,
    ResourceLimitError,
    check_utf8_bytes,
    require_strict_int,
    resolve_limits,
    utf8_byte_length,
)
from zana_core.knowledge.models import (
    Chunk,
    ContextPackage,
    EvidenceBlock,
    SourceMetadata,
)

DEFAULT_ESTIMATOR = TextEstimator()


def evidence_block(
    chunk: Chunk,
    *,
    source: SourceMetadata | None = None,
    similarity: float | None = None,
) -> EvidenceBlock:
    """Build a structured evidence block with stable source metadata."""
    if type(chunk) is not Chunk:
        raise ValueError("Evidence chunks must be Chunk instances.")
    if source is not None and type(source) is not SourceMetadata:
        raise ValueError("Evidence source metadata must be a SourceMetadata instance.")
    title = source.display_name if source is not None else chunk.document_digest
    section = _join_bounded(chunk.heading_path, " > ") if chunk.heading_path else None
    return EvidenceBlock(
        source_id=chunk.document_digest,
        source_title=title,
        page=chunk.page_start,
        section=section,
        heading_path=tuple(chunk.heading_path),
        text=chunk.text,
        token_estimate=chunk.token_estimate,
        similarity=similarity,
    )


def _escape_evidence(value: str) -> str:
    """Escape control and evidence-delimiter characters for safe rendering."""
    parts: list[str] = []
    for char in value:
        code = ord(char)
        if code < 0x20 or code == 0x7F:
            parts.append(f"\\x{code:02x}")
        elif char in ("[", "]"):
            parts.append(f"\\{char}")
        else:
            parts.append(char)
    return "".join(parts)


def render_evidence_block(block: EvidenceBlock) -> str:
    """Render a structured evidence block that cannot alter policy.

    Control characters and brackets in source titles, sections, and document
    text are escaped so untrusted content cannot close or forge the evidence
    envelope.
    """
    if type(block) is not EvidenceBlock:
        raise ValueError("Evidence blocks must be EvidenceBlock instances.")
    source_id = block.source_id[:12]
    page = f"p. {block.page}" if block.page is not None else ""
    section = f"§{_escape_evidence(block.section)}" if block.section else ""
    locator = " | ".join(part for part in (page, section) if part)
    prefix = f"[Source {_escape_evidence(source_id)} | {_escape_evidence(block.source_title)}"
    if locator:
        prefix += f" | {locator}"
    prefix += "]"
    text = _escape_evidence(block.text.replace("\n", " ").strip())
    return f"{prefix}\n{text}\n[/Source {_escape_evidence(source_id)}]"


def fit_context(
    blocks: Sequence[EvidenceBlock] | Iterable[EvidenceBlock],
    *,
    budget_tokens: int,
    estimator: TextEstimator = DEFAULT_ESTIMATOR,
    limits: KnowledgeLimits | None = None,
) -> ContextPackage:
    """Fit evidence to deterministic token and UTF-8 byte budgets in order."""
    active = resolve_limits(limits)
    validated_budget = require_strict_int(budget_tokens, label="Context token budget")
    if validated_budget <= 0:
        raise ResourceLimitError("Context token budget must be positive.")
    if validated_budget > active.max_evidence_tokens:
        raise ResourceLimitError(
            f"Context budget exceeds the {active.max_evidence_tokens}-token limit."
        )
    if type(estimator) is not TextEstimator:
        raise ValueError("Context estimator must be a TextEstimator instance.")
    fitted: list[EvidenceBlock] = []
    total_tokens = 0
    total_bytes = 0
    count = 0
    for block in blocks:
        count += 1
        if count > active.max_evidence_count:
            raise ResourceLimitError(
                f"Evidence exceeds the {active.max_evidence_count}-block limit."
            )
        if type(block) is not EvidenceBlock:
            raise ValueError("Context blocks must be EvidenceBlock instances.")
        check_utf8_bytes(
            block.text,
            max_bytes=active.max_chunk_text_bytes,
            label="Evidence text",
        )
        rendered = render_evidence_block(block)
        estimate = estimator.estimate(rendered)
        try:
            rendered_bytes = utf8_byte_length(
                rendered,
                max_bytes=active.max_context_bytes,
                label="Rendered evidence",
            )
        except ResourceLimitError:
            continue
        if (
            total_tokens + estimate > validated_budget
            or total_bytes + rendered_bytes > active.max_context_bytes
        ):
            continue
        fitted.append(block)
        total_tokens += estimate
        total_bytes += rendered_bytes
    return ContextPackage(
        evidence=tuple(fitted),
        total_tokens=total_tokens,
        total_bytes=total_bytes,
        fitted=True,
    )


def _join_bounded(parts: Sequence[str], separator: str) -> str:
    """Join heading paths only after bounding count, parts, and separator bytes."""
    if type(parts) not in (tuple, list):
        raise ValueError("Heading path parts must be an exact builtin sequence.")
    if type(separator) is not str or not separator:
        raise ValueError("Heading separator must be a non-empty string.")
    budget = 0
    count = 0
    for part in parts:
        count += 1
        if count > 16:
            raise ResourceLimitError("Heading path exceeds the depth limit.")
        if type(part) is not str:
            raise ValueError("Heading path parts must be exact strings.")
        budget += utf8_byte_length(
            part,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="Heading path part",
        )
        if budget > HARD_MAX_STRING_BYTES:
            raise ResourceLimitError("Heading path exceeds the bounded section string limit.")
    budget += max(0, count - 1) * len(separator.encode("utf-8"))
    if budget > HARD_MAX_STRING_BYTES:
        raise ResourceLimitError("Heading path exceeds the bounded section string limit.")
    return separator.join(parts)
