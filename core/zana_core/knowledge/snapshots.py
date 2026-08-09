"""Snapshot manifests and deterministic invalidation digests."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    SnapshotManifest,
    SourceMetadata,
)

PARSER_VERSION = "markdown-text.v1"


def snapshot_invalidation_inputs(
    *,
    sources: list[SourceMetadata],
    parser_version: str = PARSER_VERSION,
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
) -> list[str]:
    """Return deterministic invalidation inputs."""
    source_lines = [
        f"{source.sha256}:{source.kind.value}:{source.size_bytes}"
        for source in sorted(sources, key=lambda item: item.sha256)
    ]
    config = chunk_config.model_dump()
    return [
        "source-hash-v1",
        *source_lines,
        f"parser:{parser_version}",
        f"chunk-config:{json.dumps(config, sort_keys=True, separators=(',', ':'))}",
        f"embedding:{embedding_identity_required}",
    ]


def digest_invalidation_inputs(
    *,
    sources: list[SourceMetadata],
    parser_version: str = PARSER_VERSION,
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
) -> str:
    inputs = snapshot_invalidation_inputs(
        sources=sources,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
    )
    payload = "\n".join(inputs).encode("utf-8")
    return f"sha256:{hashlib.sha256(payload).hexdigest()}"


def build_snapshot_manifest(
    *,
    sources: list[SourceMetadata],
    chunks: list[Chunk],
    chunk_config: ChunkConfiguration,
    embedding_identity_required: str,
    parser_version: str = PARSER_VERSION,
    created_at: datetime | None = None,
) -> SnapshotManifest:
    """Build an immutable manifest with invalidation inputs recorded."""
    snapshot_id = digest_invalidation_inputs(
        sources=sources,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
    )
    return SnapshotManifest(
        snapshot_id=snapshot_id,
        parser_version=parser_version,
        chunk_config=chunk_config,
        embedding_identity_required=embedding_identity_required,
        sources=sorted(sources, key=lambda item: item.sha256),
        chunks=list(chunks),
        created_at=created_at or datetime.now(UTC),
    )
