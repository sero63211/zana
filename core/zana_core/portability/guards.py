"""Deterministic exclusive operation and target guards."""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from threading import Lock

from zana_core.portability.models import (
    OperationStage,
    PortabilityError,
    RecoveryAction,
)
from zana_core.portability.paths import secure_mkdir

MAX_LIVE_TARGET_LOCKS = 64
MAX_TARGET_KEY_CHARS = 1024
MAX_OPERATION_ID_CHARS = 200
_CONCRETE_PATH = type(Path())


def _exact_guard_path(value: object) -> Path:
    if type(value) is not _CONCRETE_PATH:
        raise PortabilityError(
            "guard data root must be an exact concrete pathlib.Path",
            code="DATA_ROOT_INVALID",
            stage=OperationStage.LOCK,
        )
    return value


def _guard_dir_flags() -> int:
    return os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


def _open_guard_parent_dirfd(path: Path) -> tuple[int, str]:
    """Open an exact absolute path's parent via all-component dirfd walk."""
    candidate = _exact_guard_path(path)
    if not candidate.is_absolute():
        raise PortabilityError(
            "guard data root must be absolute",
            code="DATA_ROOT_NOT_ABSOLUTE",
            stage=OperationStage.LOCK,
        )
    for part in candidate.parts:
        if part in ("", ".", ".."):
            raise PortabilityError(
                "guard path contains an unsafe component",
                code="PATH_UNSAFE_COMPONENT",
                stage=OperationStage.LOCK,
            )
    parent = candidate.parent
    fd = os.open(parent.anchor, _guard_dir_flags())
    try:
        for part in parent.parts[1:]:
            try:
                child_fd = os.open(part, _guard_dir_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise PortabilityError(
                    "guard ancestor could not be opened",
                    code="PATH_OPEN_FAILED",
                    stage=OperationStage.LOCK,
                ) from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise PortabilityError(
            "guard ancestor could not be opened",
            code="PATH_OPEN_FAILED",
            stage=OperationStage.LOCK,
        ) from error
    return fd, candidate.name


def _open_guard_dirfd(path: Path) -> int:
    """Open an exact absolute directory via all-component dirfd walk."""
    candidate = _exact_guard_path(path)
    if not candidate.is_absolute():
        raise PortabilityError(
            "guard data root must be absolute",
            code="DATA_ROOT_NOT_ABSOLUTE",
            stage=OperationStage.LOCK,
        )
    for part in candidate.parts:
        if part in ("", ".", ".."):
            raise PortabilityError(
                "guard path contains an unsafe component",
                code="PATH_UNSAFE_COMPONENT",
                stage=OperationStage.LOCK,
            )
    fd = os.open(candidate.anchor, _guard_dir_flags())
    try:
        for part in candidate.parts[1:]:
            try:
                child_fd = os.open(part, _guard_dir_flags(), dir_fd=fd)
            except OSError as error:
                os.close(fd)
                raise PortabilityError(
                    "guard path could not be opened",
                    code="PATH_OPEN_FAILED",
                    stage=OperationStage.LOCK,
                ) from error
            os.close(fd)
            fd = child_fd
    except OSError as error:
        os.close(fd)
        raise PortabilityError(
            "guard path could not be opened",
            code="PATH_OPEN_FAILED",
            stage=OperationStage.LOCK,
        ) from error
    return fd


class ConcurrentOperationError(PortabilityError):
    """Another operation already holds the exclusive target guard."""


class _RefCountedLock:
    """A lock plus waiter count; removed from the registry when unused."""

    __slots__ = ("lock", "refs")

    def __init__(self) -> None:
        self.lock = Lock()
        self.refs = 0


_IN_PROCESS_LOCKS: dict[str, _RefCountedLock] = {}
_IN_PROCESS_GUARD = Lock()


def in_process_lock_count() -> int:
    """Return the number of live target locks (used by tests)."""
    with _IN_PROCESS_GUARD:
        return len(_IN_PROCESS_LOCKS)


class OperationGuard:
    """Atomic-create guard file plus ref-counted in-process lock.

    The lock directory is created lazily at first acquisition, so
    constructing a guard has no filesystem side effect.
    """

    def __init__(self, data_root: Path) -> None:
        root = _exact_guard_path(data_root)
        if not root.is_absolute():
            raise PortabilityError(
                "guard data root must be absolute",
                code="DATA_ROOT_NOT_ABSOLUTE",
                stage=OperationStage.LOCK,
            )
        fd = _open_guard_dirfd(root)
        try:
            info = os.fstat(fd)
            if not stat.S_ISDIR(info.st_mode):
                raise PortabilityError(
                    "guard data root must be a real directory",
                    code="DATA_ROOT_NOT_DIRECTORY",
                    stage=OperationStage.LOCK,
                )
        finally:
            os.close(fd)
        if root.is_symlink() or not root.is_dir():
            raise PortabilityError(
                "guard data root must be a real directory",
                code="DATA_ROOT_NOT_DIRECTORY",
                stage=OperationStage.LOCK,
            )
        self._locks_dir = root / "portability" / "locks"

    @contextmanager
    def acquire(self, operation_id: str, target_key: str) -> Iterator[None]:
        """Acquire exclusive ownership; stale or busy targets fail closed."""
        if type(target_key) is not str or not target_key or len(target_key) > MAX_TARGET_KEY_CHARS:
            raise PortabilityError(
                "target key exceeds the length limit",
                code="TARGET_KEY_TOO_LONG",
                stage=OperationStage.LOCK,
                recovery_action=RecoveryAction.RETRY,
            )
        if (
            type(operation_id) is not str
            or not operation_id
            or len(operation_id) > MAX_OPERATION_ID_CHARS
        ):
            raise PortabilityError(
                "operation id is empty or exceeds the length limit",
                code="OPERATION_ID_INVALID",
                stage=OperationStage.LOCK,
                recovery_action=RecoveryAction.RETRY,
            )
        digest = hashlib.sha256(target_key.encode("utf-8")).hexdigest()
        short_digest = digest[:12]
        with _IN_PROCESS_GUARD:
            if digest not in _IN_PROCESS_LOCKS and len(_IN_PROCESS_LOCKS) >= MAX_LIVE_TARGET_LOCKS:
                raise ConcurrentOperationError(
                    "live target lock registry is full",
                    code="LOCK_REGISTRY_FULL",
                    stage=OperationStage.LOCK,
                    recovery_action=RecoveryAction.RETRY,
                )
            entry = _IN_PROCESS_LOCKS.setdefault(digest, _RefCountedLock())
            entry.refs += 1
        acquired = entry.lock.acquire(timeout=0)
        if not acquired:
            with _IN_PROCESS_GUARD:
                entry.refs -= 1
                if entry.refs == 0:
                    _IN_PROCESS_LOCKS.pop(digest, None)
            raise ConcurrentOperationError(
                f"another operation holds target {short_digest}",
                code="CONCURRENT_OPERATION",
                stage=OperationStage.LOCK,
                recovery_action=RecoveryAction.RETRY,
            )
        created = False
        locks_fd: int | None = None
        try:
            secure_mkdir(self._locks_dir, mode=0o700, stage=OperationStage.LOCK)
            locks_fd = _open_guard_dirfd(self._locks_dir)
            try:
                flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY
                flags |= os.O_NOFOLLOW | os.O_CLOEXEC
                fd = os.open(f"{digest}.lock", flags, 0o600, dir_fd=locks_fd)
                created = True
            except FileExistsError:
                raise ConcurrentOperationError(
                    f"guard file already exists for target {short_digest}",
                    code="GUARD_FILE_EXISTS",
                    stage=OperationStage.LOCK,
                    recovery_action=RecoveryAction.RETRY,
                ) from None
            try:
                handle = os.fdopen(fd, "w")
            except Exception:
                with suppress(OSError):
                    os.close(fd)
                raise
            with handle:
                _write_fd_full(handle.fileno(), operation_id.encode("utf-8"))
                handle.flush()
                os.fsync(handle.fileno())
            yield
        finally:
            try:
                if created and locks_fd is not None:
                    _unlink_guard_dirfd(locks_fd, f"{digest}.lock")
            finally:
                entry.lock.release()
                with _IN_PROCESS_GUARD:
                    entry.refs -= 1
                    if entry.refs == 0:
                        _IN_PROCESS_LOCKS.pop(digest, None)
                if locks_fd is not None:
                    os.close(locks_fd)


def _write_fd_full(fd: int, data: bytes) -> None:
    if type(fd) is not int or type(data) is not bytes:
        raise OSError("guard write requires an exact integer fd and bytes")
    view = memoryview(data)
    while view:
        written = os.write(fd, view)
        if type(written) is not int or not 1 <= written <= len(view):
            raise OSError("short write while creating operation guard")
        view = view[written:]


def _unlink_guard_dirfd(fd: int, name: str) -> None:
    """Remove an owned guard file relative to a held locks dirfd."""
    try:
        os.unlink(name, dir_fd=fd)
    except FileNotFoundError:
        return
