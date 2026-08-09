"""Bounded local synchronous event sinks."""

from __future__ import annotations

import os
import re
import stat
import threading
from collections import deque
from contextlib import suppress
from pathlib import Path, PosixPath, WindowsPath
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zana_core.observability.events import Event
from zana_core.observability.serialization import (
    MAX_ENCODED_LINE_BYTES,
    serialize_event,
)

MAX_COMPOSITE_CHILDREN = 8
MAX_SNAPSHOT_EVENTS = 200
MAX_RETENTION_FILES = 5
MAX_RETENTION_HARD_CAP = 20
MAX_MEMORY_EVENTS_HARD_CAP = 10_000
MAX_MEMORY_BYTES_HARD_CAP = 64 * 1024 * 1024
MAX_LOG_BYTES_HARD_CAP = 64 * 1024 * 1024
MAX_FILENAME_LENGTH = 128
_SAFE_FILENAME_RE = re.compile(rf"^[A-Za-z0-9][A-Za-z0-9._-]{{0,{MAX_FILENAME_LENGTH - 1}}}$")

_REQUIRED_DIRFD_FLAGS = ("O_DIRECTORY", "O_CLOEXEC", "O_NOFOLLOW")


class _StrictModel(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra="forbid")


class SinkStats(_StrictModel):
    """Immutable sink statistics with exact safe counters."""

    events_written: int = Field(default=0, ge=0)
    events_dropped: int = Field(default=0, ge=0)
    bytes_written: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)


class WriteResult(_StrictModel):
    """Result of one sink write; failures never crash the caller."""

    ok: bool
    event_id: str = Field(max_length=128)
    dropped: bool = False
    error: str | None = Field(default=None, max_length=64)


class RedactedRecord(_StrictModel):
    """One canonical redacted bounded record retained by a memory sink."""

    event_id: str = Field(max_length=128)
    line: str = Field(max_length=MAX_ENCODED_LINE_BYTES + 16)
    bytes: int = Field(ge=0)


class PlatformUnsupportedError(NotImplementedError):
    """Anchored directory-relative I/O is unavailable on this platform."""


def _safe_event_id(event: Any) -> str:
    if type(event) is not Event:
        return ""
    try:
        value = object.__getattribute__(event, "operation_id")
        if type(value) is not str:
            return ""
        if len(value) > 128:
            return ""
        if len(value.encode("utf-8", errors="replace")) > 512:
            return ""
        if any(ord(character) < 32 or ord(character) == 127 for character in value):
            return ""
        return value
    except (AttributeError, TypeError):
        return ""


def _bump(stats: SinkStats, **updates: int) -> SinkStats:
    return stats.model_copy(
        update={name: getattr(stats, name) + delta for name, delta in updates.items()}
    )


class BoundedMemorySink:
    """Ring buffer of canonical redacted records capped by count and bytes."""

    def __init__(
        self,
        *,
        max_events: int,
        max_bytes: int,
    ) -> None:
        if type(max_events) is not int or type(max_bytes) is not int:
            raise ValueError("memory sink bounds must be exact ints")
        if max_events <= 0 or max_events > MAX_MEMORY_EVENTS_HARD_CAP:
            raise ValueError("max_events must be within the hard cap")
        if max_bytes <= 0 or max_bytes > MAX_MEMORY_BYTES_HARD_CAP:
            raise ValueError("max_bytes must be within the hard cap")
        self.max_events = max_events
        self.max_bytes = max_bytes
        self._records: deque[RedactedRecord] = deque()
        self._held_bytes = 0
        self._stats = SinkStats()
        self._lock = threading.RLock()

    def write(self, event: Event) -> WriteResult:
        with self._lock:
            event_id = _safe_event_id(event)
            try:
                line = serialize_event(event)
            except TypeError:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_REJECTED")
            except Exception:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")
            size = len(line.encode("utf-8"))
            if size > self.max_bytes:
                self._stats = _bump(self._stats, events_dropped=1)
                return WriteResult(ok=False, event_id=event_id, dropped=True)
            record = RedactedRecord(event_id=event_id, line=line, bytes=size)
            self._records.append(record)
            self._held_bytes += size
            self._stats = _bump(
                self._stats,
                events_written=1,
                bytes_written=size,
            )
            while len(self._records) > self.max_events or self._held_bytes > self.max_bytes:
                removed = self._records.popleft()
                self._held_bytes -= removed.bytes
                self._stats = _bump(self._stats, events_dropped=1)
            return WriteResult(ok=True, event_id=event_id)

    def snapshot(self, *, limit: int | None = None) -> tuple[RedactedRecord, ...]:
        with self._lock:
            if limit is None:
                allowed = MAX_SNAPSHOT_EVENTS
            elif type(limit) is not int or limit < 0:
                raise ValueError("snapshot limit must be a non-negative exact int")
            else:
                allowed = min(limit, MAX_SNAPSHOT_EVENTS)
            if allowed == 0:
                return ()
            return tuple(self._records)[-allowed:]

    def stats(self) -> SinkStats:
        with self._lock:
            return self._stats.model_copy()

    def held_bytes(self) -> int:
        with self._lock:
            return self._held_bytes

    def event_count(self) -> int:
        with self._lock:
            return len(self._records)


