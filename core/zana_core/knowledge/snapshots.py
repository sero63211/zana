"""Snapshot manifests and deterministic invalidation digests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from datetime import UTC, datetime

from zana_core.knowledge.limits import (
    HARD_MAX_SNAPSHOT_RETAINED_BYTES,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    check_utf8_bytes,
    resolve_limits,
)
from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    SnapshotManifest,
    SourceMetadata,
)

PARSER_VERSION = "markdown-text.v1"


def _bounded_sources(
    sources: Iterable[SourceMetadata],
    *,
    limits: KnowledgeLimits,
) -> list[SourceMetadata]:
    collected: list[SourceMetadata] = []
    try:
        for source in sources:
            if len(collected) >= limits.max_source_count:
                raise ResourceLimitError(
                    f"Snapshot exceeds the {limits.max_source_count}-source limit."
                )
            if type(source) is not SourceMetadata:
                raise ResourceLimitError("Snapshot sources must be SourceMetadata instances.")
            collected.append(source)
    except ResourceLimitError:
        raise
    except Exception:
        raise ResourceLimitError("Snapshot sources could not be collected safely.") from None
    return collected


def _bounded_chunks(
    chunks: Iterable[Chunk],
    *,
    limits: KnowledgeLimits,
) -> list[Chunk]:
    collected: list[Chunk] = []
    try:
        for chunk in chunks:
            if len(collected) >= limits.max_chunk_count:
                raise ResourceLimitError(
                    f"Snapshot exceeds the {limits.max_chunk_count}-chunk limit."
                )
            if type(chunk) is not Chunk:
                raise ResourceLimitError("Snapshot chunks must be Chunk instances.")
            collected.append(chunk)
    except ResourceLimitError:
        raise
    except Exception:
        raise ResourceLimitError("Snapshot chunks could not be collected safely.") from None
    return collected


def snapshot_invalidation_inputs(
    *,
    sources: Iterable[SourceMetadata],
    parser_version: str = PARSER_VERSION,
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
    limits: KnowledgeLimits | None = None,
) -> list[str]:
    """Return deterministic invalidation inputs."""
    active = resolve_limits(limits)
    if type(chunk_config) is not ChunkConfiguration:
        raise ResourceLimitError(
            "Snapshot chunk configuration must be an exact ChunkConfiguration instance."
        )
    if type(parser_version) is not str:
        raise ResourceLimitError("Parser version must be an exact string.")
    if type(embedding_identity_required) is not str:
        raise ResourceLimitError("Embedding identity must be an exact string.")
    check_utf8_bytes(
        parser_version,
        max_bytes=active.max_string_bytes,
        label="Parser version",
    )
    check_utf8_bytes(
        embedding_identity_required,
        max_bytes=active.max_string_bytes,
        label="Embedding identity",
    )
    collected = _bounded_sources(sources, limits=active)
    source_lines = [
        f"{source.sha256}:{source.kind.value}:{source.size_bytes}"
        for source in sorted(collected, key=lambda item: item.sha256)
    ]
    config = chunk_config.model_dump()
    inputs = [
        "source-hash-v1",
        *source_lines,
        f"parser:{parser_version}",
        f"chunk-config:{json.dumps(config, sort_keys=True, separators=(',', ':'))}",
        f"embedding:{embedding_identity_required}",
    ]
    budget = RetainedByteBudget(
        HARD_MAX_SNAPSHOT_RETAINED_BYTES, label="Snapshot invalidation inputs"
    )
    for input_line in inputs:
        budget.add(input_line, label="invalidation input")
    return inputs


def digest_invalidation_inputs(
    *,
    sources: Iterable[SourceMetadata],
    parser_version: str = PARSER_VERSION,
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
    limits: KnowledgeLimits | None = None,
) -> str:
    inputs = snapshot_invalidation_inputs(
        sources=sources,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
        limits=limits,
    )
    hasher = hashlib.sha256()
    for input_line in inputs:
        hasher.update(input_line.encode("utf-8"))
        hasher.update(b"\n")
    return f"sha256:{hasher.hexdigest()}"


def build_snapshot_manifest(
    *,
    sources: Iterable[SourceMetadata],
    chunks: Iterable[Chunk],
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
    parser_version: str = PARSER_VERSION,
    created_at: datetime | None = None,
    limits: KnowledgeLimits | None = None,
) -> SnapshotManifest:
    """Build an immutable manifest with invalidation inputs recorded."""
    active = resolve_limits(limits)
    collected_sources = _bounded_sources(sources, limits=active)
    collected_chunks = _bounded_chunks(chunks, limits=active)
    snapshot_id = digest_invalidation_inputs(
        sources=collected_sources,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
        limits=active,
    )
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
        sources=tuple(sorted(collected_sources, key=lambda item: item.sha256)),
        chunks=tuple(collected_chunks),
        created_at=created_at or datetime.now(UTC),
    )
