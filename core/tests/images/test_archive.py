"""Safe archive extraction and honest codec availability tests."""

from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from zana_core.images.archive import (
    ArchiveCodecError,
    ArchiveFormat,
    CodecUnavailableError,
    TarCodec,
    available_codecs,
    safe_extract_tar,
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

    def test_empty_members_are_tolerated(self, tmp_path: Path) -> None:
        archive = tmp_path / "empty.tar"
        with tarfile.open(archive, "w") as tar:
            info = tarfile.TarInfo("")
            info.type = tarfile.DIRTYPE
            tar.addfile(info)
        assert safe_extract_tar(archive, tmp_path / "out") == 0


class TestCodecInterface:
    def test_available_codecs_include_tar(self) -> None:
        codecs = available_codecs()
        assert ArchiveFormat.TAR in codecs

    def test_tar_codec_pack_unpack_round_trip(self, tmp_path: Path) -> None:
        root = tmp_path / "layout"
        (root / "blobs" / "sha256").mkdir(parents=True)
        (root / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        (root / "index.json").write_text("{}")
        archive = tmp_path / "image.tar"
        codec = TarCodec()
        digest = codec.pack(root, archive)
        assert digest.startswith("sha256:")
        destination = tmp_path / "out"
        count = codec.unpack(archive, destination)
        assert count == 2
        assert (destination / "oci-layout").read_text() == '{"imageLayoutVersion":"1.0.0"}'

    def test_tar_codec_never_claims_zstd(self) -> None:
        assert TarCodec().format_name == ArchiveFormat.TAR
        assert TarCodec().extension == ".tar"

    def test_zstd_is_honest_when_unavailable(self) -> None:
        assert zstd_available() is (
            available_codecs() == [ArchiveFormat.TAR, ArchiveFormat.TAR_ZSTD]
        )
        if not zstd_available():
            from zana_core.images.archive import ZstdTarCodec

            with pytest.raises(CodecUnavailableError):
                ZstdTarCodec().pack(Path("."), Path("/tmp/none"))
