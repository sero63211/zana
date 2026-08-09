"""Snapshot manifest and invalidation tests."""

from __future__ import annotations

from datetime import UTC, datetime

from zana_core.knowledge.models import ChunkConfiguration, SourceMetadata
from zana_core.knowledge.snapshots import (
    build_snapshot_manifest,
    digest_invalidation_inputs,
)


def source(sha256: str = "sha256:" + "2" * 64, size: int = 100) -> SourceMetadata:
    return SourceMetadata(
        original_path="/approved/doc.md",
        display_name="doc.md",
        kind="markdown",
        size_bytes=size,
        sha256=sha256,
    )


class TestSnapshotInvalidation:
    def test_digest_changes_with_each_invalidation_input(self) -> None:
        config = ChunkConfiguration(target_tokens=640)
        base = digest_invalidation_inputs(
            sources=[source()],
            chunk_config=config,
            embedding_identity_required="embedding:v1",
        )
        assert base != digest_invalidation_inputs(
            sources=[source(sha256="sha256:" + "9" * 64)],
            chunk_config=config,
            embedding_identity_required="embedding:v1",
        )
        assert base != digest_invalidation_inputs(
            sources=[source()],
            chunk_config=ChunkConfiguration(target_tokens=512),
            embedding_identity_required="embedding:v1",
        )
        assert base != digest_invalidation_inputs(
            sources=[source()],
            chunk_config=config,
            embedding_identity_required="embedding:v2",
        )
        assert base != digest_invalidation_inputs(
            sources=[source()],
            parser_version="parser:v2",
            chunk_config=config,
            embedding_identity_required="embedding:v1",
        )

    def test_source_order_does_not_change_digest(self) -> None:
        config = ChunkConfiguration()
        a = source(sha256="sha256:" + "b" * 64, size=1)
        b = source(sha256="sha256:" + "c" * 64, size=2)
        first = digest_invalidation_inputs(
            sources=[a, b],
            chunk_config=config,
            embedding_identity_required="e",
        )
        second = digest_invalidation_inputs(
            sources=[b, a],
            chunk_config=config,
            embedding_identity_required="e",
        )
        assert first == second

    def test_manifest_records_inputs_and_embedding_placeholder(self) -> None:
        manifest = build_snapshot_manifest(
            sources=[source()],
            chunks=[],
            chunk_config=ChunkConfiguration(),
            embedding_identity_required="placeholder:not-yet-acquired",
            created_at=datetime(2026, 8, 9, tzinfo=UTC),
        )
        assert manifest.snapshot_id.startswith("sha256:")
        assert manifest.embedding_identity_required == "placeholder:not-yet-acquired"
        assert manifest.sources[0].sha256 == "sha256:" + "2" * 64
