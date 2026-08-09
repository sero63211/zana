"""Persistent local vector index over an injected durable record store.

The public :class:`LanceDBIndex` is the canonical local knowledge index bound
to an immutable :class:`IndexIdentity`.  It owns the durable, atomically
published manifest that records that identity, all bounded validation, and the
``VectorIndex`` contract used by :class:`RetrievalService`.  Row storage and
search are delegated to a ``RecordStore``.

LanceDB is the production record store.  It is imported lazily so ZANA starts
without it; when the package is absent the store reports an honest
``BackendUnavailableError`` instead of fabricating availability.  Tests inject
an exact tiny local ``RecordStore`` fixture to exercise the real orchestration
logic (identity, atomic publication, bounds, corruption, provenance) without
claiming that LanceDB is installed.
"""

from __future__ import annotations

import importlib
import json
import os
import stat as stat_module
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path, PosixPath, WindowsPath
from typing import Any, Protocol

from zana_core.knowledge.embeddings import (
    BackendUnavailableError,
    EmbeddingError,
    IndexIdentity,
    VectorRecord,
    validate_vector_index,
)
from zana_core.knowledge.limits import (
    HARD_MAX_INDEX_RETAINED_BYTES,
    KnowledgeLimits,
    ResourceLimitError,
    check_utf8_bytes,
    require_finite_number,
    require_strict_int,
    resolve_limits,
    utf8_byte_length,
)

INDEX_MANIFEST_NAME = "manifest.json"
INDEX_MANIFEST_TMP = ".manifest.json.tmp"
INDEX_FORMAT = 1


class IndexError(Exception):
    """Base failure for the persistent local index."""


class IndexNotFoundError(IndexError):
    """Raised when no published index exists at a location."""


class IndexCorruptionError(IndexError):
    """Raised when a persisted index or manifest is corrupt or unsafe."""


class IndexIncompatibleError(IndexError):
    """Raised when a persisted index identity does not match the expected one."""


def _safe_path_value(value: object) -> bool:
    return type(value) is str or type(value) in (Path, PosixPath, WindowsPath)


def _contains_symlink(path: Path) -> bool:
    """Return whether a path component of an absolute path is a symlink."""
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


def _normalise_location(
    location: str | Path,
    *,
    create: bool,
    limits: KnowledgeLimits,
) -> Path:
    if not _safe_path_value(location):
        raise IndexCorruptionError("Index location must be an exact string or Path value.")
    raw = Path(location)
    try:
        utf8_byte_length(str(raw), max_bytes=limits.max_path_bytes, label="Index location")
    except ResourceLimitError:
        raise IndexCorruptionError("Index location exceeds the configured byte limit.") from None
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    if _contains_symlink(absolute):
        raise IndexCorruptionError("Index location must not contain symlinks.")
    if ".." in absolute.parts:
        raise IndexCorruptionError("Index location must not contain traversal components.")
    try:
        parent = absolute.parent
        if not parent.exists():
            raise IndexCorruptionError("Index parent directory does not exist.")
        if _contains_symlink(parent):
            raise IndexCorruptionError("Index parent directory must not be a symlink.")
        candidate = absolute.resolve(strict=False)
    except (OSError, RuntimeError):
        raise IndexCorruptionError("Index location could not be validated safely.") from None
    if create:
        try:
            candidate.mkdir(mode=0o700, parents=False, exist_ok=True)
        except OSError:
            raise IndexCorruptionError("Index directory could not be created safely.") from None
    if not candidate.exists():
        raise IndexNotFoundError(f"No published index was found at {location}.")
    if not candidate.is_dir():
        raise IndexCorruptionError("Index location is not a directory.")
    try:
        stat_result = os.lstat(candidate)
    except OSError:
        raise IndexCorruptionError("Index directory could not be inspected.") from None
    if stat_module.S_ISLNK(stat_result.st_mode):
        raise IndexCorruptionError("Index directory must not be a symlink.")
    return candidate


