"""Snapshot manifest and invalidation tests."""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from zana_core.knowledge.limits import ResourceLimitError
from zana_core.knowledge.models import ChunkConfiguration, SourceMetadata
from zana_core.knowledge.snapshots import (
    build_snapshot_manifest,
    digest_invalidation_inputs,
    read_snapshot,
    write_snapshot,
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


class TestSnapshotImmutability:
    def _manifest(self, sha: str, *, created_at=None) -> object:  # noqa: ANN001
        return build_snapshot_manifest(
            sources=[source(sha256=sha)],
            chunks=[],
            chunk_config=ChunkConfiguration(),
            embedding_identity_required="embedding:v1",
            created_at=created_at or datetime(2026, 8, 10, tzinfo=UTC),
        )

    def test_same_identity_is_idempotent(self, tmp_path) -> None:  # noqa: ANN001
        manifest = self._manifest("sha256:" + "5" * 64)
        first = write_snapshot(tmp_path, manifest)
        original = first.read_bytes()
        second = write_snapshot(tmp_path, manifest)
        assert second == first
        assert second.read_bytes() == original

    def test_different_identity_fails_and_preserves_bytes(self, tmp_path) -> None:  # noqa: ANN001
        first = self._manifest("sha256:" + "5" * 64)
        published = write_snapshot(tmp_path, first)
        original = published.read_bytes()
        second = self._manifest("sha256:" + "6" * 64)
        with pytest.raises(ResourceLimitError):
            write_snapshot(tmp_path, second)
        assert published.read_bytes() == original
        assert read_snapshot(tmp_path).snapshot_id == first.snapshot_id

    def test_publication_failure_cleans_temp(self, tmp_path, monkeypatch) -> None:  # noqa: ANN001
        manifest = self._manifest("sha256:" + "7" * 64)

        def failing_replace(source, target):  # noqa: ANN001, ARG002
            raise OSError("final rename failed")

        monkeypatch.setattr(os, "replace", failing_replace)
        with pytest.raises(ResourceLimitError):
            write_snapshot(tmp_path, manifest)
        assert not (tmp_path / ".snapshot.json.tmp").exists()
        assert not (tmp_path / "snapshot.json").exists()
