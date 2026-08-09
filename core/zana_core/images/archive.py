"""Canonical OCI archive codecs, safe extraction, and codec selection.

This is the single canonical archive implementation. Export/import services
must use these codecs and this extraction path; no parallel tar parser or
codec exists elsewhere in the repository.

Every codec and extractor is streaming and bounded: hard immutable maxima,
one-pass member iteration, bounded chunk copies with a shared absolute
deadline, honest size-change detection, deterministic member collection, and
rollback of only the outputs this operation created.
"""

from __future__ import annotations

import gzip
import hashlib
import math
import os
import stat
import tarfile
import time
from contextlib import suppress
from dataclasses import dataclass
from enum import Enum
from pathlib import Path, PurePosixPath
from typing import Any, BinaryIO

MAX_ARCHIVE_MEMBERS = 4096
MAX_MEMBER_BYTES = 512 * 1024 * 1024
MAX_TOTAL_BYTES = 2 * 1024 * 1024 * 1024
MAX_MEMBER_DEPTH = 32
MAX_MEMBER_PATH_CHARS = 1024
MAX_CHUNK_BYTES = 1024 * 1024
DEFAULT_DEADLINE_SECONDS = 300.0
MAX_DEADLINE_SECONDS = 3600.0
MAX_COLLECTOR_ENTRIES = 8192
MAX_MARKER_POLICY_ENTRIES = 64
MAX_MARKER_POLICY_CHARS = 200
_CONCRETE_PATH = type(Path())


def _require_os_support() -> None:
    """Fail closed unless all required path-open primitives exist."""
    for attribute in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY"):
        if not hasattr(os, attribute):
            raise ArchiveCodecError("secure filesystem open is unsupported on this platform")


def _exact_path(value: object, label: str = "path") -> Path:
    if type(value) is not _CONCRETE_PATH:
        raise ArchiveCodecError(f"{label} must be an exact concrete pathlib.Path")
    return value


