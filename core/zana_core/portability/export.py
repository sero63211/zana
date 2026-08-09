"""Atomic real-filesystem image export service.

All integrity work (archive writing, OCI validation, secret/mutable-state
exclusion) is delegated to the canonical ``zana_core.images`` stack. One
injected monotonic deadline bounds the entire operation; every phase receives
only the remaining time.
"""

from __future__ import annotations

import os
import shutil
from collections.abc import Callable, Sequence
from pathlib import Path

from zana_core.images import archive as _images_archive
from zana_core.images import secrets as _images_secrets
from zana_core.images.archive import (
    ArchiveCodecError,
    ArchiveFormat,
    CodecLimits,
    ImageCodec,
    collect_bounded_layout_entries,
)
from zana_core.images.oci import OciValidationError, validate_oci_layout
from zana_core.portability.guards import OperationGuard
from zana_core.portability.models import (
    CleanupEvidence,
    Deadline,
    DiskPreflight,
    ExportRequest,
    ExportResult,
    OperationStage,
    PortabilityError,
    PortabilityLimits,
    PreconditionError,
    RecoveryAction,
    utc_now,
)
from zana_core.portability.paths import (
    _open_parent_dirfd,
    confine,
    deadline_digest,
    open_regular_nofollow,
    require_directory,
    secure_mkdir,
    sibling_temp_path,
    validate_approved_roots,
    validate_data_root,
)

DiskUsage = Callable[[Path], tuple[int, int, int]]
CodecFactory = Callable[[ArchiveFormat], ImageCodec]
Clock = Callable[[], float]


def _default_disk_usage(path: Path) -> tuple[int, int, int]:
    usage = shutil.disk_usage(path)
    return usage.total, usage.used, usage.free


def _default_codec(format_name: ArchiveFormat) -> ImageCodec:
    return _images_archive.codec_for_format(format_name)


def _default_clock() -> float:
    from time import monotonic

    return monotonic()


