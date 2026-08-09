"""Import validation and atomic artifact-store registration plans."""

from __future__ import annotations

import math
import os
import stat
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, cast

from zana_core.artifacts.digest import validate_digest
from zana_core.artifacts.store import ArtifactStore
from zana_core.images.models import ImageRunnability
from zana_core.images.oci import OciValidationError, ValidatedLayout, validate_oci_layout

MAX_IMPORT_JSON_BYTES = 1024 * 1024
MAX_IMPORT_BLOB_BYTES = 16 * 1024**3
MAX_IMPORT_TOTAL_BYTES = 32 * 1024**3
MAX_IMPORT_CHUNK_BYTES = 1024 * 1024
MAX_IMPORT_DEADLINE_SECONDS = 3600.0
MAX_AVAILABLE_BASE_DIGESTS = 1024


class ImportValidationError(ValueError):
    """Raised when a layout cannot produce a registration plan."""


@dataclass(frozen=True)
class ImportLimits:
    """Hard-capped import bounds shared with the operation deadline."""

    max_json_bytes: int = MAX_IMPORT_JSON_BYTES
    max_blob_bytes: int = MAX_IMPORT_BLOB_BYTES
    max_total_bytes: int = MAX_IMPORT_TOTAL_BYTES
    chunk_size: int = MAX_IMPORT_CHUNK_BYTES
    deadline_seconds: float = 300.0

    def validated(self) -> ImportLimits:
        for name, value, maximum in (
            ("max_json_bytes", self.max_json_bytes, MAX_IMPORT_JSON_BYTES),
            ("max_blob_bytes", self.max_blob_bytes, MAX_IMPORT_BLOB_BYTES),
            ("max_total_bytes", self.max_total_bytes, MAX_IMPORT_TOTAL_BYTES),
            ("chunk_size", self.chunk_size, MAX_IMPORT_CHUNK_BYTES),
        ):
            if type(value) is not int or value <= 0 or value > maximum:
                raise ImportValidationError(f"{name} must be a bounded positive integer")
        if (
            type(self.deadline_seconds) not in (int, float)
            or type(self.deadline_seconds) is bool
            or not math.isfinite(float(self.deadline_seconds))
            or self.deadline_seconds <= 0
            or self.deadline_seconds > MAX_IMPORT_DEADLINE_SECONDS
        ):
            raise ImportValidationError("deadline must be finite and within the hard cap")
        if self.max_json_bytes > self.max_total_bytes or self.chunk_size > self.max_blob_bytes:
            raise ImportValidationError("import limits are internally inconsistent")
        return self


def _deadline_value(deadline_seconds: float | None, default: float) -> float:
    value = default if deadline_seconds is None else deadline_seconds
    if type(value) not in (int, float) or type(value) is bool:
        raise ImportValidationError("deadline must be an exact builtin number")
    if not math.isfinite(value) or value <= 0 or value > MAX_IMPORT_DEADLINE_SECONDS:
        raise ImportValidationError("deadline must be finite and within the hard cap")
    return value


class _BoundedSource:
    """Deadline/bounds-aware streaming source for artifact-store copies."""

    def __init__(
        self,
        path: Path,
        *,
        chunk_size: int,
        max_bytes: int,
        max_total_bytes: int,
        total_so_far: int,
        start: float,
        deadline_seconds: float,
        expected_size: int | None = None,
    ) -> None:
        _require_os_support()
        self._path = path
        self._chunk_size = chunk_size
        self._max_bytes = max_bytes
        self._max_total_bytes = max_total_bytes
        self._total_so_far = total_so_far
        self._start = start
        self._deadline_seconds = deadline_seconds
        try:
            self._fd = os.open(
                path,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            )
        except OSError as error:
            raise ImportValidationError("blob source could not be opened") from error
        self._initial = os.fstat(self._fd)
        if not stat.S_ISREG(self._initial.st_mode):
            os.close(self._fd)
            raise ImportValidationError("blob source is not a regular file")
        self._expected_size = self._initial.st_size if expected_size is None else expected_size
        self._handle = os.fdopen(self._fd, "rb")
        self.bytes_read = 0
        self.closed = False

    @property
    def size(self) -> int:
        return self._initial.st_size

    def read(self, size: int = -1) -> bytes:
        if self.closed:
            return b""
        _check_deadline(self._start, self._deadline_seconds)
        request = self._chunk_size if size < 0 else min(size, self._chunk_size)
        chunk = self._handle.read(request)
        self.bytes_read += len(chunk)
        if self.bytes_read > self._expected_size or self.bytes_read > self._max_bytes:
            raise ImportValidationError("blob changed or exceeded the per-blob byte limit")
        if self._total_so_far + self.bytes_read > self._max_total_bytes:
            raise ImportValidationError("blob copy exceeded the total byte limit")
        return chunk

    def verify_complete(self) -> None:
        """Require EOF, exact expected size, and unchanged fd identity."""
        if self.closed:
            raise ImportValidationError("blob source was closed before verification")
        extra = self._handle.read(1)
        if extra:
            raise ImportValidationError("blob source grew beyond the verified bytes")
        current = os.fstat(self._handle.fileno())
        if (
            (current.st_dev, current.st_ino) != (self._initial.st_dev, self._initial.st_ino)
            or current.st_size != self._initial.st_size
            or self.bytes_read != self._expected_size
        ):
            raise ImportValidationError("blob source changed during registration copy")

    def close(self) -> None:
        if not self.closed:
            self._handle.close()
            self.closed = True


