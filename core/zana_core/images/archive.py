"""Safe OCI archive extraction and honest codec capability detection."""

from __future__ import annotations

import shutil
import tarfile
from pathlib import Path, PurePosixPath
from typing import Any

MAX_ARCHIVE_MEMBERS = 4096
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024

EXPECTED_LAYOUT_MEMBERS = frozenset({"oci-layout", "index.json", "manifest.json", "blobs"})


class ArchiveCodecError(ValueError):
    """Raised when an archive cannot be read or extracted safely."""


class CodecUnavailableError(ArchiveCodecError):
    """Raised when a requested codec is not installed in the environment."""


class ArchiveFormat:
    """Named archive format used by export/import codecs."""

    TAR = "tar"
    TAR_ZSTD = "tar.zst"


def _safe_relative_name(name: str) -> Path:
    if name.startswith("/") or "\x00" in name:
        raise ArchiveCodecError(f"Unsafe archive member name: {name!r}")
    candidate = PurePosixPath(name)
    if ".." in candidate.parts:
        raise ArchiveCodecError(f"Traversal archive member rejected: {name!r}")
    if candidate.is_absolute():
        raise ArchiveCodecError(f"Absolute archive member rejected: {name!r}")
    return Path(*candidate.parts)


def safe_extract_tar(
    archive_path: Path,
    destination: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
) -> int:
    """Extract a tar archive under ``destination`` with strict safety limits.

    Rejects absolute paths, ``..`` traversal, symlinks/hardlinks/device
    nodes, duplicate members, unexpected top-level members, and size/count
    limit violations. Returns the number of extracted files.
    """

    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = set()
    total_bytes = 0
    extracted = 0

    with tarfile.open(archive_path, "r:*") as tar:
        for member in tar.getmembers():
            if len(seen) >= max_members:
                raise ArchiveCodecError("Archive member count exceeds the safety limit.")
            if member.issym() or member.islnk():
                raise ArchiveCodecError(
                    f"Symlink/hardlink archive member rejected: {member.name!r}"
                )
            if not (member.isfile() or member.isdir()):
                raise ArchiveCodecError(f"Special archive member rejected: {member.name!r}")
            if member.isfile():
                if member.size < 0 or member.size > max_member_bytes:
                    raise ArchiveCodecError(f"Archive member exceeds size limit: {member.name!r}")
                total_bytes += member.size
                if total_bytes > max_total_bytes:
                    raise ArchiveCodecError("Archive total size exceeds the safety limit.")
            relative = _safe_relative_name(member.name)
            if len(relative.parts) == 0:
                continue
            if relative in seen:
                raise ArchiveCodecError(f"Duplicate archive member rejected: {member.name!r}")
            seen.add(relative)
            if relative.parts[0] != "blobs" and relative.parts[0] not in EXPECTED_LAYOUT_MEMBERS:
                raise ArchiveCodecError(f"Unexpected archive member rejected: {member.name!r}")
            target = (destination / relative).resolve()
            if target != destination and not target.is_relative_to(destination):
                raise ArchiveCodecError(f"Archive member escapes destination: {member.name!r}")
            if member.isdir():
                target.mkdir(parents=True, exist_ok=True)
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            source = tar.extractfile(member)
            if source is None:
                raise ArchiveCodecError(f"Could not read archive member: {member.name!r}")
            with open(target, "xb") as output:
                shutil.copyfileobj(source, output, length=1024 * 1024)
            extracted += 1

    return extracted


def _zstandard_import() -> Any | None:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        return None
    return zstandard


def zstd_available() -> bool:
    """Return whether a real zstd Python capability is installed."""

    return _zstandard_import() is not None


def available_codecs() -> list[str]:
    """List codecs that can actually be used in this environment."""

    codecs = [ArchiveFormat.TAR]
    if zstd_available():
        codecs.append(ArchiveFormat.TAR_ZSTD)
    return codecs


class ImageCodec:
    """Portability codec interface for OCI layout archives."""

    format_name = ArchiveFormat.TAR
    extension = ".tar"

    def pack(self, layout_root: Path, archive_path: Path) -> str:
        """Create an archive containing the OCI layout and return its sha256 digest."""

        return self._write_archive(layout_root, archive_path)

    def unpack(self, archive_path: Path, destination: Path) -> int:
        """Extract a validated archive and return the extracted file count."""

        return self._read_archive(archive_path, destination)

    def _write_archive(self, layout_root: Path, archive_path: Path) -> str:
        raise NotImplementedError

    def _read_archive(self, archive_path: Path, destination: Path) -> int:
        raise NotImplementedError


