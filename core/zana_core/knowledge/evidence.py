"""Structured evidence rendering and deterministic context budgeting."""

from __future__ import annotations

from zana_core.knowledge.chunker import TextEstimator
from zana_core.knowledge.models import Chunk, ContextPackage, EvidenceBlock, SourceMetadata

DEFAULT_ESTIMATOR = TextEstimator()


def evidence_block(
    chunk: Chunk,
    *,
    source: SourceMetadata | None = None,
    similarity: float | None = None,
) -> EvidenceBlock:
    """Build a structured evidence block with stable source metadata."""
    title = source.display_name if source is not None else chunk.document_digest
    section = " > ".join(chunk.heading_path) if chunk.heading_path else None
    return EvidenceBlock(
        source_id=chunk.document_digest,
        source_title=title,
        page=chunk.page_start,
        section=section,
        heading_path=list(chunk.heading_path),
        text=chunk.text,
        token_estimate=chunk.token_estimate,
        similarity=similarity,
    )


def render_evidence_block(block: EvidenceBlock) -> str:
    """Render a structured evidence block that cannot alter policy."""
    page = f"p. {block.page}" if block.page is not None else ""
    section = f"§{block.section}" if block.section else ""
    locator = " | ".join(part for part in (page, section) if part)
    prefix = f"[Source {block.source_id[:12]} | {block.source_title}"
    if locator:
        prefix += f" | {locator}"
    prefix += "]"
    text = block.text.replace("\n", " ").strip()
    return f"{prefix}\n{text}\n[/Source {block.source_id[:12]}]"


def fit_context(
    blocks: list[EvidenceBlock],
    *,
    budget_tokens: int,
    estimator: TextEstimator = DEFAULT_ESTIMATOR,
) -> ContextPackage:
    """Fit evidence to a deterministic token budget in stable order."""
    fitted: list[EvidenceBlock] = []
    total = 0
    for block in blocks:
        estimate = estimator.estimate(render_evidence_block(block))
        if total + estimate > budget_tokens:
            continue
        fitted.append(block)
        total += estimate
    return ContextPackage(evidence=fitted, total_tokens=total, fitted=True)