def _require_os_support() -> None:
    for attribute in ("O_NOFOLLOW", "O_CLOEXEC"):
        if not hasattr(os, attribute):
            raise ImportValidationError("secure filesystem open is unsupported on this platform")


def _check_deadline(start: float, deadline_seconds: float) -> None:
    if time.monotonic() - start > deadline_seconds:
        raise ImportValidationError("import registration exceeded the deadline")


def _fresh_import_limits(limits: object) -> ImportLimits:
    if limits is None:
        return ImportLimits().validated()
    if type(limits) is not ImportLimits:
        raise ImportValidationError("import limits must be exact ImportLimits or None")
    return limits.validated()


@dataclass(frozen=True)
class ImageRegistrationPlan:
    """Atomic registration inputs validated before any store mutation."""

    image_digest: str
    config_digest: str
    manifest_digest: str
    index_digest: str
    blob_digests: tuple[str, ...]
    runnability: ImageRunnability
    config_name: str
    config_version: str
    base_model_digest: str | None
    base_model_key: str
    total_size: int


@dataclass(frozen=True)
class RegistrationResult:
    """Outcome of registering a validated layout into an artifact store."""

    image_digest: str
    registered_blob_digests: tuple[str, ...]
    runnability: ImageRunnability
    already_present_digests: tuple[str, ...] = ()


@dataclass(frozen=True)
class ImageImportResult:
    """Validation result combining the plan and a possible registration result."""

    plan: ImageRegistrationPlan
    registration: RegistrationResult | None = None


def plan_import(
    layout_root: Path,
    *,
    available_base_digests: set[str] | None = None,
    base_available: Callable[[str], bool] | None = None,
    limits: ImportLimits | None = None,
    deadline_seconds: float | None = None,
) -> ImageRegistrationPlan:
    """Validate an extracted OCI layout and produce a registration plan."""

    active = _fresh_import_limits(limits)
    trusted_available = _trusted_base_digests(available_base_digests)
    try:
        layout = validate_oci_layout(
            layout_root,
            max_json_bytes=active.max_json_bytes,
            max_blob_bytes=active.max_blob_bytes,
            max_total_bytes=active.max_total_bytes,
            chunk_size=active.chunk_size,
            deadline_seconds=_deadline_value(deadline_seconds, active.deadline_seconds),
        )
    except OciValidationError as error:
        raise ImportValidationError(
            "OCI layout validation failed due to secret or malformed content"
        ) from error
    return _plan_from_layout(layout, _resolve_available(layout, trusted_available, base_available))


def register_into_store(
    store: ArtifactStore,
    layout_root: Path,
    *,
    available_base_digests: set[str] | None = None,
    base_available: Callable[[str], bool] | None = None,
    limits: ImportLimits | None = None,
    deadline_seconds: float | None = None,
) -> ImageImportResult:
    """Register a validated layout's immutable blobs into ``store``.

    All blobs are copied before any metadata is produced; registration returns
    the deterministic result without touching the database. Missing or weak
    base identity is preserved as ``not-runnable`` state.
    """

    active = _fresh_import_limits(limits)
    trusted_available = _trusted_base_digests(available_base_digests)
    effective_deadline = _deadline_value(deadline_seconds, active.deadline_seconds)
    start = time.monotonic()
    try:
        layout = validate_oci_layout(
            layout_root,
            max_json_bytes=active.max_json_bytes,
            max_blob_bytes=active.max_blob_bytes,
            max_total_bytes=active.max_total_bytes,
            chunk_size=active.chunk_size,
            deadline_seconds=effective_deadline,
        )
    except OciValidationError as error:
        raise ImportValidationError(
            "OCI layout validation failed due to secret or malformed content"
        ) from error
    plan = _plan_from_layout(layout, _resolve_available(layout, trusted_available, base_available))
    _check_deadline(start, effective_deadline)
    registered: list[str] = []
    already_present: list[str] = []
    copied_bytes = 0
    for digest in layout.blob_digests:
        _check_deadline(start, effective_deadline)
        source = layout.root / "blobs" / "sha256" / digest.removeprefix("sha256:")
        if _store_exists(store, digest):
            already_present.append(digest)
        else:
            copied = _copy_bounded_to_store(
                store,
                source,
                digest,
                chunk_size=active.chunk_size,
                max_bytes=active.max_blob_bytes,
                max_total_bytes=active.max_total_bytes,
                total_so_far=copied_bytes,
                start=start,
                deadline_seconds=effective_deadline,
            )
            copied_bytes += copied
            registered.append(digest)
    config_digest = layout.config_digest
    config_source = layout.root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    _check_deadline(start, effective_deadline)
    if _store_exists(store, config_digest):
        already_present.append(config_digest)
    else:
        copied = _copy_bounded_to_store(
            store,
            config_source,
            config_digest,
            chunk_size=active.chunk_size,
            max_bytes=active.max_blob_bytes,
            max_total_bytes=active.max_total_bytes,
            total_so_far=copied_bytes,
            start=start,
            deadline_seconds=effective_deadline,
        )
        copied_bytes += copied
        registered.append(config_digest)
    result = RegistrationResult(
        image_digest=plan.image_digest,
        registered_blob_digests=tuple(registered),
        runnability=plan.runnability,
        already_present_digests=tuple(sorted(already_present)),
    )
    return ImageImportResult(plan=plan, registration=result)


