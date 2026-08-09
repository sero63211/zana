"""Tests for safe archive import over canonical validation/registration."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.portability.helpers import (
    archive_file,
    build_layout,
    corrupt_layout_config_with_secret,
    default_config,
    tar_with_members,
)
from zana_core.artifacts import ArtifactStore, digest_bytes
from zana_core.images import archive as images_archive
from zana_core.images import import_plan as images_import_plan
from zana_core.images.archive import GzipTarCodec, TarCodec
from zana_core.images.models import RunnableState
from zana_core.portability.import_ import ImportService
from zana_core.portability.models import (
    CodecKind,
    Deadline,
    ImportRequest,
    PortabilityError,
    PortabilityLimits,
)


class FailingUnpackCodec(TarCodec):
    """Canonical codec subclass that fails during unpack."""

    def _read_archive(self, archive_path: Path, destination: Path, **kwargs) -> int:
        (destination / "partial.bin").write_bytes(b"partial")
        raise OSError("simulated unpack failure")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, ArtifactStore]:
    approved = tmp_path / "approved"
    approved.mkdir()
    data = approved / "data"
    store = ArtifactStore(tmp_path / "store")
    return approved, data, store


def make_service(roots, **kwargs) -> ImportService:
    approved, data, store = roots
    return ImportService(store, [approved], data, **kwargs)


def test_successful_import_registers_blobs(roots) -> None:
    approved, data, store = roots
    base_digest = digest_bytes(b"base weights")
    layout, manifest_digest = build_layout(
        approved / "lay",
        config=default_config(base_model_digest=base_digest),
        layer_bytes=b"knowledge",
    )
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    result = service.import_archive(ImportRequest(operation_id="op-import-1", source=str(source)))
    assert result.registration.image_digest == manifest_digest
    assert result.registration.blobs
    for blob in result.registration.blobs:
        assert store.exists(blob.digest)
        assert store.verify(blob.digest) == blob.size_bytes
    assert result.registration.runnable is RunnableState.NOT_RUNNABLE_MISSING_BASE
    assert result.registration.base_model_digest == base_digest
    assert result.archive_digest == digest_bytes(source.read_bytes())
    assert result.cleanup.workspace_removed is True
    assert not any((data / "portability" / "workspaces").iterdir())
    assert source.exists()


def test_import_uses_canonical_registration(roots, monkeypatch) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    source = archive_file(approved / "image.tar", layout)
    calls: list[Path] = []
    real = images_import_plan.register_into_store

    def recording(store, layout_root, **kwargs):
        calls.append(Path(layout_root))
        return real(store, layout_root, **kwargs)

    monkeypatch.setattr(images_import_plan, "register_into_store", recording)
    service = make_service(roots)
    service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert len(calls) == 1
    assert calls[0].is_relative_to(data / "portability" / "workspaces")


def test_reimport_is_idempotent(roots) -> None:
    approved, data, store = roots
    layout, manifest_digest = build_layout(approved / "lay", layer_bytes=b"knowledge")
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    first = service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    second = service.import_archive(ImportRequest(operation_id="op-2", source=str(source)))
    assert first.registration.image_digest == second.registration.image_digest == manifest_digest
    assert [blob.digest for blob in first.registration.blobs] == [
        blob.digest for blob in second.registration.blobs
    ]
    assert all(blob.already_present for blob in second.registration.blobs)
    blob_files = list((store.root / "blobs" / "sha256").iterdir())
    assert len(blob_files) == len(first.registration.blobs)


def test_gzip_import_round_trip(roots) -> None:
    approved, data, store = roots
    layout, manifest_digest = build_layout(approved / "lay")
    source = approved / "image.tar.gz"
    GzipTarCodec().pack(layout, source)
    service = make_service(roots)
    result = service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert result.registration.image_digest == manifest_digest
    assert result.codec is CodecKind.TAR_GZ


def test_source_symlink_rejected(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    real = archive_file(approved / "real.tar", layout)
    link = approved / "link.tar"
    link.symlink_to(real)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(link)))
    assert exc.value.code == "SYMLINK_NOT_ALLOWED"
    assert real.exists()


def test_source_outside_approved_root_rejected(roots, tmp_path: Path) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    outside = tmp_path / "outside"
    outside.mkdir()
    source = archive_file(outside / "image.tar", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "PATH_NOT_APPROVED"


def test_archive_size_limit_rejected(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay", layer_bytes=b"x" * 2048)
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(
            ImportRequest(
                operation_id="op-1",
                source=str(source),
                limits=PortabilityLimits(
                    max_archive_bytes=512,
                    max_unpacked_bytes=256,
                    max_member_bytes=128,
                    max_json_bytes=128,
                    chunk_size=64,
                ),
            )
        )
    assert exc.value.code == "ARCHIVE_SIZE_LIMIT_EXCEEDED"


def test_codec_extension_mismatch_rejected(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(
            ImportRequest(
                operation_id="op-1",
                source=str(source),
                codec=CodecKind.TAR_GZ,
            )
        )
    assert exc.value.code == "CODEC_EXTENSION_MISMATCH"


@pytest.mark.skipif(
    images_archive.zstd_available(),
    reason="honest zstd unavailable assertion requires no zstandard",
)
def test_zstd_import_fails_closed_when_unavailable(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    source = archive_file(approved / "image.tar.zst", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "CODEC_UNAVAILABLE"


def test_corrupt_blob_import_rejected(roots) -> None:
    approved, data, store = roots
    layout, _digest = build_layout(approved / "lay", layer_bytes=b"layer")
    blob = next((layout / "blobs" / "sha256").iterdir())
    blob.write_bytes(blob.read_bytes() + b"x")
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "OCI_VALIDATION_FAILED"
    assert not any((store.root / "blobs" / "sha256").iterdir())
    assert not any((data / "portability" / "workspaces").iterdir())
    assert source.exists()


def test_traversal_archive_import_rejected(roots) -> None:
    approved, data, _store = roots
    data_bytes = tar_with_members([("oci-layout", b"{}"), ("../escape.txt", b"x")])
    source = approved / "image.tar"
    source.write_bytes(data_bytes)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "ARCHIVE_EXTRACTION_FAILED"
    assert "Traversal" in exc.value.message or "traversal" in exc.value.message
    assert not any((data / "portability" / "workspaces").iterdir())


def test_mutable_state_member_import_rejected(roots) -> None:
    approved, data, _store = roots
    data_bytes = tar_with_members(
        [("oci-layout", b"{}"), ("instances/inst-1/memories.json", b"{}")]
    )
    source = approved / "image.tar"
    source.write_bytes(data_bytes)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "ARCHIVE_EXTRACTION_FAILED"
    assert not any((data / "portability" / "workspaces").iterdir())


def test_secret_value_config_import_rejected(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    corrupt_layout_config_with_secret(layout)
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert exc.value.code == "OCI_VALIDATION_FAILED"
    assert "secret" in exc.value.message


def test_missing_base_model_is_not_runnable(roots) -> None:
    approved, data, _store = roots
    base_digest = digest_bytes(b"base weights")
    layout, _digest = build_layout(
        approved / "lay", config=default_config(base_model_digest=base_digest)
    )
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    result = service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert result.registration.runnable is RunnableState.NOT_RUNNABLE_MISSING_BASE
    assert result.registration.base_model_digest == base_digest
    assert result.registration.db_intent.runnable is RunnableState.NOT_RUNNABLE_MISSING_BASE
    assert result.registration.db_intent.table == "images"


def test_base_model_available_is_runnable(roots) -> None:
    approved, data, _store = roots
    base_digest = digest_bytes(b"base weights")
    layout, _digest = build_layout(
        approved / "lay", config=default_config(base_model_digest=base_digest)
    )
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots, base_available=lambda digest: digest == base_digest)
    result = service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert result.registration.runnable is RunnableState.RUNNABLE


def test_injected_unpack_failure_cleans_up_and_preserves_source(roots) -> None:
    approved, data, store = roots
    layout, _digest = build_layout(approved / "lay")
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots, codec_factory=lambda kind: FailingUnpackCodec())
    with pytest.raises(OSError):
        service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    assert source.exists()
    workspaces = data / "portability" / "workspaces"
    assert not any(workspaces.iterdir())
    assert not any((store.root / "blobs" / "sha256").iterdir())


def test_guard_file_removed_after_import(roots) -> None:
    approved, data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    source = archive_file(approved / "image.tar", layout)
    service = make_service(roots)
    service.import_archive(ImportRequest(operation_id="op-1", source=str(source)))
    locks = data / "portability" / "locks"
    assert list(locks.iterdir()) == []


def test_unpacked_member_collection_respects_cap(roots) -> None:
    approved, data, _store = roots
    root = data / "unpacked"
    root.mkdir(parents=True)
    for index in range(5):
        (root / f"f{index}.txt").write_bytes(b"x")
    from zana_core.portability.import_ import _relative_files

    deadline = Deadline(30.0)
    with pytest.raises(PortabilityError) as exc:
        _relative_files(root, PortabilityLimits(max_members=3), deadline)
    assert exc.value.code == "MEMBER_LIMIT_EXCEEDED"