def _atomic_write_text(path: Path, text: str) -> None:
    """Atomically publish a file at ``path`` using write-then-replace."""
    if type(text) is not str:
        raise IndexCorruptionError("Manifest payload must be a string.")
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
    except (OSError, TypeError, ValueError):
        raise IndexCorruptionError(
            "The index manifest could not be published atomically."
        ) from None
    finally:
        if fd is not None:
            with _suppress_oserror():
                os.close(fd)
        if tmp_name is not None:
            with _suppress_oserror():
                os.unlink(tmp_name)


class _SuppressOSError:
    def __enter__(self) -> None:
        return None

    def __exit__(self, *args: object) -> bool:
        return True


def _suppress_oserror() -> _SuppressOSError:
    return _SuppressOSError()


def _build_manifest(identity: IndexIdentity) -> str:
    identity_data = identity.model_dump()
    payload = {
        "format": INDEX_FORMAT,
        "identity": identity_data,
        "identity_key": identity.identity_key(),
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _read_manifest(location: Path, *, limits: KnowledgeLimits) -> str:
    manifest_path = location / INDEX_MANIFEST_NAME
    if not manifest_path.exists():
        raise IndexNotFoundError(f"No published index was found at {location}.")
    if _contains_symlink(manifest_path):
        raise IndexCorruptionError("Index manifest must not be a symlink.")
    try:
        stat_result = os.lstat(manifest_path)
        size = stat_result.st_size
        if size < 0 or size > HARD_MAX_INDEX_RETAINED_BYTES:
            raise IndexCorruptionError("Index manifest exceeds the retained byte budget.")
        with open(manifest_path, encoding="utf-8") as handle:
            text = handle.read(size + 1)
    except IndexCorruptionError:
        raise
    except (OSError, UnicodeError):
        raise IndexCorruptionError("Index manifest could not be read safely.") from None
    if len(text.encode("utf-8")) > HARD_MAX_INDEX_RETAINED_BYTES:
        raise IndexCorruptionError("Index manifest exceeds the retained byte budget.")
    return text


def _parse_manifest(text: str, *, limits: KnowledgeLimits) -> IndexIdentity:
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        raise IndexCorruptionError("Index manifest is not valid JSON.") from None
    if type(payload) is not dict:
        raise IndexCorruptionError("Index manifest must be a JSON object.")
    if payload.get("format") != INDEX_FORMAT:
        raise IndexCorruptionError("Index manifest uses an unsupported format version.")
    identity_data = payload.get("identity")
    identity_key = payload.get("identity_key")
    if type(identity_key) is not str or type(identity_data) is not dict:
        raise IndexCorruptionError("Index manifest is missing identity data.")
    try:
        identity = IndexIdentity(**identity_data)
    except (ValueError, TypeError):
        raise IndexCorruptionError("Index manifest contains a malformed identity.") from None
    if identity.identity_key() != identity_key:
        raise IndexCorruptionError("Index manifest identity does not match its stored key.")
    return identity


class RecordStore(Protocol):
    """Durable row storage + search engine contract for the index."""

    def load_records(self) -> list[VectorRecord]: ...

    def upsert(self, records: Sequence[VectorRecord]) -> None: ...

    def search(
        self,
        vector: list[float] | tuple[float, ...],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]: ...

    def close(self) -> None: ...


class LanceDBRecordStore:
    """Production LanceDB-backed record store with lazy import.

    The ``lancedb`` package is imported only when a store is constructed.  When
    it is absent the constructor raises an honest
    :class:`BackendUnavailableError`; no table or index is fabricated.
    """

    def __init__(self, location: Path, *, identity: IndexIdentity) -> None:
        if type(identity) is not IndexIdentity:
            raise IndexIncompatibleError("LanceDB store requires an exact IndexIdentity.")
        try:
            lancedb = importlib.import_module("lancedb")
        except Exception:
            raise BackendUnavailableError(
                "LanceDB is not installed; the persistent local vector index is unavailable."
            ) from None
        self._lancedb = lancedb
        self._location = location / "table"
        self._identity = identity
        self._db = lancedb.connect(str(self._location))

    def load_records(self) -> list[VectorRecord]:
        return self._list_records()

    def upsert(self, records: Sequence[VectorRecord]) -> None:
        if type(records) not in (tuple, list):
            raise ResourceLimitError("Record store upserts require an exact tuple or list.")
        if not records:
            return
        rows = [self._record_to_row(record) for record in records]
        try:
            table = self._db.open_table("vectors")
            table.add(rows)
        except Exception:
            self._db.create_table("vectors", data=rows, mode="overwrite")

    def search(
        self,
        vector: list[float] | tuple[float, ...],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]:
        table = self._db.open_table("vectors")
        results = table.search(list(vector)).limit(int(limit)).to_list()
        candidates: list[tuple[VectorRecord, float]] = []
        for row in results:
            if type(row) is not dict:
                raise IndexCorruptionError("LanceDB returned a non-object row.")
            record = self._row_to_record(row)
            score = row.get("_distance")
            if type(score) is not int and type(score) is not float:
                raise IndexCorruptionError("LanceDB returned a malformed score.")
            similarity = 1.0 - float(score)
            if not (-1.0 <= similarity <= 1.0):
                raise IndexCorruptionError("LanceDB returned an out-of-range similarity score.")
            candidates.append((record, similarity))
        return candidates

    def close(self) -> None:
        return None

    def _list_records(self) -> list[VectorRecord]:
        try:
            table = self._db.open_table("vectors")
            rows = table.to_lance().to_table().to_pylist()
        except Exception:
            raise IndexCorruptionError("LanceDB table could not be read safely.") from None
        return [self._row_to_record(row) for row in rows]

    def _record_to_row(self, record: VectorRecord) -> dict[str, Any]:
        return {
            "chunk_id": record.chunk_id,
            "document_digest": record.document_digest,
            "source_title": record.source_title,
            "page_start": record.page_start,
            "page_end": record.page_end,
            "heading_path": list(record.heading_path),
            "section_id": record.section_id,
            "text": record.text,
            "vector": list(record.vector),
            "metadata_json": record.metadata_json.model_dump(),
        }

    def _row_to_record(self, row: dict[str, Any]) -> VectorRecord:
        try:
            metadata = row.get("metadata_json") or {}
            return VectorRecord(
                chunk_id=row["chunk_id"],
                document_digest=row["document_digest"],
                source_title=row.get("source_title") or "",
                page_start=row.get("page_start"),
                page_end=row.get("page_end"),
                heading_path=tuple(row.get("heading_path") or ()),
                section_id=row.get("section_id") or "",
                text=row.get("text") or "",
                vector=tuple(row["vector"]),
                metadata_json=metadata,
            )
        except (KeyError, TypeError, ValueError):
            raise IndexCorruptionError("LanceDB returned a malformed or corrupt row.") from None


class LanceDBIndex:
    """Canonical persistent local vector index bound to an immutable identity.

    Implements the :class:`VectorIndex` contract consumed by
    :class:`RetrievalService`.  The manifested :class:`IndexIdentity` is written
    atomically so an index is either fully published or absent; identity and
    embedding binding are immutable across opens and upserts.
    """

    def __init__(
        self,
        *,
        identity: IndexIdentity,
        location: Path,
        store: RecordStore,
        limits: KnowledgeLimits,
    ) -> None:
        if type(identity) is not IndexIdentity:
            raise IndexIncompatibleError("Index requires an exact IndexIdentity instance.")
        self.identity = identity
        self._location = location
        self._store = store
        self._limits = limits

    @property
    def location(self) -> Path:
        return self._location

    @classmethod
    def create(
        cls,
        location: str | Path,
        *,
        identity: IndexIdentity,
        records: Sequence[VectorRecord] | Iterable[VectorRecord],
        store: RecordStore | None = None,
        limits: KnowledgeLimits | None = None,
    ) -> LanceDBIndex:
        active = resolve_limits(limits)
        if type(identity) is not IndexIdentity:
            raise IndexIncompatibleError("Index identity must be an exact IndexIdentity instance.")
        validated_records = validate_vector_index(identity, records, limits=active)
        loc = _normalise_location(location, create=True, limits=active)
        if (loc / INDEX_MANIFEST_NAME).exists():
            raise IndexCorruptionError(
                "An index is already published at this location; refusing to overwrite it."
            )
        effective_store = store if store is not None else LanceDBRecordStore(loc, identity=identity)
        manifest = _build_manifest(identity)
        _atomic_write_text(loc / INDEX_MANIFEST_TMP, manifest)
        effective_store.upsert(validated_records)
        os.replace(loc / INDEX_MANIFEST_TMP, loc / INDEX_MANIFEST_NAME)
        return cls(
            identity=identity,
            location=loc,
            store=effective_store,
            limits=active,
        )

    @classmethod
    def open(
        cls,
        location: str | Path,
        *,
        expected_identity: IndexIdentity | None = None,
        store: RecordStore | None = None,
        limits: KnowledgeLimits | None = None,
    ) -> LanceDBIndex:
        active = resolve_limits(limits)
        if expected_identity is not None and type(expected_identity) is not IndexIdentity:
            raise IndexIncompatibleError(
                "Expected index identity must be an exact IndexIdentity instance."
            )
        loc = _normalise_location(location, create=False, limits=active)
        text = _read_manifest(loc, limits=active)
        identity = _parse_manifest(text, limits=active)
        if (
            expected_identity is not None
            and identity.identity_key() != expected_identity.identity_key()
        ):
            raise IndexIncompatibleError(
                "Persisted index identity does not match the expected index identity."
            )
        effective_store = store if store is not None else LanceDBRecordStore(loc, identity=identity)
        records = effective_store.load_records()
        validate_vector_index(identity, records, limits=active)
        index = cls(
            identity=identity,
            location=loc,
            store=effective_store,
            limits=active,
        )
        return index

    def records(self) -> list[VectorRecord]:
        records = self._store.load_records()
        return validate_vector_index(self.identity, records, limits=self._limits)

    def upsert(self, records: Sequence[VectorRecord] | Iterable[VectorRecord]) -> int:
        validated = validate_vector_index(self.identity, records, limits=self._limits)
        try:
            self._store.upsert(validated)
        except (EmbeddingError, ResourceLimitError, IndexError):
            raise
        except Exception:
            raise IndexCorruptionError("The record store could not persist the upsert.") from None
        return len(validated)

    def search(
        self,
        vector: list[float] | tuple[float, ...],
        limit: int,
    ) -> list[tuple[VectorRecord, float]]:
        """Return a bounded list of ``(VectorRecord, score)`` candidates."""
        active_limit = require_strict_int(limit, label="Search limit")
        if active_limit < 1 or active_limit > self._limits.max_candidate_count:
            raise ResourceLimitError(
                f"Search limit must be within the "
                f"{self._limits.max_candidate_count}-candidate limit."
            )
        try:
            results = self._store.search(vector, active_limit)
        except (EmbeddingError, ResourceLimitError, IndexError):
            raise
        except Exception:
            raise IndexCorruptionError("The record store failed during search.") from None
        if type(results) is not list or len(results) > active_limit:
            raise IndexCorruptionError("The record store returned more candidates than requested.")
        candidates: list[tuple[VectorRecord, float]] = []
        for entry in results:
            if type(entry) is not tuple or len(entry) != 2:
                raise IndexCorruptionError("The record store returned a malformed candidate.")
            record, score = entry
            self._validate_candidate(record, score)
            candidates.append((record, score))
        return candidates

    def _validate_candidate(self, record: VectorRecord, score: float) -> None:
        if type(record) is not VectorRecord:
            raise IndexCorruptionError("The record store returned a non-VectorRecord candidate.")
        try:
            validated_score = require_finite_number(score, label="Candidate score")
        except ResourceLimitError:
            raise IndexCorruptionError(
                "The record store returned a non-finite candidate score."
            ) from None
        if not (-1.0 <= validated_score <= 1.0):
            raise IndexCorruptionError("The record store returned an out-of-range score.")
        try:
            check_utf8_bytes(
                record.chunk_id,
                max_bytes=self._limits.max_key_bytes,
                label="Candidate chunk_id",
            )
            check_utf8_bytes(
                record.document_digest,
                max_bytes=self._limits.max_string_bytes,
                label="Candidate document_digest",
            )
            if len(record.vector) != self.identity.embedding.dimensions:
                raise IndexCorruptionError(
                    "The record store returned a candidate with mismatched dimensions."
                )
        except ResourceLimitError:
            raise IndexCorruptionError(
                "The record store returned an over-limit candidate."
            ) from None

    def close(self) -> None:
        self._store.close()
