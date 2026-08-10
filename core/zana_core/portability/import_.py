"""Safe archive import with canonical validation and full cleanup."""

from __future__ import annotations

import os
import shutil
import uuid
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path

from zana_core.artifacts import ArtifactStore
from zana_core.artifacts.digest import validate_digest
from zana_core.images import archive as _images_archive
from zana_core.images import import_plan as _images_import_plan
from zana_core.images import secrets as _images_secrets
from zana_core.images.archive import ArchiveCodecError, ArchiveFormat, CodecLimits, ImageCodec
from zana_core.images.import_plan import ImportLimits, ImportValidationError
from zana_core.images.oci import OciValidationError, validate_oci_layout
from zana_core.portability.guards import OperationGuard
from zana_core.portability.models import (
    ABS_MAX_UNPACKED_BYTES,
    MAX_BLOB_REGISTRATIONS,
    BlobRegistration,
    CleanupEvidence,
    CodecKind,
    Deadline,
    ImportRequest,
    ImportResult,
    OperationStage,
    PortabilityError,
    PortabilityLimits,
    RecoveryAction,
    RegistrationDbIntent,
    RegistrationPlan,
    utc_now,
)
from zana_core.portability.paths import (
    confine,
    deadline_digest,
    fsync_directory,
    open_regular_nofollow,
    remove_quietly,
    remove_tree_confined,
    require_regular_file,
    secure_mkdir,
    validate_approved_roots,
    validate_data_root,
)

BaseAvailabilityProbe = Callable[[str], bool]
CodecFactory = Callable[[ArchiveFormat], ImageCodec]
Clock = Callable[[], float]


def _default_base_available(_digest: str) -> bool:
    # No model registry is wired in this lane; nothing is known available.
    return False


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


