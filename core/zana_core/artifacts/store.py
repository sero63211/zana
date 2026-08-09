"""Deterministic, atomic, content-addressed blob storage."""

from __future__ import annotations

import hashlib
import os
import shutil
import stat
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import BinaryIO

from zana_core.artifacts.digest import (
    DEFAULT_CHUNK_SIZE,
    ArtifactCorruptedError,
    ArtifactError,
    ArtifactNotFoundError,
    RootEscapeError,
    digest_bytes,
    digest_stream,
    validate_digest,
)

BLOBS_DIR = "blobs"
SHA256_DIR = "sha256"
TMP_DIR = ".tmp"
WORKSPACES_DIR = "workspaces"


class InvalidArtifactSourceError(ArtifactError, ValueError):
    """Raised when a source path is not an accepted regular file."""


class ArtifactStore:
    """Immutable content-addressed blob store confined to one root directory."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).expanduser().resolve(strict=False)
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / BLOBS_DIR / SHA256_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / TMP_DIR).mkdir(parents=True, exist_ok=True)
        (self.root / WORKSPACES_DIR).mkdir(parents=True, exist_ok=True)

    def blob_path(self, digest: str) -> Path:
        """Return the deterministic blob path for a digest without resolving it."""
        validate_digest(digest)
        return self.root / BLOBS_DIR / SHA256_DIR / digest.removeprefix("sha256:")

    def put_bytes(self, data: bytes) -> str:
        """Atomically store bytes and return their canonical digest."""
        digest = digest_bytes(data)
        temp = self._new_temp_path()
        try:
            with open(temp, "xb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            self._finalize(temp, digest)
        except Exception:
            _remove_quietly(temp)
            raise
        return digest

    def put_file(self, source: Path, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
        """Atomically store a regular file's content with bounded memory."""
        source_path = _regular_source_path(source)
        with source_path.open("rb") as handle:
            return self.put_stream(handle, chunk_size=chunk_size)

    def put_stream(self, stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
        """Stream content into the store and return its canonical digest."""
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        temp = self._new_temp_path()
        hasher = hashlib.sha256()
        try:
            with open(temp, "xb") as output:
                while True:
                    chunk = stream.read(chunk_size)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    output.write(chunk)
                output.flush()
                os.fsync(output.fileno())
            digest = f"sha256:{hasher.hexdigest()}"
            self._finalize(temp, digest)
        except Exception:
            _remove_quietly(temp)
            raise
        return digest

    def exists(self, digest: str) -> bool:
        """Return whether a valid blob exists for ``digest``."""
        path = self._confined_path(self.blob_path(digest))
        return path.is_file()

    def open(self, digest: str) -> BinaryIO:
        """Open a blob for streaming reads without loading it into memory."""
        return self._open_blob(digest)

    def read(self, digest: str) -> bytes:
        """Read a blob and verify it still matches its digest."""
        path = self._confined_path(self.blob_path(digest))
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ArtifactNotFoundError(digest) from None
        except OSError as error:
            raise ArtifactError(f"Could not open blob {digest}: {error}") from error
        with os.fdopen(fd, "rb") as handle:
            data = handle.read()
        actual = digest_bytes(data)
        if actual != digest:
            raise ArtifactCorruptedError(f"Blob {digest} is corrupted; found {actual}.")
        return data

    def size(self, digest: str) -> int:
        """Return the byte size of a stored blob."""
        with self._open_blob(digest) as handle:
            return os.fstat(handle.fileno()).st_size

    def verify(self, digest: str) -> int:
        """Recompute a blob's digest with bounded memory and return its size."""
        validate_digest(digest)
        with self._open_blob(digest) as handle:
            actual = digest_stream(handle)
            size_bytes = os.fstat(handle.fileno()).st_size
        if actual != digest:
            raise ArtifactCorruptedError(f"Blob {digest} is corrupted; found {actual}.")
        return size_bytes

    def delete(self, digest: str) -> None:
        """Delete a blob. Callers must confirm it is unreferenced first."""
        path = self._confined_path(self.blob_path(digest))
        if not path.is_file():
            raise ArtifactNotFoundError(digest)
        path.unlink()

    @contextmanager
    def workspace(self) -> Iterator[Path]:
        """Yield a private temporary workspace under the store root."""
        workspace = self.root / WORKSPACES_DIR / uuid.uuid4().hex
        workspace.mkdir(parents=False)
        try:
            yield workspace
        finally:
            _remove_tree_confined(workspace, self.root)

    def _new_temp_path(self) -> Path:
        return self.root / TMP_DIR / f".put-{uuid.uuid4().hex}"

    def _finalize(self, temp: Path, digest: str) -> None:
        validate_digest(digest)
        final = self._confined_path(self.blob_path(digest))
        final.parent.mkdir(parents=True, exist_ok=True)
        if final.exists():
            # Deduplicate identical content. A digest path can only contain
            # identical bytes unless the existing blob is corrupted.
            try:
                self.verify(digest)
            except ArtifactCorruptedError:
                raise ArtifactCorruptedError(
                    f"Refusing to replace corrupted blob {digest}."
                ) from None
            return
        os.replace(temp, final)
        _fsync_directory(final.parent)

    def _confined_path(self, path: Path) -> Path:
        resolved_root = self.root
        resolved = path.resolve(strict=False)
        if not resolved.is_relative_to(resolved_root):
            raise RootEscapeError(f"Path escapes the artifact root: {path}")
        current = resolved_root
        for part in path.relative_to(resolved_root).parts:
            current = current / part
            if current.is_symlink():
                raise RootEscapeError(f"Symlink is not allowed inside the artifact root: {current}")
        return path

    def _open_blob(self, digest: str) -> BinaryIO:
        path = self._confined_path(self.blob_path(digest))
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        except FileNotFoundError:
            raise ArtifactNotFoundError(digest) from None
        except OSError as error:
            raise ArtifactError(f"Could not open blob {digest}: {error}") from error
        return os.fdopen(fd, "rb")


@contextmanager
def temporary_workspace(root: Path) -> Iterator[Path]:
    """Create a safe temporary workspace rooted at ``root`` and clean it up."""
    store = ArtifactStore(root)
    with store.workspace() as workspace:
        yield workspace


def _regular_source_path(path: Path) -> Path:
    candidate = Path(path).expanduser()
    if candidate.is_symlink():
        raise RootEscapeError("Symlinked source files are not accepted for ingestion.")
    if not candidate.exists():
        raise ArtifactNotFoundError(str(candidate))
    if not stat.S_ISREG(candidate.stat().st_mode):
        raise InvalidArtifactSourceError(f"Source is not a regular file: {candidate}")
    return candidate


def _remove_quietly(path: Path) -> None:
    with suppress(OSError):
        path.unlink()


def _remove_tree_confined(path: Path, root: Path) -> None:
    resolved_root = root.resolve()
    resolved = path.resolve()
    if resolved == resolved_root or not resolved.is_relative_to(resolved_root):
        raise RootEscapeError(f"Refusing to remove a path outside the artifact root: {path}")
    if path.is_symlink():
        raise RootEscapeError(f"Refusing to remove a symlink: {path}")
    shutil.rmtree(path)


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)