class TarCodec(ImageCodec):
    """Uncompressed tar codec. Never labeled as tar.zst."""

    format_name = ArchiveFormat.TAR
    extension = ".tar"

    def _write_archive(self, layout_root: Path, archive_path: Path) -> str:
        with tarfile.open(archive_path, "w", format=tarfile.PAX_FORMAT) as tar:
            tar.add(layout_root, arcname="")
        return _file_digest(archive_path)

    def _read_archive(self, archive_path: Path, destination: Path) -> int:
        return safe_extract_tar(archive_path, destination)


class ZstdTarCodec(ImageCodec):
    """Real tar.zst codec, available only when zstandard is installed."""

    format_name = ArchiveFormat.TAR_ZSTD
    extension = ".tar.zst"

    def pack(self, layout_root: Path, archive_path: Path) -> str:
        with open(archive_path, "wb") as output:
            compressor = _zstd_compressor()
            with compressor.stream_writer(output) as writer:
                with tarfile.open(fileobj=writer, mode="w", format=tarfile.PAX_FORMAT) as tar:
                    tar.add(layout_root, arcname="")
                writer.flush(_zstd_flush_frame())
        return _file_digest(archive_path)

    def unpack(self, archive_path: Path, destination: Path) -> int:
        with open(archive_path, "rb") as source:
            decompressor = _zstd_decompressor()
            with (
                decompressor.stream_reader(source) as reader,
                tarfile.open(fileobj=reader, mode="r|") as tar,
            ):
                return _safe_extract_stream(tar, destination)

    def _write_archive(self, layout_root: Path, archive_path: Path) -> str:
        raise NotImplementedError

    def _read_archive(self, archive_path: Path, destination: Path) -> int:
        raise NotImplementedError


def _require_zstandard():
    module = _zstandard_import()
    if module is None:
        raise CodecUnavailableError(
            "tar.zst requires the zstandard package, which is not installed."
        )
    return module


def _zstd_compressor() -> Any:
    module = _require_zstandard()
    return module.ZstdCompressor(level=3)


def _zstd_decompressor() -> Any:
    module = _require_zstandard()
    return module.ZstdDecompressor()


def _zstd_flush_frame() -> Any:
    module = _require_zstandard()
    return module.FLUSH_FRAME


def _file_digest(path: Path) -> str:
    from zana_core.artifacts.digest import digest_stream

    with path.open("rb") as handle:
        return digest_stream(handle)


def _safe_extract_stream(tar: tarfile.TarFile, destination: Path) -> int:
    destination = Path(destination).resolve()
    destination.mkdir(parents=True, exist_ok=True)
    seen: set[Path] = set()
    total_bytes = 0
    extracted = 0
    for member in tar:
        if len(seen) >= MAX_ARCHIVE_MEMBERS:
            raise ArchiveCodecError("Archive member count exceeds the safety limit.")
        if member.issym() or member.islnk():
            raise ArchiveCodecError(f"Symlink/hardlink archive member rejected: {member.name!r}")
        if not (member.isfile() or member.isdir()):
            raise ArchiveCodecError(f"Special archive member rejected: {member.name!r}")
        if member.isfile():
            if member.size < 0 or member.size > MAX_MEMBER_BYTES:
                raise ArchiveCodecError(f"Archive member exceeds size limit: {member.name!r}")
            total_bytes += member.size
            if total_bytes > MAX_TOTAL_BYTES:
                raise ArchiveCodecError("Archive total size exceeds the safety limit.")
        relative = _safe_relative_name(member.name)
        if relative in seen:
            raise ArchiveCodecError(f"Duplicate archive member rejected: {member.name!r}")
        seen.add(relative)
        if len(relative.parts) == 0:
            continue
        if relative.parts[0] != "blobs" and relative.parts[0] not in EXPECTED_LAYOUT_MEMBERS:
            raise ArchiveCodecError(f"Unexpected archive member rejected: {member.name!r}")
        target = (destination / relative).resolve()
        if target != destination and not target.is_relative_to(destination):
            raise ArchiveCodecError(f"Archive member escapes destination: {member.name!r}")
        if member.isdir():
            target.mkdir(parents=True, exist_ok=True)
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        source = tar.extractfile(member)
        if source is None:
            raise ArchiveCodecError(f"Could not read archive member: {member.name!r}")
        with open(target, "xb") as output:
            shutil.copyfileobj(source, output, length=1024 * 1024)
        extracted += 1
    return extracted