def _fresh_import_request(request: ImportRequest) -> ImportRequest:
    if type(request) is not ImportRequest:
        raise PortabilityError(
            "request must be an exact ImportRequest model",
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
        return ImportRequest.model_validate(fresh_raw)
    except Exception as error:
        raise PortabilityError(
            "request could not be revalidated",
            code="REQUEST_INVALID",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        ) from error


class ImportService:
    """Import a real archive into the artifact store with bounded cleanup."""

    def __init__(
        self,
        store: ArtifactStore,
        approved_roots: Sequence[Path],
        data_root: Path,
        *,
        base_available: BaseAvailabilityProbe | None = None,
        codec_factory: CodecFactory | None = None,
        clock: Clock | None = None,
    ) -> None:
        roots = validate_approved_roots(approved_roots)
        resolved_data = validate_data_root(data_root, roots)
        secure_mkdir(resolved_data, mode=0o700, stage=OperationStage.PREFLIGHT)
        self._store = store
        self._approved_roots = roots
        self._data_root = resolved_data
        self._guard = OperationGuard(resolved_data)
        self._base_available = _default_base_available if base_available is None else base_available
        self._codec_factory = _default_codec if codec_factory is None else codec_factory
        self._clock = _default_clock if clock is None else clock

    def import_archive(
        self,
        request: ImportRequest,
        *,
        retain_layouts_root: Path | None = None,
        available_base_digests: set[str] | None = None,
    ) -> ImportResult:
        """Validate, unpack, register, and clean up the temp workspace.

        When ``retain_layouts_root`` is provided it must be a directory under
        the data root. The validated layout is retained at
        ``<retain_layouts_root>/<image-digest-hex>`` atomically when absent;
        an existing retained layout is revalidated against the archive plan
        before it is accepted, so duplicate imports stay idempotent and
        conflicting material fails closed.
        """
        request = _fresh_import_request(request)
        stages: list[OperationStage] = [OperationStage.PREFLIGHT]
        limits = request.limits
        deadline = Deadline(limits.deadline_seconds, clock=self._clock)
        deadline.check(OperationStage.PREFLIGHT)
        retain_parent = (
            _validated_retain_root(
                retain_layouts_root,
                self._approved_roots,
                self._data_root,
            )
            if retain_layouts_root is not None
            else None
        )
        source_path = Path(request.source)
        if source_path.is_symlink():
            raise PortabilityError(
                "symlinked archive sources are not accepted",
                code="SYMLINK_NOT_ALLOWED",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.REMOVE_SYMLINK,
            )
        source = confine(
            source_path,
            self._approved_roots,
            stage=OperationStage.PREFLIGHT,
        )
        require_regular_file(source, stage=OperationStage.PREFLIGHT)
        deadline.check(OperationStage.PREFLIGHT)
        source_fd, source_info = open_regular_nofollow(source, stage=OperationStage.PREFLIGHT)
        source_size = source_info.st_size
        if source_size > limits.max_archive_bytes:
            os.close(source_fd)
            raise PortabilityError(
                f"archive size {source_size} exceeds limit {limits.max_archive_bytes}",
                code="ARCHIVE_SIZE_LIMIT_EXCEEDED",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.REPAIR_ARCHIVE,
            )
        try:
            codec = self._resolve_codec(source, request.codec)
            if codec.format_name not in _images_archive.available_codecs():
                raise PortabilityError(
                    f"codec {codec.format_name} is not available on this host",
                    code="CODEC_UNAVAILABLE",
                    stage=OperationStage.PREFLIGHT,
                    recovery_action=RecoveryAction.INSTALL_ZSTD,
                )
        except Exception:
            os.close(source_fd)
            raise
        workspace = self._data_root / "portability" / "workspaces" / uuid.uuid4().hex
        workspace = secure_mkdir(workspace, mode=0o700, stage=OperationStage.UNPACK)
        workspace_created = True
        try:
            with self._guard.acquire(request.operation_id, f"import:{source}"):
                stages.append(OperationStage.LOCK)
                self._admit_disk_space(workspace, source_size, limits)
                deadline.check(OperationStage.UNPACK)
                snapshot = workspace / "archive.snapshot"
                archive_digest = deadline_digest(
                    source,
                    chunk_size=limits.chunk_size,
                    max_bytes=limits.max_archive_bytes,
                    deadline=deadline,
                    stage=OperationStage.UNPACK,
                    output_path=snapshot,
                    source_fd=source_fd,
                )
                os.close(source_fd)
                source_fd = -1
                deadline.check(OperationStage.UNPACK)
                try:
                    codec.unpack(
                        snapshot,
                        workspace,
                        limits=_codec_limits(limits),
                        deadline=deadline.remaining(),
                    )
                except ArchiveCodecError as error:
                    raise PortabilityError(
                        "archive extraction failed due to traversal or unsafe content",
                        code="ARCHIVE_EXTRACTION_FAILED",
                        stage=OperationStage.UNPACK,
                        recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                    ) from error
                stages.append(OperationStage.UNPACK)
                deadline.check(OperationStage.SECRET_SCAN)
                member_names = _relative_files(workspace, limits, deadline)
                try:
                    _images_secrets.ExclusionScanner().scan_member_names(member_names)
                    _images_secrets.scan_layout_payloads(
                        workspace,
                        max_json_bytes=limits.max_json_bytes,
                        deadline=deadline,
                    )
                except _images_secrets.ExclusionError as error:
                    raise PortabilityError(
                        "import exclusion scan rejected unsafe content",
                        code="EXCLUSION_REJECTED",
                        stage=OperationStage.SECRET_SCAN,
                        recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                    ) from error
                stages.append(OperationStage.SECRET_SCAN)
                deadline.check(OperationStage.OCI_VALIDATION)
                try:
                    result = _images_import_plan.register_into_store(
                        self._store,
                        workspace,
                        base_available=self._base_available,
                        available_base_digests=available_base_digests,
                        limits=ImportLimits(
                            max_json_bytes=limits.max_json_bytes,
                            max_blob_bytes=limits.max_member_bytes,
                            max_total_bytes=limits.max_unpacked_bytes,
                            chunk_size=limits.chunk_size,
                            deadline_seconds=limits.deadline_seconds,
                        ),
                        deadline_seconds=deadline.remaining(),
                    )
                except (OciValidationError, ImportValidationError) as error:
                    raise PortabilityError(
                        "OCI layout validation failed due to secret or malformed content",
                        code="OCI_VALIDATION_FAILED",
                        stage=OperationStage.OCI_VALIDATION,
                        recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                    ) from error
                stages.append(OperationStage.OCI_VALIDATION)
                deadline.check(OperationStage.REGISTER)
                stages.append(OperationStage.REGISTER)
                deadline.check(OperationStage.COMPLETE)
                plan = result.plan
                registration = result.registration
                if registration is not None:
                    already_values = registration.already_present_digests
                    if (
                        type(already_values) is not tuple
                        or len(already_values) > MAX_BLOB_REGISTRATIONS
                    ):
                        raise PortabilityError(
                            "registration already-present digests are invalid",
                            code="REGISTRATION_INVALID",
                            stage=OperationStage.REGISTER,
                            recovery_action=RecoveryAction.RETRY,
                        )
                    already = set(already_values)
                else:
                    already = set()
                blobs: list[BlobRegistration] = []
                blob_values = plan.blob_digests
                if type(blob_values) is not tuple or len(blob_values) > MAX_BLOB_REGISTRATIONS:
                    raise PortabilityError(
                        "registration blob digests are invalid",
                        code="REGISTRATION_INVALID",
                        stage=OperationStage.REGISTER,
                        recovery_action=RecoveryAction.RETRY,
                    )
                all_digests = list(blob_values)
                if (
                    plan.config_digest not in all_digests
                    and len(all_digests) < MAX_BLOB_REGISTRATIONS
                ):
                    all_digests.append(plan.config_digest)
                for digest in all_digests:
                    deadline.check(OperationStage.REGISTER)
                    blobs.append(
                        BlobRegistration(
                            digest=digest,
                            size_bytes=_safe_store_size(self._store, digest),
                            already_present=digest in already,
                        )
                    )
                stages.append(OperationStage.COMPLETE)
                layout_root_value: str | None = None
                layout_created = False
                if retain_parent is not None:
                    layout_root_value, layout_created = self._retain_layout(
                        workspace,
                        plan,
                        retain_parent,
                        limits,
                        deadline,
                    )
                    if layout_created:
                        workspace_created = False
                cleanup = CleanupEvidence(
                    removed_paths=() if layout_created else (str(workspace),),
                    workspace_removed=not layout_created,
                )
                if not layout_created:
                    self._cleanup_workspace(workspace)
                    workspace_created = False
                return ImportResult(
                    operation_id=request.operation_id,
                    source=str(source),
                    archive_digest=archive_digest,
                    codec=codec.format_name,
                    registration=RegistrationPlan(
                        image_digest=plan.image_digest,
                        config_digest=plan.config_digest,
                        manifest_digest=plan.manifest_digest,
                        config_name=plan.config_name,
                        config_version=plan.config_version,
                        base_model_key=plan.base_model_key,
                        blobs=tuple(blobs),
                        runnable=plan.runnability.state,
                        runnable_reason=plan.runnability.reason,
                        base_model_digest=plan.base_model_digest,
                        db_intent=RegistrationDbIntent(
                            image_digest=plan.image_digest,
                            config_digest=plan.config_digest,
                            base_model_digest=plan.base_model_digest,
                            runnable=plan.runnability.state,
                        ),
                    ),
                    stages=tuple(stages),
                    cleanup=cleanup,
                    layout_root=layout_root_value,
                    layout_created=layout_created,
                    completed_at=utc_now(),
                )
        finally:
            if source_fd != -1:
                os.close(source_fd)
            if workspace_created:
                remove_tree_confined(workspace, self._data_root)

    def _admit_disk_space(
        self,
        workspace: Path,
        archive_size: int,
        limits: PortabilityLimits,
    ) -> None:
        """Conservative disk admission before extraction."""
        try:
            usage = shutil.disk_usage(self._data_root)
        except OSError as error:
            raise PortabilityError(
                "could not inspect disk space",
                code="DISK_PREFLIGHT_FAILED",
                stage=OperationStage.DISK_PREFLIGHT,
                recovery_action=RecoveryAction.RETRY,
            ) from error
        if type(usage.free) is not int or usage.free < 0:
            raise PortabilityError(
                "disk usage probe returned an invalid result",
                code="DISK_PREFLIGHT_FAILED",
                stage=OperationStage.DISK_PREFLIGHT,
                recovery_action=RecoveryAction.RETRY,
            )
        required = archive_size + limits.max_unpacked_bytes + limits.min_free_slack_bytes
        if usage.free < required:
            raise PortabilityError(
                "insufficient disk space for import",
                code="DISK_INSUFFICIENT",
                stage=OperationStage.DISK_PREFLIGHT,
                recovery_action=RecoveryAction.FREE_DISK_SPACE,
            )

    def _cleanup_workspace(self, workspace: Path) -> None:
        try:
            remove_tree_confined(workspace, self._data_root)
        except Exception as error:
            raise PortabilityError(
                "import workspace cleanup failed",
                code="CLEANUP_FAILED",
                stage=OperationStage.CLEANUP,
                recovery_action=RecoveryAction.CLEAR_TEMP_WORKSPACE,
            ) from error

    def _retain_layout(
        self,
        workspace: Path,
        plan: _images_import_plan.ImageRegistrationPlan,
        retain_parent: Path,
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> tuple[str, bool]:
        """Retain one validated layout; return (layout_root, created)."""
        digest = plan.image_digest
        try:
            validate_digest(digest)
        except Exception as error:
            raise PortabilityError(
                "registration image digest is not canonical",
                code="REGISTRATION_INVALID",
                stage=OperationStage.REGISTER,
                recovery_action=RecoveryAction.RETRY,
            ) from error
        target = retain_parent / digest.removeprefix("sha256:")
        try:
            secure_mkdir(retain_parent, mode=0o700, stage=OperationStage.CLEANUP)
        except Exception as error:
            raise PortabilityError(
                "portability layout directory could not be prepared",
                code="LAYOUT_RETENTION_FAILED",
                stage=OperationStage.CLEANUP,
                recovery_action=RecoveryAction.RETRY,
            ) from error
        if target.is_symlink() or (target.exists() and not target.is_dir()):
            raise PortabilityError(
                "retained portability layout is not a real directory",
                code="LAYOUT_CONFLICT",
                stage=OperationStage.CLEANUP,
                recovery_action=RecoveryAction.REPAIR_ARCHIVE,
            )
        if target.exists():
            try:
                existing = validate_oci_layout(
                    target,
                    max_json_bytes=limits.max_json_bytes,
                    max_blob_bytes=limits.max_member_bytes,
                    max_total_bytes=limits.max_unpacked_bytes,
                    chunk_size=limits.chunk_size,
                    deadline_seconds=deadline.remaining(),
                )
            except OciValidationError as error:
                raise PortabilityError(
                    "existing retained layout is corrupted",
                    code="LAYOUT_CONFLICT",
                    stage=OperationStage.CLEANUP,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                ) from error
            if existing.index_digest != plan.image_digest:
                raise PortabilityError(
                    "existing retained layout does not match this image digest",
                    code="LAYOUT_CONFLICT",
                    stage=OperationStage.CLEANUP,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                )
            if existing.config_digest != plan.config_digest:
                raise PortabilityError(
                    "existing retained layout does not match this image config",
                    code="LAYOUT_CONFLICT",
                    stage=OperationStage.CLEANUP,
                    recovery_action=RecoveryAction.REPAIR_ARCHIVE,
                )
            return str(target), False
        remove_quietly(workspace / "archive.snapshot")
        try:
            os.rename(workspace, target)
        except OSError as error:
            raise PortabilityError(
                "validated layout could not be retained",
                code="LAYOUT_RETENTION_FAILED",
                stage=OperationStage.CLEANUP,
                recovery_action=RecoveryAction.RETRY,
            ) from error
        with suppress(Exception):
            fsync_directory(target.parent)
        return str(target), True

    def _resolve_codec(self, source: Path, requested: CodecKind | None) -> ImageCodec:
        suffixes = source.suffixes
        combined = (
            "".join(suffixes[-2:]) if len(suffixes) >= 2 else (suffixes[-1] if suffixes else "")
        )
        codec = _images_archive.codec_for_extension(combined)
        if codec is None:
            codec = _images_archive.codec_for_extension(source.suffix)
        if codec is None:
            raise PortabilityError(
                f"unsupported archive extension for {source.name}",
                code="CODEC_UNKNOWN",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.USE_SUPPORTED_CODEC,
            )
        if requested is not None and requested != codec.format_name:
            raise PortabilityError(
                f"requested codec {requested.value} does not match extension {source.suffix}",
                code="CODEC_EXTENSION_MISMATCH",
                stage=OperationStage.PREFLIGHT,
                recovery_action=RecoveryAction.USE_SUPPORTED_CODEC,
            )
        return self._codec_factory(codec.format_name)


def _relative_files(
    root: Path,
    limits: PortabilityLimits,
    deadline: Deadline,
) -> list[str]:
    try:
        entries = _images_archive.walk_bounded_tree(
            root,
            remaining_budget=limits.max_members,
            max_depth=limits.max_depth,
            max_path_chars=limits.max_path_chars,
        )
    except ArchiveCodecError as error:
        raise PortabilityError(
            "unpacked member collection exceeded limits",
            code="MEMBER_LIMIT_EXCEEDED",
            stage=OperationStage.SECRET_SCAN,
            recovery_action=RecoveryAction.REPAIR_ARCHIVE,
        ) from error
    deadline.check(OperationStage.SECRET_SCAN)
    return [name for name, _path in entries]


def _validated_retain_root(
    value: Path,
    approved_roots: Sequence[Path],
    data_root: Path,
) -> Path:
    if type(value) is not type(Path()):
        raise PortabilityError(
            "retain layouts root must be an exact concrete pathlib.Path",
            code="LAYOUT_RETENTION_FAILED",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    confined = confine(value, approved_roots, stage=OperationStage.PREFLIGHT)
    if confined == data_root:
        raise PortabilityError(
            "retain layouts root must not be the data root itself",
            code="LAYOUT_RETENTION_FAILED",
            stage=OperationStage.PREFLIGHT,
            recovery_action=RecoveryAction.RETRY,
        )
    return confined


def _safe_store_size(store: ArtifactStore, digest: str) -> int:
    try:
        size = store.size(digest)
    except Exception as error:
        raise PortabilityError(
            "artifact store size check failed",
            code="STORE_SIZE_FAILED",
            stage=OperationStage.REGISTER,
            recovery_action=RecoveryAction.RETRY,
        ) from error
    if (
        not isinstance(size, int)
        or isinstance(size, bool)
        or size < 0
        or size > ABS_MAX_UNPACKED_BYTES
    ):
        raise PortabilityError(
            "artifact store size returned an invalid result",
            code="STORE_SIZE_FAILED",
            stage=OperationStage.REGISTER,
            recovery_action=RecoveryAction.RETRY,
        )
    return size


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
