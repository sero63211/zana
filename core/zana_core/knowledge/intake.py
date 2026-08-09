"""Safe approved-path intake: validation, hashing, and immutable copy."""

from __future__ import annotations

import hashlib
import os
import stat as stat_module
from collections.abc import Iterable, Iterator
from contextlib import suppress
from pathlib import Path, PosixPath, WindowsPath
from typing import BinaryIO

from zana_core.knowledge.limits import (
    HARD_MAX_SOURCE_BYTES,
    HARD_MAX_STREAM_CHUNK_SIZE,
    HARD_MAX_TIMEOUT_SECONDS,
    KnowledgeLimits,
    ResourceLimitError,
    check_deadline,
    check_utf8_bytes,
    make_deadline,
    require_strict_int,
    resolve_limits,
    utf8_byte_length,
)
from zana_core.knowledge.models import (
    DocumentKind,
    ParserError,
    SourceMetadata,
    validate_bounded_metadata,
)

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
DEFAULT_MAX_BYTES = 8 * 1024 * 1024
STREAM_CHUNK_SIZE = 1024 * 1024
INTAKE_COPY_DIR = ".zana-intake-copy"
MAX_ROOTS = 16


def _safe_path_value(value: object) -> bool:
    """Return whether a value is an exact builtin string/path primitive."""
    return type(value) is str or type(value) in (Path, PosixPath, WindowsPath)


class IntakeError(Exception):
    """Base class for intake validation failures."""


class UnsupportedTypeError(IntakeError):
    """Raised when a file kind is not supported."""


class UnreadableFileError(IntakeError):
    """Raised when an approved path cannot be read."""


class OversizeFileError(IntakeError):
    """Raised when a file exceeds the configured size limit."""


class PathEscapeError(IntakeError):
    """Raised when a path traverses or resolves outside approved roots."""


def _is_symlink_path(path: Path) -> bool:
    """Return whether an existing component of an absolute path is a symlink."""
    current = path
    while True:
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            parent = current.parent
            if parent == current:
                return False
            current = parent
            continue
        if stat_module.S_ISLNK(mode):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _required_flag(name: str) -> int:
    """Return a required OS flag, failing closed when the primitive is absent."""
    if not hasattr(os, name):
        raise UnreadableFileError(f"Platform primitive {name} is required for safe intake.")
    return getattr(os, name)


def _open_flags(*names: str) -> int:
    result = 0
    for name in names:
        result |= _required_flag(name)
    return result


def _open_root_fd(root: Path, expected_identity: tuple[int, int]) -> int:
    """Open a verified directory fd for a canonical approved root."""
    flags = _open_flags(
        "O_RDONLY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_CLOEXEC",
    )
    try:
        fd = os.open(root, flags)
        st = os.fstat(fd)
    except OSError:
        raise UnreadableFileError("The approved intake root could not be opened safely.") from None
    if not stat_module.S_ISDIR(st.st_mode):
        os.close(fd)
        raise UnreadableFileError("The approved intake root is not a directory.")
    if (st.st_dev, st.st_ino) != expected_identity:
        os.close(fd)
        raise UnreadableFileError("The approved intake root changed identity during intake.")
    return fd