def _fresh_portability_limits(limits: object) -> PortabilityLimits:
    if type(limits) is not PortabilityLimits:
        raise PortabilityError(
            "limits must be an exact PortabilityLimits model",
            code="LIMITS_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    raw = limits.__dict__
    if type(raw) is not dict:
        raise PortabilityError(
            "limits raw state is invalid",
            code="LIMITS_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    try:
        return PortabilityLimits.model_validate(raw)
    except Exception as error:
        raise PortabilityError(
            "limits could not be revalidated",
            code="LIMITS_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        ) from error


def _fresh_export_request(request: ExportRequest) -> ExportRequest:
    if type(request) is not ExportRequest:
        raise PortabilityError(
            "request must be an exact ExportRequest model",
            code="REQUEST_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    raw = request.__dict__
    if type(raw) is not dict:
        raise PortabilityError(
            "request raw state is invalid",
            code="REQUEST_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    try:
        fresh_raw = dict(raw)
        fresh_raw["limits"] = _fresh_portability_limits(raw["limits"])
        return ExportRequest.model_validate(fresh_raw)
    except Exception as error:
        raise PortabilityError(
            "request could not be revalidated",
            code="REQUEST_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        ) from error


class ExportService:
    """Export a validated OCI layout atomically under approved roots."""

    def __init__(
        self,
        approved_roots: Sequence[Path],
        data_root: Path,
        *,
        disk_usage: DiskUsage | None = None,
        codec_factory: CodecFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        roots = validate_approved_roots(approved_roots)
        resolved_data = validate_data_root(data_root, roots)
        # Explicit decision: the validated data root is created here and
        # nowhere else; guards create their lock directory lazily.
        secure_mkdir(resolved_data, mode=0o700, stage=OperationStage.PREFLIGHT)
        self._approved_roots = roots
        self._data_root = resolved_data
        self._guard = OperationGuard(resolved_data)
        self._disk_usage = _default_disk_usage if disk_usage is None else disk_usage
        self._codec_factory = _default_codec if codec_factory is None else codec_factory
        self._clock = _default_clock if clock is None else clock

    def export(self, request: ExportRequest) -> ExportResult:
        """Validate, write, verify, and atomically replace the destination."""
        request = _fresh_export_request(request)
        stages: list[OperationStage] = [OperationStage.PREFLIGHT]
        limits = request.limits
        deadline = Deadline(limits.deadline_seconds, clock=self._clock)
        deadline.check(OperationStage.PREFLIGHT)
        layout = confine(
            Path(request.layout_path),
            self._approved_roots,
            stage=OperationStage.PREFLIGHT,
        )
        require_directory(layout, stage=OperationStage.PREFLIGHT)
        destination = confine(
            Path(request.destination),
            self._approved_roots,
            stage=OperationStage.PREFLIGHT,
        )
        deadline.check(OperationStage.PREFLIGHT)
        parent_dir = secure_mkdir(destination.parent, mode=0o700, stage=OperationStage.PREFLIGHT)
        if destination.is_dir():
            raise PortabilityError(
                "destination must not be a directory",
                code="DESTINATION_IS_DIRECTORY",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.CHOOSE_APPROVED_PATH,
            )
        if destination.is_symlink():
            raise PortabilityError(
                "destination must not be a symlink",
                code="DESTINATION_SYMLINK",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.REMOVE_SYMLINK,
            )
        if destination.exists() and not destination.is_file():
            raise PortabilityError(
                "existing destination is not a regular file",
                code="DESTINATION_NOT_REGULAR",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.CHOOSE_APPROVED_PATH,
            )
        if not _destination_matches_format(destination, request.codec):
            raise PortabilityError(
                f"destination extension does not match codec {request.codec.value}",
                code="CODEC_EXTENSION_MISMATCH",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.USE_SUPPORTED_CODEC,
            )
        if request.codec not in _images_archive.available_codecs():
            raise PortabilityError(
                f"codec {request.codec.value} is not available on this host",
                code="CODEC_UNAVAILABLE",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.INSTALL_ZSTD,
            )

        replace_identity: tuple[int, int, int] | None = None
        with self._guard.acquire(request.operation_id, f"export:{destination}"):
            stages.append(OperationStage.LOCK)
            deadline.check(OperationStage.LOCK)
            replace_identity = _check_replace_precondition(destination, request, deadline)
            deadline.check(OperationStage.VALIDATE_LAYOUT)
            try:
                layout_info = validate_oci_layout(
                    layout,
                    max_json_bytes=limits.max_json_bytes,
                    max_blob_bytes=limits.max_member_bytes,
                    max_total_bytes=limits.max_unpacked_bytes,
                    chunk_size=limits.chunk_size,
                    deadline_seconds=deadline.remaining(),
                )
            except OciValidationError as error:
                raise PortabilityError(
                    "OCI layout validation failed due to secret or malformed content",
                    code="OCI_VALIDATION_FAILED",
                    stage=OperationStage.VALIDATE_LAYOUT,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                ) from error
            stages.append(OperationStage.VALIDATE_LAYOUT)
            deadline.check(OperationStage.SECRET_SCAN)
            member_names = _layout_member_names(layout, limits, deadline)
            try:
                _images_secrets.ExclusionScanner().scan_member_names(member_names)
                _images_secrets.scan_layout_payloads(
                    layout,
                    max_json_bytes=limits.max_json_bytes,
                    deadline=deadline,
                )
            except _images_secrets.ExclusionError as error:
                raise PortabilityError(
                    "export exclusion scan rejected unsafe content",
                    code="EXCLUSION_REJECTED",
                    stage=OperationStage.SECRET_SCAN,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                ) from error
            stages.append(OperationStage.SECRET_SCAN)
            deadline.check(OperationStage.DISK_PREFLIGHT)
            required = _conservative_required_bytes(
                layout_info.total_size,
                len(layout_info.blob_digests) + 3,
                request.codec,
                limits,
            )
            usage = self._disk_usage(parent_dir)
            _total, _used, free = _validated_disk_usage(usage)
            required = _bounded_disk_requirement(required)
            free = _bounded_disk_requirement(free)
            preflight = DiskPreflight(
                required_bytes=required,
                available_bytes=free,
                sufficient=free >= required + limits.min_free_slack_bytes,
                path=str(parent_dir),
            )
            stages.append(OperationStage.DISK_PREFLIGHT)
            if not preflight.sufficient:
                raise PortabilityError(
                    f"insufficient disk space: need {required} bytes, have {free} bytes",
                    code="DISK_INSUFFICIENT",
                    stage=OperationStage.DISK_PREFLIGHT,
                    recovery_action=RecoveryAction.FREE_DISK_SPACE,
                )

            temp = sibling_temp_path(destination, "export")
            parent_fd: int | None = None
            try:
                codec = self._codec_factory(request.codec)
                archive_digest = codec.pack(
                    layout,
                    temp,
                    limits=_codec_limits(limits),
                    deadline=deadline.remaining(),
                )
                stages.append(OperationStage.CODE_WRITE)
                deadline.check(OperationStage.FSYNC)
                parent_fd, temp_name = _open_parent_dirfd(temp, stage=OperationStage.FSYNC)
                try:
                    temp_fd = os.open(
                        temp_name,
                        os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                        dir_fd=parent_fd,
                    )
                except OSError as error:
                    raise PortabilityError(
                        "archive temp could not be reopened safely",
                        code="ARCHIVE_WRITE_FAILED",
                        stage=OperationStage.FSYNC,
                        recovery_action=RecoveryAction.RETRY,
                    ) from error
                try:
                    os.fsync(temp_fd)
                    actual_digest = deadline_digest(
                        temp,
                        chunk_size=limits.chunk_size,
                        max_bytes=limits.max_archive_bytes,
                        deadline=deadline,
                        stage=OperationStage.FSYNC,
                        source_fd=temp_fd,
                    )
                finally:
                    os.close(temp_fd)
                stages.append(OperationStage.FSYNC)
                if actual_digest != archive_digest:
                    raise PortabilityError(
                        "archive digest verification failed after writing",
                        code="ARCHIVE_DIGEST_MISMATCH",
                        stage=OperationStage.FSYNC,
                        recovery_action=RecoveryAction.RETRY,
                    )
                deadline.check(OperationStage.ATOMIC_REPLACE)
                _revalidate_destination_dirfd(
                    destination,
                    request,
                    replace_identity,
                    deadline,
                    parent_fd,
                )
                os.rename(
                    temp_name,
                    destination.name,
                    src_dir_fd=parent_fd,
                    dst_dir_fd=parent_fd,
                )
                try:
                    os.fsync(parent_fd)
                    durability_uncertain = False
                except OSError:
                    durability_uncertain = True
                stages.append(OperationStage.ATOMIC_REPLACE)
                stages.append(OperationStage.COMPLETE)
                return ExportResult(
                    operation_id=request.operation_id,
                    archive_path=str(destination),
                    archive_digest=archive_digest,
                    layout_digest=layout_info.manifest_digest,
                    codec=request.codec,
                    stages=tuple(stages),
                    preflight=preflight,
                    cleanup=CleanupEvidence(),
                    durability_uncertain=durability_uncertain,
                    completed_at=utc_now(),
                )
            except ArchiveCodecError as error:
                _remove_temp_quietly(temp, parent_fd)
                raise PortabilityError(
                    "archive write failed",
                    code="ARCHIVE_WRITE_FAILED",
                    stage=OperationStage.CODE_WRITE,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                ) from error
            except Exception:
                _remove_temp_quietly(temp, parent_fd)
                raise
            finally:
                if parent_fd is not None:
                    os.close(parent_fd)


def _layout_member_names(
    layout: Path,
    limits: PortabilityLimits,
    deadline: Deadline,
) -> list[str]:
    names = ["oci-layout", "index.json", "manifest.json"]
    blob_dir = layout / "blobs" / "sha256"
    if blob_dir.is_dir() and not blob_dir.is_symlink():
        remaining = max(0, limits.max_members - len(names))
        try:
            blobs = collect_bounded_layout_entries(
                blob_dir,
                remaining_budget=remaining,
                label="layout blob",
            )
        except ArchiveCodecError as error:
            raise PortabilityError(
                "layout member collection exceeded limits",
                code="MEMBER_LIMIT_EXCEEDED",
                stage=OperationStage.SECRET_SCAN,
                recovery_action=RecoveryAction.REPAIR_ARCHIVE,
            ) from error
        names.extend(f"blobs/sha256/{blob.name}" for blob in blobs)
    deadline.check(OperationStage.SECRET_SCAN)
    return names


def _destination_matches_format(destination: Path, codec: ArchiveFormat) -> bool:
    suffixes = destination.suffixes
    combined = "".join(suffixes[-2:]) if len(suffixes) >= 2 else (suffixes[-1] if suffixes else "")
    mapped = _images_archive.codec_for_extension(combined)
    if mapped is None:
        mapped = _images_archive.codec_for_extension(destination.suffix)
    return mapped is not None and mapped.format_name == codec


def _codec_limits(limits: PortabilityLimits) -> CodecLimits:
    if type(limits) is not PortabilityLimits:
        raise PortabilityError(
            "limits must be an exact PortabilityLimits model",
            code="LIMITS_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    return CodecLimits(
        max_members=limits.max_members,
        max_member_bytes=limits.max_member_bytes,
        max_unpacked_bytes=limits.max_unpacked_bytes,
        max_depth=limits.max_depth,
        max_path_chars=limits.max_path_chars,
        chunk_size=limits.chunk_size,
        deadline_seconds=limits.deadline_seconds,
    )


def _check_replace_precondition(
    destination: Path,
    request: ExportRequest,
    deadline: Deadline,
) -> tuple[int, int, int] | None:
    if not destination.exists():
        if request.replace_allowed and request.replace_token != "absent":
            raise PreconditionError(
                "destination does not exist; replace token 'absent' is required",
                code="REPLACE_PRECONDITION_FAILED",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
            )
        return None
    if destination.is_symlink():
        raise PreconditionError(
            "destination is a symlink; replace refused",
            code="REPLACE_PRECONDITION_FAILED",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.REMOVE_SYMLINK,
        )
    if not request.replace_allowed or not request.replace_token:
        raise PreconditionError(
            "existing destination requires replace_allowed and a matching token",
            code="REPLACE_PRECONDITION_FAILED",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )
    fd, info = open_regular_nofollow(destination, stage=OperationStage.PREFLIGHT)
    try:
        current = deadline_digest(
            destination,
            chunk_size=request.limits.chunk_size,
            max_bytes=request.limits.max_archive_bytes,
            deadline=deadline,
            stage=OperationStage.PREFLIGHT,
            source_fd=fd,
        )
    finally:
        os.close(fd)
    if current != request.replace_token:
        raise PreconditionError(
            "replace token does not match current destination digest",
            code="STALE_REPLACE_TOKEN",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )
    return info.st_dev, info.st_ino, info.st_size


def _revalidate_destination(
    destination: Path,
    request: ExportRequest,
    identity: tuple[int, int, int] | None,
    deadline: Deadline,
) -> None:
    """Re-verify exact destination identity/digest immediately before commit."""
    if identity is None:
        try:
            fd = os.open(
                destination,
                _read_flags(),
            )
        except FileNotFoundError:
            return
        except OSError as error:
            raise PreconditionError(
                "destination precondition could not be revalidated",
                code="REPLACE_PRECONDITION_FAILED",
                stage=OperationStage.ATOMIC_REPLACE,
            ) from error
        os.close(fd)
        raise PreconditionError(
            "destination appeared after the replace precondition",
            code="STALE_REPLACE_TOKEN",
            stage=OperationStage.ATOMIC_REPLACE,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )
    fd, info = open_regular_nofollow(destination, stage=OperationStage.ATOMIC_REPLACE)
    try:
        if (info.st_dev, info.st_ino) != (identity[0], identity[1]):
            raise PreconditionError(
                "destination identity changed before commit",
                code="STALE_REPLACE_TOKEN",
                stage=OperationStage.ATOMIC_REPLACE,
                recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
            )
        current = deadline_digest(
            destination,
            chunk_size=request.limits.chunk_size,
            max_bytes=request.limits.max_archive_bytes,
            deadline=deadline,
            stage=OperationStage.ATOMIC_REPLACE,
            source_fd=fd,
        )
    finally:
        os.close(fd)
    if current != request.replace_token:
        raise PreconditionError(
            "destination digest changed before commit",
            code="STALE_REPLACE_TOKEN",
            stage=OperationStage.ATOMIC_REPLACE,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )


def _revalidate_destination_dirfd(
    destination: Path,
    request: ExportRequest,
    identity: tuple[int, int, int] | None,
    deadline: Deadline,
    parent_fd: int,
) -> None:
    """Re-verify exact destination identity/digest from a held parent dirfd."""
    if identity is None:
        try:
            fd = os.open(
                destination.name,
                os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=parent_fd,
            )
        except FileNotFoundError:
            return
        except OSError as error:
            raise PreconditionError(
                "destination precondition could not be revalidated",
                code="REPLACE_PRECONDITION_FAILED",
                stage=OperationStage.ATOMIC_REPLACE,
            ) from error
        os.close(fd)
        raise PreconditionError(
            "destination appeared after the replace precondition",
            code="STALE_REPLACE_TOKEN",
            stage=OperationStage.ATOMIC_REPLACE,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )
    try:
        fd = os.open(
            destination.name,
            os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
            dir_fd=parent_fd,
        )
    except OSError as error:
        raise PreconditionError(
            "destination precondition could not be revalidated",
            code="REPLACE_PRECONDITION_FAILED",
            stage=OperationStage.ATOMIC_REPLACE,
        ) from error
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != (identity[0], identity[1]):
            raise PreconditionError(
                "destination identity changed before commit",
                code="STALE_REPLACE_TOKEN",
                stage=OperationStage.ATOMIC_REPLACE,
                recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
            )
        current = deadline_digest(
            destination,
            chunk_size=request.limits.chunk_size,
            max_bytes=request.limits.max_archive_bytes,
            deadline=deadline,
            stage=OperationStage.ATOMIC_REPLACE,
            source_fd=fd,
        )
    finally:
        os.close(fd)
    if current != request.replace_token:
        raise PreconditionError(
            "destination digest changed before commit",
            code="STALE_REPLACE_TOKEN",
            stage=OperationStage.ATOMIC_REPLACE,
            recovery_action=RecoveryAction.PROVIDE_REPLACE_TOKEN,
        )


def _remove_temp_quietly(temp: Path, parent_fd: int | None) -> None:
    if parent_fd is not None:
        from contextlib import suppress as _suppress

        with _suppress(OSError):
            os.unlink(temp.name, dir_fd=parent_fd)
        return
    from zana_core.portability.paths import remove_quietly

    remove_quietly(temp)


def _conservative_required_bytes(
    layout_bytes: int,
    member_count: int,
    codec_kind: ArchiveFormat,
    limits: PortabilityLimits,
) -> int:
    metadata = _checked_mul(member_count, limits.codec_metadata_bytes_per_member)
    if codec_kind == ArchiveFormat.TAR_GZ:
        scaled = _checked_mul(layout_bytes + metadata, limits.gzip_expansion_factor)
        return _checked_add_int(int(scaled), limits.min_free_slack_bytes)
    metadata_int = int(metadata)
    return _checked_add_int(
        layout_bytes, _checked_add_int(metadata_int, limits.min_free_slack_bytes)
    )


def _checked_mul(left: int | float, right: int | float) -> int | float:
    result = left * right
    if result < 0:
        raise OverflowError("disk requirement underflow")
    return result


def _checked_add_int(left: int, right: int) -> int:
    result = left + right
    if result < 0:
        raise OverflowError("disk requirement underflow")
    return result


def _validated_disk_usage(usage: object) -> tuple[int, int, int]:
    if type(usage) is not tuple or len(usage) != 3:
        raise PortabilityError(
            "disk usage probe returned an invalid result",
            code="DISK_PREFLIGHT_FAILED",
            stage=OperationStage.DISK_PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    values: list[int] = []
    for value in usage:
        if type(value) is not int or value < 0:
            raise PortabilityError(
                "disk usage probe returned a non-finite value",
                code="DISK_PREFLIGHT_FAILED",
                stage=OperationStage.DISK_PREFLIGHT,
                recovery_action=RecoveryAction.RETRY,
            )
        values.append(value)
    return values[0], values[1], values[2]


def _read_flags() -> int:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    if hasattr(os, "O_CLOEXEC"):
        flags |= os.O_CLOEXEC
    return flags


def _bounded_disk_requirement(value: int) -> int:
    """Clamp preflight values to the DiskPreflight model maximum."""
    maximum = 32 * 1024**3 * 4
    if value < 0:
        return 0
    return min(value, maximum)


def _remove_quietly(path: Path) -> None:
    from zana_core.portability.paths import remove_quietly

    remove_quietly(path)
