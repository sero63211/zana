"""Immutable artifact store behavior and security tests."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from zana_core.artifacts import (
    ArtifactCorruptedError,
    ArtifactNotFoundError,
    ArtifactStore,
    InvalidDigestError,
    RootEscapeError,
    digest_bytes,
    temporary_workspace,
)


@pytest.fixture
def store(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(tmp_path / "artifacts")


class _BoundedReader:
    """Stream stub that proves reads stay within the configured chunk size."""

    def __init__(self, data: bytes, max_read: int) -> None:
        self._data = data
        self._max_read = max_read
        self._offset = 0

    def read(self, size: int) -> bytes:
        assert size <= self._max_read
        chunk = self._data[self._offset : self._offset + size]
        self._offset += len(chunk)
        return chunk


class _FailingReader:
    """Stream stub that fails after emitting partial content."""

    def __init__(self, prefix: bytes) -> None:
        self._prefix = prefix
        self._emitted = False

    def read(self, size: int) -> bytes:
        if not self._emitted:
            self._emitted = True
            return self._prefix[:size]
        raise OSError("simulated read failure")


class TestPutAndRead:
    def test_put_bytes_uses_deterministic_confined_path(self, store: ArtifactStore) -> None:
        data = b"hello immutable world"
        digest = store.put_bytes(data)
        assert digest == digest_bytes(data)
        path = store.blob_path(digest)
        assert path.is_file()
        assert path.is_relative_to(store.root / "blobs" / "sha256")
        assert store.exists(digest)
        assert store.size(digest) == len(data)
        assert store.read(digest) == data
        assert store.verify(digest) == len(data)

    def test_put_bytes_deduplicates_identical_content(self, store: ArtifactStore) -> None:
        data = b"duplicate me"
        first = store.put_bytes(data)
        second = store.put_bytes(data)
        assert first == second
        blob_files = list((store.root / "blobs" / "sha256").iterdir())
        assert len(blob_files) == 1

    def test_put_file_matches_put_bytes(self, store: ArtifactStore, tmp_path: Path) -> None:
        source = tmp_path / "source.bin"
        data = b"file content " * 1000
        source.write_bytes(data)
        assert store.put_file(source) == store.put_bytes(data)
        assert store.read(store.put_file(source)) == data

    def test_put_stream_is_bounded_by_chunk_size(self, store: ArtifactStore) -> None:
        data = b"streamed " * 2000
        reader = _BoundedReader(data, max_read=2048)
        digest = store.put_stream(reader, chunk_size=2048)
        assert digest == digest_bytes(data)
        assert store.read(digest) == data

    def test_put_stream_different_content_gets_different_digest(
        self,
        store: ArtifactStore,
    ) -> None:
        first = store.put_bytes(b"a")
        second = store.put_bytes(b"b")
        assert first != second
        assert store.read(first) == b"a"
        assert store.read(second) == b"b"

    def test_reput_after_delete_recreates_blob(self, store: ArtifactStore) -> None:
        digest = store.put_bytes(b"recreate")
        store.delete(digest)
        assert not store.exists(digest)
        assert store.put_bytes(b"recreate") == digest
        assert store.read(digest) == b"recreate"


class TestConcurrency:
    def test_concurrent_same_content_writes_are_safe(self, store: ArtifactStore) -> None:
        data = b"same bytes " * 5000

        def writer() -> str:
            return store.put_bytes(data)

        with ThreadPoolExecutor(max_workers=8) as pool:
            digests = list(pool.map(lambda _: writer(), range(16)))

        assert len(set(digests)) == 1
        assert store.verify(digests[0]) == len(data)
        assert len(list((store.root / "blobs" / "sha256").iterdir())) == 1

    def test_concurrent_distinct_contents_are_isolated(self, store: ArtifactStore) -> None:
        payloads = [bytes([index % 256]) * 2048 for index in range(12)]

        def writer(payload: bytes) -> str:
            return store.put_bytes(payload)

        with ThreadPoolExecutor(max_workers=6) as pool:
            digests = list(pool.map(writer, payloads))

        assert len(set(digests)) == len(payloads)
        for digest, payload in zip(digests, payloads, strict=True):
            assert store.read(digest) == payload


class TestAtomicFailureCleanup:
    def test_failed_stream_leaves_no_blob_or_temp(self, store: ArtifactStore) -> None:
        with pytest.raises(OSError):
            store.put_stream(_FailingReader(b"partial" * 100), chunk_size=64)
        blob_dir = store.root / "blobs" / "sha256"
        assert list(blob_dir.iterdir()) == []
        assert list((store.root / ".tmp").iterdir()) == []

    def test_failed_put_file_leaves_store_clean(self, store: ArtifactStore, tmp_path: Path) -> None:
        with pytest.raises(ArtifactNotFoundError):
            store.put_file(tmp_path / "does-not-exist.bin")
        assert list((store.root / "blobs" / "sha256").iterdir()) == []
        assert list((store.root / ".tmp").iterdir()) == []


class TestCorruptionDetection:
    def test_mutated_blob_is_rejected(self, store: ArtifactStore) -> None:
        digest = store.put_bytes(b"original bytes")
        store.blob_path(digest).write_bytes(b"tampered bytes")
        with pytest.raises(ArtifactCorruptedError):
            store.verify(digest)
        with pytest.raises(ArtifactCorruptedError):
            store.read(digest)

    def test_truncated_blob_is_rejected(self, store: ArtifactStore) -> None:
        digest = store.put_bytes(b"full content that must survive")
        with store.blob_path(digest).open("r+b") as handle:
            handle.truncate(4)
        with pytest.raises(ArtifactCorruptedError):
            store.verify(digest)


class TestMissingBlobs:
    @pytest.fixture
    def missing_digest(self) -> str:
        return digest_bytes(b"not stored")

    def test_missing_blob_raises_not_found(
        self,
        store: ArtifactStore,
        missing_digest: str,
    ) -> None:
        with pytest.raises(ArtifactNotFoundError):
            store.open(missing_digest)
        with pytest.raises(ArtifactNotFoundError):
            store.read(missing_digest)
        with pytest.raises(ArtifactNotFoundError):
            store.size(missing_digest)
        with pytest.raises(ArtifactNotFoundError):
            store.verify(missing_digest)
        with pytest.raises(ArtifactNotFoundError):
            store.delete(missing_digest)

    def test_exists_is_false_for_missing_blob(
        self,
        store: ArtifactStore,
        missing_digest: str,
    ) -> None:
        assert not store.exists(missing_digest)


class TestRootConfinement:
    def test_traversal_in_digest_is_rejected(self, store: ArtifactStore) -> None:
        with pytest.raises(InvalidDigestError):
            store.blob_path("../outside")
        with pytest.raises(InvalidDigestError):
            store.blob_path("sha256:../../etc/passwd")
        with pytest.raises(InvalidDigestError):
            store.exists("sha256:" + "0" * 64 + "/../escape")

    def test_symlinked_blob_escape_is_rejected(
        self,
        store: ArtifactStore,
        tmp_path: Path,
    ) -> None:
        outside = tmp_path / "outside.bin"
        outside.write_bytes(b"outside")
        digest = digest_bytes(b"outside")
        store.blob_path(digest).symlink_to(outside)

        with pytest.raises(RootEscapeError):
            store.exists(digest)
        with pytest.raises(RootEscapeError):
            store.open(digest)
        with pytest.raises(RootEscapeError):
            store.read(digest)
        with pytest.raises(RootEscapeError):
            store.size(digest)
        with pytest.raises(RootEscapeError):
            store.verify(digest)
        with pytest.raises(RootEscapeError):
            store.delete(digest)

    def test_symlinked_blob_directory_escape_is_rejected(
        self,
        store: ArtifactStore,
        tmp_path: Path,
    ) -> None:
        outside_dir = tmp_path / "outside-dir"
        outside_dir.mkdir()
        digest = digest_bytes(b"escaped")
        (outside_dir / digest.removeprefix("sha256:")).write_bytes(b"escaped")
        (store.root / "blobs" / "sha256").rmdir()
        (store.root / "blobs" / "sha256").symlink_to(outside_dir, target_is_directory=True)

        with pytest.raises(RootEscapeError):
            store.exists(digest)
        with pytest.raises(RootEscapeError):
            store.open(digest)

    def test_symlinked_source_file_is_rejected(
        self,
        store: ArtifactStore,
        tmp_path: Path,
    ) -> None:
        target = tmp_path / "target.bin"
        target.write_bytes(b"content")
        link = tmp_path / "link.bin"
        link.symlink_to(target)
        with pytest.raises(RootEscapeError):
            store.put_file(link)


class TestTemporaryWorkspaces:
    def test_workspace_cleanup_preserves_user_directories(
        self,
        store: ArtifactStore,
    ) -> None:
        precious = store.root / "workspaces" / "user-precious"
        precious.mkdir()
        marker = precious / "keep.txt"
        marker.write_text("do not delete")

        with store.workspace() as workspace:
            assert workspace.parent == store.root / "workspaces"
            (workspace / "job-data.bin").write_bytes(b"temporary")

        assert not workspace.exists()
        assert marker.read_text() == "do not delete"
        assert store.root.exists()

    def test_module_level_temporary_workspace_cleans_up(self, tmp_path: Path) -> None:
        with temporary_workspace(tmp_path / "artifacts") as workspace:
            assert workspace.exists()
            (workspace / "nested" / "file.txt").parent.mkdir(parents=True)
            (workspace / "nested" / "file.txt").write_text("x")
            workspace_path = workspace
        assert not workspace_path.exists()
        assert (tmp_path / "artifacts").exists()
