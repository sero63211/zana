"""Safe archive extraction and honest codec availability tests."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from zana_core.images.archive import (
    ArchiveCodecError,
    ArchiveFormat,
    CodecLimits,
    CodecUnavailableError,
    GzipTarCodec,
    TarCodec,
    available_codecs,
    codec_for_extension,
    codec_for_format,
    collect_bounded_layout_entries,
    safe_extract_tar,
    walk_bounded_tree,
    zstd_available,
)


def _write_tar(path: Path, members: dict[str, bytes]) -> None:
    with tarfile.open(path, "w") as tar:
        for name, content in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


class TestSafeExtraction:
    def test_regular_members_extract_under_destination(self, tmp_path: Path) -> None:
        archive = tmp_path / "layout.tar"
        destination = tmp_path / "out"
        _write_tar(
            archive,
            {
                "oci-layout": b'{"imageLayoutVersion":"1.0.0"}',
                "index.json": b"{}",
            },
        )
        count = safe_extract_tar(archive, destination)
        assert count == 2
        assert (destination / "oci-layout").read_bytes() == b'{"imageLayoutVersion":"1.0.0"}'

    @pytest.mark.parametrize(
        "name",
        ["/etc/passwd", "../escape", "blobs/../../escape", "a/../../escape"],
    )
    def test_traversal_and_absolute_members_are_rejected(
        self,
        tmp_path: Path,
        name: str,
    ) -> None:
        archive = tmp_path / "bad.tar"
        _write_tar(archive, {name: b"bad"})
        with pytest.raises(ArchiveCodecError):
            safe_extract_tar(archive, tmp_path / "out")

    def test_symlink_member_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("link")
            info.type = tarfile.SYMTYPE
            info.linkname = "/etc/passwd"
            tar.addfile(info)
        with pytest.raises(ArchiveCodecError, match="Symlink"):
            safe_extract_tar(archive, tmp_path / "out")

    def test_hardlink_member_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("link")
            info.type = tarfile.LNKTYPE
            info.linkname = "target"
            tar.addfile(info)
        with pytest.raises(ArchiveCodecError, match="hardlink"):
            safe_extract_tar(archive, tmp_path / "out")

    def test_device_node_member_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("device")
            info.type = tarfile.CHRTYPE
            tar.addfile(info)
        with pytest.raises(ArchiveCodecError, match="Special"):
            safe_extract_tar(archive, tmp_path / "out")

    def test_duplicate_member_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar"
        with tarfile.open(archive, "w") as tar:
            for _ in range(2):
                info = tarfile.TarInfo("oci-layout")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(ArchiveCodecError, match="Duplicate"):
            safe_extract_tar(archive, tmp_path / "out")

    def test_unexpected_member_is_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "bad.tar"
        _write_tar(archive, {"secret.txt": b"secret"})
        with pytest.raises(ArchiveCodecError, match="Unexpected"):
            safe_extract_tar(archive, tmp_path / "out")

    def test_member_count_limit_is_enforced(self, tmp_path: Path) -> None:
        archive = tmp_path / "big.tar"
        with tarfile.open(archive, "w") as tar:
            for index in range(5):
                info = tarfile.TarInfo(f"blobs/sha256/{index}")
                info.size = 1
                tar.addfile(info, io.BytesIO(b"x"))
        with pytest.raises(ArchiveCodecError, match="member count"):
            safe_extract_tar(archive, tmp_path / "out", max_members=3)

    def test_member_size_limit_is_enforced(self, tmp_path: Path) -> None:
        archive = tmp_path / "big.tar"
        _write_tar(archive, {"oci-layout": b"x" * 100})
        with pytest.raises(ArchiveCodecError, match="size limit"):
            safe_extract_tar(archive, tmp_path / "out", max_member_bytes=10)

    def test_empty_members_are_rejected(self, tmp_path: Path) -> None:
        archive = tmp_path / "empty.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        with pytest.raises(ArchiveCodecError, match="empty"):
            safe_extract_tar(archive, tmp_path / "out")


class TestCodecInterface:
    def test_available_codecs_include_tar(self) -> None:
        codecs = available_codecs()
        assert ArchiveFormat.TAR in codecs
        assert ArchiveFormat.TAR_GZ in codecs

    def test_tar_codec_pack_unpack_round_trip(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        archive = tmp_path / "image.tar"
        codec = TarCodec()
        digest = codec.pack(root, archive)
        assert digest.startswith("sha256:")
        destination = tmp_path / "out"
        count = codec.unpack(archive, destination)
        assert count == 3
        assert (destination / "oci-layout").read_text() == '{"imageLayoutVersion":"1.0.0"}'

    def test_tar_codec_never_claims_zstd(self) -> None:
        assert TarCodec().format_name == ArchiveFormat.TAR
        assert TarCodec().extension == ".tar"

    def test_zstd_is_honest_when_unavailable(self) -> None:
        assert zstd_available() is (ArchiveFormat.TAR_ZSTD in available_codecs())
        if not zstd_available():
            from zana_core.images.archive import ZstdTarCodec

            with pytest.raises(CodecUnavailableError):
                ZstdTarCodec().pack(Path("."), Path("/tmp/none"))

    def test_gzip_codec_round_trip(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        archive = tmp_path / "image.tar.gz"
        codec = GzipTarCodec()
        digest = codec.pack(root, archive)
        assert digest.startswith("sha256:")
        destination = tmp_path / "out"
        assert codec.unpack(archive, destination) == 3
        assert (destination / "index.json").read_text() == "{}"

    def test_tar_codec_pack_is_deterministic(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        first = tmp_path / "a.tar"
        second = tmp_path / "b.tar"
        TarCodec().pack(root, first)
        TarCodec().pack(root, second)
        assert first.read_bytes() == second.read_bytes()

    def test_gzip_codec_pack_is_deterministic(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        first = tmp_path / "a.tar.gz"
        second = tmp_path / "b.tar.gz"
        GzipTarCodec().pack(root, first)
        GzipTarCodec().pack(root, second)
        assert first.read_bytes() == second.read_bytes()

    def test_codec_for_format_and_extension_mapping(self) -> None:
        assert isinstance(codec_for_format(ArchiveFormat.TAR), TarCodec)
        assert isinstance(codec_for_format(ArchiveFormat.TAR_GZ), GzipTarCodec)
        assert isinstance(codec_for_extension(".tar"), TarCodec)
        assert isinstance(codec_for_extension(".tgz"), GzipTarCodec)
        assert isinstance(codec_for_extension(".tar.gz"), GzipTarCodec)
        assert codec_for_extension(".zip") is None
        with pytest.raises(ArchiveCodecError):
            codec_for_format("not-a-format")

    def test_safe_extract_depth_limit(self, tmp_path: Path) -> None:
        archive = tmp_path / "deep.tar"
        _write_tar(archive, {"blobs/sha256/a/b/c.txt": b"x"})
        with pytest.raises(ArchiveCodecError, match="depth"):
            safe_extract_tar(archive, tmp_path / "out", max_depth=2)

    def test_safe_extract_path_length_limit(self, tmp_path: Path) -> None:
        archive = tmp_path / "long.tar"
        _write_tar(archive, {"blobs/sha256/" + "f" * 200: b"x"})
        with pytest.raises(ArchiveCodecError, match="character"):
            safe_extract_tar(archive, tmp_path / "out", max_path_chars=64)

    def test_safe_extract_deadline(self, tmp_path: Path) -> None:
        archive = tmp_path / "slow.tar"
        _write_tar(archive, {"oci-layout": b"{}"})
        with pytest.raises(ArchiveCodecError, match="deadline"):
            safe_extract_tar(archive, tmp_path / "out", deadline=-1.0)

    def test_tar_writer_member_count_cap(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        for index in range(5):
            (root / "blobs" / "sha256" / f"{index:064x}").write_bytes(b"x")
        limits = CodecLimits(
            max_members=3,
            max_member_bytes=512 * 1024 * 1024,
            max_unpacked_bytes=2**30,
            max_depth=32,
            max_path_chars=1024,
            chunk_size=1024 * 1024,
            deadline_seconds=300.0,
        )
        with pytest.raises(ArchiveCodecError, match="member count"):
            TarCodec().pack(root, tmp_path / "out.tar", limits=limits)

    def test_tar_writer_total_bytes_cap(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text("x" * 2048)
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        limits = CodecLimits(
            max_members=4096,
            max_member_bytes=512 * 1024 * 1024,
            max_unpacked_bytes=1024,
            max_depth=32,
            max_path_chars=1024,
            chunk_size=1024 * 1024,
            deadline_seconds=300.0,
        )
        with pytest.raises(ArchiveCodecError, match="total size"):
            TarCodec().pack(root, tmp_path / "out.tar", limits=limits)

    def test_bounded_collector_stops_after_limit_plus_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        blob_dir = tmp_path / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        for index in range(20):
            (blob_dir / f"{index:064x}").write_bytes(b"x")
        real_iterdir = Path.iterdir
        pulled: list[str] = []

        def guarded_iterdir(self: Path):
            for index, item in enumerate(real_iterdir(self)):
                pulled.append(item.name)
                if index >= 4:
                    raise AssertionError("collector materialized more than limit+1 entries")
                yield item

        monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
        with pytest.raises(ArchiveCodecError, match="member count"):
            collect_bounded_layout_entries(blob_dir, remaining_budget=3)
        assert len(pulled) <= 4

    def test_bounded_collector_sorts_only_bounded_list(self, tmp_path: Path) -> None:
        blob_dir = tmp_path / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        for name in ("c", "a", "b"):
            (blob_dir / name).write_bytes(b"x")
        collected = collect_bounded_layout_entries(blob_dir, remaining_budget=3)
        assert [item.name for item in collected] == ["a", "b", "c"]

    def test_bounded_collector_counts_unexpected_entries(self, tmp_path: Path) -> None:
        blob_dir = tmp_path / "blobs" / "sha256"
        blob_dir.mkdir(parents=True)
        (blob_dir / "subdir").mkdir()
        with pytest.raises(ArchiveCodecError, match="unexpected entry"):
            collect_bounded_layout_entries(blob_dir, remaining_budget=3)

    def test_bounded_tree_walker_stops_after_limit_plus_one(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = tmp_path / "tree"
        (root / "a").mkdir(parents=True)
        for index in range(12):
            (root / "a" / f"f{index:04d}.txt").write_bytes(b"x")
        real_iterdir = Path.iterdir
        pulled: list[str] = []

        def guarded_iterdir(self: Path):
            for item in real_iterdir(self):
                pulled.append(str(item.relative_to(root)))
                if len(pulled) > 5:
                    raise AssertionError("walker materialized more than limit+1 entries")
                yield item

        monkeypatch.setattr(Path, "iterdir", guarded_iterdir)
        with pytest.raises(ArchiveCodecError, match="member count"):
            walk_bounded_tree(root, remaining_budget=3)
        assert len(pulled) <= 5

    def test_bounded_tree_walker_enforces_depth_and_path_chars(self, tmp_path: Path) -> None:
        root = tmp_path / "tree"
        deep = root / "a" / "b" / "c" / "d"
        deep.mkdir(parents=True)
        (deep / "f.txt").write_bytes(b"x")
        with pytest.raises(ArchiveCodecError, match="depth"):
            walk_bounded_tree(root, remaining_budget=10, max_depth=2)
        long_root = tmp_path / "long"
        long_root.mkdir()
        (long_root / ("x" * 200)).write_bytes(b"x")
        with pytest.raises(ArchiveCodecError, match="character"):
            walk_bounded_tree(long_root, remaining_budget=10, max_path_chars=64)

    def test_bounded_tree_walker_rejects_symlink_and_non_regular(self, tmp_path: Path) -> None:
        root = tmp_path / "tree"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.write_bytes(b"x")
        (root / "link").symlink_to(outside)
        with pytest.raises(ArchiveCodecError, match="symlink"):
            walk_bounded_tree(root, remaining_budget=10)
        (root / "link").unlink()
        (root / "subdir").mkdir()
        (root / "subdir" / "f.txt").write_bytes(b"x")
        with pytest.raises(ArchiveCodecError, match="member count"):
            walk_bounded_tree(root, remaining_budget=0)

    def test_safe_extract_never_uses_getmembers(self, tmp_path: Path) -> None:
        archive = tmp_path / "layout.tar"
        _write_tar(archive, {"oci-layout": b"{}"})

        def forbidden(*args, **kwargs):
            raise AssertionError("getmembers/getnames must not be used")

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(tarfile.TarFile, "getmembers", forbidden)
        monkeypatch.setattr(tarfile.TarFile, "getnames", forbidden)
        try:
            count = safe_extract_tar(archive, tmp_path / "out")
        finally:
            monkeypatch.undo()
        assert count == 1

    def test_safe_extract_hard_limits_rejected_before_work(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.tar"
        _write_tar(archive, {"oci-layout": b"{}"})
        destination = tmp_path / "never-created"
        for kwargs in (
            {"max_members": 0},
            {"max_members": 9000},
            {"chunk_size": 0},
            {"deadline": float("inf")},
            {"deadline": -1.0},
            {"max_member_bytes": None},
            {"max_total_bytes": float("nan")},
        ):
            with pytest.raises(ArchiveCodecError):
                safe_extract_tar(archive, destination, **kwargs)
        assert not destination.exists()

    def test_truncated_member_removes_partial_output(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "a.tar"
        _write_tar(archive, {"oci-layout": b"abc"})
        destination = tmp_path / "out"
        real_extractfile = tarfile.TarFile.extractfile

        def truncated(self: tarfile.TarFile, member: tarfile.TarInfo):
            real_extractfile(self, member)

            class _Truncated:
                def __init__(self) -> None:
                    self._emitted = False

                def read(self, size: int = -1) -> bytes:
                    if not self._emitted:
                        self._emitted = True
                        return b"ab"
                    return b""

            return _Truncated()

        monkeypatch.setattr(tarfile.TarFile, "extractfile", truncated)
        with pytest.raises(ArchiveCodecError, match="size mismatch"):
            safe_extract_tar(archive, destination)
        assert not (destination / "oci-layout").exists()

    def test_symlink_root_rejected_before_resolve(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        (real / "f.txt").write_bytes(b"x")
        link = tmp_path / "link"
        link.symlink_to(real)
        with pytest.raises(ArchiveCodecError, match="symlink"):
            walk_bounded_tree(link, remaining_budget=10)

    def test_extraction_rolls_back_created_outputs_on_later_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        archive = tmp_path / "a.tar"
        _write_tar(
            archive,
            {"oci-layout": b"{}", "index.json": b"{}", "bad-member": b"x"},
        )
        destination = tmp_path / "out"
        destination.mkdir()
        real_extractfile = tarfile.TarFile.extractfile

        def truncated(self: tarfile.TarFile, member: tarfile.TarInfo):
            original = real_extractfile(self, member)
            if member.name == "index.json":

                class _Short:
                    def read(self, size: int = -1) -> bytes:
                        return b""

                return _Short()
            return original

        monkeypatch.setattr(tarfile.TarFile, "extractfile", truncated)
        with pytest.raises(ArchiveCodecError, match="size mismatch"):
            safe_extract_tar(archive, destination)
        monkeypatch.undo()
        assert not (destination / "oci-layout").exists()
        assert not (destination / "index.json").exists()

    def test_extraction_never_deletes_pre_existing_target(self, tmp_path: Path) -> None:
        archive = tmp_path / "a.tar"
        _write_tar(archive, {"oci-layout": b"new"})
        destination = tmp_path / "out"
        destination.mkdir()
        (destination / "oci-layout").write_bytes(b"pre-existing")
        with pytest.raises(ArchiveCodecError, match="collides"):
            safe_extract_tar(archive, destination)
        assert (destination / "oci-layout").read_bytes() == b"pre-existing"

    def test_writer_honors_max_member_bytes(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text("x" * 2048)
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        limits = CodecLimits(
            max_members=4096,
            max_member_bytes=1024,
            max_unpacked_bytes=2**30,
            max_depth=32,
            max_path_chars=1024,
            chunk_size=64,
            deadline_seconds=300.0,
        )
        with pytest.raises(ArchiveCodecError, match="byte limit"):
            TarCodec().pack(root, tmp_path / "out.tar", limits=limits)

    def test_writer_fails_closed_on_missing_required_file(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text("{}")
        (root / "index.json").write_text("{}")
        with pytest.raises(ArchiveCodecError, match="missing or unsafe"):
            TarCodec().pack(root, tmp_path / "out.tar")

    def test_writer_detects_growth_after_preflight(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import os as os_module

        from zana_core.images import archive as archive_module

        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text("{}")
        (root / "index.json").write_text("{}")
        (root / "manifest.json").write_text("{}")
        real_open = os_module.open
        layout_file = (root / "oci-layout").resolve()
        grown = False

        def growing_open(path, flags, mode=0o777, dir_fd=None):
            nonlocal grown
            if Path(path).resolve() == layout_file and not grown:
                with Path(path).open("ab") as handle:
                    handle.write(b"EXTRA")
                grown = True
            if dir_fd is not None:
                return real_open(path, flags, mode, dir_fd=dir_fd)
            return real_open(path, flags, mode)

        monkeypatch.setattr(archive_module.os, "open", growing_open)
        with pytest.raises(ArchiveCodecError, match="changed size"):
            TarCodec().pack(root, tmp_path / "out.tar")
        monkeypatch.undo()
        assert not (tmp_path / "out.tar").exists()


class _GrowingHandle:
    def __init__(self, handle):
        self._handle = handle
        self._extra = False

    def read(self, size: int = -1) -> bytes:
        chunk = self._handle.read(size)
        if chunk and not self._extra:
            self._extra = True
            return chunk + b"EXTRA"
        return chunk

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self._handle.close()
