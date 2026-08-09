"""Platform-neutral approved-path confinement, validation, and bounded IO."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Sequence
from contextlib import suppress
from errno import ELOOP, ENAMETOOLONG, ENOTDIR
from pathlib import Path

from zana_core.portability.models import (
    Deadline,
    LimitExceededError,
    OperationStage,
    PathPolicyError,
    RecoveryAction,
)

MAX_APPROVED_ROOTS = 16
_CONCRETE_PATH = type(Path())


def _require_os_support() -> None:
    """Fail closed unless all required path-open primitives exist."""
    for attribute in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY"):
        if not hasattr(os, attribute):
            raise PathPolicyError(
                "secure filesystem open is unsupported on this platform",
                code="UNSUPPORTED_PLATFORM",
                stage=OperationStage.PREFLIGHT,
            )


def _exact_path(value: object) -> Path:
    if type(value) is not _CONCRETE_PATH:
        raise PathPolicyError(
            "path must be an exact concrete pathlib.Path",
            code="PATH_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    return value


def _dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _read_flags() -> int:
    return os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC


def _exclusive_flags() -> int:
    return os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_parent_dirfd(path: Path, *, stage: OperationStage) -> tuple[int, str]:
    """Open an exact absolute path's parent with all ancestors via dirfd."""
    _exact_path(path)
    if not path.is_absolute():
        raise PathPolicyError(
            _safe_path_error("path is not absolute", path=path),
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
        )
    for part in path.parts:
        if part in ("", ".", ".."):
            raise PathPolicyError(
                _safe_path_error("path contains an unsafe component", path=path),
                code="PATH_UNSAFE_COMPONENT",
                stage=stage,
            )
    parent = path.parent
    fd = os.open(parent.anchor, _dir_flags())
    try:
        for part in parent.parts[1:]:
            try:
                child_fd = os.open(part, _dir_flags(), dir_fd=fd)
            except OSError as error:
                if error.errno in (ELOOP, ENOTDIR, ENAMETOOLONG):
                    os.close(fd)
                    raise PathPolicyError(
                        _safe_path_error("path contains a symlink component", path=path),
                        code="PATH_SYMLINK_COMPONENT",
                        stage=stage,
                    ) from error
                os.close(fd)
                raise PathPolicyError(
                    _safe_path_error("ancestor could not be opened", path=parent),
                    code="PATH_OPEN_FAILED",
                    stage=stage,
                ) from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise PathPolicyError(
            _safe_path_error("ancestor could not be opened", path=parent),
            code="PATH_OPEN_FAILED",
            stage=stage,
        ) from error
    return fd, path.name


def _open_dirfd_path(path: Path, *, stage: OperationStage) -> int:
    """Open an exact absolute directory path via all-component dirfd walk."""
    _exact_path(path)
    if not path.is_absolute():
        raise PathPolicyError(
            _safe_path_error("path is not absolute", path=path),
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
        )
    for part in path.parts:
        if part in ("", ".", ".."):
            raise PathPolicyError(
                _safe_path_error("path contains an unsafe component", path=path),
                code="PATH_UNSAFE_COMPONENT",
                stage=stage,
            )
    fd = os.open(path.anchor, _dir_flags())
    try:
        for part in path.parts[1:]:
            try:
                child_fd = os.open(part, _dir_flags(), dir_fd=fd)
            except OSError as error:
                if error.errno in (ELOOP, ENOTDIR, ENAMETOOLONG):
                    os.close(fd)
                    raise PathPolicyError(
                        _safe_path_error("path contains a symlink component", path=path),
                        code="PATH_SYMLINK_COMPONENT",
                        stage=stage,
                    ) from error
                os.close(fd)
                raise PathPolicyError(
                    _safe_path_error("path could not be opened", path=path),
                    code="PATH_OPEN_FAILED",
                    stage=stage,
                ) from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise PathPolicyError(
            _safe_path_error("path could not be opened", path=path),
            code="PATH_OPEN_FAILED",
            stage=stage,
        ) from error
    return fd


def _reject_symlink_components(path: Path) -> None:
    """Verify no symlink ancestor and no symlink leaf via dirfd only."""
    parent_fd, name = _open_parent_dirfd(path, stage=OperationStage.PREFLIGHT)
    try:
        try:
            info = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            return
        if stat.S_ISLNK(info.st_mode):
            raise PathPolicyError(
                "path contains a symlink component",
                code="PATH_SYMLINK_COMPONENT",
                stage=OperationStage.PREFLIGHT,
            )
    finally:
        os.close(parent_fd)