def _open_archive_parent_dirfd(path: Path) -> tuple[int, str]:
    """Open an exact absolute path's parent via all-component dirfd walk."""
    _require_os_support()
    candidate = _exact_path(path)
    if not candidate.is_absolute():
        raise ArchiveCodecError("Path is not absolute")
    for part in candidate.parts:
        if part in ("", ".", ".."):
            raise ArchiveCodecError("Path contains an unsafe component")
    parent = candidate.parent
    fd = os.open(parent.anchor, _dir_open_flags())
    try:
        for part in parent.parts[1:]:
            try:
                child_fd = os.open(part, _dir_open_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise ArchiveCodecError("Path ancestor could not be opened safely") from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise ArchiveCodecError("Path ancestor could not be opened safely") from error
    return fd, candidate.name


@dataclass(frozen=True)
class CodecLimits:
    """Exact bounded codec limits validated before any attribute access."""

    max_members: int = MAX_ARCHIVE_MEMBERS
    max_member_bytes: int = MAX_MEMBER_BYTES
    max_unpacked_bytes: int = MAX_TOTAL_BYTES
    max_depth: int = MAX_MEMBER_DEPTH
    max_path_chars: int = MAX_MEMBER_PATH_CHARS
    chunk_size: int = MAX_CHUNK_BYTES
    deadline_seconds: float = DEFAULT_DEADLINE_SECONDS

    def __post_init__(self) -> None:
        _int_limit(self.max_members, "max_members", minimum=1, maximum=MAX_COLLECTOR_ENTRIES)
        _int_limit(self.max_member_bytes, "max_member_bytes", minimum=1, maximum=MAX_MEMBER_BYTES)
        _int_limit(
            self.max_unpacked_bytes, "max_unpacked_bytes", minimum=1, maximum=MAX_TOTAL_BYTES
        )
        _int_limit(self.max_depth, "max_depth", minimum=1, maximum=MAX_MEMBER_DEPTH)
        _int_limit(self.max_path_chars, "max_path_chars", minimum=1, maximum=MAX_MEMBER_PATH_CHARS)
        _int_limit(self.chunk_size, "chunk_size", minimum=1, maximum=MAX_CHUNK_BYTES)
        _float_limit(
            self.deadline_seconds, "deadline_seconds", minimum=0.001, maximum=MAX_DEADLINE_SECONDS
        )


EXPECTED_LAYOUT_MEMBERS = frozenset({"oci-layout", "index.json", "manifest.json", "blobs"})
REQUIRED_LAYOUT_FILES = ("oci-layout", "index.json", "manifest.json")


class ArchiveCodecError(ValueError):
    """Raised when an archive cannot be read or extracted safely."""


class CodecUnavailableError(ArchiveCodecError):
    """Raised when a requested codec is not installed in the environment."""


class ArchiveFormat(str, Enum):
    """Named archive format used by export/import codecs."""

    TAR = "tar"
    TAR_GZ = "tar.gz"
    TAR_ZSTD = "tar.zst"


_FORMAT_TO_EXTENSION = {
    ArchiveFormat.TAR: ".tar",
    ArchiveFormat.TAR_GZ: ".tar.gz",
    ArchiveFormat.TAR_ZSTD: ".tar.zst",
}

_EXTENSION_TO_FORMAT = {
    ".tar": ArchiveFormat.TAR,
    ".tgz": ArchiveFormat.TAR_GZ,
    ".tar.gz": ArchiveFormat.TAR_GZ,
    ".tzst": ArchiveFormat.TAR_ZSTD,
    ".tar.zst": ArchiveFormat.TAR_ZSTD,
}


def _validate_limit(
    value: Any,
    name: str,
    *,
    minimum: int | float,
    maximum: int | float,
    allow_float: bool = False,
) -> int | float:
    if value is None:
        raise ArchiveCodecError(f"{name} must be finite and bounded, not None")
    if type(value) is bool:
        raise ArchiveCodecError(f"{name} must be a finite number")
    if not allow_float:
        if type(value) is not int:
            raise ArchiveCodecError(f"{name} must be an exact integer")
    elif type(value) not in (int, float):
        raise ArchiveCodecError(f"{name} must be a finite number")
    if type(value) is float and not math.isfinite(value):
        raise ArchiveCodecError(f"{name} must be finite")
    if value < minimum:
        raise ArchiveCodecError(f"{name} must be at least {minimum}")
    if value > maximum:
        raise ArchiveCodecError(f"{name} must be at most {maximum}")
    return value


def _int_limit(
    value: Any,
    name: str,
    *,
    minimum: int,
    maximum: int,
) -> int:
    return int(_validate_limit(value, name, minimum=minimum, maximum=maximum))


def _float_limit(
    value: Any,
    name: str,
    *,
    minimum: float,
    maximum: float,
) -> float:
    return float(_validate_limit(value, name, minimum=minimum, maximum=maximum, allow_float=True))


def _deadline_value(deadline: Any, default: float = DEFAULT_DEADLINE_SECONDS) -> float:
    return _float_limit(
        default if deadline is None else deadline,
        "deadline",
        minimum=0.001,
        maximum=MAX_DEADLINE_SECONDS,
    )


def _check_deadline(start: float, deadline: float) -> None:
    if time.monotonic() - start > deadline:
        raise ArchiveCodecError("Archive operation exceeded the deadline.")


def _reject_symlink_components(path: Path) -> None:
    """Reject any symlink component in an absolute path, including the root."""
    candidate = _exact_path(path)
    if not candidate.is_absolute():
        raise ArchiveCodecError("Path is not absolute")
    parent_fd, name = _open_archive_parent_dirfd(candidate)
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise ArchiveCodecError("Path contains a symlink component")
    finally:
        os.close(parent_fd)


def _safe_relative_name(name: str) -> Path:
    """Validate raw POSIX segments before any path normalization."""
    if type(name) is not str:
        raise ArchiveCodecError("Archive member name must be a string")
    if name == "":
        raise ArchiveCodecError("Archive member name must not be empty")
    if name.startswith("/") or name.startswith("\\") or "\x00" in name:
        raise ArchiveCodecError("Unsafe archive member name")
    try:
        encoded = name.encode("utf-8", "strict")
    except UnicodeEncodeError:
        raise ArchiveCodecError("Archive member name is not valid UTF-8") from None
    if any(byte == 0 for byte in encoded):
        raise ArchiveCodecError("Unsafe archive member name")
    if name.endswith("/") or name.startswith("./"):
        raise ArchiveCodecError("Traversal archive member rejected")
    raw_parts = name.split("/")
    for part in raw_parts:
        if part == "" or part == "." or part == "..":
            raise ArchiveCodecError("Traversal archive member rejected")
        if part.startswith("\\") or part.endswith("\\") or "\\" in part:
            raise ArchiveCodecError("Unsafe archive member name")
        if any(ord(char) < 0x20 or ord(char) == 0x7F for char in part):
            raise ArchiveCodecError("Unsafe archive member name")
    candidate = PurePosixPath(name)
    if ".." in candidate.parts or "." in candidate.parts or "" in candidate.parts:
        raise ArchiveCodecError("Traversal archive member rejected")
    if candidate.is_absolute():
        raise ArchiveCodecError("Absolute archive member rejected")
    return Path(*candidate.parts)


def _limit_value(limits: Any | None, name: str, default: int | float) -> Any:
    if limits is None:
        return default
    if type(limits) is not CodecLimits:
        raise ArchiveCodecError("codec limits must be exact CodecLimits or None")
    raw = limits.__dict__
    if type(raw) is not dict or name not in raw:
        raise ArchiveCodecError("codec limits are malformed")
    value = raw[name]
    return default if value is None else value


class _BoundedReader:
    """Deadline/bounds-aware reader for tarfile member payloads."""

    def __init__(
        self,
        handle: BinaryIO,
        *,
        chunk_size: int,
        max_bytes: int,
        max_total_bytes: int,
        total_so_far: int,
        start: float,
        deadline: float,
        expected_size: int,
    ) -> None:
        self._handle = handle
        self._chunk_size = chunk_size
        self._max_bytes = max_bytes
        self._max_total_bytes = max_total_bytes
        self._total_so_far = total_so_far
        self._start = start
        self._deadline = deadline
        self._expected_size = expected_size
        self.bytes_read = 0

    def read(self, size: int = -1) -> bytes:
        _check_deadline(self._start, self._deadline)
        request = self._chunk_size if size < 0 else min(size, self._chunk_size)
        if request > self._max_bytes - self.bytes_read:
            raise ArchiveCodecError("Archive member exceeds the byte limit")
        chunk = self._handle.read(request)
        self.bytes_read += len(chunk)
        if self.bytes_read > self._expected_size:
            raise ArchiveCodecError("Archive member changed size (overlong read)")
        if self.bytes_read > self._max_bytes:
            raise ArchiveCodecError("Archive member exceeds the byte limit")
        if self._total_so_far + self.bytes_read > self._max_total_bytes:
            raise ArchiveCodecError("Archive total size exceeds the safety limit")
        return chunk


class _HashingWriter:
    """Forwards writes, rejects short writes, and hashes bytes actually written."""

    def __init__(self, raw: BinaryIO) -> None:
        self._raw = raw
        self._hasher = hashlib.sha256()
        self.bytes_written = 0

    def write(self, data: bytes) -> int:
        written = self._raw.write(data)
        if written != len(data):
            raise OSError("short write while building archive")
        self._hasher.update(data)
        self.bytes_written += written
        return written

    def flush(self) -> None:
        self._raw.flush()

    def tell(self) -> int:
        return self._raw.tell()

    def digest(self) -> str:
        return f"sha256:{self._hasher.hexdigest()}"


class _CreatedTracker:
    """Tracks directories/files this extraction created for rollback."""

    def __init__(self, destination: Path) -> None:
        self.destination = destination
        self._created_files: list[Path] = []
        self._created_dirs: list[Path] = []

    def created_file(self, path: Path) -> None:
        self._created_files.append(path)

    def created_dir(self, path: Path) -> None:
        if path not in self._created_dirs:
            self._created_dirs.append(path)

    def rollback(self) -> None:
        for path in reversed(self._created_files):
            _remove_quietly(path)
        for path in reversed(self._created_dirs):
            with suppress(OSError):
                path.rmdir()


def _open_created(target: Path) -> BinaryIO:
    """Open a target for exclusive creation; never deletes pre-existing data."""
    try:
        return open(target, "xb")
    except FileExistsError:
        raise ArchiveCodecError("Archive member collides with an existing file") from None


def _open_archive_nofollow(path: Path) -> int:
    _require_os_support()
    parent_fd, name = _open_archive_parent_dirfd(path)
    try:
        try:
            fd = os.open(name, _read_open_flags(), dir_fd=parent_fd)
        except OSError as error:
            raise ArchiveCodecError("Archive could not be opened safely") from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise ArchiveCodecError("Archive is not a regular file")
            return fd
        except Exception:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def _secure_mkdir_dir(path: Path, *, mode: int = 0o700) -> Path:
    """Create a directory with nofollow dirfd operations and parent fsync."""
    _require_os_support()
    if type(mode) is not int or not 0 <= mode <= 0o7777:
        raise ArchiveCodecError("directory mode must be an exact bounded integer")
    target = _exact_path(path, "extraction destination")
    if not target.is_absolute():
        raise ArchiveCodecError("Extraction destination is not absolute")
    for part in target.parts:
        if part in ("", ".", ".."):
            raise ArchiveCodecError("Extraction destination contains an unsafe component")
    fd = os.open(target.anchor, _dir_open_flags())
    try:
        for part in target.parts[1:]:
            try:
                child_fd = os.open(part, _dir_open_flags(), dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=fd)
                os.chmod(part, mode, dir_fd=fd, follow_symlinks=False)
                os.fsync(fd)
                child_fd = os.open(part, _dir_open_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise ArchiveCodecError("Extraction destination could not be created") from error
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise ArchiveCodecError("Extraction destination is not a real directory")
            os.close(fd)
            fd = child_fd
    finally:
        os.close(fd)
    return target


def _write_fd_full(fd: int, data: bytes) -> None:
    if type(fd) is not int or type(data) is not bytes:
        raise ArchiveCodecError("write requires an exact integer fd and bytes")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if type(written) is not int or not 1 <= written <= len(view):
            raise ArchiveCodecError("Short write while extracting archive member")
        view = view[written:]


def _dir_fd_for(fd: int, parts: tuple[str, ...]) -> int:
    """Open an existing child directory by nofollow dirfd path."""
    current = fd
    for part in parts:
        flags = _dir_open_flags()
        try:
            current = os.open(part, flags, dir_fd=current)
        except OSError as error:
            raise ArchiveCodecError("Archive member directory is unsafe") from error
    return current


def _copy_bounded(
    reader: Any,
    writer: BinaryIO,
    *,
    chunk_size: int,
    max_member_bytes: int,
    max_total_bytes: int,
    total_so_far: int,
    start: float,
    deadline: float,
) -> int:
    """Copy with bounded chunks, deadline checks, and byte enforcement."""
    copied = 0
    while True:
        _check_deadline(start, deadline)
        chunk = reader.read(chunk_size)
        if not chunk:
            break
        copied += len(chunk)
        if copied > max_member_bytes:
            raise ArchiveCodecError("Archive member exceeds the byte limit")
        if total_so_far + copied > max_total_bytes:
            raise ArchiveCodecError("Archive total size exceeds the safety limit")
        writer.write(chunk)
    return copied


def _extract_members(
    tar: tarfile.TarFile,
    destination: Path,
    *,
    destination_fd: int,
    max_members: int,
    max_member_bytes: int,
    max_total_bytes: int,
    max_depth: int,
    max_path_chars: int,
    chunk_size: int,
    start: float,
    deadline: float,
) -> int:
    """One canonical member extractor used by every codec."""
    seen: set[tuple[str, ...]] = set()
    total_bytes = 0
    extracted = 0
    member_count = 0
    created_files: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
    created_dirs: list[tuple[str, ...]] = []
    dir_fds: dict[tuple[str, ...], int] = {(): destination_fd}
    opened_dir_fds: list[int] = []
    try:
        for member in tar:
            member_count += 1
            _check_deadline(start, deadline)
            if member_count > max_members:
                raise ArchiveCodecError("Archive member count exceeds the safety limit")
            if member.issym() or member.islnk():
                raise ArchiveCodecError("Symlink/hardlink archive member rejected")
            if not (member.isfile() or member.isdir()):
                raise ArchiveCodecError("Special archive member rejected")
            relative = _safe_relative_name(member.name)
            if len(member.name) > max_path_chars:
                raise ArchiveCodecError("Archive member path exceeds the character limit")
            if len(relative.parts) > max_depth:
                raise ArchiveCodecError("Archive member depth exceeds the safety limit")
            if len(relative.parts) == 0:
                continue
            if relative.parts in seen:
                raise ArchiveCodecError("Duplicate archive member rejected")
            seen.add(relative.parts)
            if relative.parts[0] != "blobs" and relative.parts[0] not in EXPECTED_LAYOUT_MEMBERS:
                raise ArchiveCodecError("Unexpected archive member rejected")
            if member.isdir():
                _ensure_extract_dir(
                    relative.parts,
                    dir_fds=dir_fds,
                    created_dirs=created_dirs,
                    opened_dir_fds=opened_dir_fds,
                )
                continue
            if member.size < 0 or member.size > max_member_bytes:
                raise ArchiveCodecError("Archive member exceeds size limit")
            parent_parts = relative.parts[:-1]
            _ensure_extract_dir(
                parent_parts,
                dir_fds=dir_fds,
                created_dirs=created_dirs,
                opened_dir_fds=opened_dir_fds,
            )
            parent_fd = dir_fds[parent_parts]
            source = tar.extractfile(member)
            if source is None:
                raise ArchiveCodecError("Could not read archive member")
            try:
                output_fd = os.open(
                    relative.parts[-1],
                    _exclusive_write_flags(),
                    0o600,
                    dir_fd=parent_fd,
                )
            except FileExistsError:
                raise ArchiveCodecError("Archive member collides with an existing file") from None
            created_files.append((relative.parts, parent_parts))
            copied = 0
            try:
                while True:
                    _check_deadline(start, deadline)
                    chunk = source.read(chunk_size)
                    if not chunk:
                        break
                    copied += len(chunk)
                    if copied > max_member_bytes:
                        raise ArchiveCodecError("Archive member exceeds the byte limit")
                    if total_bytes + copied > max_total_bytes:
                        raise ArchiveCodecError("Archive total size exceeds the safety limit")
                    _write_fd_full(output_fd, chunk)
                os.fsync(output_fd)
            finally:
                os.close(output_fd)
            if copied != member.size:
                raise ArchiveCodecError(
                    "Archive member content size mismatch (truncated or changed)"
                )
            total_bytes += copied
            extracted += 1
        for parts in list(created_dirs) + [()]:
            os.fsync(dir_fds[parts])
        return extracted
    except Exception:
        for parts, parent_parts in reversed(created_files):
            with suppress(OSError):
                os.unlink(parts[-1], dir_fd=dir_fds[parent_parts])
        for parts in reversed(created_dirs):
            with suppress(OSError):
                os.rmdir(parts[-1], dir_fd=dir_fds[parts[:-1]])
        raise
    finally:
        for opened_fd in opened_dir_fds:
            with suppress(OSError):
                os.close(opened_fd)


def _ensure_extract_dir(
    parts: tuple[str, ...],
    *,
    dir_fds: dict[tuple[str, ...], int],
    created_dirs: list[tuple[str, ...]],
    opened_dir_fds: list[int],
) -> None:
    """Open/ensure one extraction directory under the root dirfd."""
    if parts in dir_fds:
        return
    parent_parts = parts[:-1]
    _ensure_extract_dir(
        parent_parts,
        dir_fds=dir_fds,
        created_dirs=created_dirs,
        opened_dir_fds=opened_dir_fds,
    )
    parent_fd = dir_fds[parent_parts]
    name = parts[-1]
    try:
        info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    except FileNotFoundError:
        os.mkdir(name, 0o700, dir_fd=parent_fd)
        created_dirs.append(parts)
    else:
        if not stat.S_ISDIR(info.st_mode):
            raise ArchiveCodecError("Archive member collides with an existing file")
    child_fd = os.open(
        name,
        _dir_open_flags(),
        dir_fd=parent_fd,
    )
    dir_fds[parts] = child_fd
    opened_dir_fds.append(child_fd)


def safe_extract_tar(
    archive_path: Path,
    destination: Path,
    *,
    max_members: int = MAX_ARCHIVE_MEMBERS,
    max_member_bytes: int = MAX_MEMBER_BYTES,
    max_total_bytes: int = MAX_TOTAL_BYTES,
    max_depth: int = MAX_MEMBER_DEPTH,
    max_path_chars: int = MAX_MEMBER_PATH_CHARS,
    chunk_size: int = MAX_CHUNK_BYTES,
    deadline: float | None = DEFAULT_DEADLINE_SECONDS,
) -> int:
    """Extract a tar archive under ``destination`` with strict safety limits."""
    member_cap = _int_limit(max_members, "max_members", minimum=1, maximum=MAX_COLLECTOR_ENTRIES)
    per_member = _int_limit(
        max_member_bytes, "max_member_bytes", minimum=1, maximum=MAX_MEMBER_BYTES
    )
    total_cap = _int_limit(max_total_bytes, "max_total_bytes", minimum=1, maximum=MAX_TOTAL_BYTES)
    depth_cap = _int_limit(max_depth, "max_depth", minimum=1, maximum=MAX_MEMBER_DEPTH)
    path_cap = _int_limit(
        max_path_chars, "max_path_chars", minimum=1, maximum=MAX_MEMBER_PATH_CHARS
    )
    chunk = _int_limit(chunk_size, "chunk_size", minimum=1, maximum=MAX_CHUNK_BYTES)
    effective_deadline = _deadline_value(deadline)
    _require_os_support()
    archive_path = _exact_path(archive_path, "archive")
    destination = _exact_path(destination, "destination")
    _reject_symlink_components(archive_path)
    _reject_symlink_components(destination)

    destination = _secure_mkdir_dir(destination)
    start = time.monotonic()
    archive_fd = _open_archive_nofollow(archive_path)
    destination_fd = os.open(
        destination,
        _dir_open_flags(),
    )
    tar = None
    try:
        source = os.fdopen(archive_fd, "rb")
        archive_fd = -1
        with tarfile.open(fileobj=source, mode="r:*") as tar:
            return _extract_members(
                tar,
                destination,
                destination_fd=destination_fd,
                max_members=member_cap,
                max_member_bytes=per_member,
                max_total_bytes=total_cap,
                max_depth=depth_cap,
                max_path_chars=path_cap,
                chunk_size=chunk,
                start=start,
                deadline=effective_deadline,
            )
    finally:
        if archive_fd != -1:
            os.close(archive_fd)
        if tar is not None:
            tar.close()
        os.close(destination_fd)


def _remove_quietly(path: Path) -> None:
    try:
        parent_fd, name = _open_archive_parent_dirfd(path)
    except ArchiveCodecError:
        return
    try:
        with suppress(OSError):
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def _open_exclusive_nofollow(path: Path) -> BinaryIO:
    """Open a path for exclusive write with O_NOFOLLOW where available."""
    _require_os_support()
    parent_fd, name = _open_archive_parent_dirfd(path)
    try:
        try:
            fd = os.open(name, _exclusive_write_flags(), 0o600, dir_fd=parent_fd)
        except FileExistsError:
            raise ArchiveCodecError("Output path already exists") from None
        return os.fdopen(fd, "wb")
    finally:
        os.close(parent_fd)


def _zstandard_import() -> Any | None:
    try:
        import zstandard  # type: ignore[import-not-found]
    except ImportError:
        return None
    return zstandard


def zstd_available() -> bool:
    """Return whether a real zstd Python capability is installed."""
    return _zstandard_import() is not None


def available_codecs() -> list[ArchiveFormat]:
    """List codecs that can actually be used in this environment."""
    codecs = [ArchiveFormat.TAR, ArchiveFormat.TAR_GZ]
    if zstd_available():
        codecs.append(ArchiveFormat.TAR_ZSTD)
    return codecs


class ImageCodec:
    """Portability codec interface for OCI layout archives."""

    format_name = ArchiveFormat.TAR
    extension = ".tar"

    def pack(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        """Create an archive containing the OCI layout and return its sha256 digest."""
        return self._write_archive(layout_root, archive_path, limits=limits, deadline=deadline)

    def unpack(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        """Extract a validated archive and return the extracted file count."""
        return self._read_archive(archive_path, destination, limits=limits, deadline=deadline)

    def _write_archive(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        raise NotImplementedError

    def _read_archive(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        raise NotImplementedError


def _codec_extract_args(limits: Any | None, deadline: Any) -> dict[str, Any]:
    return {
        "max_members": _int_limit(
            _limit_value(limits, "max_members", MAX_ARCHIVE_MEMBERS),
            "max_members",
            minimum=1,
            maximum=MAX_COLLECTOR_ENTRIES,
        ),
        "max_member_bytes": _int_limit(
            _limit_value(limits, "max_member_bytes", MAX_MEMBER_BYTES),
            "max_member_bytes",
            minimum=1,
            maximum=MAX_MEMBER_BYTES,
        ),
        "max_total_bytes": _int_limit(
            _limit_value(limits, "max_unpacked_bytes", MAX_TOTAL_BYTES),
            "max_unpacked_bytes",
            minimum=1,
            maximum=MAX_TOTAL_BYTES,
        ),
        "max_depth": _int_limit(
            _limit_value(limits, "max_depth", MAX_MEMBER_DEPTH),
            "max_depth",
            minimum=1,
            maximum=MAX_MEMBER_DEPTH,
        ),
        "max_path_chars": _int_limit(
            _limit_value(limits, "max_path_chars", MAX_MEMBER_PATH_CHARS),
            "max_path_chars",
            minimum=1,
            maximum=MAX_MEMBER_PATH_CHARS,
        ),
        "chunk_size": _int_limit(
            _limit_value(limits, "chunk_size", MAX_CHUNK_BYTES),
            "chunk_size",
            minimum=1,
            maximum=MAX_CHUNK_BYTES,
        ),
        "deadline": _deadline_value(
            deadline, float(_limit_value(limits, "deadline_seconds", DEFAULT_DEADLINE_SECONDS))
        ),
    }


class TarCodec(ImageCodec):
    """Deterministic uncompressed tar codec. Never labeled as tar.zst."""

    format_name = ArchiveFormat.TAR
    extension = ".tar"

    def _write_archive(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        return _write_deterministic_tar(
            layout_root,
            archive_path,
            limits=limits,
            deadline=deadline,
        )

    def _read_archive(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        args = _codec_extract_args(limits, deadline)
        return safe_extract_tar(archive_path, destination, **args)


class GzipTarCodec(ImageCodec):
    """Deterministic gzip-compressed tar codec (stdlib only)."""

    format_name = ArchiveFormat.TAR_GZ
    extension = ".tar.gz"

    def _write_archive(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        effective_deadline = _deadline_value(
            deadline, float(_limit_value(limits, "deadline_seconds", DEFAULT_DEADLINE_SECONDS))
        )
        archive_path = _exact_path(archive_path, "archive")
        _reject_symlink_components(archive_path)
        with _open_exclusive_nofollow(archive_path) as raw:
            start = time.monotonic()
            hashing = _HashingWriter(raw)
            with gzip.GzipFile(fileobj=hashing, mode="wb", mtime=0, filename="") as gz:
                _write_deterministic_tar(
                    layout_root,
                    archive_path,
                    fileobj=gz,
                    limits=limits,
                    deadline=effective_deadline,
                    hashing_writer=hashing,
                )
            raw.flush()
            os.fsync(raw.fileno())
        _check_deadline(start, effective_deadline)
        _fsync_directory(archive_path.parent)
        return hashing.digest()

    def _read_archive(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        args = _codec_extract_args(limits, deadline)
        return safe_extract_tar(archive_path, destination, **args)


class ZstdTarCodec(ImageCodec):
    """Real tar.zst codec, available only when zstandard is installed."""

    format_name = ArchiveFormat.TAR_ZSTD
    extension = ".tar.zst"

    def pack(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        module = _require_zstandard()
        effective_deadline = _deadline_value(
            deadline, float(_limit_value(limits, "deadline_seconds", DEFAULT_DEADLINE_SECONDS))
        )
        archive_path = _exact_path(archive_path, "archive")
        _reject_symlink_components(archive_path)
        with _open_exclusive_nofollow(archive_path) as raw:
            start = time.monotonic()
            hashing = _HashingWriter(raw)
            compressor = module.ZstdCompressor(level=3)
            with compressor.stream_writer(hashing) as writer:
                _write_deterministic_tar(
                    layout_root,
                    archive_path,
                    fileobj=writer,
                    limits=limits,
                    deadline=effective_deadline,
                    hashing_writer=hashing,
                )
                writer.flush(module.FLUSH_FRAME)
            raw.flush()
            os.fsync(raw.fileno())
        _check_deadline(start, effective_deadline)
        _fsync_directory(archive_path.parent)
        return hashing.digest()

    def unpack(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        module = _require_zstandard()
        args = _codec_extract_args(limits, deadline)
        effective_deadline = args.pop("deadline")
        _require_os_support()
        archive_path = _exact_path(archive_path, "archive")
        destination = _exact_path(destination, "destination")
        _reject_symlink_components(archive_path)
        _reject_symlink_components(destination)
        destination = _secure_mkdir_dir(destination)
        start = time.monotonic()
        archive_fd = _open_archive_nofollow(archive_path)
        destination_fd = os.open(
            destination,
            _dir_open_flags(),
        )
        source = None
        try:
            source = os.fdopen(archive_fd, "rb")
            archive_fd = -1
            decompressor = module.ZstdDecompressor()
            with (
                decompressor.stream_reader(source) as reader,
                tarfile.open(fileobj=reader, mode="r|") as tar,
            ):
                return _extract_members(
                    tar,
                    destination,
                    destination_fd=destination_fd,
                    start=start,
                    deadline=effective_deadline,
                    **args,
                )
        finally:
            if archive_fd != -1:
                os.close(archive_fd)
            if source is not None:
                source.close()
            os.close(destination_fd)

    def _write_archive(
        self,
        layout_root: Path,
        archive_path: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> str:
        return self.pack(layout_root, archive_path, limits=limits, deadline=deadline)

    def _read_archive(
        self,
        archive_path: Path,
        destination: Path,
        *,
        limits: Any | None = None,
        deadline: float | None = None,
    ) -> int:
        return self.unpack(archive_path, destination, limits=limits, deadline=deadline)


def codec_for_format(format_name: ArchiveFormat) -> ImageCodec:
    """Return the canonical codec for a format name; zstd is honest."""
    if format_name == ArchiveFormat.TAR:
        return TarCodec()
    if format_name == ArchiveFormat.TAR_GZ:
        return GzipTarCodec()
    if format_name == ArchiveFormat.TAR_ZSTD:
        return ZstdTarCodec()
    raise ArchiveCodecError(f"Unsupported archive format: {format_name!r}")


def codec_for_extension(extension: str) -> ImageCodec | None:
    """Return the canonical codec matching an archive extension, if any."""
    normalized = extension.lower()
    format_name = _EXTENSION_TO_FORMAT.get(normalized)
    if format_name is None:
        return None
    return codec_for_format(format_name)


def extension_for_format(format_name: ArchiveFormat) -> str:
    """Return the canonical extension for a format name."""
    try:
        return _FORMAT_TO_EXTENSION[format_name]
    except KeyError:
        raise ArchiveCodecError(f"Unsupported archive format: {format_name!r}") from None


def collect_bounded_layout_entries(
    directory: Path,
    *,
    remaining_budget: int,
    label: str = "layout",
) -> list[Path]:
    """Return at most ``remaining_budget`` regular files from ``directory``."""
    budget = _int_limit(
        remaining_budget, "remaining_budget", minimum=0, maximum=MAX_COLLECTOR_ENTRIES
    )
    _require_os_support()
    directory = _exact_path(directory, "layout directory")
    _reject_symlink_components(directory)
    try:
        fd = os.open(
            directory,
            _dir_open_flags(),
        )
    except FileNotFoundError:
        return []
    except OSError as error:
        raise ArchiveCodecError(f"{label} directory is unsafe") from error
    regular: list[Path] = []
    try:
        with os.scandir(fd) as entries:
            for total_entries, entry in enumerate(entries, start=1):
                if total_entries > budget:
                    raise ArchiveCodecError(f"{label} member count exceeds the safety limit")
                info = entry.stat(follow_symlinks=False)
                if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
                    raise ArchiveCodecError(f"{label} contains an unexpected entry")
                regular.append(directory / entry.name)
                if len(regular) > budget:
                    raise ArchiveCodecError(f"{label} member count exceeds the safety limit")
    finally:
        os.close(fd)
    return sorted(regular, key=lambda item: item.name)


def walk_bounded_tree(
    root: Path,
    *,
    remaining_budget: int,
    max_depth: int = MAX_MEMBER_DEPTH,
    max_path_chars: int = MAX_MEMBER_PATH_CHARS,
) -> list[tuple[str, Path]]:
    """Walk a tree with bounded total entries, returning sorted regular files."""
    budget = _int_limit(
        remaining_budget, "remaining_budget", minimum=0, maximum=MAX_COLLECTOR_ENTRIES
    )
    depth_cap = _int_limit(max_depth, "max_depth", minimum=1, maximum=MAX_MEMBER_DEPTH)
    path_cap = _int_limit(
        max_path_chars, "max_path_chars", minimum=1, maximum=MAX_MEMBER_PATH_CHARS
    )
    original = _exact_path(root, "layout root")
    _reject_symlink_components(original)
    _require_os_support()
    try:
        root_fd = os.open(
            original,
            _dir_open_flags(),
        )
    except OSError as error:
        raise ArchiveCodecError(f"Layout root is unsafe: {original.name}") from error
    regular: list[tuple[str, Path]] = []
    total_entries = 0
    open_fds: list[int] = [root_fd]
    stack: list[tuple[int, Path, tuple[str, ...], int]] = [(root_fd, original, (), 0)]
    try:
        while stack:
            fd, directory, relative, depth = stack.pop()
            with os.scandir(fd) as entries:
                for entry in entries:
                    total_entries += 1
                    if total_entries > budget:
                        raise ArchiveCodecError("Layout member count exceeds the safety limit")
                    info = entry.stat(follow_symlinks=False)
                    if stat.S_ISLNK(info.st_mode):
                        raise ArchiveCodecError("Layout contains a symlink entry")
                    child_relative = relative + (entry.name,)
                    name = "/".join(child_relative)
                    _safe_relative_name(name)
                    if len(name) > path_cap:
                        raise ArchiveCodecError("Layout member path exceeds the character limit")
                    if stat.S_ISDIR(info.st_mode):
                        if depth + 1 > depth_cap:
                            raise ArchiveCodecError("Layout member depth exceeds the safety limit")
                        child_fd = os.open(
                            entry.name,
                            _dir_open_flags(),
                            dir_fd=fd,
                        )
                        open_fds.append(child_fd)
                        stack.append((child_fd, directory / entry.name, child_relative, depth + 1))
                        continue
                    if not stat.S_ISREG(info.st_mode):
                        raise ArchiveCodecError("Layout contains an unexpected entry")
                    if depth + 1 > depth_cap:
                        raise ArchiveCodecError("Layout member depth exceeds the safety limit")
                    regular.append((name, directory / entry.name))
                    if len(regular) > budget:
                        raise ArchiveCodecError("Layout member count exceeds the safety limit")
    finally:
        for opened_fd in open_fds:
            with suppress(OSError):
                os.close(opened_fd)
    return sorted(regular, key=lambda item: item[0])


def _write_deterministic_tar(
    layout_root: Path,
    archive_path: Path | None = None,
    *,
    fileobj: Any | None = None,
    limits: Any | None = None,
    deadline: float | None = None,
    hashing_writer: _HashingWriter | None = None,
) -> str:
    """Write a deterministic tar with canonical order and normalized metadata."""
    _require_os_support()
    layout_root = _exact_path(layout_root, "layout root")
    root = layout_root
    _reject_symlink_components(layout_root)
    max_members = _int_limit(
        _limit_value(limits, "max_members", MAX_ARCHIVE_MEMBERS),
        "max_members",
        minimum=1,
        maximum=MAX_COLLECTOR_ENTRIES,
    )
    max_member_bytes = _int_limit(
        _limit_value(limits, "max_member_bytes", MAX_MEMBER_BYTES),
        "max_member_bytes",
        minimum=1,
        maximum=MAX_MEMBER_BYTES,
    )
    max_total_bytes = _int_limit(
        _limit_value(limits, "max_unpacked_bytes", MAX_TOTAL_BYTES),
        "max_unpacked_bytes",
        minimum=1,
        maximum=MAX_TOTAL_BYTES,
    )
    chunk_size = _int_limit(
        _limit_value(limits, "chunk_size", MAX_CHUNK_BYTES),
        "chunk_size",
        minimum=1,
        maximum=MAX_CHUNK_BYTES,
    )
    effective_deadline = _deadline_value(
        deadline, float(_limit_value(limits, "deadline_seconds", DEFAULT_DEADLINE_SECONDS))
    )
    files: list[tuple[str, Path]] = []
    for name in REQUIRED_LAYOUT_FILES:
        candidate = root / name
        if candidate.is_symlink() or not candidate.is_file():
            raise ArchiveCodecError(f"Required OCI file is missing or unsafe: {name}")
        files.append((name, candidate))
    blob_dir = root / "blobs" / "sha256"
    if blob_dir.is_symlink() or not blob_dir.is_dir():
        raise ArchiveCodecError("OCI blobs directory is missing or unsafe")
    remaining = max(0, max_members - len(files))
    blobs = collect_bounded_layout_entries(
        blob_dir,
        remaining_budget=remaining,
        label="layout blob",
    )
    files.extend((f"blobs/sha256/{blob.name}", blob) for blob in blobs)
    preflight_total = sum(path.stat().st_size for _, path in files)
    if preflight_total > max_total_bytes:
        raise ArchiveCodecError("Archive total size exceeds the safety limit")

    owns_file = fileobj is None
    if owns_file:
        assert archive_path is not None
        archive_path = _exact_path(archive_path, "archive")
        _reject_symlink_components(archive_path)
        raw = _open_exclusive_nofollow(archive_path)
    else:
        raw = fileobj
    if hashing_writer is None:
        hashing_writer = _HashingWriter(raw)
    start = time.monotonic()
    kwargs: dict[str, Any] = {"mode": "w", "format": tarfile.USTAR_FORMAT}
    kwargs["fileobj"] = hashing_writer if owns_file else fileobj
    try:
        with tarfile.open(**kwargs) as tar:
            running_total = 0
            for relative, path in files:
                _check_deadline(start, effective_deadline)
                info = tarfile.TarInfo(relative)
                info.size = path.stat().st_size
                if info.size > max_member_bytes:
                    raise ArchiveCodecError("Archive member exceeds the byte limit")
                info.mode = 0o644
                info.uid = 0
                info.gid = 0
                info.uname = ""
                info.gname = ""
                info.mtime = 0
                try:
                    source_fd = os.open(path, _read_open_flags())
                except OSError as error:
                    raise ArchiveCodecError("Layout file could not be opened safely") from error
                try:
                    source_info = os.fstat(source_fd)
                    if not stat.S_ISREG(source_info.st_mode):
                        raise ArchiveCodecError("Layout file is not a regular file")
                    if source_info.st_size != info.size:
                        raise ArchiveCodecError("Layout file changed size before write")
                except Exception:
                    os.close(source_fd)
                    raise
                with os.fdopen(source_fd, "rb") as handle:
                    reader = _BoundedReader(
                        handle,
                        chunk_size=chunk_size,
                        max_bytes=max_member_bytes,
                        max_total_bytes=max_total_bytes,
                        total_so_far=running_total,
                        start=start,
                        deadline=effective_deadline,
                        expected_size=info.size,
                    )
                    try:
                        tar.addfile(info, fileobj=reader)
                    except OSError as error:
                        raise ArchiveCodecError(
                            "Archive member could not be written completely"
                        ) from error
                    final_info = os.fstat(handle.fileno())
                    if (final_info.st_dev, final_info.st_ino) != (
                        source_info.st_dev,
                        source_info.st_ino,
                    ) or final_info.st_size != info.size:
                        raise ArchiveCodecError(
                            "Archive member changed identity or size during write"
                        )
                    if reader.bytes_read != info.size:
                        raise ArchiveCodecError(
                            "Archive member changed size during write (short or overlong read)"
                        )
                    running_total += reader.bytes_read
        _check_deadline(start, effective_deadline)
    except Exception:
        if owns_file:
            _remove_quietly(Path(archive_path) if archive_path is not None else Path())
        raise
    finally:
        if owns_file:
            raw.flush()
            os.fsync(raw.fileno())
            raw.close()
    if owns_file and archive_path is not None:
        final_size = archive_path.stat().st_size
        if final_size < hashing_writer.bytes_written:
            _remove_quietly(archive_path)
            raise ArchiveCodecError("Archive write was truncated")
        _fsync_directory(archive_path.parent)
    _check_deadline(start, effective_deadline)
    return hashing_writer.digest()


def _fsync_directory(path: Path) -> None:
    try:
        fd = os.open(path, _dir_open_flags())
    except OSError as error:
        raise ArchiveCodecError("Archive output directory could not be fsynced") from error
    try:
        os.fsync(fd)
    except OSError as error:
        raise ArchiveCodecError("Archive output directory fsync failed") from error
    finally:
        os.close(fd)


def _read_open_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _dir_open_flags() -> int:
    return _read_open_flags() | os.O_DIRECTORY


def _exclusive_write_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _require_zstandard():
    module = _zstandard_import()
    if module is None:
        raise CodecUnavailableError(
            "tar.zst requires the zstandard package, which is not installed."
        )
    return module