def _real_directory_root(root: Path) -> tuple[Path, int, int]:
    """Return an absolute lexical root and its identity (dev, ino)."""
    absolute = Path(os.path.abspath(os.fspath(root)))
    if absolute.anchor:
        current = Path(absolute.anchor)
        components = absolute.parts[1:]
    else:
        current = Path(absolute.parts[0])
        components = absolute.parts[1:]
    final_dev = 0
    final_ino = 0
    for component in components:
        current = current / component
        try:
            info = os.lstat(current)
        except OSError:
            raise ValueError("log_root must be an existing real directory") from None
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValueError("log_root must be a real non-symlink directory")
        final_dev = info.st_dev
        final_ino = info.st_ino
    return absolute, final_dev, final_ino


def _validate_filename(filename: str) -> str:
    if type(filename) is not str or not _SAFE_FILENAME_RE.fullmatch(filename):
        raise ValueError("log filename must be a safe plain basename")
    return filename


class RotationError(Exception):
    """Rotation failed; ``uncertain`` means rollback could not be proven."""

    def __init__(self, *, uncertain: bool = False) -> None:
        super().__init__()
        self.uncertain = uncertain


class LocalJsonlSink:
    """Synchronous, confined, rotating JSON Lines sink anchored to a real directory.

    The sink opens the validated root directory once and performs all later
    stat/open/replace/fsync operations relative to that held directory fd.
    Each operation verifies the original root path still names the same real
    directory (dev+ino); a renamed or replaced root fails closed without
    touching the replacement target.
    """

    def __init__(
        self,
        *,
        log_root: Path | str,
        filename: str,
        max_bytes: int = 64 * 1024,
        max_retention: int = MAX_RETENTION_FILES,
        flush_every: int = 1,
    ) -> None:
        if type(log_root) is Path or type(log_root) is PosixPath or type(log_root) is WindowsPath:
            root: Path = log_root
        elif type(log_root) is str:
            root = Path(log_root)
        else:
            raise ValueError("log_root must be an exact Path or str")
        if type(max_bytes) is not int or type(max_retention) is not int:
            raise ValueError("log bounds must be exact ints")
        if max_bytes <= 0 or max_bytes > MAX_LOG_BYTES_HARD_CAP:
            raise ValueError("max_bytes must be within the hard cap")
        if (
            type(max_retention) is not int
            or max_retention <= 0
            or max_retention > MAX_RETENTION_HARD_CAP
        ):
            raise ValueError("max_retention must be within the hard cap")
        if type(flush_every) is not int or flush_every != 1:
            raise ValueError("flush_every must equal the exact truthful int 1")
        self.root, self._root_dev, self._root_ino = _real_directory_root(root)
        self.filename = _validate_filename(filename)
        self.max_bytes = max_bytes
        self.max_retention = max_retention
        self.flush_every = flush_every
        self._root_fd = -1
        self._closed = False
        self._index = 0
        self._current_bytes = 0
        self._stats = SinkStats()
        self._lock = threading.RLock()
        self._root_fd = self._open_root_directory()
        try:
            self._validate_root_anchor()
            self._current_bytes = self._stat_current_bytes()
        except BaseException:
            self._close_root_fd()
            raise

    def _open_root_directory(self) -> int:
        missing = [name for name in _REQUIRED_DIRFD_FLAGS if not hasattr(os, name)]
        if missing:
            raise PlatformUnsupportedError(
                "anchored directory I/O requires O_DIRECTORY, O_CLOEXEC, and O_NOFOLLOW"
            )
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW
        try:
            path_info = os.lstat(self.root)
        except OSError as error:
            raise ValueError("log root path was replaced") from error
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISDIR(path_info.st_mode)
            or path_info.st_dev != self._root_dev
            or path_info.st_ino != self._root_ino
        ):
            raise ValueError("log root must be a real non-symlink directory")
        try:
            fd = os.open(self.root, flags)
        except TypeError as error:
            raise PlatformUnsupportedError("directory-relative I/O is unsupported") from error
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISDIR(info.st_mode)
                or info.st_dev != self._root_dev
                or info.st_ino != self._root_ino
            ):
                raise OSError("log root fd is not a directory")
            try:
                os.lstat(self.filename, dir_fd=fd)
            except FileNotFoundError:
                pass
            except TypeError as error:
                raise PlatformUnsupportedError("directory-relative I/O is unsupported") from error
            return fd
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise

    def _close_root_fd(self) -> None:
        if self._root_fd >= 0:
            with suppress(OSError):
                os.close(self._root_fd)
            self._root_fd = -1

    def close(self) -> None:
        """Release the anchored root fd deterministically."""
        with self._lock:
            self._closed = True
            self._close_root_fd()

    def __del__(self) -> None:
        with suppress(Exception):
            self.close()

    def _validate_root_anchor(self) -> None:
        fd_info = os.fstat(self._root_fd)
        if (
            not stat.S_ISDIR(fd_info.st_mode)
            or fd_info.st_dev != self._root_dev
            or fd_info.st_ino != self._root_ino
        ):
            raise OSError("log root fd is not a directory")
        try:
            path_info = os.lstat(self.root)
        except OSError as error:
            raise ValueError("log root path was replaced") from error
        if (
            stat.S_ISLNK(path_info.st_mode)
            or not stat.S_ISDIR(path_info.st_mode)
            or path_info.st_dev != self._root_dev
            or path_info.st_ino != self._root_ino
            or path_info.st_dev != fd_info.st_dev
            or path_info.st_ino != fd_info.st_ino
        ):
            raise ValueError("log root path was replaced")

    def _lstat_rel(self, name: str):
        try:
            return os.lstat(name, dir_fd=self._root_fd)
        except FileNotFoundError:
            return None

    def _stat_current_bytes(self) -> int:
        info = self._lstat_rel(self.filename)
        if info is None:
            return 0
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("current log file must be a regular non-symlink file")
        return info.st_size

    def _validate_regular_rel(self, name: str) -> bool:
        """Require a nonexistent path or an existing regular non-symlink file."""
        info = self._lstat_rel(name)
        if info is None:
            return False
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise ValueError("log path must be a regular non-symlink file")
        return True

    def _open_append_rel(self, name: str):
        flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
        fd = os.open(name, flags, 0o600, dir_fd=self._root_fd)
        try:
            opened = os.fstat(fd)
            if not stat.S_ISREG(opened.st_mode):
                raise OSError("opened log path is not a regular file")
            try:
                before_open = os.lstat(name, dir_fd=self._root_fd)
            except FileNotFoundError:
                raise OSError("log path disappeared after open") from None
            if (
                not stat.S_ISREG(before_open.st_mode)
                or before_open.st_ino != opened.st_ino
                or before_open.st_dev != opened.st_dev
            ):
                raise OSError("log path identity changed after open")
            if stat.S_IMODE(opened.st_mode) != 0o600:
                os.fchmod(fd, 0o600)
            return os.fdopen(fd, "ab")
        except BaseException:
            with suppress(OSError):
                os.close(fd)
            raise

    @staticmethod
    def _short_write(handle: Any, data: bytes) -> None:
        view = memoryview(data)
        while view:
            written = handle.write(view)
            if type(written) is not int or written <= 0 or written > len(view):
                raise OSError("short write stalled")
            view = view[written:]

    def _fsync_dir(self) -> None:
        os.fsync(self._root_fd)

    def _exists_rel(self, name: str) -> bool:
        return self._lstat_rel(name) is not None

    def _rotate(self) -> None:
        slots = [f"{self.filename}.{index}" for index in range(1, self.max_retention + 1)]
        for slot in slots:
            self._validate_regular_rel(slot)
        performed: list[tuple[str, str]] = []
        uncertain = False
        try:
            for index in range(self.max_retention - 1, 0, -1):
                source = f"{self.filename}.{index}"
                target = f"{self.filename}.{index + 1}"
                if self._exists_rel(source):
                    os.replace(
                        source,
                        target,
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd,
                    )
                    performed.append((source, target))
            if self._exists_rel(self.filename):
                os.replace(
                    self.filename,
                    f"{self.filename}.1",
                    src_dir_fd=self._root_fd,
                    dst_dir_fd=self._root_fd,
                )
                performed.append((self.filename, f"{self.filename}.1"))
            self._current_bytes = 0
            self._fsync_dir()
        except Exception:
            for source, target in reversed(performed):
                try:
                    os.replace(
                        target,
                        source,
                        src_dir_fd=self._root_fd,
                        dst_dir_fd=self._root_fd,
                    )
                except Exception:
                    uncertain = True
            self._current_bytes = self._stat_current_bytes()
            raise RotationError(uncertain=uncertain) from None

    def write(self, event: Event) -> WriteResult:
        with self._lock:
            event_id = _safe_event_id(event)
            if self._closed:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")
            try:
                line = serialize_event(event)
            except TypeError:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_REJECTED")
            except Exception:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")
            size = len(line.encode("utf-8"))
            if size > self.max_bytes:
                self._stats = _bump(self._stats, events_dropped=1)
                return WriteResult(ok=False, event_id=event_id, dropped=True)
            try:
                self._validate_root_anchor()
                existing = self._stat_current_bytes()
                self._current_bytes = existing
                if existing + size > self.max_bytes:
                    self._rotate()
                self._validate_regular_rel(self.filename)
                with self._open_append_rel(self.filename) as handle:
                    self._short_write(handle, line.encode("utf-8"))
                    handle.flush()
                    os.fsync(handle.fileno())
                    final_size = os.fstat(handle.fileno()).st_size
                self._fsync_dir()
                self._current_bytes = final_size
                self._index += 1
                self._stats = _bump(
                    self._stats,
                    events_written=1,
                    bytes_written=size,
                )
                return WriteResult(ok=True, event_id=event_id)
            except RotationError as error:
                self._stats = _bump(self._stats, failures=1)
                code = "ROTATION_UNCERTAIN" if error.uncertain else "ROTATION_FAILED"
                return WriteResult(ok=False, event_id=event_id, error=code)
            except ValueError:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_REJECTED")
            except OSError:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")
            except Exception:
                self._stats = _bump(self._stats, failures=1)
                return WriteResult(ok=False, event_id=event_id, error="WRITE_FAILED")

    def stats(self) -> SinkStats:
        with self._lock:
            return self._stats.model_copy()


