"""Focused tests for the product-level portability service."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest
from sqlalchemy import delete

from tests.portability.helpers import (
    archive_file,
    build_layout,
    corrupt_layout_config_with_secret,
    default_config,
    tar_with_members,
)
from zana_core.artifacts import ArtifactStore, digest_bytes
from zana_core.db.database import Database
from zana_core.db.models import Artifact, Image, ImageArtifact, Instance, Model, Runtime
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import (
    InstanceStatus,
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
    VerificationStatus,
)
from zana_core.images.archive import TarCodec
from zana_core.images.models import RunnableState
from zana_core.images.oci import (
    MEDIA_TYPE_OCI_INDEX,
    MEDIA_TYPE_OCI_LAYOUT,
    MEDIA_TYPE_OCI_MANIFEST,
    MEDIA_TYPE_ZANA_BEHAVIOR,
    MEDIA_TYPE_ZANA_CONFIG,
    assemble_oci_layout,
)
from zana_core.portability.boundary import OperationBoundary, OperationCancelledError
from zana_core.portability.models import CodecKind, PortabilityError
from zana_core.portability.service import PortabilityProductService


@pytest.fixture
def environment(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    database = Database(tmp_path / "zana.sqlite3")
    database.upgrade()
    yield data_root, database.session_factory
    database.close()


def make_service(environment) -> PortabilityProductService:
    data_root, session_factory = environment
    return PortabilityProductService(session_factory, data_root)


def register_layout(
    session_factory,
    layouts_root: Path,
    tmp_path: Path,
    *,
    base_model_digest: str | None = None,
    layer_bytes: bytes = b"knowledge",
):
    layout, image_digest = build_layout(
        tmp_path / "lay",
        config=default_config(base_model_digest=base_model_digest),
        layer_bytes=layer_bytes,
    )
    target = layouts_root / image_digest.removeprefix("sha256:")
    shutil.copytree(layout, target)
    manifest = json.loads((layout / "manifest.json").read_text(encoding="utf-8"))
    config_digest = manifest["config"]["digest"]
    with UnitOfWork(session_factory) as uow:
        uow.images.add(
            Image(
                digest=image_digest,
                name="policy-assistant",
                version="1.0.0",
                config_digest=config_digest,
                verification_status=VerificationStatus.VERIFIED_LOCAL,
                base_model_key="ollama:example",
                base_model_digest=base_model_digest or "",
            )
        )
    return image_digest, config_digest, target


def register_artifact_graph(
    session_factory,
    store: ArtifactStore,
    tmp_path: Path,
    *,
    base_model_digest: str | None = None,
    layer_bytes: bytes = b"knowledge",
):
    behavior = tmp_path / "behavior.json"
    behavior.write_bytes(layer_bytes if layer_bytes else b'{"policy":"helpful"}')
    layout = tmp_path / "artifact-layout"
    layout.mkdir()
    config = default_config(base_model_digest=base_model_digest)
    result = assemble_oci_layout(config, {"behavior": behavior}, layout)
    behavior_digest = digest_bytes(behavior.read_bytes())
    config_path = layout / "blobs" / "sha256" / result.config_digest.removeprefix("sha256:")
    store.put_file(config_path)
    store.put_file(behavior)
    manifest_path = layout / "manifest.json"
    index_path = layout / "index.json"
    oci_layout_path = layout / "oci-layout"
    manifest_digest = digest_bytes(manifest_path.read_bytes())
    index_digest = digest_bytes(index_path.read_bytes())
    oci_layout_digest = digest_bytes(oci_layout_path.read_bytes())
    store.put_file(manifest_path)
    store.put_file(index_path)
    store.put_file(oci_layout_path)
    with UnitOfWork(session_factory) as uow:
        uow.images.add(
            Image(
                digest=result.image_digest,
                name=config.name,
                version=config.version,
                config_digest=result.config_digest,
                verification_status=VerificationStatus.VERIFIED_LOCAL,
                base_model_key="ollama:example",
                base_model_digest=base_model_digest or "",
            )
        )
        uow.artifacts.add(
            Artifact(
                digest=behavior_digest,
                media_type=MEDIA_TYPE_ZANA_BEHAVIOR,
                local_path=str(store.blob_path(behavior_digest)),
                size_bytes=behavior.stat().st_size,
            )
        )
        uow.image_artifacts.add(
            ImageArtifact(
                image_digest=result.image_digest,
                artifact_digest=behavior_digest,
                role="behavior",
            )
        )
        for role, digest, media_type, path in (
            ("manifest", manifest_digest, MEDIA_TYPE_OCI_MANIFEST, manifest_path),
            ("index", index_digest, MEDIA_TYPE_OCI_INDEX, index_path),
            ("oci-layout", oci_layout_digest, MEDIA_TYPE_OCI_LAYOUT, oci_layout_path),
            ("config", result.config_digest, MEDIA_TYPE_ZANA_CONFIG, config_path),
        ):
            uow.artifacts.add(
                Artifact(
                    digest=digest,
                    media_type=media_type,
                    local_path=str(store.blob_path(digest)),
                    size_bytes=path.stat().st_size,
                )
            )
            uow.image_artifacts.add(
                ImageArtifact(
                    image_digest=result.image_digest,
                    artifact_digest=digest,
                    role=role,
                )
            )
    return result.image_digest, config


def seed_model(session_factory, digest: str) -> None:
    with UnitOfWork(session_factory) as uow:
        runtime = uow.runtimes.add(
            Runtime(
                kind=RuntimeKind.OLLAMA,
                endpoint="http://127.0.0.1:11434",
                source=RuntimeSource.AUTO,
                status=RuntimeStatus.ONLINE,
            )
        )
        uow.session.flush()
        uow.models.add(
            Model(
                key="ollama:example",
                runtime_id=runtime.id,
                model_id="example",
                digest=digest,
                capabilities_json=["completion"],
                identity_strength=ModelIdentityStrength.EXACT_DIGEST,
            )
        )


def test_verify_reports_exact_base_model_availability(environment, tmp_path: Path) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    base_digest = digest_bytes(b"base weights")
    image_digest, _config_digest, _layout = register_layout(
        session_factory,
        service._layouts_root,
        tmp_path,
        base_model_digest=base_digest,
    )
    seed_model(session_factory, base_digest)

    verified = service.verify(image_digest)
    assert verified.runnable is RunnableState.RUNNABLE
    assert verified.base_model_digest == base_digest
    assert verified.base_model_available is True
    assert verified.layout_source == "persisted"

    with UnitOfWork(session_factory) as uow:
        model = next(iter(uow.models.list()))
        model.digest = None

    unverified = service.verify(image_digest)
    assert unverified.runnable is RunnableState.NOT_RUNNABLE_MISSING_BASE
    assert unverified.base_model_available is False


def test_verify_missing_material_and_corruption_are_actionable(
    environment,
    tmp_path: Path,
) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)
    layout, missing_digest = build_layout(tmp_path / "missing-lay")
    manifest = json.loads((layout / "manifest.json").read_text(encoding="utf-8"))
    with UnitOfWork(session_factory) as uow:
        uow.images.add(
            Image(
                digest=missing_digest,
                name="policy-assistant",
                version="1.0.0",
                config_digest=manifest["config"]["digest"],
                verification_status=VerificationStatus.VERIFIED_LOCAL,
                base_model_key="ollama:example",
                base_model_digest="",
            )
        )

    missing = service.verify(missing_digest)
    assert missing.status == "registry-mismatch"

    corrupted_digest, _config_digest, target = register_layout(
        session_factory,
        service._layouts_root,
        tmp_path / "corrupt-lay",
    )
    blob = next((target / "blobs" / "sha256").iterdir())
    blob.write_bytes(blob.read_bytes() + b"corrupt")
    corrupted = service.verify(corrupted_digest)
    assert corrupted.status == "corrupted"


def test_export_round_trip_preserves_digest(environment, tmp_path: Path) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    image_digest, _config_digest, layout = register_layout(
        session_factory,
        service._layouts_root,
        tmp_path,
    )
    destination = service.exports_root / "policy-assistant.tar"
    result = service.export(
        image_digest,
        output_path=str(destination),
        codec=CodecKind.TAR,
        replace_token=None,
        replace_allowed=False,
        user_approved=True,
    )
    expected = TarCodec().pack(layout, tmp_path / "canonical.tar")
    assert result.result.archive_digest == expected
    assert destination.exists()
    assert result.relative_path == "portability/exports/policy-assistant.tar"
    assert result.report_relative_path == "portability/exports/policy-assistant.tar.report.json"
    report = Path(destination.parent, f"{destination.name}.report.json")
    assert report.is_file()
    report_payload = json.loads(report.read_text(encoding="utf-8"))
    assert report_payload["archive_digest"] == result.result.archive_digest
    assert report_payload["image_digest"] == image_digest
    assert "token" not in json.dumps(report_payload)
    assert result.report_digest == digest_bytes(report.read_bytes())
    assert not list((data_root / "portability" / "tmp").iterdir())


def test_export_requires_approval_and_confined_path(environment, tmp_path: Path) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)
    image_digest, _config_digest, _layout = register_layout(
        session_factory,
        service._layouts_root,
        tmp_path,
    )
    with pytest.raises(PortabilityError) as approval:
        service.export(
            image_digest,
            output_path=str(service.exports_root / "x.tar"),
            codec=CodecKind.TAR,
            replace_token=None,
            replace_allowed=False,
            user_approved=False,
        )
    assert approval.value.code == "APPROVAL_REQUIRED"
    with pytest.raises(PortabilityError) as confined:
        service.export(
            image_digest,
            output_path=str(tmp_path / "outside.tar"),
            codec=CodecKind.TAR,
            replace_token=None,
            replace_allowed=False,
            user_approved=True,
        )
    assert confined.value.code == "PATH_NOT_APPROVED"


def test_import_round_trip_registers_graph_and_is_idempotent(
    environment,
    tmp_path: Path,
) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    base_digest = digest_bytes(b"base weights")
    layout, image_digest = build_layout(
        tmp_path / "lay",
        config=default_config(base_model_digest=base_digest),
        layer_bytes=b"knowledge",
    )
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    seed_model(session_factory, base_digest)

    first = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
    )
    assert first.created is True
    assert first.idempotent is False
    assert first.result.registration.image_digest == image_digest
    assert first.result.registration.runnable is RunnableState.RUNNABLE
    assert first.result.registration.base_model_digest == base_digest
    assert first.base_model_available is True
    assert first.artifact_count >= 4
    assert first.result.layout_root is not None
    retained = Path(first.result.layout_root)
    assert retained.is_dir()

    with UnitOfWork(session_factory) as uow:
        assert uow.images.get(image_digest) is not None
        assert len(uow.image_artifacts.list_for_image(image_digest)) >= 4

    second = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
    )
    assert second.idempotent is True
    assert second.created is False
    assert second.base_model_available is True
    with UnitOfWork(session_factory) as uow:
        assert len(uow.images.list()) == 1
        assert len(uow.image_artifacts.list_for_image(image_digest)) >= 4

    exported = service.export(
        image_digest,
        output_path=str(service.exports_root / "roundtrip.tar"),
        codec=CodecKind.TAR,
        replace_token=None,
        replace_allowed=False,
        user_approved=True,
    )
    assert exported.result.archive_digest == digest_bytes(archive.read_bytes())
    assert exported.report_digest.startswith("sha256:")


def test_import_rejects_corrupt_traversal_and_secret_archives(
    environment,
    tmp_path: Path,
) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)

    corrupt_layout, _digest = build_layout(tmp_path / "corrupt-lay", layer_bytes=b"layer")
    blob = next((corrupt_layout / "blobs" / "sha256").iterdir())
    blob.write_bytes(blob.read_bytes() + b"x")
    corrupt_archive = archive_file(tmp_path / "corrupt.tar", corrupt_layout)
    corrupt_source = service.imports_root / "corrupt.tar"
    shutil.copy2(corrupt_archive, corrupt_source)
    with pytest.raises(PortabilityError) as corrupt:
        service.import_archive(
            local_path=str(corrupt_source),
            codec=CodecKind.TAR,
            user_approved=True,
        )
    assert corrupt.value.code == "OCI_VALIDATION_FAILED"

    traversal = service.imports_root / "traversal.tar"
    traversal.write_bytes(tar_with_members([("oci-layout", b"{}"), ("../escape", b"x")]))
    with pytest.raises(PortabilityError) as unsafe:
        service.import_archive(
            local_path=str(traversal),
            codec=CodecKind.TAR,
            user_approved=True,
        )
    assert unsafe.value.code == "ARCHIVE_EXTRACTION_FAILED"

    secret_layout, _digest = build_layout(tmp_path / "secret-lay")
    corrupt_layout_config_with_secret(secret_layout)
    secret_archive = archive_file(tmp_path / "secret.tar", secret_layout)
    secret_source = service.imports_root / "secret.tar"
    shutil.copy2(secret_archive, secret_source)
    with pytest.raises(PortabilityError) as secret:
        service.import_archive(
            local_path=str(secret_source),
            codec=CodecKind.TAR,
            user_approved=True,
        )
    assert secret.value.code == "OCI_VALIDATION_FAILED"

    with UnitOfWork(session_factory) as uow:
        assert uow.images.list() == []
        assert uow.artifacts.list() == []
    assert not list((service._data_root / "portability" / "workspaces").iterdir())


def test_import_conflicting_registry_record_fails_closed(
    environment,
    tmp_path: Path,
) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)
    layout, image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    first = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
    )
    assert first.created is True
    with UnitOfWork(session_factory) as uow:
        image = uow.images.get(image_digest)
        assert image is not None
        image.config_digest = "sha256:" + "a" * 64
    with pytest.raises(PortabilityError) as conflict:
        service.import_archive(
            local_path=str(source),
            codec=CodecKind.TAR,
            user_approved=True,
        )
    assert conflict.value.code == "IMPORT_CONFLICT"


def test_delete_respects_reference_rules_and_retains_blobs(
    environment,
    tmp_path: Path,
) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    layout, image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    imported = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
    )
    store = ArtifactStore(data_root / "artifacts")
    blob_count = len(list((store.root / "blobs" / "sha256").iterdir()))

    with pytest.raises(PortabilityError) as unconfirmed:
        service.delete(image_digest, confirmed=False)
    assert unconfirmed.value.code == "DELETE_CONFIRMATION_REQUIRED"

    with UnitOfWork(session_factory) as uow:
        uow.instances.add(
            Instance(
                name="instance-1",
                image_digest=image_digest,
                status=InstanceStatus.STOPPED,
                state_schema_version=1,
            )
        )
    with pytest.raises(PortabilityError) as in_use:
        service.delete(image_digest, confirmed=True)
    assert in_use.value.code == "IMAGE_IN_USE"

    with UnitOfWork(session_factory) as uow:
        for instance in uow.instances.list():
            uow.instances.delete(instance)
    deleted = service.delete(image_digest, confirmed=True)
    assert deleted.deleted is True
    assert deleted.artifacts_retained is True
    with UnitOfWork(session_factory) as uow:
        assert uow.images.get(image_digest) is None
        assert uow.image_artifacts.list_for_image(image_digest) == []
    assert len(list((store.root / "blobs" / "sha256").iterdir())) == blob_count
    assert Path(imported.result.layout_root).is_dir()

    with pytest.raises(PortabilityError) as missing:
        service.delete(image_digest, confirmed=True)
    assert missing.value.code == "IMAGE_NOT_FOUND"


def test_reconstructed_artifact_graph_exports_exact_archive(
    environment,
    tmp_path: Path,
) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    store = ArtifactStore(data_root / "artifacts")
    image_digest, _config = register_artifact_graph(
        session_factory,
        store,
        tmp_path,
    )
    verified = service.verify(image_digest)
    assert verified.layout_source == "reconstructed"
    exported = service.export(
        image_digest,
        output_path=str(service.exports_root / "reconstructed.tar"),
        codec=CodecKind.TAR,
        replace_token=None,
        replace_allowed=False,
        user_approved=True,
    )
    assert exported.result.archive_digest.startswith("sha256:")
    assert exported.result.archive_digest == digest_bytes(
        (service.exports_root / "reconstructed.tar").read_bytes()
    )
    assert not list((data_root / "portability" / "tmp").iterdir())


def test_reconstruction_rejects_duplicate_role(environment, tmp_path: Path) -> None:
    from zana_core.portability.service import _unique_registry_rows

    row = ("behavior", "sha256:" + "a" * 64, "application/x", 1, "/tmp/blob")
    with pytest.raises(PortabilityError) as exc:
        _unique_registry_rows((row, row))
    assert exc.value.code == "REGISTRY_MISMATCH"


def test_reconstruction_rejects_role_media_size_path_and_extra_roles(
    environment,
    tmp_path: Path,
) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    store = ArtifactStore(data_root / "artifacts")
    image_digest, _config = register_artifact_graph(
        session_factory,
        store,
        tmp_path,
    )
    cases = [
        ("media", lambda artifact: setattr(artifact, "media_type", "application/wrong")),
        ("size", lambda artifact: setattr(artifact, "size_bytes", artifact.size_bytes + 1)),
        ("path", lambda artifact: setattr(artifact, "local_path", "/tmp/foreign")),
    ]
    for label, mutate in cases:
        with UnitOfWork(session_factory) as uow:
            behavior = next(
                row
                for row in uow.image_artifacts.list_for_image(image_digest)
                if row.role == "behavior"
            )
            artifact = uow.artifacts.get(behavior.artifact_digest)
            assert artifact is not None
            mutate(artifact)
        verified = service.verify(image_digest)
        assert verified.status == "registry-mismatch", label
        with UnitOfWork(session_factory) as uow:
            behavior = next(
                row
                for row in uow.image_artifacts.list_for_image(image_digest)
                if row.role == "behavior"
            )
            artifact = uow.artifacts.get(behavior.artifact_digest)
            assert artifact is not None
            artifact.media_type = MEDIA_TYPE_ZANA_BEHAVIOR
            artifact.size_bytes = store.size(behavior.artifact_digest)
            artifact.local_path = str(store.blob_path(behavior.artifact_digest))

    with UnitOfWork(session_factory) as uow:
        behavior = next(
            row
            for row in uow.image_artifacts.list_for_image(image_digest)
            if row.role == "behavior"
        )
        behavior_digest = behavior.artifact_digest
        uow.session.execute(
            delete(ImageArtifact).where(
                ImageArtifact.image_digest == image_digest,
                ImageArtifact.role == "behavior",
            )
        )
    missing = service.verify(image_digest)
    assert missing.status == "registry-mismatch"

    with UnitOfWork(session_factory) as uow:
        uow.image_artifacts.add(
            ImageArtifact(
                image_digest=image_digest,
                artifact_digest=behavior_digest,
                role="behavior",
            )
        )
        extra_digest = store.put_bytes(b"unexpected-extra")
        uow.artifacts.add(
            Artifact(
                digest=extra_digest,
                media_type="application/extra",
                local_path=str(store.blob_path(extra_digest)),
                size_bytes=1,
            )
        )
        uow.image_artifacts.add(
            ImageArtifact(
                image_digest=image_digest,
                artifact_digest=extra_digest,
                role="unexpected",
            )
        )
    extra = service.verify(image_digest)
    assert extra.status == "registry-mismatch"


def test_import_base_availability_is_explicit_not_guessed(
    environment,
    tmp_path: Path,
) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)
    base_digest = digest_bytes(b"base weights")
    layout, _image_digest = build_layout(
        tmp_path / "lay",
        config=default_config(base_model_digest=base_digest),
        layer_bytes=b"knowledge",
    )
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    seed_model(session_factory, base_digest)
    imported = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
    )
    assert imported.base_model_available is True
    assert imported.result.registration.runnable is RunnableState.RUNNABLE

    from zana_core.portability.service import _base_model_available_from_digest

    assert _base_model_available_from_digest(base_digest, {base_digest}) is True
    assert _base_model_available_from_digest(None, {base_digest}) is False
    assert _base_model_available_from_digest(base_digest, set()) is False


def test_cancellation_and_progress_boundary_fail_closed(
    environment,
    tmp_path: Path,
) -> None:
    data_root, session_factory = environment
    service = make_service(environment)
    layout, image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    export_digest, _config_digest, _layout = register_layout(
        session_factory,
        service._layouts_root,
        tmp_path,
        layer_bytes=b"knowledge",
    )

    stages: list[str] = []
    cancelled = OperationBoundary(
        cancel=lambda: True,
        progress=lambda stage, _fraction: stages.append(stage),
    )
    with pytest.raises(OperationCancelledError) as exc:
        service.import_archive(
            local_path=str(source),
            codec=CodecKind.TAR,
            user_approved=True,
            boundary=cancelled,
        )
    assert exc.value.code == "CANCELLED"
    assert stages == []
    workspaces = data_root / "portability" / "workspaces"
    if workspaces.exists():
        assert not list(workspaces.iterdir())

    counts = {"checks": 0}

    def cancel_after_two() -> bool:
        counts["checks"] += 1
        return counts["checks"] >= 3

    progress: list[str] = []
    boundary = OperationBoundary(
        cancel=cancel_after_two,
        progress=lambda stage, _fraction: progress.append(stage),
    )
    with pytest.raises(OperationCancelledError):
        service.export(
            export_digest,
            output_path=str(service.exports_root / "cancel.tar"),
            codec=CodecKind.TAR,
            replace_token=None,
            replace_allowed=False,
            user_approved=True,
            boundary=boundary,
        )
    assert "CANCELLED" in progress or "preflight" in progress
    assert not (service.exports_root / "cancel.tar").exists()

    verify_stages: list[str] = []
    verify_boundary = OperationBoundary(
        cancel=lambda: True,
        progress=lambda stage, _fraction: verify_stages.append(stage),
    )
    with pytest.raises(OperationCancelledError):
        service.verify(export_digest, boundary=verify_boundary)
    assert verify_stages == []


def test_progress_boundary_records_only_real_stages(environment, tmp_path: Path) -> None:
    _data_root, session_factory = environment
    service = make_service(environment)
    layout, _image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    archive = archive_file(tmp_path / "image.tar", layout)
    source = service.imports_root / "image.tar"
    shutil.copy2(archive, source)
    stages: list[str] = []
    boundary = OperationBoundary(progress=lambda stage, _fraction: stages.append(stage))
    imported = service.import_archive(
        local_path=str(source),
        codec=CodecKind.TAR,
        user_approved=True,
        boundary=boundary,
    )
    assert imported.created is True
    assert stages == ["preflight", "unpack", "oci_validation", "complete"]
