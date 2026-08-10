"""Product-level portability orchestration over the canonical services.

This service owns the boundary between the persisted Image/Artifact registry
and the real archive services. Every database unit is short and closed before
archive I/O; exact base-model availability is always derived from the
persisted model digest, never from a display name.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Sequence
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import sessionmaker

from zana_core.artifacts import ArtifactStore
from zana_core.artifacts.digest import digest_bytes, validate_digest
from zana_core.db.models import Artifact, Image, ImageArtifact, Instance, Model
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import VerificationStatus
from zana_core.images import archive as _images_archive
from zana_core.images import secrets as _images_secrets
from zana_core.images.models import RunnableState, ZanaImageConfig
from zana_core.images.oci import (
    MEDIA_TYPE_OCI_INDEX,
    MEDIA_TYPE_OCI_LAYOUT,
    MEDIA_TYPE_OCI_MANIFEST,
    MEDIA_TYPE_ZANA_CONFIG,
    ROLE_MEDIA_TYPES,
    Index,
    Manifest,
    OciLayoutFile,
    OciValidationError,
    assemble_oci_layout,
    canonical_json_bytes,
    validate_oci_layout,
)
from zana_core.portability.boundary import OperationBoundary
from zana_core.portability.export import ExportService, _layout_member_names
from zana_core.portability.import_ import ImportService
from zana_core.portability.models import (
    CodecKind,
    Deadline,
    ExportRequest,
    ImportRequest,
    OperationStage,
    PortabilityError,
    PortabilityLimits,
    RecoveryAction,
)
from zana_core.portability.paths import (
    confine,
    open_regular_nofollow,
    remove_tree_confined,
    secure_mkdir,
    validate_approved_roots,
)

MAX_LAYOUT_ROLES = 64


@dataclass(frozen=True)
class LayoutRole:
    """One immutable layout artifact projected from validated OCI metadata."""

    role: str
    digest: str
    media_type: str
    size: int


@dataclass(frozen=True)
class RegisteredLayout:
    """A validated layout rooted at a persistent or reconstructed path."""

    path: Path
    temporary: bool
    source: str


@dataclass(frozen=True)
class RegistrySnapshot:
    """DB-only registry snapshot; no filesystem I/O is performed here."""

    image: Image
    base_digest: str | None
    base_available: bool
    persistent_layout: Path | None
    artifact_rows: tuple[tuple[str, str, str, int, str], ...]


@dataclass(frozen=True)
class ProductVerify:
    """Actionable verify result with exact base-model availability."""

    digest: str
    status: str
    runnable: RunnableState
    runnable_reason: str
    base_model_digest: str | None
    base_model_available: bool
    layout_source: str


@dataclass(frozen=True)
class ProductExport:
    """Export result plus a safe relative archive path."""

    result: Any
    relative_path: str
    report_relative_path: str
    report_digest: str


@dataclass(frozen=True)
class ProductImport:
    """Import result plus registry idempotency state."""

    result: Any
    idempotent: bool
    created: bool
    base_model_available: bool
    artifact_count: int


@dataclass(frozen=True)
class ProductDelete:
    """Delete result; immutable blobs are intentionally never garbage collected."""

    digest: str
    deleted: bool
    artifacts_retained: bool


class PortabilityProductService:
    """Authenticated export/verify/import/delete orchestration."""

    def __init__(
        self,
        session_factory: sessionmaker,
        data_root: Path,
        *,
        export_service: ExportService | None = None,
        import_service: ImportService | None = None,
        store: ArtifactStore | None = None,
    ) -> None:
        if type(session_factory) is not sessionmaker:
            raise PortabilityError(
                "session factory must be an exact sqlalchemy sessionmaker",
                code="SESSION_FACTORY_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
        resolved = Path(data_root).resolve(strict=False)
        secure_mkdir(resolved, mode=0o700, stage=OperationStage.PREFLIGHT)
        roots = validate_approved_roots([resolved])
        self._data_root = resolved
        self._session_factory = session_factory
        self._store = store if store is not None else ArtifactStore(resolved / "artifacts")
        self._exports_root = secure_mkdir(
            resolved / "portability" / "exports",
            mode=0o700,
            stage=OperationStage.PREFLIGHT,
        )
        self._imports_root = secure_mkdir(
            resolved / "portability" / "imports",
            mode=0o700,
            stage=OperationStage.PREFLIGHT,
        )
        self._layouts_root = secure_mkdir(
            resolved / "portability" / "layouts",
            mode=0o700,
            stage=OperationStage.PREFLIGHT,
        )
        self._tmp_root = secure_mkdir(
            resolved / "portability" / "tmp",
            mode=0o700,
            stage=OperationStage.PREFLIGHT,
        )
        self._export_service = export_service or ExportService(roots, resolved)
        self._import_service = import_service or ImportService(
            self._store,
            roots,
            resolved,
        )

    @property
    def exports_root(self) -> Path:
        return self._exports_root

    @property
    def imports_root(self) -> Path:
        return self._imports_root

    def verify(
        self,
        digest: str,
        *,
        limits: PortabilityLimits | None = None,
        deadline_seconds: float | None = None,
        boundary: OperationBoundary | None = None,
    ) -> ProductVerify:
        """Verify one registered image's exact registry graph and base model."""
        canonical = _canonical_digest(digest)
        active = _fresh_limits(limits, deadline_seconds)
        deadline = Deadline(active.deadline_seconds)
        active_boundary = boundary if boundary is not None else OperationBoundary.noop()
        active_boundary.check(OperationStage.PREFLIGHT, fraction=0.0)
        with UnitOfWork(self._session_factory) as uow:
            image = uow.images.get(canonical)
            if image is None:
                raise _portability_error(
                    "IMAGE_NOT_FOUND",
                    "No image exists with this digest.",
                    stage=OperationStage.PREFLIGHT,
                    actions=("list_images",),
                )
            snapshot = self._snapshot_registry(uow, image)
        active_boundary.check(OperationStage.VALIDATE_LAYOUT, fraction=0.2)
        base_digest = snapshot.base_digest
        base_available = snapshot.base_available
        try:
            registered = self._materialize_layout(snapshot, active, deadline)
        except PortabilityError as error:
            if error.code == "IMAGE_MATERIAL_MISSING":
                return ProductVerify(
                    digest=canonical,
                    status="material-missing",
                    runnable=RunnableState.NOT_RUNNABLE_UNKNOWN,
                    runnable_reason="Image material is missing from the local registry.",
                    base_model_digest=base_digest,
                    base_model_available=base_available,
                    layout_source="missing",
                )
            if error.code in (
                "REGISTRY_MISMATCH",
                "IMAGE_MATERIAL_CORRUPTED",
            ):
                status = "registry-mismatch" if error.code == "REGISTRY_MISMATCH" else "corrupted"
                return ProductVerify(
                    digest=canonical,
                    status=status,
                    runnable=RunnableState.NOT_RUNNABLE_UNKNOWN,
                    runnable_reason=error.message,
                    base_model_digest=base_digest,
                    base_model_available=base_available,
                    layout_source=snapshot.persistent_layout is not None
                    and "persisted"
                    or "reconstructed",
                )
            raise
        active_boundary.check(OperationStage.SECRET_SCAN, fraction=0.6)
        try:
            validated = self._validate_layout_for_image(
                registered.path,
                snapshot.image,
                active,
                deadline,
            )
        except PortabilityError as error:
            if error.code in (
                "IMAGE_MATERIAL_CORRUPTED",
                "REGISTRY_MISMATCH",
                "BASE_MODEL_MISMATCH",
            ):
                status = {
                    "IMAGE_MATERIAL_CORRUPTED": "corrupted",
                    "REGISTRY_MISMATCH": "registry-mismatch",
                    "BASE_MODEL_MISMATCH": "base-model-mismatch",
                }[error.code]
                return ProductVerify(
                    digest=canonical,
                    status=status,
                    runnable=RunnableState.NOT_RUNNABLE_UNKNOWN,
                    runnable_reason=error.message,
                    base_model_digest=base_digest,
                    base_model_available=base_available,
                    layout_source=registered.source,
                )
            raise
        finally:
            if registered.temporary:
                _remove_owned_layout(registered.path, self._data_root)
        active_boundary.check(OperationStage.COMPLETE, fraction=1.0)
        runnable_set = None if base_digest is None else ({base_digest} if base_available else set())
        runnability = validated.config.base_model.runnability(runnable_set)
        return ProductVerify(
            digest=canonical,
            status=runnability.state.value,
            runnable=runnability.state,
            runnable_reason=runnability.reason,
            base_model_digest=base_digest,
            base_model_available=base_available,
            layout_source=registered.source,
        )

    def export(
        self,
        digest: str,
        *,
        output_path: str,
        codec: CodecKind,
        replace_token: str | None,
        replace_allowed: bool,
        user_approved: bool,
        limits: PortabilityLimits | None = None,
        deadline_seconds: float | None = None,
        boundary: OperationBoundary | None = None,
    ) -> ProductExport:
        """Export a verified registered image into the managed exports root."""
        active_boundary = boundary if boundary is not None else OperationBoundary.noop()
        active_boundary.check(OperationStage.PREFLIGHT, fraction=0.0)
        if not user_approved:
            raise _portability_error(
                "APPROVAL_REQUIRED",
                "Export requires explicit user approval.",
                stage=OperationStage.PREFLIGHT,
                actions=("confirm_export_path",),
            )
        canonical = _canonical_digest(digest)
        active = _fresh_limits(limits, deadline_seconds)
        deadline = Deadline(active.deadline_seconds)
        destination = confine(
            Path(output_path),
            [self._exports_root],
            stage=OperationStage.PREFLIGHT,
        )
        if destination.is_symlink():
            raise _portability_error(
                "DESTINATION_SYMLINK",
                "The export destination must not be a symlink.",
                stage=OperationStage.PREFLIGHT,
                actions=("remove_symlink",),
            )
        if codec not in _images_archive.available_codecs():
            raise _portability_error(
                "CODEC_UNAVAILABLE",
                "The requested archive codec is not available on this host.",
                stage=OperationStage.PREFLIGHT,
                actions=("use_supported_codec", "install_zstd"),
            )
        with UnitOfWork(self._session_factory) as uow:
            image = _image_or_404(uow, canonical)
            snapshot = self._snapshot_registry(uow, image)
        active_boundary.check(OperationStage.VALIDATE_LAYOUT, fraction=0.2)
        registered = self._materialize_layout(snapshot, active, deadline)
        self._validate_layout_for_image(registered.path, snapshot.image, active, deadline)
        active_boundary.check(OperationStage.CODE_WRITE, fraction=0.6)
        request = ExportRequest(
            operation_id=uuid.uuid4().hex,
            layout_path=str(registered.path),
            destination=str(destination),
            codec=codec,
            replace_token=replace_token,
            replace_allowed=replace_allowed,
            limits=active,
        )
        try:
            result = self._export_service.export(request)
        finally:
            if registered.temporary:
                _remove_owned_layout(registered.path, self._data_root)
        active_boundary.check(OperationStage.FSYNC, fraction=0.9)
        report_path, report_digest = self._write_sidecar_report(
            destination,
            result,
            active,
            image_digest=digest,
        )
        active_boundary.check(OperationStage.COMPLETE, fraction=1.0)
        return ProductExport(
            result=result,
            relative_path=_safe_relative_path(result.archive_path, self._data_root),
            report_relative_path=_safe_relative_path(str(report_path), self._data_root),
            report_digest=report_digest,
        )

    def import_archive(
        self,
        *,
        local_path: str,
        codec: CodecKind | None,
        user_approved: bool,
        limits: PortabilityLimits | None = None,
        deadline_seconds: float | None = None,
        boundary: OperationBoundary | None = None,
    ) -> ProductImport:
        """Import a user-approved archive and register it atomically."""
        active_boundary = boundary if boundary is not None else OperationBoundary.noop()
        active_boundary.check(OperationStage.PREFLIGHT, fraction=0.0)
        if not user_approved:
            raise _portability_error(
                "APPROVAL_REQUIRED",
                "Import requires explicit user approval.",
                stage=OperationStage.PREFLIGHT,
                actions=("confirm_import_path",),
            )
        active = _fresh_limits(limits, deadline_seconds)
        source = confine(
            Path(local_path),
            [self._imports_root],
            stage=OperationStage.PREFLIGHT,
        )
        with UnitOfWork(self._session_factory) as uow:
            base_digests = _available_base_digests(uow)
        active_boundary.check(OperationStage.UNPACK, fraction=0.3)
        import_result = self._import_service.import_archive(
            ImportRequest(
                operation_id=uuid.uuid4().hex,
                source=str(source),
                codec=codec,
                limits=active,
            ),
            retain_layouts_root=self._layouts_root,
            available_base_digests=base_digests,
        )
        active_boundary.check(OperationStage.OCI_VALIDATION, fraction=0.7)
        try:
            product = self._register_import(import_result, active, base_digests)
        except Exception:
            if import_result.layout_created and import_result.layout_root:
                with suppress(Exception):
                    _remove_owned_layout(
                        Path(import_result.layout_root),
                        self._data_root,
                    )
            raise
        active_boundary.check(OperationStage.COMPLETE, fraction=1.0)
        return ProductImport(
            result=import_result,
            idempotent=product.idempotent,
            created=product.created,
            base_model_available=product.base_model_available,
            artifact_count=product.artifact_count,
        )

    def delete(self, digest: str, *, confirmed: bool) -> ProductDelete:
        """Delete only registry/image rows; immutable blobs stay untouched."""
        if not confirmed:
            raise _portability_error(
                "DELETE_CONFIRMATION_REQUIRED",
                "Image deletion requires explicit user confirmation.",
                stage=OperationStage.PREFLIGHT,
                actions=("confirm_delete",),
            )
        canonical = _canonical_digest(digest)
        uow = UnitOfWork(self._session_factory)
        try:
            image = uow.images.get(canonical)
            if image is None:
                raise _portability_error(
                    "IMAGE_NOT_FOUND",
                    "No image exists with this digest.",
                    stage=OperationStage.PREFLIGHT,
                    actions=("list_images",),
                )
            referenced = uow.session.scalars(
                select(Instance.id).where(Instance.image_digest == canonical).limit(1)
            ).first()
            if referenced is not None:
                raise _portability_error(
                    "IMAGE_IN_USE",
                    "This image is referenced by an instance and cannot be deleted.",
                    stage=OperationStage.PREFLIGHT,
                    actions=("list_instances",),
                )
            uow.session.execute(
                delete(ImageArtifact).where(ImageArtifact.image_digest == canonical)
            )
            uow.images.delete(image)
            uow.commit()
        except Exception:
            uow.rollback()
            raise
        finally:
            uow.close()
        return ProductDelete(digest=canonical, deleted=True, artifacts_retained=True)

    def _snapshot_registry(
        self,
        uow: UnitOfWork,
        image: Image,
    ) -> RegistrySnapshot:
        hex_digest = image.digest.removeprefix("sha256:")
        persistent: Path | None = None
        for candidate in (
            self._layouts_root / hex_digest,
            self._data_root / "images" / "manifests" / hex_digest,
        ):
            if candidate.is_symlink():
                raise _portability_error(
                    "IMAGE_MATERIAL_INVALID",
                    "Registered image layout must not be a symlink.",
                    stage=OperationStage.VALIDATE_LAYOUT,
                    actions=("repair_image_material",),
                )
            if candidate.is_dir():
                persistent = candidate
                break
        rows: list[tuple[str, str, str, int, str]] = []
        for row in uow.image_artifacts.list_for_image(image.digest):
            artifact = uow.artifacts.get(row.artifact_digest)
            if artifact is None:
                continue
            rows.append(
                (
                    row.role,
                    row.artifact_digest,
                    artifact.media_type,
                    artifact.size_bytes,
                    artifact.local_path,
                )
            )
        return RegistrySnapshot(
            image=image,
            base_digest=_image_base_digest(image),
            base_available=_base_available(uow, _image_base_digest(image)),
            persistent_layout=persistent,
            artifact_rows=tuple(rows),
        )

    def _materialize_layout(
        self,
        snapshot: RegistrySnapshot,
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> RegisteredLayout:
        if snapshot.persistent_layout is not None:
            return RegisteredLayout(
                snapshot.persistent_layout,
                temporary=False,
                source="persisted",
            )
        return self._reconstruct_layout(snapshot, limits, deadline)

    def _reconstruct_layout(
        self,
        snapshot: RegistrySnapshot,
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> RegisteredLayout:
        rows = _unique_registry_rows(snapshot.artifact_rows)
        required_roles = ("manifest", "index", "config")
        for role in required_roles:
            if role not in rows:
                raise _registry_mismatch(f"Registered image is missing the {role} artifact record.")
        if "oci-layout" in rows:
            self._verify_oci_layout_row(rows["oci-layout"], limits, deadline)
        self._verify_index_row(rows["index"], snapshot.image.digest, limits, deadline)
        manifest_row = rows["manifest"]
        manifest_payload, _manifest_bytes = self._verify_manifest_row(
            manifest_row,
            limits,
            deadline,
        )
        config_row = rows["config"]
        config_digest = manifest_payload.config.digest
        if config_row[1] != config_digest:
            raise _registry_mismatch(
                "Registered config digest does not match the manifest descriptor."
            )
        if config_row[1] != snapshot.image.config_digest:
            raise _registry_mismatch(
                "Registered config digest does not match the image registry record."
            )
        if config_row[2] != MEDIA_TYPE_ZANA_CONFIG:
            raise _registry_mismatch("Registered config media type is invalid.")
        config_bytes = self._verify_store_bytes(config_row[1], limits.max_json_bytes)
        if config_row[3] != len(config_bytes):
            raise _registry_mismatch("Registered config size does not match the blob.")
        self._verify_canonical_row_path(config_row)
        self._verify_store_digest(config_row[1])
        try:
            payload = json.loads(config_bytes.decode("utf-8"))
            if type(payload) is not dict:
                raise ValueError("config is not an object")
            config = ZanaImageConfig.model_validate(payload)
        except Exception as error:
            raise _registry_mismatch("The registered image config cannot be parsed.") from error
        blobs: dict[str, Path] = {}
        seen_layer_roles: set[str] = set()
        for descriptor in manifest_payload.layers:
            role = descriptor.annotations.get("org.zana.role")
            if type(role) is not str or role not in ROLE_MEDIA_TYPES:
                raise _registry_mismatch("Manifest layer has an unknown image role.")
            if role in seen_layer_roles:
                raise _registry_mismatch("Manifest layer roles are not unique.")
            seen_layer_roles.add(role)
            if role not in rows:
                raise _registry_mismatch(f"Registered image is missing the {role} artifact record.")
            row = rows[role]
            if row[1] != descriptor.digest:
                raise _registry_mismatch(
                    f"Registered {role} digest does not match the manifest descriptor."
                )
            if row[2] != descriptor.media_type:
                raise _registry_mismatch(
                    f"Registered {role} media type does not match the manifest descriptor."
                )
            if row[3] != descriptor.size:
                raise _registry_mismatch(
                    f"Registered {role} size does not match the manifest descriptor."
                )
            self._verify_canonical_row_path(row)
            self._verify_store_digest(row[1])
            blobs[role] = self._store.blob_path(row[1])
        allowed_extra = {"oci-layout"}
        for role in rows:
            if role in required_roles or role in seen_layer_roles or role in allowed_extra:
                continue
            raise _registry_mismatch(
                f"Registered image carries an unexpected {role} artifact record."
            )
        if not blobs:
            raise _registry_mismatch("Registered image has no canonical artifact layers.")
        root = self._tmp_root / uuid.uuid4().hex
        try:
            secure_mkdir(root, mode=0o700, stage=OperationStage.VALIDATE_LAYOUT)
            assemble_oci_layout(
                config,
                blobs,
                root,
                max_blob_bytes=limits.max_member_bytes,
                max_total_blob_bytes=limits.max_unpacked_bytes,
                chunk_size=limits.chunk_size,
                deadline_seconds=deadline.remaining(),
            )
        except OciValidationError as error:
            _remove_owned_layout(root, self._data_root)
            raise _registry_mismatch(
                "The registered artifact graph cannot be reassembled."
            ) from error
        return RegisteredLayout(root, temporary=True, source="reconstructed")

    def _verify_index_row(
        self,
        row: tuple[str, str, str, int, str],
        image_digest: str,
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> None:
        if row[1] != image_digest:
            raise _registry_mismatch(
                "Registered index digest does not match the image registry record."
            )
        if row[2] != MEDIA_TYPE_OCI_INDEX:
            raise _registry_mismatch("Registered index media type is invalid.")
        data = self._verify_store_bytes(row[1], limits.max_json_bytes)
        if row[3] != len(data):
            raise _registry_mismatch("Registered index size does not match the blob.")
        self._verify_canonical_row_path(row)
        self._verify_store_digest(row[1])
        try:
            payload = json.loads(data.decode("utf-8"))
            if type(payload) is not dict:
                raise ValueError("index is not an object")
            Index.model_validate(payload)
        except Exception as error:
            raise _registry_mismatch("The registered image index cannot be parsed.") from error
        deadline.check(OperationStage.VALIDATE_LAYOUT)

    def _verify_manifest_row(
        self,
        row: tuple[str, str, str, int, str],
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> tuple[Manifest, bytes]:
        if row[2] != MEDIA_TYPE_OCI_MANIFEST:
            raise _registry_mismatch("Registered manifest media type is invalid.")
        data = self._verify_store_bytes(row[1], limits.max_json_bytes)
        if row[3] != len(data):
            raise _registry_mismatch("Registered manifest size does not match the blob.")
        self._verify_canonical_row_path(row)
        self._verify_store_digest(row[1])
        try:
            payload = json.loads(data.decode("utf-8"))
            if type(payload) is not dict:
                raise ValueError("manifest is not an object")
            manifest = Manifest.model_validate(payload)
        except Exception as error:
            raise _registry_mismatch("The registered image manifest cannot be parsed.") from error
        deadline.check(OperationStage.VALIDATE_LAYOUT)
        return manifest, data

    def _verify_oci_layout_row(
        self,
        row: tuple[str, str, str, int, str],
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> None:
        if row[2] != MEDIA_TYPE_OCI_LAYOUT:
            raise _registry_mismatch("Registered oci-layout media type is invalid.")
        data = self._verify_store_bytes(row[1], limits.max_json_bytes)
        canonical = canonical_json_bytes(OciLayoutFile())
        if data != canonical or row[3] != len(canonical):
            raise _registry_mismatch("Registered oci-layout content is not canonical.")
        self._verify_canonical_row_path(row)
        self._verify_store_digest(row[1])
        deadline.check(OperationStage.VALIDATE_LAYOUT)

    def _verify_canonical_row_path(
        self,
        row: tuple[str, str, str, int, str],
    ) -> None:
        canonical = self._store.blob_path(row[1])
        if row[4] != str(canonical):
            raise _registry_mismatch(
                "A registered artifact path conflicts with the artifact store."
            )

    def _verify_store_digest(self, digest: str) -> None:
        try:
            self._store.verify(digest)
        except Exception as error:
            raise _portability_error(
                "IMAGE_MATERIAL_CORRUPTED",
                "A registered image blob failed digest verification.",
                stage=OperationStage.VALIDATE_LAYOUT,
                actions=("reimport_image", "rebuild_image"),
            ) from error

    def _verify_store_bytes(self, digest: str, max_bytes: int) -> bytes:
        try:
            handle = self._store.open(digest)
        except Exception as error:
            raise _registry_mismatch(
                "A registered image metadata blob is not available."
            ) from error
        try:
            data = handle.read(max_bytes + 1)
        finally:
            handle.close()
        if len(data) > max_bytes:
            raise _registry_mismatch("A registered image metadata blob exceeds the size limit.")
        return data

    def _validate_layout_for_image(
        self,
        layout: Path,
        image: Image,
        limits: PortabilityLimits,
        deadline: Deadline,
    ) -> Any:
        try:
            validated = validate_oci_layout(
                layout,
                max_json_bytes=limits.max_json_bytes,
                max_blob_bytes=limits.max_member_bytes,
                max_total_bytes=limits.max_unpacked_bytes,
                chunk_size=limits.chunk_size,
                deadline_seconds=deadline.remaining(),
            )
        except OciValidationError as error:
            raise _portability_error(
                "IMAGE_MATERIAL_CORRUPTED",
                "Registered image material failed OCI digest validation.",
                stage=OperationStage.VALIDATE_LAYOUT,
                actions=("repair_image_material", "reimport_image"),
            ) from error
        if validated.index_digest != image.digest:
            raise _portability_error(
                "REGISTRY_MISMATCH",
                "The layout digest does not match the registered image digest.",
                stage=OperationStage.VALIDATE_LAYOUT,
                actions=("repair_image_registry", "reimport_image"),
            )
        if validated.config_digest != image.config_digest:
            raise _portability_error(
                "REGISTRY_MISMATCH",
                "The layout config digest does not match the registered image.",
                stage=OperationStage.VALIDATE_LAYOUT,
                actions=("repair_image_registry", "reimport_image"),
            )
        exact_base = validated.config.base_model.identity_digest
        if exact_base != _image_base_digest(image):
            raise _portability_error(
                "BASE_MODEL_MISMATCH",
                "The layout base model digest does not match the registry record.",
                stage=OperationStage.VALIDATE_LAYOUT,
                actions=("repair_image_registry",),
            )
        member_names = _layout_member_names(layout, limits, deadline)
        try:
            _images_secrets.ExclusionScanner().scan_member_names(member_names)
            _images_secrets.scan_layout_payloads(
                layout,
                max_json_bytes=limits.max_json_bytes,
                deadline=deadline,
            )
        except _images_secrets.ExclusionError as error:
            raise _portability_error(
                "EXCLUSION_REJECTED",
                "Registered image material would serialize secret content.",
                stage=OperationStage.SECRET_SCAN,
                actions=("repair_image_material",),
            ) from error
        return validated

    def _register_import(
        self,
        import_result: Any,
        limits: PortabilityLimits,
        available_base_digests: set[str],
    ) -> ProductImport:
        if not import_result.layout_root:
            raise _portability_error(
                "LAYOUT_RETENTION_FAILED",
                "A validated import layout could not be retained.",
                stage=OperationStage.REGISTER,
                actions=("retry_import",),
            )
        layout_root = Path(import_result.layout_root)
        roles = _read_layout_roles(layout_root, max_bytes=limits.max_json_bytes)
        for role in roles:
            self._ensure_role_material(role, layout_root, limits)
        plan = import_result.registration
        base_model_available = _base_model_available_from_digest(
            plan.base_model_digest,
            available_base_digests,
        )
        uow = UnitOfWork(self._session_factory)
        try:
            existing = uow.images.get(plan.image_digest)
            idempotent = existing is not None
            if existing is not None and _conflicts_with_image(existing, plan):
                raise _portability_error(
                    "IMPORT_CONFLICT",
                    "The archive conflicts with an already registered image record.",
                    stage=OperationStage.REGISTER,
                    actions=("list_images",),
                )
            if existing is None:
                uow.images.add(
                    Image(
                        digest=plan.image_digest,
                        name=plan.config_name,
                        version=plan.config_version,
                        config_digest=plan.config_digest,
                        verification_status=VerificationStatus.UNVERIFIED,
                        base_model_key=plan.base_model_key,
                        base_model_digest=plan.base_model_digest or "",
                    )
                )
            for role in roles:
                artifact = uow.artifacts.get(role.digest)
                if artifact is None:
                    artifact = Artifact(
                        digest=role.digest,
                        media_type=role.media_type,
                        local_path=str(self._store.blob_path(role.digest)),
                        size_bytes=role.size,
                    )
                    uow.artifacts.add(artifact)
                elif (
                    artifact.local_path != str(self._store.blob_path(role.digest))
                    or artifact.media_type != role.media_type
                ):
                    raise _portability_error(
                        "IMPORT_CONFLICT",
                        "An existing artifact record conflicts with the archive.",
                        stage=OperationStage.REGISTER,
                        actions=("repair_image_registry",),
                    )
                row = uow.session.get(
                    ImageArtifact,
                    (plan.image_digest, role.digest, role.role),
                )
                if row is None:
                    uow.image_artifacts.add(
                        ImageArtifact(
                            image_digest=plan.image_digest,
                            artifact_digest=role.digest,
                            role=role.role,
                        )
                    )
            uow.commit()
        except Exception:
            uow.rollback()
            raise
        finally:
            uow.close()
        return ProductImport(
            result=import_result,
            idempotent=idempotent,
            created=not idempotent,
            base_model_available=base_model_available,
            artifact_count=len(roles),
        )

    def _ensure_role_material(
        self,
        role: LayoutRole,
        layout_root: Path,
        limits: PortabilityLimits,
    ) -> None:
        if role.role in ("index", "manifest"):
            data = _json_bytes(
                layout_root / f"{role.role}.json",
                max_bytes=limits.max_json_bytes,
            )
            actual = self._store.put_bytes(data)
            if actual != role.digest:
                raise _portability_error(
                    "IMPORT_CONFLICT",
                    "Retained layout metadata digest changed during registration.",
                    stage=OperationStage.REGISTER,
                    actions=("retry_import",),
                )
        try:
            self._store.verify(role.digest)
        except Exception as error:
            raise _portability_error(
                "IMAGE_MATERIAL_MISSING",
                "A registered image blob is missing from the artifact store.",
                stage=OperationStage.REGISTER,
                actions=("retry_import",),
            ) from error

    def _write_sidecar_report(
        self,
        destination: Path,
        export_result: Any,
        limits: PortabilityLimits,
        *,
        image_digest: str,
    ) -> tuple[Path, str]:
        """Atomically write a bounded secret-free sidecar export report."""
        from zana_core.portability.models import utc_now
        from zana_core.portability.paths import (
            _open_parent_dirfd,
            fsync_directory,
            sibling_temp_path,
        )

        payload = {
            "report_schema_version": 1,
            "archive_digest": export_result.archive_digest,
            "image_digest": image_digest,
            "codec": export_result.codec.value,
            "stages": [stage.value for stage in export_result.stages],
            "durability_uncertain": export_result.durability_uncertain,
            "created_at": utc_now().isoformat(),
        }
        try:
            data = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        except (TypeError, ValueError) as error:
            raise _portability_error(
                "REPORT_SERIALIZATION_FAILED",
                "The export report could not be serialized.",
                stage=OperationStage.FSYNC,
                actions=("retry_export",),
            ) from error
        if len(data) > limits.max_json_bytes:
            raise _portability_error(
                "REPORT_TOO_LARGE",
                "The export report exceeds the bounded size limit.",
                stage=OperationStage.FSYNC,
                actions=("retry_export",),
            )
        report_path = Path(str(destination) + ".report.json")
        temp = sibling_temp_path(report_path, "report")
        parent_fd, temp_name = _open_parent_dirfd(temp, stage=OperationStage.FSYNC)
        created = False
        try:
            flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
            fd = os.open(temp_name, flags, 0o600, dir_fd=parent_fd)
            created = True
            try:
                _write_fd_full(fd, data)
                os.fsync(fd)
            finally:
                os.close(fd)
            os.rename(temp_name, report_path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            with suppress(Exception):
                fsync_directory(report_path.parent)
        except OSError as error:
            if created:
                with suppress(OSError):
                    os.unlink(temp_name, dir_fd=parent_fd)
            raise _portability_error(
                "REPORT_WRITE_FAILED",
                "The export sidecar report could not be written atomically.",
                stage=OperationStage.FSYNC,
                actions=("retry_export",),
            ) from error
        finally:
            os.close(parent_fd)
        return report_path, digest_bytes(data)


def _canonical_digest(value: str) -> str:
    if type(value) is not str:
        raise _portability_error(
            "INVALID_DIGEST",
            "Image digest must be a string.",
            stage=OperationStage.PREFLIGHT,
            actions=("fix_request_payload",),
        )
    try:
        return validate_digest(value)
    except Exception as error:
        raise _portability_error(
            "INVALID_DIGEST",
            "Image digest is not canonical.",
            stage=OperationStage.PREFLIGHT,
            actions=("fix_request_payload",),
        ) from error


def _fresh_limits(
    limits: PortabilityLimits | None,
    deadline_seconds: float | None,
) -> PortabilityLimits:
    if limits is None:
        active = PortabilityLimits()
    elif type(limits) is PortabilityLimits:
        raw = limits.__dict__
        if type(raw) is not dict:
            raise _portability_error(
                "LIMITS_INVALID",
                "Portability limits are malformed.",
                stage=OperationStage.PREFLIGHT,
                actions=("retry",),
            )
        active = PortabilityLimits.model_validate(raw)
    else:
        raise _portability_error(
            "LIMITS_INVALID",
            "Portability limits must use the canonical model.",
            stage=OperationStage.PREFLIGHT,
            actions=("retry",),
        )
    if deadline_seconds is not None:
        raw = dict(active.__dict__)
        raw["deadline_seconds"] = deadline_seconds
        active = PortabilityLimits.model_validate(raw)
    return active


def _image_or_404(uow: UnitOfWork, digest: str) -> Image:
    image = uow.images.get(digest)
    if image is None:
        raise _portability_error(
            "IMAGE_NOT_FOUND",
            "No image exists with this digest.",
            stage=OperationStage.PREFLIGHT,
            actions=("list_images",),
        )
    return image


def _image_base_digest(image: Image) -> str | None:
    value = image.base_model_digest
    if type(value) is not str or not value:
        return None
    try:
        return validate_digest(value)
    except Exception:
        return None


def _available_base_digests(uow: UnitOfWork) -> set[str]:
    values = uow.session.scalars(
        select(Model.digest).where(Model.digest.is_not(None)).limit(1024)
    ).all()
    collected: set[str] = set()
    for value in values:
        if type(value) is str:
            try:
                collected.add(validate_digest(value))
            except Exception:
                continue
    return collected


def _base_available(uow: UnitOfWork, digest: str | None) -> bool:
    if digest is None:
        return False
    return digest in _available_base_digests(uow)


def _base_model_available_from_digest(
    base_model_digest: str | None,
    available_base_digests: set[str],
) -> bool:
    if base_model_digest is None:
        return False
    if type(available_base_digests) is not set:
        return False
    return base_model_digest in available_base_digests


def _conflicts_with_image(existing: Image, plan: Any) -> bool:
    return (
        existing.config_digest != plan.config_digest
        or existing.name != plan.config_name
        or existing.version != plan.config_version
        or existing.base_model_digest != (plan.base_model_digest or "")
    )


def _read_layout_roles(layout: Path, *, max_bytes: int) -> tuple[LayoutRole, ...]:
    try:
        index_payload = _read_json_bounded(layout / "index.json", max_bytes)
        manifest_payload = _read_json_bounded(layout / "manifest.json", max_bytes)
    except Exception as error:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            "The retained layout metadata cannot be read.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        ) from error
    roles: list[LayoutRole] = []
    manifests = _exact_list(index_payload.get("manifests"), "index manifests")
    if len(manifests) != 1:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            "The retained OCI index must contain exactly one manifest.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    manifest_descriptor = _exact_dict(manifests[0], "index manifest")
    manifest_digest = _exact_digest(manifest_descriptor.get("digest"))
    manifest_size = _exact_int(manifest_descriptor.get("size"), "manifest size")
    roles.append(
        LayoutRole(
            role="manifest",
            digest=manifest_digest,
            media_type=MEDIA_TYPE_OCI_MANIFEST,
            size=manifest_size,
        )
    )
    index_bytes = _json_bytes(layout / "index.json", max_bytes)
    roles.append(
        LayoutRole(
            role="index",
            digest=_canonical_digest(digest_bytes(index_bytes)),
            media_type=MEDIA_TYPE_OCI_INDEX,
            size=len(index_bytes),
        )
    )
    config = _exact_dict(manifest_payload.get("config"), "manifest config")
    config_digest = _exact_digest(config.get("digest"))
    config_size = _exact_int(config.get("size"), "config size")
    roles.append(
        LayoutRole(
            role="config",
            digest=config_digest,
            media_type=MEDIA_TYPE_ZANA_CONFIG,
            size=config_size,
        )
    )
    layers = _exact_list(manifest_payload.get("layers"), "manifest layers")
    for layer in layers:
        descriptor = _exact_dict(layer, "layer descriptor")
        media_type = descriptor.get("mediaType")
        if type(media_type) is not str or not media_type.startswith("application/"):
            raise _portability_error(
                "LAYOUT_METADATA_INVALID",
                "A retained layout layer has an invalid media type.",
                stage=OperationStage.REGISTER,
                actions=("retry_import",),
            )
        annotations = _exact_dict(descriptor.get("annotations"), "layer annotations")
        role = annotations.get("org.zana.role")
        if type(role) is not str or role not in ROLE_MEDIA_TYPES:
            raise _portability_error(
                "LAYOUT_METADATA_INVALID",
                "A retained layout layer has an unknown image role.",
                stage=OperationStage.REGISTER,
                actions=("retry_import",),
            )
        roles.append(
            LayoutRole(
                role=role,
                digest=_exact_digest(descriptor.get("digest")),
                media_type=media_type,
                size=_exact_int(descriptor.get("size"), "layer size"),
            )
        )
    if len(roles) > MAX_LAYOUT_ROLES:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            "Retained layout role count exceeds the hard limit.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    return tuple(roles)


def _json_bytes(path: Path, max_bytes: int) -> bytes:
    fd, _info = open_regular_nofollow(path, stage=OperationStage.REGISTER)
    try:
        data = os.read(fd, max_bytes + 1)
        if len(data) > max_bytes:
            raise PortabilityError(
                "Retained layout JSON exceeds the size limit",
                code="LAYOUT_METADATA_INVALID",
                stage=OperationStage.REGISTER,
                recovery_action=RecoveryAction.RETRY,
            )
        return data
    finally:
        os.close(fd)


def _read_json_bounded(path: Path, max_bytes: int) -> dict[str, Any]:
    data = _json_bytes(path, max_bytes)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as error:
        raise PortabilityError(
            "Retained layout metadata is not valid JSON",
            code="LAYOUT_METADATA_INVALID",
            stage=OperationStage.REGISTER,
            recovery_action=RecoveryAction.RETRY,
        ) from error
    if type(payload) is not dict:
        raise PortabilityError(
            "Retained layout metadata must be an object",
            code="LAYOUT_METADATA_INVALID",
            stage=OperationStage.REGISTER,
            recovery_action=RecoveryAction.RETRY,
        )
    return payload


def _exact_dict(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            f"Retained layout {label} is malformed.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    return value


def _exact_list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            f"Retained layout {label} is malformed.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    return value


def _exact_int(value: Any, label: str) -> int:
    if type(value) is not int or value < 0:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            f"Retained layout {label} is invalid.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    return value


def _exact_digest(value: Any) -> str:
    if type(value) is not str:
        raise _portability_error(
            "LAYOUT_METADATA_INVALID",
            "A retained layout digest is malformed.",
            stage=OperationStage.REGISTER,
            actions=("retry_import",),
        )
    return _canonical_digest(value)


def _safe_relative_path(path: str, data_root: Path) -> str:
    try:
        candidate = Path(path)
        if not candidate.is_absolute():
            return candidate.name
        relative = candidate.relative_to(data_root)
        return "/".join(relative.parts)
    except (ValueError, OSError):
        return Path(path).name


def _remove_owned_layout(path: Path, data_root: Path) -> None:
    try:
        if path.is_symlink() or not path.is_dir():
            return
        remove_tree_confined(path, data_root)
    except Exception:
        return


def _unique_registry_rows(
    rows: tuple[tuple[str, str, str, int, str], ...],
) -> dict[str, tuple[str, str, str, int, str]]:
    collected: dict[str, tuple[str, str, str, int, str]] = {}
    for row in rows:
        role = row[0]
        if role in collected:
            raise _registry_mismatch(f"Registered image has duplicate {role} artifact records.")
        collected[role] = row
    return collected


def _write_fd_full(fd: int, data: bytes) -> None:
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if type(written) is not int or not 1 <= written <= len(view):
            raise OSError("short write while creating export report")
        view = view[written:]


def _registry_mismatch(message: str) -> PortabilityError:
    return _portability_error(
        "REGISTRY_MISMATCH",
        message,
        stage=OperationStage.VALIDATE_LAYOUT,
        actions=("repair_image_registry", "reimport_image"),
    )


def _portability_error(
    code: str,
    message: str,
    *,
    stage: OperationStage,
    actions: Sequence[str],
) -> PortabilityError:
    return PortabilityError(
        message,
        code=code,
        stage=stage,
        recovery_action=actions[0] if actions else RecoveryAction.RETRY,
    )