class ApprovedPathResolver:
    """Resolves and validates a path against explicit approved roots."""

    def __init__(
        self,
        roots: tuple[str | Path, ...] | list[str | Path],
        *,
        limits: KnowledgeLimits | None = None,
    ) -> None:
        active = resolve_limits(limits)
        if type(roots) not in (tuple, list):
            raise PathEscapeError("Approved intake roots must be an exact tuple or list.")
        if len(roots) > MAX_ROOTS:
            raise PathEscapeError(f"Approved intake roots exceed the {MAX_ROOTS}-root limit.")
        collected: list[str | Path] = []
        for root in roots:
            if not _safe_path_value(root):
                raise PathEscapeError("Approved intake roots must be exact string or Path values.")
            collected.append(root)
        if not collected:
            raise PathEscapeError("At least one approved intake root is required.")
        resolved: list[Path] = []
        identities: list[tuple[int, int]] = []
        for root in collected:
            try:
                utf8_byte_length(
                    str(root),
                    max_bytes=active.max_path_bytes,
                    label="Intake root",
                )
                raw = Path(root)
                if not raw.is_absolute():
                    raw = Path.cwd() / raw
                if _is_symlink_path(raw):
                    raise PathEscapeError("Approved intake roots must not contain symlinks.")
                try:
                    st = os.lstat(raw)
                except OSError:
                    raise PathEscapeError(
                        "An approved intake root could not be inspected."
                    ) from None
                if not stat_module.S_ISDIR(st.st_mode):
                    raise PathEscapeError("Approved intake roots must be existing directories.")
                candidate = raw.resolve(strict=True)
            except (OSError, ResourceLimitError):
                raise PathEscapeError("An approved intake root could not be validated.") from None
            except PathEscapeError:
                raise
            if candidate == Path(candidate.anchor):
                raise PathEscapeError("Filesystem roots are not approved intake roots.")
            if candidate == Path.home().resolve(strict=False):
                raise PathEscapeError("Home directories are not approved intake roots.")
            if candidate == Path.cwd().resolve(strict=False):
                raise PathEscapeError("The working directory is not an approved intake root.")
            resolved.append(candidate)
            identities.append((st.st_dev, st.st_ino))
        _validate_root_set(resolved)
        self.roots: tuple[Path, ...] = tuple(resolved)
        self.root_identities: tuple[tuple[int, int], ...] = tuple(identities)
        self.limits = active

    def resolve(self, path: str | Path) -> Path:
        if not _safe_path_value(path):
            raise PathEscapeError("Intake paths must be exact string or Path values.")
        raw = Path(path)
        try:
            utf8_byte_length(
                str(raw),
                max_bytes=self.limits.max_path_bytes,
                label="Intake path",
            )
        except ResourceLimitError:
            raise PathEscapeError("The approved path exceeds the configured byte limit.") from None
        if ".." in raw.parts:
            raise PathEscapeError("The path contains traversal components.")
        if INTAKE_COPY_DIR in raw.parts:
            raise PathEscapeError("Intake copies cannot be selected as sources.")
        absolute = raw if raw.is_absolute() else Path.cwd() / raw
        if _is_symlink_path(absolute):
            raise PathEscapeError("Approved intake paths must not contain symlinks.")
        candidate = raw.resolve(strict=False)
        for root in self.roots:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            return candidate
        raise PathEscapeError("The path escapes the approved intake roots.")

    @property
    def root_paths(self) -> list[Path]:
        return list(self.roots)


def _validate_root_set(roots: list[Path]) -> None:
    if len(set(roots)) != len(roots):
        raise PathEscapeError("Duplicate approved intake roots are not allowed.")
    for index, root in enumerate(roots):
        for other in roots[index + 1 :]:
            if root in other.parents or other in root.parents:
                raise PathEscapeError(
                    "Parent-child overlapping approved intake roots are not allowed."
                )


def detect_kind(path: Path) -> DocumentKind:
    if type(path) not in (Path, PosixPath, WindowsPath):
        raise UnsupportedTypeError("Source kind detection requires an exact path.")
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return DocumentKind.MARKDOWN
    if suffix == ".txt":
        return DocumentKind.TEXT
    if suffix == ".pdf":
        return DocumentKind.PDF
    return DocumentKind.UNSUPPORTED


def digest_stream(
    stream: BinaryIO,
    chunk_size: int = STREAM_CHUNK_SIZE,
    *,
    max_bytes: int | None = None,
    deadline_seconds: float | None = None,
) -> str:
    """SHA-256 a stream with bounded memory, chunk and byte caps.

    ``max_bytes=None`` still applies the finite hard source cap; the stream is
    never byte-unbounded.  Reads must return exact bytes or the operation
    fails with a generic typed error.
    """
    validated_chunk = require_strict_int(chunk_size, label="Stream chunk size")
    if validated_chunk < 1 or validated_chunk > HARD_MAX_STREAM_CHUNK_SIZE:
        raise ResourceLimitError(
            f"Stream chunk size must be between 1 and {HARD_MAX_STREAM_CHUNK_SIZE} bytes."
        )
    byte_limit = (
        HARD_MAX_SOURCE_BYTES
        if max_bytes is None
        else require_strict_int(max_bytes, label="Stream byte cap")
    )
    if byte_limit > HARD_MAX_SOURCE_BYTES:
        raise ResourceLimitError("Stream byte cap exceeds the hard source byte limit.")
    deadline = make_deadline(deadline_seconds, hard_max=HARD_MAX_TIMEOUT_SECONDS)
    hasher = hashlib.sha256()
    counted = 0
    while True:
        check_deadline(deadline, label="intake hashing")
        try:
            chunk = stream.read(validated_chunk)
        except Exception:
            raise UnreadableFileError("The stream could not be read safely.") from None
        if type(chunk) is not bytes:
            raise UnreadableFileError("The stream returned non-byte data.")
        if not chunk:
            break
        counted += len(chunk)
        if counted > byte_limit:
            raise OversizeFileError("Stream exceeded the configured byte limit.")
        hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def _open_verified_regular(path: Path) -> tuple[int, os.stat_result]:
    """Open a regular file no-follow and return its fd and verified stat."""
    flags = _open_flags(
        "O_RDONLY",
        "O_NOFOLLOW",
        "O_CLOEXEC",
    )
    try:
        fd = os.open(path, flags)
        st = os.fstat(fd)
    except OSError:
        raise UnreadableFileError("The approved path could not be opened safely.") from None
    if not stat_module.S_ISREG(st.st_mode):
        os.close(fd)
        raise UnreadableFileError("The approved path is not a regular file.")
    return fd, st


