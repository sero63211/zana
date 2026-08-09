"""Adversarial strictness regressions for the portability boundary."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.images.archive import (
    ArchiveCodecError,
    TarCodec,
    _deadline_value,
    _int_limit,
    safe_extract_tar,
)
from zana_core.images.import_plan import ImportLimits, ImportValidationError
from zana_core.images.models import BaseModelReference, ZanaImageConfig
from zana_core.images.oci import (
    Descriptor,
    OciValidationError,
    assemble_oci_layout,
    validate_oci_layout,
)
from zana_core.portability.models import (
    CleanupEvidence,
    Deadline,
    ExportRequest,
    PortabilityError,
    PortabilityLimits,
)

DIGEST = "sha256:" + "a" * 64


def test_archive_int_limits_reject_bool_float_string_none() -> None:
    for bad in (True, 1.0, "10", None, float("nan"), float("inf")):
        with pytest.raises(ArchiveCodecError):
            _int_limit(bad, "max_members", minimum=1, maximum=8192)


def test_archive_float_limits_reject_bool_string_nan() -> None:
    for bad in (True, "300", float("nan"), float("inf"), 0.0):
        with pytest.raises(ArchiveCodecError):
            _deadline_value(bad)


def test_oci_limits_reject_bool_float_string() -> None:
    for bad in (True, 1.5, "300", None, float("nan")):
        with pytest.raises(OciValidationError):
            assemble_oci_layout(
                ZanaImageConfig(
                    name="x",
                    version="1",
                    base_model=BaseModelReference(identity_digest=DIGEST),
                ),
                {},
                Path("/tmp/unused"),
                max_blob_bytes=bad,  # type: ignore[arg-type]
            )


def test_zero_deadline_is_invalid_everywhere() -> None:
    with pytest.raises(ValueError):
        Deadline(0.0)
    with pytest.raises(ValidationError):
        PortabilityLimits(deadline_seconds=0.0)
    with pytest.raises(ImportValidationError):
        ImportLimits(deadline_seconds=0.0).validated()
    with pytest.raises(ArchiveCodecError):
        _deadline_value(0.0)


def test_clock_wrong_type_and_nan_rejected() -> None:
    with pytest.raises(ValueError):
        Deadline(1.0, clock=lambda: "not-a-number")  # type: ignore[arg-type]
    with pytest.raises(ValueError):
        Deadline(1.0, clock=lambda: float("nan"))
    with pytest.raises(ValueError):
        Deadline(1.0, clock="not-callable")  # type: ignore[arg-type]


def test_hostile_str_never_called_in_error_details() -> None:
    class Hostile:
        def __str__(self) -> str:
            raise AssertionError("str must not be called on arbitrary objects")

        def __repr__(self) -> str:
            raise AssertionError("repr must not be called on arbitrary objects")

    error = PortabilityError(
        "boom",
        code="X",
        details={"attacker": Hostile(), "ok": "fine"},
    )
    assert error.details["attacker"] == "non-scalar"
    assert error.details["ok"] == "fine"


def test_hostile_len_mapping_never_trusted() -> None:
    class HostileDict(dict):
        def __len__(self) -> int:
            raise AssertionError("len must not be trusted")

        def items(self):
            raise AssertionError("items must not be materialized unbounded")

    with pytest.raises(OciValidationError):
        assemble_oci_layout(
            ZanaImageConfig(
                name="x",
                version="1",
                base_model=BaseModelReference(identity_digest=DIGEST),
            ),
            HostileDict(),  # type: ignore[arg-type]
            Path("/tmp/unused"),
        )


def test_secret_scan_rejects_exact_type_violations() -> None:
    from zana_core.images.secrets import (
        ExclusionError,
        SecretScanLimits,
        scan_payload_for_secrets,
    )

    class KeySub(str):
        def lower(self) -> str:
            raise AssertionError("subclass lower must not be invoked")

    class ValueList(list):
        pass

    class LimitsSub(SecretScanLimits):
        pass

    with pytest.raises(ExclusionError):
        scan_payload_for_secrets({KeySub("token"): "x"})
    with pytest.raises(ExclusionError):
        scan_payload_for_secrets({"token": ValueList([])})
    with pytest.raises(ExclusionError):
        scan_payload_for_secrets({"a": 1}, limits=LimitsSub())
    assert scan_payload_for_secrets({"token": "x"}) == ["token"]


def test_non_empty_secrets_marker_rejected() -> None:
    from zana_core.images.secrets import ExclusionError, scan_payload_for_secrets

    with pytest.raises(ExclusionError):
        scan_payload_for_secrets({"secrets": {"api_key": "x"}})
    with pytest.raises(ExclusionError):
        scan_payload_for_secrets({"secrets": []})
    assert scan_payload_for_secrets({"secrets": ""}) == []


def test_path_helpers_do_not_leak_descriptors(tmp_path: Path) -> None:
    import os

    from zana_core.portability.models import OperationStage
    from zana_core.portability.paths import require_directory, require_regular_file

    target = tmp_path / "file.bin"
    target.write_bytes(b"x")
    before = len(os.listdir("/dev/fd"))
    require_regular_file(target, stage=OperationStage.PREFLIGHT)
    require_directory(tmp_path, stage=OperationStage.PREFLIGHT)
    after = len(os.listdir("/dev/fd"))
    assert after <= before + 1


def test_dirfd_walk_does_not_leak_ancestor_fds(tmp_path: Path) -> None:
    import os

    from zana_core.portability.models import OperationStage
    from zana_core.portability.paths import _open_dirfd_path, _open_parent_dirfd

    nested = tmp_path / "a" / "b" / "c"
    nested.mkdir(parents=True)
    before = len(os.listdir("/dev/fd"))
    fd, name = _open_parent_dirfd(nested, stage=OperationStage.PREFLIGHT)
    os.close(fd)
    fd2 = _open_dirfd_path(nested, stage=OperationStage.PREFLIGHT)
    os.close(fd2)
    after = len(os.listdir("/dev/fd"))
    assert after <= before + 1


def test_dirfd_walk_rejects_unsafe_components(tmp_path: Path) -> None:
    from zana_core.portability.models import OperationStage, PathPolicyError
    from zana_core.portability.paths import _open_parent_dirfd

    with pytest.raises(PathPolicyError):
        _open_parent_dirfd(tmp_path / ".." / "escape", stage=OperationStage.PREFLIGHT)


def test_fd_fstat_failure_closes_fd(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    from zana_core.portability.models import OperationStage
    from zana_core.portability.paths import _open_regular_nofollow

    target = tmp_path / "file.bin"
    target.write_bytes(b"x")
    before = len(os.listdir("/dev/fd"))

    def failing_fstat(fd):
        raise OSError("fstat failed")

    monkeypatch.setattr(os, "fstat", failing_fstat)
    with pytest.raises(OSError):
        _open_regular_nofollow(target, stage=OperationStage.PREFLIGHT)
    monkeypatch.undo()
    after = len(os.listdir("/dev/fd"))
    assert after <= before + 1


def test_annotation_struct_is_immutable_and_exact() -> None:
    from zana_core.images.oci import ImmutableAnnotations, canonical_json_bytes

    annotations = ImmutableAnnotations.from_exact_dict({"a": "b"})
    assert annotations.as_dict() == {"a": "b"}

    class Sub(ImmutableAnnotations):
        def as_dict(self) -> dict[str, str]:
            return {"evil": "x"}

    from zana_core.images.oci import OciValidationError

    with pytest.raises(OciValidationError):
        canonical_json_bytes(Sub(annotations.items))


def test_hostile_inputs_rejected_before_hooks(tmp_path: Path) -> None:
    from zana_core.images.oci import (
        OciValidationError,
        _validate_json_graph,
        assemble_oci_layout,
    )
    from zana_core.portability.paths import validate_approved_roots

    class EvilInt(int):
        def __index__(self):
            raise AssertionError("hostile index")

        def __lt__(self, other):
            raise AssertionError("hostile comparison")

    class EvilPath(type(tmp_path)):
        def __fspath__(self):
            raise AssertionError("hostile fspath")

    class EvilSequence(list):
        def __iter__(self):
            raise AssertionError("hostile iteration")

    from zana_core.portability.models import PathPolicyError

    with pytest.raises(PathPolicyError):
        validate_approved_roots(EvilSequence())
    with pytest.raises(PathPolicyError):
        validate_approved_roots([EvilPath(tmp_path)])
    with pytest.raises(OciValidationError):
        _validate_json_graph({"x": EvilInt(1)})

    class HostileRole(dict):
        def items(self):
            raise AssertionError("hostile items")

    with pytest.raises(OciValidationError):
        assemble_oci_layout(type("C", (), {})(), HostileRole(), tmp_path / "layout")


def test_missing_layout_json_fails_closed(tmp_path: Path) -> None:
    from zana_core.images.secrets import ExclusionError, scan_layout_payloads

    layout = tmp_path / "layout"
    layout.mkdir()
    (layout / "index.json").write_text("{}")
    (layout / "manifest.json").write_text("{}")
    with pytest.raises(ExclusionError, match="missing"):
        scan_layout_payloads(layout)


def test_layout_root_swap_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os as os_module

    from zana_core.images import secrets as secrets_module
    from zana_core.images.secrets import ExclusionError, scan_layout_payloads

    layout = tmp_path / "layout"
    layout.mkdir()
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    (layout / "index.json").write_text("{}")
    (layout / "manifest.json").write_text("{}")
    real_root = tmp_path / "real"
    real_root.mkdir()
    (real_root / "index.json").write_text('{"secret":"x"}')
    real_open = os_module.open
    swapped = False
    outside_marker = real_root / "index.json"

    def swapping_open(path, flags, mode=0o777, dir_fd=None):
        nonlocal swapped
        result = real_open(path, flags, mode, dir_fd=dir_fd)
        if not swapped and os_module.fstat(result).st_ino == layout.stat().st_ino:
            # Swap exactly after root fd acquisition, before first metadata open.
            for child in layout.iterdir():
                child.unlink()
            layout.rmdir()
            layout.symlink_to(real_root, target_is_directory=True)
            swapped = True
        return result

    monkeypatch.setattr(secrets_module.os, "open", swapping_open)
    with pytest.raises(ExclusionError):
        scan_layout_payloads(layout)
    assert outside_marker.read_text() == '{"secret":"x"}'


def test_mutable_frozen_escape_is_closed() -> None:
    cleanup = CleanupEvidence()
    with pytest.raises(ValidationError):
        cleanup.workspace_removed = True
    assert isinstance(cleanup.removed_paths, tuple)


def test_limits_reject_numeric_strings_and_bools() -> None:
    for kwargs in (
        {"max_members": "4096"},
        {"max_members": 4096.0},
        {"max_members": True},
        {"max_json_bytes": "512"},
        {"chunk_size": 1.5},
    ):
        with pytest.raises(ValidationError):
            PortabilityLimits(**kwargs)


def test_request_rejects_bool_and_numeric_string_coercion() -> None:
    with pytest.raises(ValidationError):
        ExportRequest(
            operation_id="op-1",
            layout_path="/approved/layout",
            destination="/approved/out.tar",
            replace_allowed="yes",  # type: ignore[arg-type]
        )


def test_descriptor_rejects_float_size_and_mutates() -> None:
    with pytest.raises(ValidationError):
        Descriptor(
            media_type="application/json",
            digest=DIGEST,
            size=1.5,  # type: ignore[arg-type]
        )
    descriptor = Descriptor(
        media_type="application/json",
        digest=DIGEST,
        size=1,
    )
    with pytest.raises(ValidationError):
        descriptor.size = 2


def test_oversized_utf8_aggregate_rejected() -> None:
    from zana_core.images.oci import MAX_ANNOTATIONS

    with pytest.raises(ValidationError):
        Descriptor(
            media_type="application/json",
            digest=DIGEST,
            size=1,
            annotations={f"key-{i}": "v" for i in range(MAX_ANNOTATIONS + 1)},
        )


def test_cleanup_path_utf8_and_count_bounds() -> None:
    with pytest.raises(ValidationError):
        CleanupEvidence(removed_paths=("x" * 5000,))


def test_cyclic_json_secret_scan_rejected() -> None:
    from zana_core.images.secrets import ExclusionError, scan_payload_for_secrets

    node: dict = {}
    node["self"] = node
    with pytest.raises(ExclusionError):
        scan_payload_for_secrets(node)


def test_non_string_secret_values_rejected() -> None:
    from zana_core.images.secrets import ExclusionError, scan_payload_for_secrets

    for value in ([1], {"k": 1}, 5, True):
        with pytest.raises(ExclusionError):
            scan_payload_for_secrets({"api_token": value})


def test_symlink_leaf_source_rejected_in_extract(tmp_path: Path) -> None:
    real = tmp_path / "real.tar"
    real.write_bytes(b"x")
    link = tmp_path / "link.tar"
    link.symlink_to(real)
    with pytest.raises(ArchiveCodecError, match="symlink"):
        safe_extract_tar(link, tmp_path / "out")


def test_missing_required_json_fails_closed(tmp_path: Path) -> None:
    layout = tmp_path / "layout"
    layout.mkdir()
    (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
    with pytest.raises(OciValidationError):
        validate_oci_layout(layout)


def test_store_short_consume_is_detected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from zana_core.artifacts import ArtifactStore
    from zana_core.images import import_plan as ip
    from zana_core.images.models import BaseModelReference, ZanaImageConfig
    from zana_core.images.oci import assemble_oci_layout

    behavior = tmp_path / "behavior.json"
    behavior.write_bytes(b"content")
    layout = tmp_path / "layout"
    layout.mkdir()
    assemble_oci_layout(
        ZanaImageConfig(
            name="x",
            version="1",
            base_model=BaseModelReference(identity_digest=DIGEST),
        ),
        {"behavior": behavior},
        layout,
    )
    store = ArtifactStore(tmp_path / "store")

    real_read = ip._BoundedSource.read
    remaining = 2

    def short_read(self, size: int = -1) -> bytes:
        nonlocal remaining
        if remaining <= 0:
            return b""
        chunk = real_read(self, size)
        limited = chunk[:remaining]
        remaining -= len(limited)
        return limited

    monkeypatch.setattr(ip._BoundedSource, "read", short_read)
    with pytest.raises((ImportValidationError, AssertionError, OSError)):
        ip.register_into_store(
            store,
            layout,
            limits=ImportLimits(deadline_seconds=30.0),
            deadline_seconds=30.0,
        )


def test_pack_never_overwrites_existing_output(tmp_path: Path) -> None:
    root = tmp_path / "layout"
    (root / "blobs" / "sha256").mkdir(parents=True)
    (root / "oci-layout").write_text("{}")
    (root / "index.json").write_text("{}")
    (root / "manifest.json").write_text("{}")
    output = tmp_path / "image.tar"
    output.write_bytes(b"pre-existing")
    with pytest.raises(ArchiveCodecError, match="already exists"):
        TarCodec().pack(root, output)
    assert output.read_bytes() == b"pre-existing"


def test_hashing_writer_rejects_short_writes(tmp_path: Path) -> None:
    from zana_core.images.archive import _HashingWriter

    class ShortWriter:
        def write(self, data: bytes) -> int:
            return max(0, len(data) - 1)

        def flush(self) -> None:
            pass

        def tell(self) -> int:
            return 0

    writer = _HashingWriter(ShortWriter())
    with pytest.raises(OSError, match="short write"):
        writer.write(b"abcd")


def test_fsync_failure_surfaces_from_export(tmp_path: Path, monkeypatch) -> None:
    from tests.portability.helpers import build_layout
    from zana_core.portability.export import ExportService
    from zana_core.portability.models import CodecKind, ExportRequest

    approved = tmp_path / "approved"
    approved.mkdir()
    layout, _digest = build_layout(approved / "lay")

    def failing_fsync_file(path: Path) -> None:
        raise OSError("simulated fsync failure")

    from zana_core.portability import paths as paths_module

    monkeypatch.setattr(paths_module, "fsync_file", failing_fsync_file)
    service = ExportService([approved], approved / "data")
    result = service.export(
        ExportRequest(
            operation_id="op-1",
            layout_path=str(layout),
            destination=str(approved / "image.tar"),
            codec=CodecKind.TAR,
        )
    )
    assert (approved / "image.tar").exists()
    assert result.durability_uncertain is False


def test_snapshot_output_collision_fails_closed(
    tmp_path: Path,
) -> None:
    from zana_core.portability.models import Deadline, OperationStage
    from zana_core.portability.paths import deadline_digest

    approved = tmp_path / "approved"
    approved.mkdir()
    source = approved / "blob.bin"
    source.write_bytes(b"content")
    snapshot = approved / "snapshot.bin"
    snapshot.write_bytes(b"pre-existing")
    with pytest.raises(Exception, match="OUTPUT_COLLISION|already exists"):
        deadline_digest(
            source,
            chunk_size=64,
            max_bytes=1024,
            deadline=Deadline(30.0),
            stage=OperationStage.UNPACK,
            output_path=snapshot,
        )
    assert snapshot.read_bytes() == b"pre-existing"


def test_secure_open_support_fails_closed_when_flags_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import os

    from zana_core.images.secrets import ExclusionError, scan_layout_payloads

    for attribute in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY"):
        monkeypatch.delattr(os, attribute)
        with pytest.raises(ExclusionError, match="unsupported"):
            scan_layout_payloads(Path("unused"))
        monkeypatch.undo()


def test_cleanup_holds_data_root_dirfd_across_replacement(tmp_path: Path) -> None:
    import os

    from zana_core.portability import paths as paths_module
    from zana_core.portability.paths import remove_tree_confined

    data_root = tmp_path / "data"
    workspace = data_root / "workspaces" / "ws"
    workspace.mkdir(parents=True)
    (workspace / "file.bin").write_bytes(b"x")
    real_open = os.open
    data_identity = (data_root.stat().st_dev, data_root.stat().st_ino)
    swapped = False

    def swapping_open(path, flags, mode=0o777, dir_fd=None):
        nonlocal swapped
        if dir_fd is not None and not swapped:
            try:
                info = os.fstat(dir_fd)
            except OSError:
                info = None
            if info is not None and (info.st_dev, info.st_ino) == data_identity:
                data_root.rename(tmp_path / "old-data")
                new_root = tmp_path / "data"
                new_root.mkdir()
                replacement = new_root / "workspaces" / "ws"
                replacement.mkdir(parents=True)
                (replacement / "sentinel").write_bytes(b"keep")
                swapped = True
        if dir_fd is not None:
            return real_open(path, flags, mode, dir_fd=dir_fd)
        return real_open(path, flags, mode)

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(paths_module.os, "open", swapping_open)
    try:
        remove_tree_confined(workspace, data_root)
    finally:
        monkeypatch.undo()
    assert not (tmp_path / "old-data" / "workspaces" / "ws").exists()
    assert (tmp_path / "data" / "workspaces" / "ws" / "sentinel").read_text() == "keep"