def _safe_path_error(message: str, *, path: Path) -> str:
    """Return an error message with only a safe basename, never a full path."""
    name = path.name or "?"
    return f"{message}: {name}"


def validate_approved_roots(roots: Sequence[Path]) -> tuple[Path, ...]:
    """Canonically validate and copy a small set of safe approved roots.

    Rejects empty, oversized, relative, tilde-expanded, symlinked, duplicated,
    filesystem-root, home, cwd, and parent-child aliased roots before any
    caller filesystem mutation. Returns resolved absolute roots only.
    """
    if type(roots) not in (list, tuple):
        raise PathPolicyError(
            "approved roots must be an exact builtin list or tuple",
            code="ROOTS_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    collected: list[Path] = []
    for index, raw in enumerate(roots):
        if index >= MAX_APPROVED_ROOTS + 1:
            raise PathPolicyError(
                "approved root count exceeds the hard limit",
                code="ROOTS_TOO_MANY",
                stage=OperationStage.PREFLIGHT,
            )
        if type(raw) is not _CONCRETE_PATH:
            raise PathPolicyError(
                "approved roots must contain exact concrete pathlib.Path values",
                code="ROOT_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
        collected.append(raw)
    if not collected:
        raise PathPolicyError(
            "at least one approved root is required",
            code="ROOTS_EMPTY",
            stage=OperationStage.PREFLIGHT,
        )
    if len(collected) > MAX_APPROVED_ROOTS:
        raise PathPolicyError(
            "approved root count exceeds the hard limit",
            code="ROOTS_TOO_MANY",
            stage=OperationStage.PREFLIGHT,
        )
    resolved: list[Path] = []
    seen: set[Path] = set()
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    for candidate in collected:
        if not candidate.is_absolute():
            raise PathPolicyError(
                "approved roots must be absolute paths; expansion is not allowed",
                code="ROOT_NOT_ABSOLUTE",
                stage=OperationStage.PREFLIGHT,
            )
        if "~" in candidate.parts:
            raise PathPolicyError(
                "approved roots must not contain shell-style expansion",
                code="ROOT_EXPANSION_NOT_ALLOWED",
                stage=OperationStage.PREFLIGHT,
            )
        _reject_symlink_components(candidate)
        if candidate.is_symlink():
            raise PathPolicyError(
                "approved roots must not be symlinks",
                code="ROOT_SYMLINK",
                stage=OperationStage.PREFLIGHT,
            )
        root_path = candidate.resolve(strict=False)
        if root_path == Path(root_path.anchor):
            raise PathPolicyError(
                "filesystem root is not an allowed approved root",
                code="ROOT_IS_FILESYSTEM_ROOT",
                stage=OperationStage.PREFLIGHT,
            )
        if root_path in (home, cwd):
            raise PathPolicyError(
                "home and current working directories are not allowed as approved roots",
                code="ROOT_TOO_BROAD",
                stage=OperationStage.PREFLIGHT,
            )
        if not root_path.is_dir():
            raise PathPolicyError(
                "approved root must be an existing directory",
                code="ROOT_NOT_DIRECTORY",
                stage=OperationStage.PREFLIGHT,
            )
        if root_path in seen:
            raise PathPolicyError(
                "duplicate approved roots are not allowed",
                code="ROOT_DUPLICATE",
                stage=OperationStage.PREFLIGHT,
            )
        for existing in resolved:
            if (
                root_path == existing
                or root_path.is_relative_to(existing)
                or existing.is_relative_to(root_path)
            ):
                raise PathPolicyError(
                    "approved roots must not alias each other as parent and child",
                    code="ROOT_ALIAS",
                    stage=OperationStage.PREFLIGHT,
                )
        seen.add(root_path)
        resolved.append(root_path)
    return tuple(resolved)


def validate_data_root(
    data_root: Path,
    approved_roots: Sequence[Path],
) -> Path:
    """Validate a data root as an approved root or a direct child of one."""
    if type(data_root) is not _CONCRETE_PATH:
        raise PathPolicyError(
            "data root must be an exact concrete pathlib.Path",
            code="DATA_ROOT_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    candidate = data_root
    if type(approved_roots) not in (list, tuple):
        raise PathPolicyError(
            "approved roots must be an exact bounded builtin list or tuple",
            code="ROOTS_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    if not 1 <= len(approved_roots) <= MAX_APPROVED_ROOTS:
        raise PathPolicyError(
            "approved root count exceeds the hard limit",
            code="ROOTS_TOO_MANY",
            stage=OperationStage.PREFLIGHT,
        )
    for root in approved_roots:
        if type(root) is not _CONCRETE_PATH:
            raise PathPolicyError(
                "approved roots must contain exact concrete pathlib.Path values",
                code="ROOT_INVALID",
                stage=OperationStage.PREFLIGHT,
            )
    if not candidate.is_absolute():
        raise PathPolicyError(
            "data root must be an absolute path; expansion is not allowed",
            code="DATA_ROOT_NOT_ABSOLUTE",
            stage=OperationStage.PREFLIGHT,
        )
    if "~" in candidate.parts:
        raise PathPolicyError(
            "data root must not contain shell-style expansion",
            code="DATA_ROOT_EXPANSION_NOT_ALLOWED",
            stage=OperationStage.PREFLIGHT,
        )
    _reject_symlink_components(candidate)
    if candidate.is_symlink():
        raise PathPolicyError(
            "data root must not be a symlink",
            code="DATA_ROOT_SYMLINK",
            stage=OperationStage.PREFLIGHT,
        )
    resolved = candidate.resolve(strict=False)
    if resolved == Path(resolved.anchor):
        raise PathPolicyError(
            "filesystem root is not an allowed data root",
            code="DATA_ROOT_IS_FILESYSTEM_ROOT",
            stage=OperationStage.PREFLIGHT,
        )
    home = Path.home().resolve()
    cwd = Path.cwd().resolve()
    if resolved in (home, cwd):
        raise PathPolicyError(
            "home and current working directories are not allowed as data roots",
            code="DATA_ROOT_TOO_BROAD",
            stage=OperationStage.PREFLIGHT,
        )
    if not any(resolved == root or resolved.is_relative_to(root) for root in approved_roots):
        raise PathPolicyError(
            "data root must be an approved root or a child of one",
            code="DATA_ROOT_NOT_APPROVED",
            stage=OperationStage.PREFLIGHT,
        )
    return resolved


def confine(path: str | Path, approved_roots: Sequence[Path], *, stage: OperationStage) -> Path:
    """Resolve ``path`` and require it to live under one approved root."""
    if type(path) is not _CONCRETE_PATH:
        raise PathPolicyError(
            "path must be an exact concrete pathlib.Path",
            code="PATH_INVALID",
            stage=stage,
        )
    candidate = path
    if type(approved_roots) not in (list, tuple):
        raise PathPolicyError(
            "approved roots must be an exact bounded builtin list or tuple",
            code="ROOTS_INVALID",
            stage=stage,
        )
    if not 1 <= len(approved_roots) <= MAX_APPROVED_ROOTS:
        raise PathPolicyError(
            "approved root count exceeds the hard limit",
            code="ROOTS_TOO_MANY",
            stage=stage,
        )
    for root in approved_roots:
        if type(root) is not _CONCRETE_PATH:
            raise PathPolicyError(
                "approved roots must contain exact concrete pathlib.Path values",
                code="ROOT_INVALID",
                stage=stage,
            )
    if not isinstance(candidate, Path) or not candidate.is_absolute():
        raise PathPolicyError(
            "path is not absolute",
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
            recovery_action=RecoveryAction.CHOOSE_APPROVED_PATH,
        )
    resolved = candidate.resolve(strict=False) if isinstance(candidate, Path) else candidate
    for root in approved_roots:
        if resolved == root or resolved.is_relative_to(root):
            return resolved
    raise PathPolicyError(
        _safe_path_error("path is outside every approved root", path=candidate),
        code="PATH_NOT_APPROVED",
        stage=stage,
        recovery_action=RecoveryAction.CHOOSE_APPROVED_PATH,
    )


def require_regular_file(path: Path, *, stage: OperationStage) -> Path:
    """Require an existing non-symlink regular file (stable-fd verified)."""
    fd, _info = _open_regular_nofollow(path, stage=stage)
    os.close(fd)
    return path


def require_directory(path: Path, *, stage: OperationStage) -> Path:
    """Require an existing non-symlink directory (stable-fd verified)."""
    fd = _open_directory_nofollow(path, stage=stage)
    os.close(fd)
    return path


def _open_regular_nofollow(path: Path, *, stage: OperationStage) -> tuple[int, os.stat_result]:
    _require_os_support()
    _exact_path(path)
    if not path.is_absolute():
        raise PathPolicyError(
            _safe_path_error("path is not absolute", path=path),
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
        )
    parent_fd, name = _open_parent_dirfd(path, stage=stage)
    try:
        try:
            fd = os.open(name, _read_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            raise PathPolicyError(
                _safe_path_error("path does not exist", path=path),
                code="PATH_NOT_FOUND",
                stage=stage,
            ) from None
        except OSError as error:
            raise PathPolicyError(
                _safe_path_error("path could not be opened", path=path),
                code="PATH_OPEN_FAILED",
                stage=stage,
            ) from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode):
                raise PathPolicyError(
                    _safe_path_error("path is not a regular file", path=path),
                    code="NOT_REGULAR_FILE",
                    stage=stage,
                )
            return fd, info
        except Exception:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def open_regular_nofollow(path: Path, *, stage: OperationStage) -> tuple[int, os.stat_result]:
    """Public stable regular-file opener; caller owns the returned fd."""
    return _open_regular_nofollow(path, stage=stage)


def _open_directory_nofollow(path: Path, *, stage: OperationStage) -> int:
    _require_os_support()
    _exact_path(path)
    if not path.is_absolute():
        raise PathPolicyError(
            _safe_path_error("path is not absolute", path=path),
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
        )
    parent_fd, name = _open_parent_dirfd(path, stage=stage)
    try:
        try:
            fd = os.open(name, _dir_flags(), dir_fd=parent_fd)
        except FileNotFoundError:
            raise PathPolicyError(
                _safe_path_error("path does not exist", path=path),
                code="PATH_NOT_FOUND",
                stage=stage,
            ) from None
        except OSError as error:
            raise PathPolicyError(
                _safe_path_error("path could not be opened", path=path),
                code="PATH_OPEN_FAILED",
                stage=stage,
            ) from error
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise PathPolicyError(
                    _safe_path_error("path is not a directory", path=path),
                    code="NOT_DIRECTORY",
                    stage=stage,
                )
            return fd
        except Exception:
            os.close(fd)
            raise
    finally:
        os.close(parent_fd)


def secure_mkdir(path: Path, *, mode: int = 0o700, stage: OperationStage) -> Path:
    """Create one directory via dirfd, exact mode, and parent fsync."""
    _require_os_support()
    if type(mode) is not int or not 0 <= mode <= 0o7777:
        raise PathPolicyError(
            "directory mode must be an exact bounded integer",
            code="MODE_INVALID",
            stage=stage,
        )
    if type(stage) is not OperationStage:
        raise PathPolicyError(
            "stage must be an exact OperationStage",
            code="STAGE_INVALID",
            stage=stage,
        )
    target = _exact_path(path)
    if not target.is_absolute():
        raise PathPolicyError(
            _safe_path_error("path is not absolute", path=target),
            code="PATH_NOT_ABSOLUTE",
            stage=stage,
        )
    fd = os.open(target.anchor, _dir_flags())
    try:
        for part in target.parts[1:]:
            try:
                child_fd = os.open(part, _dir_flags(), dir_fd=fd)
            except FileNotFoundError:
                os.mkdir(part, mode, dir_fd=fd)
                os.chmod(part, mode, dir_fd=fd, follow_symlinks=False)
                os.fsync(fd)
                child_fd = os.open(part, _dir_flags(), dir_fd=fd)
            except OSError as error:
                raise PathPolicyError(
                    _safe_path_error("path could not be created", path=target),
                    code="PATH_OPEN_FAILED",
                    stage=stage,
                ) from error
            info = os.fstat(child_fd)
            if not stat.S_ISDIR(info.st_mode):
                os.close(child_fd)
                raise PathPolicyError(
                    _safe_path_error("path is not a directory", path=target),
                    code="NOT_DIRECTORY",
                    stage=stage,
                )
            os.close(fd)
            fd = child_fd
    finally:
        os.close(fd)
    return target


def sibling_temp_path(destination: Path, tag: str) -> Path:
    """Unique sibling temp path so atomic replace stays on one filesystem."""
    import uuid

    _exact_path(destination)
    if type(tag) is not str or not tag or len(tag) > 64:
        raise PathPolicyError(
            "temp tag must be a bounded non-empty string",
            code="TEMP_TAG_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    if any(ord(char) < 0x20 or ord(char) == 0x7F for char in tag):
        raise PathPolicyError(
            "temp tag must not contain control characters",
            code="TEMP_TAG_INVALID",
            stage=OperationStage.PREFLIGHT,
        )
    return destination.parent / f".{destination.name}.{tag}.{uuid.uuid4().hex}.tmp"


def deadline_digest(
    path: Path,
    *,
    chunk_size: int,
    max_bytes: int,
    deadline: Deadline,
    stage: OperationStage,
    output_path: Path | None = None,
    source_fd: int | None = None,
) -> str:
    """Stream a file's SHA-256 with bounded chunks, bytes, and one deadline."""
    _require_os_support()
    if type(deadline) is not Deadline:
        raise LimitExceededError(
            "deadline must be an exact Deadline",
            code="INVALID_DEADLINE",
            stage=stage,
        )
    if source_fd is not None and type(source_fd) is not int:
        raise LimitExceededError(
            "source fd must be an exact integer",
            code="INVALID_SOURCE_FD",
            stage=stage,
        )
    if type(chunk_size) is not int or chunk_size <= 0 or chunk_size > 8 * 1024**2:
        raise LimitExceededError(
            "digest chunk size is outside the hard bounds",
            code="INVALID_CHUNK_SIZE",
            stage=stage,
        )
    if type(max_bytes) is not int or max_bytes <= 0 or max_bytes > 32 * 1024**3:
        raise LimitExceededError(
            "digest byte limit is outside the hard bounds",
            code="INVALID_BYTE_LIMIT",
            stage=stage,
        )
    hasher = hashlib.sha256()
    total = 0
    owned_fd = False
    source_info: os.stat_result | None = None
    output_fd: int | None = None
    snapshot_created = False
    output_parent_fd: int | None = None
    fd: int | None = None
    if output_path is not None:
        _exact_path(output_path)
        output_parent_fd, output_name = _open_parent_dirfd(output_path, stage=stage)
        try:
            try:
                output_fd = os.open(output_name, _exclusive_flags(), 0o600, dir_fd=output_parent_fd)
            except FileExistsError:
                raise LimitExceededError(
                    "snapshot output already exists",
                    code="OUTPUT_COLLISION",
                    stage=stage,
                ) from None
            snapshot_created = True
        except Exception:
            if output_parent_fd is not None:
                os.close(output_parent_fd)
                output_parent_fd = None
            raise
    try:
        if source_fd is not None:
            fd = source_fd
        else:
            fd, source_info = _open_regular_nofollow(path, stage=stage)
            owned_fd = True
        if source_info is None:
            source_info = os.fstat(fd)
            if not stat.S_ISREG(source_info.st_mode):
                raise PathPolicyError(
                    _safe_path_error("path is not a regular file", path=path),
                    code="NOT_REGULAR_FILE",
                    stage=stage,
                )
        while True:
            deadline.check(stage)
            chunk = os.read(fd, chunk_size)
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise LimitExceededError(
                    "file exceeds the byte limit during digest verification",
                    code="SIZE_LIMIT_EXCEEDED",
                    stage=stage,
                    recovery_action=RecoveryAction.RETRY,
                )
            hasher.update(chunk)
            if output_fd is not None:
                _write_fd_full(output_fd, chunk)
        current = os.fstat(fd)
        if (current.st_dev, current.st_ino) != (
            source_info.st_dev,
            source_info.st_ino,
        ) or current.st_size != source_info.st_size:
            raise LimitExceededError(
                "file changed identity or size during digest verification",
                code="SOURCE_CHANGED",
                stage=stage,
            )
        if output_fd is not None:
            os.fsync(output_fd)
            os.fchmod(output_fd, 0o400)
    except Exception:
        if snapshot_created and output_parent_fd is not None and output_path is not None:
            with suppress(OSError):
                os.unlink(output_path.name, dir_fd=output_parent_fd)
            with suppress(OSError):
                os.fsync(output_parent_fd)
        raise
    finally:
        if output_fd is not None:
            with suppress(OSError):
                os.close(output_fd)
        if (
            snapshot_created
            and output_parent_fd is not None
            and output_path is not None
            and output_fd is None
        ):
            with suppress(OSError):
                os.unlink(output_path.name, dir_fd=output_parent_fd)
            with suppress(OSError):
                os.fsync(output_parent_fd)
        if owned_fd and fd is not None:
            os.close(fd)
        if output_parent_fd is not None:
            with suppress(OSError):
                os.close(output_parent_fd)
    return f"sha256:{hasher.hexdigest()}"


def _write_fd_full(fd: int, data: bytes) -> None:
    if type(fd) is not int or type(data) is not bytes:
        raise OSError("snapshot write requires exact integer fd and bytes")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if type(written) is not int or not 1 <= written <= len(view):
            raise OSError("short write while creating snapshot")
        view = view[written:]


def remove_tree_confined(path: Path, data_root: Path) -> None:
    """Remove a workspace by dirfd only when confined under the data root."""
    _require_os_support()
    _exact_path(path)
    _exact_path(data_root)
    if path == data_root or not path.is_relative_to(data_root):
        raise PathPolicyError(
            _safe_path_error("refusing to remove a path outside the data root", path=path),
            code="CLEANUP_ESCAPE",
            stage=OperationStage.CLEANUP,
        )
    data_root_fd = _open_dirfd_path(data_root, stage=OperationStage.CLEANUP)
    relative = path.relative_to(data_root)
    current_fd = data_root_fd
    root_fd: int | None = None
    try:
        for part in relative.parts[:-1]:
            try:
                child_fd = os.open(part, _dir_flags(), dir_fd=current_fd)
            except OSError as error:
                if current_fd >= 0:
                    os.close(current_fd)
                    current_fd = -1
                raise PathPolicyError(
                    _safe_path_error("workspace child could not be opened", path=path),
                    code="CLEANUP_OPEN_FAILED",
                    stage=OperationStage.CLEANUP,
                ) from error
            os.close(current_fd)
            current_fd = child_fd
        try:
            root_fd = os.open(relative.parts[-1], _dir_flags(), dir_fd=current_fd)
        except OSError as error:
            if current_fd >= 0:
                os.close(current_fd)
                current_fd = -1
            raise PathPolicyError(
                _safe_path_error("workspace could not be opened for cleanup", path=path),
                code="CLEANUP_OPEN_FAILED",
                stage=OperationStage.CLEANUP,
            ) from error
        _remove_tree_entries(root_fd, depth=0)
        os.rmdir(relative.parts[-1], dir_fd=current_fd)
        os.fsync(current_fd)
    finally:
        if root_fd is not None:
            os.close(root_fd)
        if current_fd >= 0:
            os.close(current_fd)


def _remove_tree_entries(fd: int, *, depth: int) -> None:
    if depth > 32:
        raise PathPolicyError(
            "workspace cleanup exceeded the depth limit",
            code="CLEANUP_DEPTH_LIMIT",
            stage=OperationStage.CLEANUP,
        )
    entries_seen = 0
    with os.scandir(fd) as entries:
        for entry in entries:
            entries_seen += 1
            if entries_seen > 10_000:
                raise PathPolicyError(
                    "workspace cleanup exceeded the entry limit",
                    code="CLEANUP_ENTRY_LIMIT",
                    stage=OperationStage.CLEANUP,
                )
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                child_fd = os.open(
                    entry.name,
                    _dir_flags(),
                    dir_fd=fd,
                )
                try:
                    _remove_tree_entries(child_fd, depth=depth + 1)
                finally:
                    os.close(child_fd)
                os.rmdir(entry.name, dir_fd=fd)
            else:
                os.unlink(entry.name, dir_fd=fd)


def remove_quietly(path: Path) -> None:
    try:
        parent_fd, name = _open_parent_dirfd(path, stage=OperationStage.CLEANUP)
    except PathPolicyError:
        return
    try:
        with suppress(OSError):
            os.unlink(name, dir_fd=parent_fd)
    finally:
        os.close(parent_fd)


def fsync_file(path: Path) -> None:
    fd, _info = _open_regular_nofollow(path, stage=OperationStage.FSYNC)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def fsync_directory(path: Path) -> bool:
    """Fsync a directory; return False when durability could not be confirmed."""
    _require_os_support()
    try:
        fd = _open_dirfd_path(path, stage=OperationStage.FSYNC)
    except PathPolicyError:
        return False
    try:
        os.fsync(fd)
        return True
    except OSError:
        return False
    finally:
        os.close(fd)