def _open_relative_verified(
    root_fd: int,
    root: Path,
    candidate: Path,
) -> tuple[int, os.stat_result]:
    """Walk from a verified root dirfd to the source without path-based opens."""
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        raise UnreadableFileError("Source path escapes the approved intake root.") from None
    parts = relative.parts
    if not parts or any(part == ".." or part == INTAKE_COPY_DIR for part in parts):
        raise UnreadableFileError("Source path contains unsafe components.")
    current_fd = root_fd
    try:
        for index, part in enumerate(parts):
            is_leaf = index == len(parts) - 1
            if is_leaf:
                flags = _open_flags("O_RDONLY", "O_NOFOLLOW", "O_CLOEXEC")
                fd = os.open(part, flags, dir_fd=current_fd)
                st = os.fstat(fd)
                if not stat_module.S_ISREG(st.st_mode):
                    os.close(fd)
                    raise UnreadableFileError("Source path is not a regular file.")
                return fd, st
            flags = _open_flags(
                "O_RDONLY",
                "O_DIRECTORY",
                "O_NOFOLLOW",
                "O_CLOEXEC",
            )
            fd = os.open(part, flags, dir_fd=current_fd)
            st = os.fstat(fd)
            if not stat_module.S_ISDIR(st.st_mode):
                os.close(fd)
                raise UnreadableFileError("Source path contains a non-directory component.")
            if current_fd != root_fd:
                os.close(current_fd)
            current_fd = fd
    except UnreadableFileError:
        if current_fd != root_fd:
            with suppress(OSError):
                os.close(current_fd)
        raise
    except TypeError:
        if current_fd != root_fd:
            with suppress(OSError):
                os.close(current_fd)
        raise UnreadableFileError(
            "Directory-fd primitives are required for safe source intake."
        ) from None
    except OSError:
        if current_fd != root_fd:
            with suppress(OSError):
                os.close(current_fd)
        raise UnreadableFileError("The source path could not be opened safely.") from None
    raise UnreadableFileError("Source path could not be resolved safely.")


def check_readable(path: str | Path) -> None:
    """Validate a regular non-symlink file through a no-follow open."""
    fd, _ = _open_absolute_verified(path)
    os.close(fd)


