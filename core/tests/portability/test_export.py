"""Tests for atomic export service behavior over canonical primitives."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from tests.portability.helpers import build_layout, corrupt_layout_config_with_secret
from zana_core.artifacts import ArtifactStore, digest_bytes
from zana_core.images import archive as images_archive
from zana_core.images.archive import ArchiveFormat, TarCodec
from zana_core.images.oci import validate_oci_layout
from zana_core.portability.export import ExportService
from zana_core.portability.models import (
    ABS_MAX_UNPACKED_BYTES,
    BlobRegistration,
    CleanupEvidence,
    CodecKind,
    Deadline,
    ExportRequest,
    ExportResult,
    OperationStage,
    PortabilityError,
    PortabilityLimits,
    PreconditionError,
    RegistrationDbIntent,
    RegistrationPlan,
    RunnableState,
)


class FailingCodec(TarCodec):
    """Canonical codec subclass that fails mid-write."""

    def _write_archive(self, layout_root: Path, archive_path: Path, **kwargs) -> str:
        archive_path.write_bytes(b"partial")
        raise OSError("simulated write failure")


@pytest.fixture
def roots(tmp_path: Path) -> tuple[Path, Path, ArtifactStore]:
    approved = tmp_path / "approved"
    approved.mkdir()
    data = approved / "data"
    store = ArtifactStore(tmp_path / "store")
    return approved, data, store


def make_service(roots, **kwargs) -> ExportService:
    approved, data, _store = roots
    return ExportService([approved], data, **kwargs)


def test_successful_export_round_trip(roots) -> None:
    approved, _data, _store = roots
    layout, _image_digest = build_layout(approved / "lay", layer_bytes=b"knowledge")
    service = make_service(roots)
    destination = approved / "policy-assistant_1.0.0_oci.tar"
    result = service.export(
        ExportRequest(
            operation_id="op-export-1",
            layout_path=str(layout),
            destination=str(destination),
            codec=CodecKind.TAR,
        )
    )
    assert destination.is_file()
    assert result.archive_digest == digest_bytes(destination.read_bytes())
    assert result.layout_digest == validate_oci_layout(layout).manifest_digest
    assert result.codec is CodecKind.TAR
    assert result.preflight.sufficient is True
    assert OperationStage.ATOMIC_REPLACE in result.stages


def test_identical_layout_yields_identical_archive_digest(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay", layer_bytes=b"knowledge")
    service = make_service(roots)
    first = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(approved / "a.tar"),
            codec=CodecKind.TAR,
        )
    )
    second = service.export(
        ExportRequest(
            operation_id="op-2",
            layout_path=str(layout),
            destination=str(approved / "b.tar"),
            codec=CodecKind.TAR,
        )
    )
    assert first.archive_digest == second.archive_digest
    assert (approved / "a.tar").read_bytes() == (approved / "b.tar").read_bytes()


def test_gzip_export_is_deterministic(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    service = make_service(roots)
    first = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(approved / "a.tar.gz"),
            codec=CodecKind.TAR_GZ,
        )
    )
    second = service.export(
        ExportRequest(
            operation_id="op-2",
            layout_path=str(layout),
            destination=str(approved / "b.tar.gz"),
            codec=CodecKind.TAR_GZ,
        )
    )
    assert first.archive_digest == second.archive_digest


def test_export_uses_canonical_codec(roots, monkeypatch) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    calls: list[ArchiveFormat] = []
    real = images_archive.codec_for_format

    def recording(format_name: ArchiveFormat):
        calls.append(format_name)
        return real(format_name)

    monkeypatch.setattr(images_archive, "codec_for_format", recording)
    service = make_service(roots)
    result = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(approved / "image.tar"),
            codec=CodecKind.TAR,
        )
    )
    assert calls and all(call is ArchiveFormat.TAR for call in calls)
    canonical = TarCodec().pack(layout, approved / "canonical.tar")
    assert result.archive_digest == canonical


def test_export_digest_matches_canonical_tar_codec(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    service = make_service(roots)
    destination = approved / "image.tar"
    result = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(destination),
            codec=CodecKind.TAR,
        )
    )
    assert result.archive_digest == TarCodec().pack(layout, approved / "direct.tar")


def test_existing_destination_requires_matching_replace_token(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    destination = approved / "image.tar"
    destination.write_bytes(b"old bytes")
    service = make_service(roots)
    with pytest.raises(PreconditionError):
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(destination),
                codec=CodecKind.TAR,
            )
        )
    assert destination.read_bytes() == b"old bytes"


def test_stale_replace_token_fails_closed(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    destination = approved / "image.tar"
    destination.write_bytes(b"current")
    service = make_service(roots)
    with pytest.raises(PreconditionError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(destination),
                codec=CodecKind.TAR,
                replace_allowed=True,
                replace_token="sha256:" + "0" * 64,
            )
        )
    assert exc.value.code == "STALE_REPLACE_TOKEN"
    assert destination.read_bytes() == b"current"


def test_safe_replace_with_matching_token(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    destination = approved / "image.tar"
    original = b"old bytes"
    destination.write_bytes(original)
    service = make_service(roots)
    result = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(destination),
            codec=CodecKind.TAR,
            replace_allowed=True,
            replace_token=digest_bytes(original),
        )
    )
    assert result.archive_digest == digest_bytes(destination.read_bytes())


def test_directory_destination_rejected(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    target_dir = approved / "target-dir"
    target_dir.mkdir()
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(target_dir),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "DESTINATION_IS_DIRECTORY"


def test_symlink_destination_rejected(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    target = approved / "real.tar"
    target.write_bytes(b"old")
    link = approved / "link.tar"
    link.symlink_to(target)
    service = make_service(roots)
    with pytest.raises(PortabilityError):
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(link),
                codec=CodecKind.TAR,
            )
        )
    assert target.read_bytes() == b"old"


def test_extension_codec_mismatch_rejected(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar.gz"),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "CODEC_EXTENSION_MISMATCH"


@pytest.mark.skipif(
    images_archive.zstd_available(),
    reason="honest zstd unavailable assertion requires no zstandard",
)
def test_zstd_export_fails_closed_when_unavailable(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar.zst"),
                codec=CodecKind.TAR_ZSTD,
            )
        )
    assert exc.value.code == "CODEC_UNAVAILABLE"
    assert not (approved / "image.tar.zst").exists()


def test_disk_shortage_fails_before_write(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    service = make_service(roots, disk_usage=lambda path: (1000, 900, 10))
    destination = approved / "image.tar"
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(destination),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "DISK_INSUFFICIENT"
    assert not destination.exists()
    assert not list(approved.glob(".image.tar.*.tmp"))


def test_injected_write_failure_preserves_destination(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    destination = approved / "image.tar"
    destination.write_bytes(b"original")
    service = make_service(roots, codec_factory=lambda kind: FailingCodec())
    with pytest.raises(OSError):
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(destination),
                codec=CodecKind.TAR,
                replace_allowed=True,
                replace_token=digest_bytes(b"original"),
            )
        )
    assert destination.read_bytes() == b"original"
    assert not list(approved.glob(".image.tar.*.tmp"))


def test_secret_value_in_layout_rejected_before_write(roots, monkeypatch) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    corrupt_layout_config_with_secret(layout)
    service = make_service(roots)
    destination = approved / "image.tar"
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(destination),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "OCI_VALIDATION_FAILED"
    assert "secret" in exc.value.message
    assert not destination.exists()


def test_layout_outside_approved_root_rejected(roots, tmp_path: Path) -> None:
    approved, _data, _store = roots
    outside = tmp_path / "outside"
    layout, _digest = build_layout(outside)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar"),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "PATH_NOT_APPROVED"


def test_small_limits_fail_closed_on_export(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay", layer_bytes=b"x" * 4096)
    service = make_service(roots)
    with pytest.raises(PortabilityError):
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar"),
                codec=CodecKind.TAR,
                limits=PortabilityLimits(max_json_bytes=128),
            )
        )


def test_layout_member_name_collection_respects_cap(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    blob_dir = layout / "blobs" / "sha256"
    for index in range(5):
        (blob_dir / f"{index:064x}").write_bytes(b"x")
    from zana_core.portability.export import _layout_member_names

    deadline = Deadline(30.0)
    with pytest.raises(PortabilityError) as exc:
        _layout_member_names(layout, PortabilityLimits(max_members=3), deadline)
    assert exc.value.code == "MEMBER_LIMIT_EXCEEDED"


def test_cumulative_deadline_fails_across_phases(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    calls = {"count": 0}

    def clock() -> float:
        calls["count"] += 1
        return 0.0 if calls["count"] < 4 else 10.0

    service = ExportService([approved], approved / "data", clock=clock)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar"),
                codec=CodecKind.TAR,
                limits=PortabilityLimits(deadline_seconds=0.5),
            )
        )
    assert exc.value.code == "DEADLINE_EXCEEDED"
    assert not (approved / "image.tar").exists()


def test_slow_deadline_digest_raises(roots) -> None:
    from zana_core.portability.models import Deadline as RealDeadline
    from zana_core.portability.paths import deadline_digest

    approved, _data, _store = roots
    target = approved / "blob.bin"
    target.write_bytes(b"x" * 4096)

    from zana_core.portability.models import LimitExceededError

    deadline = RealDeadline(30.0)
    deadline._start = deadline._start - 1000.0
    try:
        deadline_digest(
            target,
            chunk_size=64,
            max_bytes=4096,
            deadline=deadline,
            stage=OperationStage.FSYNC,
        )
    except Exception as error:
        assert isinstance(error, PortabilityError | LimitExceededError)
        assert getattr(error, "code", "") == "DEADLINE_EXCEEDED"
    else:
        raise AssertionError("deadline_digest did not raise")


def test_seventeen_approved_roots_rejected_without_mutation(roots) -> None:
    approved, _data, _store = roots
    data_root = approved / "must-not-exist"
    many_roots = [approved / f"root-{index}" for index in range(17)]
    with pytest.raises(PortabilityError) as exc:
        ExportService(many_roots, data_root)
    assert exc.value.code == "ROOTS_TOO_MANY"
    assert not data_root.exists()


def test_unsafe_approved_root_rejects_with_zero_mutation(roots) -> None:
    approved, _data, _store = roots
    data_root = approved / "must-not-exist"
    with pytest.raises(PortabilityError) as exc:
        ExportService([Path("/")], data_root)
    assert exc.value.code in {
        "ROOT_IS_FILESYSTEM_ROOT",
        "ROOT_TOO_BROAD",
    }
    assert not data_root.exists()
    with pytest.raises(PortabilityError):
        ExportService([Path("~/expanded")], data_root)
    assert not data_root.exists()


def test_duplicate_and_aliased_roots_rejected(roots) -> None:
    approved, _data, _store = roots
    child = approved / "child"
    child.mkdir()
    with pytest.raises(PortabilityError) as exc:
        ExportService([approved, approved], approved / "data")
    assert exc.value.code == "ROOT_DUPLICATE"
    with pytest.raises(PortabilityError) as exc:
        ExportService([approved, child], approved / "data")
    assert exc.value.code == "ROOT_ALIAS"


def test_absurd_limits_are_rejected() -> None:
    with pytest.raises(ValidationError):
        PortabilityLimits(max_archive_bytes=2**40)
    with pytest.raises(ValidationError):
        PortabilityLimits(
            max_archive_bytes=2048,
            max_unpacked_bytes=4096,
            max_member_bytes=1024,
        )
    with pytest.raises(ValidationError):
        PortabilityLimits(
            max_archive_bytes=2048,
            max_unpacked_bytes=1024,
            max_member_bytes=512,
            max_json_bytes=2048,
        )
    with pytest.raises(ValidationError):
        PortabilityLimits(
            max_archive_bytes=2048,
            max_unpacked_bytes=1024,
            max_member_bytes=512,
            chunk_size=1024,
        )
    with pytest.raises(ValidationError):
        PortabilityLimits(deadline_seconds=1e9)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_members=10**7)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_depth=1000)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_json_bytes=1024**3)
    with pytest.raises(ValidationError):
        PortabilityLimits(min_free_slack_bytes=2**31)
    with pytest.raises(ValidationError):
        PortabilityLimits(codec_metadata_bytes_per_member=2**21)
    with pytest.raises(ValidationError):
        PortabilityLimits(gzip_expansion_factor=float("inf"))
    with pytest.raises(ValidationError):
        PortabilityLimits(deadline_seconds=float("nan"))
    with pytest.raises(ValidationError):
        PortabilityLimits(chunk_size=2 * 1024 * 1024)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_members=9000)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_depth=40)
    with pytest.raises(ValidationError):
        PortabilityLimits(max_json_bytes=2 * 1024 * 1024)


def test_limits_and_requests_are_frozen() -> None:
    limits = PortabilityLimits()
    with pytest.raises(ValidationError):
        limits.max_members = 5
    request = ExportRequest(
        operation_id="op-1",
        layout_path="/approved/layout",
        destination="/approved/out.tar",
    )
    with pytest.raises(ValidationError):
        request.operation_id = "changed"


def test_request_string_bounds() -> None:
    with pytest.raises(ValidationError):
        ExportRequest(
            operation_id="op-1",
            layout_path="x" * 5000,
            destination="/approved/out.tar",
        )
    with pytest.raises(ValidationError):
        ExportRequest(
            operation_id="op-1",
            layout_path="/approved/layout",
            destination="/approved/out.tar",
            replace_token="t" * 300,
        )


def test_result_and_registration_model_bounds() -> None:
    with pytest.raises(ValidationError):
        CleanupEvidence(removed_paths=[f"path-{index}" for index in range(40)])
    with pytest.raises(ValidationError):
        BlobRegistration(digest="d" * 300, size_bytes=1)
    with pytest.raises(ValidationError):
        BlobRegistration(
            digest="sha256:" + "a" * 64,
            size_bytes=ABS_MAX_UNPACKED_BYTES + 1,
        )
    intent = RegistrationDbIntent(
        image_digest="sha256:" + "a" * 64,
        config_digest="sha256:" + "b" * 64,
        runnable=RunnableState.RUNNABLE,
    )
    with pytest.raises(ValidationError):
        RegistrationPlan(
            image_digest="sha256:" + "a" * 64,
            config_digest="sha256:" + "b" * 64,
            manifest_digest="sha256:" + "c" * 64,
            blobs=[
                BlobRegistration(
                    digest="sha256:" + "a" * 64,
                    size_bytes=1,
                )
                for _ in range(8193)
            ],
            runnable=RunnableState.RUNNABLE,
            runnable_reason="ok",
            db_intent=intent,
        )
    with pytest.raises(ValidationError):
        ExportResult(
            operation_id="op-1",
            archive_path="/approved/out.tar",
            archive_digest="sha256:" + "a" * 64,
            layout_digest="sha256:" + "b" * 64,
            codec=CodecKind.TAR,
            stages=[OperationStage.PREFLIGHT] * 33,
            preflight=None,
            completed_at=None,
        )


def test_oci_total_byte_limit_enforced_on_export(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay", layer_bytes=b"x" * 4096)
    service = make_service(roots)
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar"),
                codec=CodecKind.TAR,
                limits=PortabilityLimits(
                    max_archive_bytes=2048,
                    max_unpacked_bytes=1024,
                    max_member_bytes=512,
                    max_json_bytes=512,
                    chunk_size=64,
                ),
            )
        )
    assert exc.value.code == "OCI_VALIDATION_FAILED"
    assert not (approved / "image.tar").exists()


def test_approved_root_symlink_component_rejected(roots, tmp_path: Path) -> None:
    approved, _data, _store = roots
    real_dir = tmp_path / "real"
    real_dir.mkdir()
    link_dir = tmp_path / "link-dir"
    link_dir.symlink_to(real_dir)
    with pytest.raises(PortabilityError) as exc:
        ExportService([link_dir / "sub"], approved / "data")
    assert exc.value.code == "PATH_SYMLINK_COMPONENT"
    assert not (approved / "data").exists()


def test_data_root_symlink_rejected(roots, tmp_path: Path) -> None:
    approved, _data, _store = roots
    real = tmp_path / "real-data"
    real.mkdir()
    link = tmp_path / "link-data"
    link.symlink_to(real)
    with pytest.raises(PortabilityError) as exc:
        ExportService([approved], link)
    assert exc.value.code in {"DATA_ROOT_SYMLINK", "PATH_SYMLINK_COMPONENT"}


def test_export_cleans_temp_on_codec_error(roots) -> None:
    from zana_core.images.archive import ArchiveCodecError

    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")

    class RaisingCodec(TarCodec):
        def _write_archive(self, layout_root: Path, archive_path: Path, **kwargs) -> str:
            raise ArchiveCodecError("injected codec failure")

    service = make_service(roots, codec_factory=lambda kind: RaisingCodec())
    with pytest.raises(PortabilityError) as exc:
        service.export(
            ExportRequest(
                operation_id="op-1",
                layout_path=str(layout),
                destination=str(approved / "image.tar"),
                codec=CodecKind.TAR,
            )
        )
    assert exc.value.code == "ARCHIVE_WRITE_FAILED"
    assert not list(approved.glob(".image.tar.*.tmp"))
    assert not (approved / "image.tar").exists()


def test_export_does_not_fail_after_commit_on_deadline(roots) -> None:
    approved, _data, _store = roots
    layout, _digest = build_layout(approved / "lay")
    calls = {"count": 0}

    def clock() -> float:
        calls["count"] += 1
        return 0.0 if calls["count"] < 100 else 999.0

    service = ExportService([approved], approved / "data", clock=clock)
    result = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(approved / "image.tar"),
            codec=CodecKind.TAR,
        )
    )
    assert (approved / "image.tar").exists()
    assert result.archive_digest.startswith("sha256:")


def test_error_details_are_sanitized() -> None:
    from zana_core.portability.models import PortabilityError

    error = PortabilityError(
        "boom",
        code="X",
        details={
            "ok": "value",
            "secret": "s" * 1000,
            "nested": {"a": {"b": 1}},
        },
    )
    assert error.details["secret"] == "oversized"
    assert len(error.details) <= 3


def test_deadline_rejects_invalid_values() -> None:
    from zana_core.portability.models import Deadline

    for bad in (0, -1, float("inf"), float("nan"), "300"):
        with pytest.raises(ValueError):
            Deadline(bad)