class CompositeSink:
    """Small fixed set of child sinks with fail-isolated results."""

    def __init__(
        self,
        children: list[BoundedMemorySink | LocalJsonlSink | TelemetryDisabledSink]
        | tuple[BoundedMemorySink | LocalJsonlSink | TelemetryDisabledSink, ...],
    ) -> None:
        if type(children) not in (list, tuple):
            raise ValueError("composite children must be an exact list or tuple")
        if len(children) > MAX_COMPOSITE_CHILDREN:
            raise ValueError("composite sink has too many children")
        for child in children:
            if type(child) not in (BoundedMemorySink, LocalJsonlSink, TelemetryDisabledSink):
                raise ValueError("composite children must be exact supported sinks")
        self.children = tuple(children)

    def write(self, event: Event) -> WriteResult:
        event_id = _safe_event_id(event)
        total_failures = 0
        for child in self.children:
            try:
                result = child.write(event)
                if not result.ok:
                    total_failures += 1
            except Exception:
                total_failures += 1
        return WriteResult(
            ok=total_failures == 0,
            event_id=event_id,
            error="COMPOSITE_CHILD_FAILED" if total_failures else None,
        )

    def stats(self) -> SinkStats:
        total = SinkStats()
        for child in self.children:
            try:
                child_stats = child.stats()
                total = total.model_copy(
                    update={
                        "events_written": total.events_written + child_stats.events_written,
                        "events_dropped": total.events_dropped + child_stats.events_dropped,
                        "bytes_written": total.bytes_written + child_stats.bytes_written,
                        "failures": total.failures + child_stats.failures,
                    }
                )
            except Exception:
                total = total.model_copy(update={"failures": total.failures + 1})
        return total


class TelemetryDisabledSink:
    """Explicit no-op sink: telemetry stays off, no network/remote transport."""

    def write(self, event: Event) -> WriteResult:
        return WriteResult(ok=True, event_id=_safe_event_id(event), dropped=True)

    def stats(self) -> SinkStats:
        return SinkStats()