def check_size(path: str | Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    validated = require_strict_int(max_bytes, label="Intake size limit")
    if validated < 1 or validated > HARD_MAX_SOURCE_BYTES:
        raise ResourceLimitError("The configured size limit is outside the hard byte cap.")
    fd, st = _open_absolute_verified(path)
    try:
        size = st.st_size
    finally:
        os.close(fd)
    if size > validated:
        raise OversizeFileError("File exceeds the configured intake limit.")


def _open_absolute_verified(path: str | Path) -> tuple[int, os.stat_result]:
    """Open an absolute regular file through an anchored component walk."""
    if not _safe_path_value(path):
        raise UnreadableFileError("Intake paths must be exact string or Path values.")
    raw = Path(path)
    if not raw.is_absolute():
        raw = Path.cwd() / raw
    anchor = Path(raw.anchor)
    root_fd = _open_root_fd(anchor, (os.lstat(anchor).st_dev, os.lstat(anchor).st_ino))
    try:
        return _open_relative_verified(root_fd, anchor, raw)
    finally:
        with suppress(OSError):
            os.close(root_fd)


def prepare_source(
    path: str | Path,
    *,
    approved_root: str | Path,
    max_bytes: int | None = None,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
) -> SourceMetadata:
    """Validate, hash, and copy one approved file into an immutable intake copy."""
    active = resolve_limits(limits)
    deadline = make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    return _prepare_source_absolute(
        path,
        approved_root=approved_root,
        max_bytes=max_bytes,
        limits=active,
        deadline=deadline,
    )


def prepare_sources(
    paths: Iterable[str | Path],
    *,
    approved_root: str | Path,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
) -> Iterator[SourceMetadata]:
    """Prepare approved sources one at a time under one shared absolute deadline.

    Copies already yielded to the caller are caller-owned; a later failure
    does not remove them, so there is no false atomicity claim.
    """
    active = resolve_limits(limits)
    deadline = make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    try:
        path_iter = iter(paths)
    except Exception:
        raise UnreadableFileError("Source paths could not be iterated safely.") from None
    count = 0
    while True:
        try:
            path = next(path_iter)
        except StopIteration:
            break
        except Exception:
            raise UnreadableFileError("Source paths could not be iterated safely.") from None
        count += 1
        check_deadline(deadline, label="source intake")
        if count > active.max_source_count:
            raise ResourceLimitError(
                f"Source intake exceeds the {active.max_source_count}-source limit."
            )
        yield _prepare_source_absolute(
            path,
            approved_root=approved_root,
            max_bytes=None,
            limits=active,
            deadline=deadline,
        )


def _prepare_source_absolute(
    path: str | Path,
    *,
    approved_root: str | Path,
    max_bytes: int | None,
    limits: KnowledgeLimits,
    deadline: float,
) -> SourceMetadata:
    byte_limit = limits.max_source_bytes
    if max_bytes is not None:
        byte_limit = require_strict_int(max_bytes, label="max_bytes")
        if byte_limit > limits.max_source_bytes:
            raise ResourceLimitError("max_bytes must be within the configured source size limit.")
    check_deadline(deadline, label="intake")
    resolver = ApprovedPathResolver([approved_root], limits=limits)
    candidate = resolver.resolve(path)
    kind = detect_kind(candidate)
    if kind in {DocumentKind.PDF, DocumentKind.UNSUPPORTED}:
        raise UnsupportedTypeError("Unsupported source type; supported: markdown, txt.")
    check_utf8_bytes(
        str(candidate),
        max_bytes=limits.max_path_bytes,
        label="Source path",
    )
    check_utf8_bytes(
        candidate.name,
        max_bytes=limits.max_string_bytes,
        label="Source name",
    )
    copy_name = f"{candidate.name}.{os.getpid()}.{_nonce()}"
    full_copy_path = os.path.join(
        str(resolver.roots[0]),
        INTAKE_COPY_DIR,
        copy_name,
    )
    check_utf8_bytes(
        full_copy_path,
        max_bytes=limits.max_path_bytes,
        label="Intake copy path",
    )
    check_deadline(deadline, label="intake")

    root_fd = _open_root_fd(
        resolver.roots[0],
        resolver.root_identities[0],
    )
    ws_fd: int | None = None
    source_fd: int | None = None
    dest_fd: int | None = None
    completed = False
    try:
        ws_fd = _prepare_workspace(root_fd)
        source_fd, before = _open_relative_verified(
            root_fd,
            resolver.roots[0],
            candidate,
        )
        dest_flags = _open_flags(
            "O_WRONLY",
            "O_CREAT",
            "O_EXCL",
            "O_NOFOLLOW",
            "O_CLOEXEC",
        )
        dest_fd = os.open(copy_name, dest_flags, 0o600, dir_fd=ws_fd)
        dest_stat = os.fstat(dest_fd)
        if not stat_module.S_ISREG(dest_stat.st_mode):
            raise UnreadableFileError("The intake copy is not a regular file.")
        hasher = hashlib.sha256()
        counted = 0
        while True:
            check_deadline(deadline, label="intake copy")
            try:
                chunk = os.read(source_fd, STREAM_CHUNK_SIZE)
            except OSError:
                raise UnreadableFileError("The approved file could not be read safely.") from None
            if not chunk:
                break
            counted += len(chunk)
            if counted > byte_limit:
                raise OversizeFileError("File exceeded the configured intake limit while copying.")
            hasher.update(chunk)
            _write_all(dest_fd, chunk)
        os.fsync(dest_fd)
        after = os.fstat(source_fd)
        if (
            before.st_dev != after.st_dev
            or before.st_ino != after.st_ino
            or before.st_size != after.st_size
            or before.st_mtime_ns != after.st_mtime_ns
            or before.st_ctime_ns != after.st_ctime_ns
            or counted != after.st_size
        ):
            raise UnreadableFileError("The approved file changed while being copied.")
        digest = f"sha256:{hasher.hexdigest()}"
        os.fchmod(dest_fd, 0o400)
        os.fsync(dest_fd)
        os.fsync(ws_fd)
        extra = {"copy_path": full_copy_path}
        validate_bounded_metadata(extra, limits=limits)
        result = SourceMetadata(
            original_path=str(candidate),
            display_name=candidate.name,
            kind=kind,
            size_bytes=after.st_size,
            sha256=digest,
            approved=True,
            extra=extra,
        )
        completed = True
        return result
    except (OversizeFileError, UnreadableFileError, ResourceLimitError):
        raise
    except Exception:
        raise UnreadableFileError("The approved file could not be hashed or copied.") from None
    finally:
        if source_fd is not None:
            with suppress(OSError):
                os.close(source_fd)
        if dest_fd is not None:
            with suppress(OSError):
                os.close(dest_fd)
        if not completed and ws_fd is not None:
            with suppress(OSError):
                _unlink_at(ws_fd, copy_name)
        if ws_fd is not None:
            with suppress(OSError):
                os.close(ws_fd)
        with suppress(OSError):
            os.close(root_fd)


def _prepare_workspace(root_fd: int) -> int:
    """Create/validate the private workspace inside a verified root dirfd."""
    try:
        with suppress(FileExistsError):
            os.mkdir(INTAKE_COPY_DIR, 0o700, dir_fd=root_fd)
    except (OSError, TypeError):
        raise UnreadableFileError("The private intake workspace could not be created.") from None
    flags = _open_flags(
        "O_RDONLY",
        "O_DIRECTORY",
        "O_NOFOLLOW",
        "O_CLOEXEC",
    )
    try:
        fd = os.open(INTAKE_COPY_DIR, flags, dir_fd=root_fd)
        st = os.fstat(fd)
    except (OSError, TypeError):
        raise UnreadableFileError(
            "The private intake workspace could not be opened safely."
        ) from None
    if not stat_module.S_ISDIR(st.st_mode):
        os.close(fd)
        raise UnreadableFileError("The private intake workspace must be a real directory.")
    if stat_module.S_IMODE(st.st_mode) != 0o700:
        os.close(fd)
        raise UnreadableFileError("The private intake workspace must have 0700 permissions.")
    with suppress(OSError):
        os.fsync(root_fd)
    return fd


def _unlink_at(dir_fd: int, name: str) -> None:
    try:
        os.unlink(name, dir_fd=dir_fd)
    except TypeError:
        # Fail closed rather than path-cleaning on platforms without dir_fd;
        # the owning directory fd was already verified and must remain the
        # only trusted base for cleanup.
        raise UnreadableFileError("Intake cleanup requires a directory-fd primitive.") from None


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        try:
            written = os.write(fd, view)
        except OSError:
            raise UnreadableFileError("The intake copy could not be written.") from None
        if written <= 0:
            raise UnreadableFileError("The intake copy could not be written.")
        view = view[written:]


def to_parser_error(error: IntakeError) -> ParserError:
    """Convert an intake failure into a generic typed parser error."""
    if isinstance(error, UnsupportedTypeError):
        return ParserError(
            code="UNSUPPORTED_DOCUMENT_TYPE",
            message="Unsupported source type; supported: markdown, txt.",
            recoverable=True,
            actions=("select_supported_file",),
        )
    if isinstance(error, OversizeFileError):
        return ParserError(
            code="DOCUMENT_TOO_LARGE",
            message="The source file exceeds the configured size limit.",
            recoverable=True,
            actions=("reduce_file_size",),
        )
    if isinstance(error, PathEscapeError):
        return ParserError(
            code="PATH_ESCAPE_REJECTED",
            message="Approved intake paths must resolve inside the configured roots.",
            recoverable=False,
            actions=("select_approved_file",),
        )
    return ParserError(
        code="DOCUMENT_UNREADABLE",
        message="The source file could not be read safely.",
        recoverable=True,
        actions=("select_readable_file",),
    )


def _nonce() -> str:
    import secrets

    return secrets.token_hex(6)