def _copy_bounded_to_store(
    store: ArtifactStore,
    source: Path,
    expected_digest: str,
    *,
    chunk_size: int,
    max_bytes: int,
    max_total_bytes: int,
    total_so_far: int,
    start: float,
    deadline_seconds: float,
) -> int:
    _check_deadline(start, deadline_seconds)
    reader = _BoundedSource(
        source,
        chunk_size=chunk_size,
        max_bytes=max_bytes,
        max_total_bytes=max_total_bytes,
        total_so_far=total_so_far,
        start=start,
        deadline_seconds=deadline_seconds,
        expected_size=None,
    )
    try:
        if type(reader) is not _BoundedSource:
            raise ImportValidationError("blob source must be an exact _BoundedSource")
        expected_size = reader.size
        actual_digest = _store_put_stream(store, cast(BinaryIO, reader), chunk_size=chunk_size)
        reader.verify_complete()
    finally:
        reader.close()
    if actual_digest != expected_digest:
        raise ImportValidationError("blob digest mismatch after registration copy")
    return expected_size


def _store_exists(store: ArtifactStore, digest: str) -> bool:
    try:
        result = store.exists(digest)
    except Exception as error:
        raise ImportValidationError("artifact store existence check failed") from error
    if type(result) is not bool:
        raise ImportValidationError("artifact store existence check returned an invalid result")
    return result


def _store_put_stream(store: ArtifactStore, stream: BinaryIO, *, chunk_size: int) -> str:
    try:
        result = store.put_stream(stream, chunk_size=chunk_size)
    except Exception as error:
        raise ImportValidationError("artifact store copy failed") from error
    if type(result) is not str or not result.startswith("sha256:"):
        raise ImportValidationError("artifact store copy returned an invalid digest")
    return result


def _call_base_available(
    base_available: Callable[[str], bool] | None,
    digest: str,
) -> bool | None:
    if base_available is None:
        return None
    if not callable(base_available):
        raise ImportValidationError("base availability probe must be callable")
    try:
        result = base_available(digest)
    except Exception as error:
        raise ImportValidationError("base availability probe failed") from error
    if type(result) is not bool:
        raise ImportValidationError("base availability probe returned an invalid result")
    return result


def _plan_from_layout(
    layout: ValidatedLayout,
    available_base_digests: set[str] | None,
) -> ImageRegistrationPlan:
    runnability = layout.config.runnability(available_base_digests)
    base_model_key = layout.config.base_model.display_name or "unknown"
    return ImageRegistrationPlan(
        image_digest=layout.index_digest,
        config_digest=layout.config_digest,
        manifest_digest=layout.manifest_digest,
        index_digest=layout.index_digest,
        blob_digests=layout.blob_digests,
        runnability=runnability,
        config_name=layout.config.name,
        config_version=layout.config.version,
        base_model_digest=layout.config.base_model.identity_digest,
        base_model_key=base_model_key,
        total_size=layout.total_size,
    )


def _resolve_available(
    layout: ValidatedLayout,
    available_base_digests: set[str] | None,
    base_available: Callable[[str], bool] | None,
) -> set[str] | None:
    if base_available is None:
        return available_base_digests
    digest = layout.config.base_model.identity_digest
    if digest is None:
        return available_base_digests
    if available_base_digests is None:
        resolved: set[str] = set()
    else:
        resolved = set(available_base_digests)
    if _call_base_available(base_available, digest) is True:
        resolved.add(digest)
    return resolved


def _trusted_base_digests(available_base_digests: set[str] | None) -> set[str] | None:
    """Admit only None or an exact bounded set of canonical digest strings."""
    if available_base_digests is None:
        return None
    if type(available_base_digests) is not set:
        raise ImportValidationError("available base digests must be an exact builtin set")
    if len(available_base_digests) > MAX_AVAILABLE_BASE_DIGESTS:
        raise ImportValidationError("available base digest count exceeds the hard limit")
    trusted: set[str] = set()
    for digest in available_base_digests:
        if type(digest) is not str:
            raise ImportValidationError("available base digest must be an exact string")
        try:
            validate_digest(digest)
        except Exception as error:
            raise ImportValidationError("available base digest is not canonical") from error
        trusted.add(digest)
    return trusted
