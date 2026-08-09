"""Snapshot manifests and deterministic invalidation digests."""

from __future__ import annotations

import hashlib
import json
import os
import stat as stat_module
import tempfile
from collections.abc import Iterable
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path, PosixPath, WindowsPath

from zana_core.knowledge.embeddings import EmbeddingIdentity, IndexIdentity
from zana_core.knowledge.limits import (
    HARD_MAX_SNAPSHOT_RETAINED_BYTES,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    check_utf8_bytes,
    resolve_limits,
    utf8_byte_length,
)
from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    SnapshotManifest,
    SourceMetadata,
)

PARSER_VERSION = "markdown-text.v1"
SNAPSHOT_MANIFEST_NAME = "snapshot.json"
SNAPSHOT_MANIFEST_TMP = ".snapshot.json.tmp"


def _safe_path_value(value: object) -> bool:
    return type(value) is str or type(value) in (Path, PosixPath, WindowsPath)


def _contains_symlink(path: Path) -> bool:
    current = path
    while True:
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            parent = current.parent
            if parent == current:
                return False
            current = parent
            continue
        if stat_module.S_ISLNK(mode):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _snapshot_dir(
    directory: str | Path,
    *,
    create: bool,
    limits: KnowledgeLimits,
) -> Path:
    if not _safe_path_value(directory):
        raise ResourceLimitError("Snapshot directory must be an exact string or Path value.")
    raw = Path(directory)
    try:
        utf8_byte_length(str(raw), max_bytes=limits.max_path_bytes, label="Snapshot directory")
    except ResourceLimitError:
        raise ResourceLimitError("Snapshot directory exceeds the configured byte limit.") from None
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    if _contains_symlink(absolute):
        raise ResourceLimitError("Snapshot directory must not contain symlinks.")
    if ".." in absolute.parts:
        raise ResourceLimitError("Snapshot directory must not contain traversal components.")
    try:
        candidate = absolute.resolve(strict=False)
        if create:
            candidate.mkdir(mode=0o700, parents=False, exist_ok=True)
    except OSError:
        raise ResourceLimitError(
            "Snapshot directory could not be validated or created safely."
        ) from None
    if not candidate.exists():
        raise ResourceLimitError("No published snapshot manifest was found.")
    if not candidate.is_dir() or _contains_symlink(candidate):
        raise ResourceLimitError("Snapshot directory must be a real, non-symlink directory.")
    return candidate


def _atomic_write_text(path: Path, text: str) -> None:
    directory = path.parent
    fd: int | None = None
    tmp_name: str | None = None
    try:
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(directory))
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        fd = None
        os.replace(tmp_name, path)
        tmp_name = None
    except OSError:
        raise ResourceLimitError(
            "The snapshot manifest could not be published atomically."
        ) from None
    finally:
        if fd is not None:
            with suppress(OSError):
                os.close(fd)
        if tmp_name is not None:
            with suppress(OSError):
                os.unlink(tmp_name)


def chunk_config_digest(config: ChunkConfiguration) -> str:
    """Deterministic canonical SHA-256 of a chunk configuration."""
    if type(config) is not ChunkConfiguration:
        raise ResourceLimitError("Chunk configuration must be an exact instance.")
    raw = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":"))
    return f"sha256:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def build_index_identity(
    *,
    manifest: SnapshotManifest,
    embedding: EmbeddingIdentity,
    chunker_identity: str = "zana.heading-aware.v1",
) -> IndexIdentity:
    """Build an immutable vector index identity bound to a snapshot manifest."""
    if type(manifest) is not SnapshotManifest:
        raise ResourceLimitError("Index identity requires an exact SnapshotManifest instance.")
    if type(embedding) is not EmbeddingIdentity:
        raise ResourceLimitError("Index identity requires an exact EmbeddingIdentity instance.")
    if type(chunker_identity) is not str or not chunker_identity:
        raise ResourceLimitError("Chunker identity must be a non-empty exact string.")
    check_utf8_bytes(
        chunker_identity,
        max_bytes=4 * 1024,
        label="Chunker identity",
    )
    return IndexIdentity(
        snapshot_digest=manifest.snapshot_id,
        parser_version=manifest.parser_version,
        chunker_identity=chunker_identity,
        chunk_config_digest=chunk_config_digest(manifest.chunk_config),
        embedding=embedding,
    )


def write_snapshot(
    directory: str | Path,
    manifest: SnapshotManifest,
    *,
    limits: KnowledgeLimits | None = None,
) -> Path:
    """Atomically persist an immutable snapshot manifest to a directory."""
    active = resolve_limits(limits)
    if type(manifest) is not SnapshotManifest:
        raise ResourceLimitError("Snapshot persistence requires an exact SnapshotManifest.")
    directory_path = _snapshot_dir(directory, create=True, limits=active)
    payload = manifest.model_dump_json()
    _atomic_write_text(directory_path / SNAPSHOT_MANIFEST_TMP, payload)
    os.replace(directory_path / SNAPSHOT_MANIFEST_TMP, directory_path / SNAPSHOT_MANIFEST_NAME)
    return directory_path / SNAPSHOT_MANIFEST_NAME


def read_snapshot(
    directory: str | Path,
    *,
    limits: KnowledgeLimits | None = None,
) -> SnapshotManifest:
    """Load and integrity-verify a persisted snapshot manifest."""
    active = resolve_limits(limits)
    directory_path = _snapshot_dir(directory, create=False, limits=active)
    manifest_path = directory_path / SNAPSHOT_MANIFEST_NAME
    if not manifest_path.exists() or _contains_symlink(manifest_path):
        raise ResourceLimitError("No published snapshot manifest was found.")
    try:
        stat_result = os.lstat(manifest_path)
        if stat_result.st_size < 0 or stat_result.st_size > HARD_MAX_SNAPSHOT_RETAINED_BYTES:
            raise ResourceLimitError("Snapshot manifest exceeds the retained byte budget.")
        with open(manifest_path, encoding="utf-8") as handle:
            payload = handle.read(stat_result.st_size + 1)
    except (OSError, UnicodeError):
        raise ResourceLimitError("Snapshot manifest could not be read safely.") from None
    if len(payload.encode("utf-8")) > HARD_MAX_SNAPSHOT_RETAINED_BYTES:
        raise ResourceLimitError("Snapshot manifest exceeds the retained byte budget.")
    try:
        raw = json.loads(payload)
        if type(raw) is not dict:
            raise TypeError("Snapshot manifest must be a JSON object.")
        created = raw.get("created_at")
        if isinstance(created, str):
            raw["created_at"] = datetime.fromisoformat(created.replace("Z", "+00:00"))
        manifest = SnapshotManifest(**raw)
    except (ValueError, TypeError):
        raise ResourceLimitError("Snapshot manifest is corrupt or malformed.") from None
    expected = digest_invalidation_inputs(
        sources=manifest.sources,
        parser_version=manifest.parser_version,
        chunk_config=manifest.chunk_config,
        embedding_identity_required=manifest.embedding_identity_required,
        limits=active,
    )
    if expected != manifest.snapshot_id:
        raise ResourceLimitError("Snapshot manifest does not match its recorded identity.")
    return manifest


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
