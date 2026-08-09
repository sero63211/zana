"""Sink tests: immutable records, bounded JSONL I/O, rotation, composite."""

from __future__ import annotations

import json
import os
import stat
import threading
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.observability.events import Event, EventKind, Severity
from zana_core.observability.serialization import serialize_event
from zana_core.observability.sinks import (
    BoundedMemorySink,
    CompositeSink,
    LocalJsonlSink,
    PlatformUnsupportedError,
    RedactedRecord,
    SinkStats,
    TelemetryDisabledSink,
    WriteResult,
)


def _event(message: str = "event", **overrides) -> Event:
    defaults = {"kind": EventKind.SYSTEM, "severity": Severity.INFO, "message": message}
    defaults.update(overrides)
    return Event(**defaults)


def _messages(path: Path) -> list[str]:
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8").splitlines()
    return [json.loads(line)["message"] for line in lines if line]


class TestStrictPublicTypes:
    def test_write_result_and_stats_reject_coercion(self) -> None:
        with pytest.raises(ValidationError):
            WriteResult(ok=1, event_id="x")
        with pytest.raises(ValidationError):
            SinkStats(events_written=True)
        with pytest.raises(ValidationError):
            SinkStats(events_written=-1)
        with pytest.raises(ValidationError):
            RedactedRecord(event_id="x", line="{}", bytes=True)

    def test_write_result_is_immutable(self) -> None:
        result = WriteResult(ok=True, event_id="op-1")
        with pytest.raises(ValidationError):
            result.ok = False  # type: ignore[misc]


class TestBoundedMemorySink:
    def test_count_and_byte_eviction(self) -> None:
        sink = BoundedMemorySink(max_events=3, max_bytes=400)
        for _ in range(6):
            sink.write(_event("x" * 80))
        snapshot = sink.snapshot(limit=100)
        assert len(snapshot) <= 3
        assert sink.stats().events_dropped >= 3

    def test_snapshot_copy_limit(self) -> None:
        sink = BoundedMemorySink(max_events=100, max_bytes=100000)
        for index in range(10):
            sink.write(_event(f"e{index}"))
        assert len(sink.snapshot(limit=3)) == 3
        assert len(sink.snapshot(limit=1000)) == 10
        assert sink.snapshot(limit=0) == ()

    def test_constructor_and_snapshot_bounds(self) -> None:
        with pytest.raises(ValueError):
            BoundedMemorySink(max_events=0, max_bytes=100)
        with pytest.raises(ValueError):
            BoundedMemorySink(max_events=1, max_bytes=0)
        with pytest.raises(ValueError):
            BoundedMemorySink(max_events=True, max_bytes=100)
        with pytest.raises(ValueError):
            BoundedMemorySink(max_events=10001, max_bytes=1000)
        with pytest.raises(ValueError):
            BoundedMemorySink(max_events=1, max_bytes=64 * 1024 * 1024 + 1)
        sink = BoundedMemorySink(max_events=1, max_bytes=100000)
        with pytest.raises(ValueError):
            sink.snapshot(limit=-1)
        with pytest.raises(ValueError):
            sink.snapshot(limit=1.5)

    def test_single_event_over_max_dropped_not_success(self) -> None:
        sink = BoundedMemorySink(max_events=1, max_bytes=10)
        result = sink.write(_event("x" * 100))
        assert result.ok is False
        assert result.dropped is True
        assert sink.snapshot() == ()

    def test_stored_records_are_redacted_and_never_original_event(self) -> None:
        sink = BoundedMemorySink(max_events=10, max_bytes=100000)
        event = _event("hello", payload={"token": "super-secret", "path": "/private/doc.md"})
        sink.write(event)
        records = sink.snapshot()
        assert isinstance(records, tuple)
        assert isinstance(records[0], RedactedRecord)
        assert not isinstance(records[0], Event)
        assert "super-secret" not in records[0].line
        assert "doc.md" in records[0].line

    def test_held_byte_accounting_is_exact(self) -> None:
        sink = BoundedMemorySink(max_events=100, max_bytes=10000)
        size = len(serialize_event(_event("x" * 20)).encode("utf-8"))
        for _ in range(5):
            sink.write(_event("x" * 20))
        assert sink.held_bytes() == 5 * size
        assert sink.stats().bytes_written == 5 * size
        assert sink.event_count() == 5

        evicting = BoundedMemorySink(max_events=2, max_bytes=10000)
        for _ in range(5):
            evicting.write(_event("x" * 20))
        assert evicting.event_count() == 2
        assert evicting.held_bytes() == 2 * size
        assert evicting.stats().bytes_written == 5 * size
        assert evicting.stats().events_dropped == 3

    def test_post_construction_mutation_cannot_reach_stored_record(self) -> None:
        raw: dict[str, object] = {"token": "original-secret"}
        event = _event("hello", payload=raw)
        sink = BoundedMemorySink(max_events=2, max_bytes=100000)
        sink.write(event)
        raw["token"] = "changed-secret"
        line = sink.snapshot()[0].line
        assert "original-secret" not in line
        assert "changed-secret" not in line

    def test_hostile_event_write_is_generic(self) -> None:
        sink = BoundedMemorySink(max_events=2, max_bytes=100000)
        result = sink.write(object())  # type: ignore[arg-type]
        assert result.ok is False
        assert result.error == "WRITE_REJECTED"
        assert sink.stats().failures == 1

    def test_concurrent_writes_have_exact_stats_and_bounds(self) -> None:
        sink = BoundedMemorySink(max_events=500, max_bytes=1_000_000)
        errors: list[Exception] = []

        def worker(offset: int) -> None:
            try:
                for index in range(50):
                    sink.write(_event(f"t{offset}-{index}", payload={"n": index}))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(offset,)) for offset in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        stats = sink.stats()
        assert stats.events_written == 400
        assert stats.failures == 0
        assert sink.event_count() <= 500
        assert sink.held_bytes() <= 1_000_000
        assert len(sink.snapshot()) <= 200


class TestLocalJsonlSink:
    def test_append_and_rotation(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=500,
            max_retention=2,
        )
        for _ in range(8):
            result = sink.write(_event("x" * 40))
            assert result.ok is True
        assert sink.stats().events_written == 8
        assert sink.stats().failures == 0
        files = [name for name in tmp_path.iterdir() if name.name.startswith("events.jsonl")]
        assert len(files) >= 1

    def test_retention_cap(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=500,
            max_retention=2,
        )
        for _ in range(20):
            sink.write(_event("y" * 50))
        rotated = [name for name in tmp_path.iterdir() if name.name.startswith("events.jsonl.")]
        assert len(rotated) <= 2

    def test_rotation_never_lists_log_root(self, tmp_path: Path, monkeypatch) -> None:
        def fail_listdir(_path):
            raise AssertionError("os.listdir must not be used")

        monkeypatch.setattr("os.listdir", fail_listdir)
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=500,
            max_retention=3,
        )
        for _ in range(10):
            result = sink.write(_event("z" * 50))
            assert result.ok is True

    def test_symlink_and_traversal_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.jsonl"
        outside.write_text("", encoding="utf-8")
        (tmp_path / "link.jsonl").symlink_to(outside)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="link.jsonl")

    def test_write_failure_returns_result(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.jsonl"
        outside.write_text("", encoding="utf-8")
        (tmp_path / "events.jsonl.1").symlink_to(outside)
        (tmp_path / "events.jsonl").write_text("x" * 900, encoding="utf-8")
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=500)
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_REJECTED"

    def test_constructor_bounds(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=0)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_retention=0)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_retention=21)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", flush_every=2)
        with pytest.raises(ValueError):
            LocalJsonlSink(
                log_root=tmp_path,
                filename="events.jsonl",
                max_bytes=64 * 1024 * 1024 + 1,
            )
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path / "missing", filename="events.jsonl")
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=True)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", flush_every=False)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", flush_every=True)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", flush_every=1.0)

    def test_root_must_be_real_non_symlink_directory(self, tmp_path: Path) -> None:
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "root-link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=link, filename="events.jsonl")
        nested = tmp_path / "nested"
        nested.mkdir()
        nested_link = nested / "inner-link"
        nested_link.symlink_to(real, target_is_directory=True)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=nested_link, filename="events.jsonl")
        regular_file = tmp_path / "not-a-dir"
        regular_file.write_text("x", encoding="utf-8")
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=regular_file, filename="events.jsonl")

    def test_root_parent_swap_fails_closed_write(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sink = LocalJsonlSink(log_root=root, filename="events.jsonl")
        moved = tmp_path / "moved"
        root.rename(moved)
        (tmp_path / "root").symlink_to(outside, target_is_directory=True)

        result = sink.write(_event())

        assert result.ok is False
        assert result.error == "WRITE_REJECTED"
        assert sink.stats().failures == 1
        assert not (outside / "events.jsonl").exists()
        assert not (moved / "events.jsonl").exists()

    def test_root_parent_swap_fails_closed_rotation(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        (root / "events.jsonl").write_text("current", encoding="utf-8")
        (root / "events.jsonl.1").write_text("history", encoding="utf-8")
        outside = tmp_path / "outside"
        outside.mkdir()
        max_bytes = len(serialize_event(_event()).encode("utf-8"))
        sink = LocalJsonlSink(log_root=root, filename="events.jsonl", max_bytes=max_bytes)
        moved = tmp_path / "moved"
        root.rename(moved)
        (tmp_path / "root").symlink_to(outside, target_is_directory=True)

        result = sink.write(_event())

        assert result.ok is False
        assert result.error == "WRITE_REJECTED"
        assert sink.stats().failures == 1
        assert not (outside / "events.jsonl").exists()
        assert not (outside / "events.jsonl.1").exists()
        assert (moved / "events.jsonl").read_text(encoding="utf-8") == "current"
        assert (moved / "events.jsonl.1").read_text(encoding="utf-8") == "history"

    def test_root_parent_swap_fsync_uses_anchored_fd(self, tmp_path: Path) -> None:
        root = tmp_path / "root"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        sink = LocalJsonlSink(log_root=root, filename="events.jsonl")
        moved = tmp_path / "moved"
        root.rename(moved)
        (tmp_path / "root").symlink_to(outside, target_is_directory=True)

        sink._fsync_dir()

        assert not (outside / "events.jsonl").exists()
        assert not (moved / "events.jsonl").exists()

    def test_close_releases_root_and_fails_later_writes(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        fd = sink._root_fd
        sink.close()
        assert sink._root_fd == -1
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"
        assert sink.stats().failures == 1
        with pytest.raises(OSError):
            os.fstat(fd)

    def test_platform_unsupported_fails_typed(self, tmp_path: Path, monkeypatch) -> None:
        def fail_open(*args, **kwargs):
            raise TypeError("dir_fd unsupported")

        monkeypatch.setattr(os, "open", fail_open)
        with pytest.raises(PlatformUnsupportedError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")

    def test_safe_filename_required(self, tmp_path: Path) -> None:
        for bad in (
            "sub/events.jsonl",
            "../events.jsonl",
            "/events.jsonl",
            "..",
            ".",
            "",
            "bad\\name",
            "bad\nname",
            ".hidden",
            "x" * 129,
        ):
            with pytest.raises(ValueError):
                LocalJsonlSink(log_root=tmp_path, filename=bad)

    def test_rotated_history_persists_in_exact_order(self, tmp_path: Path) -> None:
        line = serialize_event(_event("seed"))
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=len(line.encode("utf-8")) - 1,
            max_retention=2,
        )
        for index in range(5):
            assert sink.write(_event(f"e{index}")).ok is True
        assert _messages(tmp_path / "events.jsonl") == ["e4"]
        assert _messages(tmp_path / "events.jsonl.1") == ["e3"]
        assert _messages(tmp_path / "events.jsonl.2") == ["e2"]
        assert not (tmp_path / "events.jsonl.3").exists()

    def test_symlink_target_untouched(self, tmp_path: Path) -> None:
        outside = tmp_path / "outside.jsonl"
        outside.write_text("private", encoding="utf-8")
        (tmp_path / "events.jsonl").symlink_to(outside)
        with pytest.raises(ValueError):
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        assert outside.read_text(encoding="utf-8") == "private"

    @pytest.mark.skipif(os.name != "posix", reason="mode check is POSIX-specific")
    def test_file_mode_0600(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        assert sink.write(_event()).ok is True
        mode = (tmp_path / "events.jsonl").stat().st_mode & 0o777
        assert mode == 0o600

    def test_existing_oversized_file_rotates_before_append(self, tmp_path: Path) -> None:
        (tmp_path / "events.jsonl").write_text("x" * 600, encoding="utf-8")
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=500,
            max_retention=2,
        )
        result = sink.write(_event("after-restart"))
        assert result.ok is True
        current = (tmp_path / "events.jsonl").stat().st_size
        assert current <= 500

    def test_open_uses_nofollow_and_cloexec(self, tmp_path: Path, monkeypatch) -> None:
        real_open = os.open
        captured: list[int] = []

        def fake_open(path, flags, mode=0o777, **kwargs):
            captured.append(flags)
            return real_open(path, flags, mode, **kwargs)

        monkeypatch.setattr(os, "open", fake_open)
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        assert sink.write(_event()).ok is True
        create_flags = [flags for flags in captured if flags & os.O_CREAT]
        assert create_flags
        assert all(flags & os.O_NOFOLLOW for flags in create_flags)
        assert all(flags & os.O_CLOEXEC for flags in create_flags)

    def test_nonregular_fd_rejected(self, tmp_path: Path, monkeypatch) -> None:
        class FakeStat:
            st_mode = stat.S_IFIFO
            st_ino = 1
            st_dev = 1
            st_size = 0

        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        monkeypatch.setattr(os, "fstat", lambda fd: FakeStat())
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"
        assert sink.stats().failures == 1

    def test_fd_identity_mismatch_rejected(self, tmp_path: Path, monkeypatch) -> None:
        real_fstat = os.fstat

        class FakeStat:
            st_mode = stat.S_IFREG | 0o600
            st_ino = 999999
            st_dev = 1
            st_size = 0

        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        monkeypatch.setattr(os, "fstat", lambda fd: FakeStat())
        assert real_fstat is not None
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"

    def test_short_write_loop_writes_full_bytes(self, tmp_path: Path, monkeypatch) -> None:
        class FakeWriter:
            def __init__(self, fd):
                self.fd = fd
                self.parts: list[bytes] = []

            def __enter__(self):
                return self

            def __exit__(self, exc_type, exc, traceback):
                self.close()

            def write(self, view):
                count = min(3, len(view))
                self.parts.append(bytes(view[:count]))
                return count

            def flush(self):
                pass

            def fileno(self):
                return self.fd

            def close(self):
                os.close(self.fd)

        fake = FakeWriter(os.open(tmp_path / "events.jsonl", os.O_CREAT | os.O_WRONLY, 0o600))
        monkeypatch.setattr(os, "fdopen", lambda fd, mode: fake)
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=1_000_000)
        event = _event("short", timestamp=datetime(2026, 1, 1, tzinfo=UTC))
        line = serialize_event(event)
        assert sink.write(event).ok is True
        assert b"".join(fake.parts) == line.encode("utf-8")

    def test_short_write_requires_exact_int_progress(self, tmp_path: Path, monkeypatch) -> None:
        class EvilInt(int):
            pass

        results = [EvilInt(1), True, 0, -1, lambda view: len(view) + 1]

        for index, bad_result in enumerate(results):

            class FlakyWriter:
                def __init__(self, fd):
                    self.fd = fd

                def __enter__(self):
                    return self

                def __exit__(self, exc_type, exc, traceback):
                    self.close()

                def write(self, view, bad=bad_result):
                    return bad(view) if callable(bad) else bad

                def flush(self):
                    pass

                def fileno(self):
                    return self.fd

                def close(self):
                    os.close(self.fd)

            root = tmp_path / f"short-write-{index}"
            root.mkdir()
            monkeypatch.setattr(os, "fdopen", lambda fd, mode: FlakyWriter(fd))
            sink = LocalJsonlSink(log_root=root, filename="events.jsonl")
            result = sink.write(_event())
            assert result.ok is False
            assert result.error == "WRITE_FAILED"
            assert sink.stats().failures == 1

    def test_fdopen_cleanup_on_failure(self, tmp_path: Path, monkeypatch) -> None:
        closed: list[int] = []
        real_close = os.close

        def record_close(fd):
            closed.append(fd)
            real_close(fd)

        def fail_fdopen(fd, mode):
            raise OSError("fdopen failed")

        monkeypatch.setattr(os, "close", record_close)
        monkeypatch.setattr(os, "fdopen", fail_fdopen)
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"
        assert closed

    def test_flush_fsync_and_close_failures_return_generic(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        for hook in ("flush", "close"):

            def make_writer(fd, failing: str = hook):
                class FlakyWriter:
                    def __init__(self, file_fd):
                        self.fd = file_fd

                    def write(self, view):
                        os.write(self.fd, bytes(view))
                        return len(view)

                    def flush(self):
                        if failing == "flush":
                            raise OSError("flush failed")

                    def fileno(self):
                        return self.fd

                    def close(self):
                        if failing == "close":
                            raise OSError("close failed")
                        os.close(self.fd)

                return FlakyWriter(fd)

            monkeypatch.setattr(os, "fdopen", lambda fd, mode: make_writer(fd))
            root = tmp_path / f"case-{hook}"
            root.mkdir()
            sink = LocalJsonlSink(log_root=root, filename="events.jsonl")
            result = sink.write(_event())
            assert result.ok is False
            assert result.error == "WRITE_FAILED"
            assert sink.stats().failures == 1

        def fail_fsync(fd):
            raise OSError("fsync failed")

        monkeypatch.setattr(os, "fsync", fail_fsync)
        fsync_root = tmp_path / "case-fsync"
        fsync_root.mkdir()
        sink = LocalJsonlSink(log_root=fsync_root, filename="events.jsonl")
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"
        assert sink.stats().failures == 1

    def test_parent_dir_fsync_called(self, tmp_path: Path, monkeypatch) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        calls = []
        monkeypatch.setattr(sink, "_fsync_dir", lambda: calls.append(1))
        assert sink.write(_event()).ok is True
        assert calls

    def test_rename_failure_leaves_current_content(self, tmp_path: Path, monkeypatch) -> None:
        current = tmp_path / "events.jsonl"
        current.write_text("prior-content", encoding="utf-8")
        max_bytes = len(serialize_event(_event()).encode("utf-8"))
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=max_bytes)

        def fail_replace(source, target, **kwargs):
            raise OSError("replace failed")

        monkeypatch.setattr(os, "replace", fail_replace)
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "ROTATION_FAILED"
        assert current.read_text(encoding="utf-8") == "prior-content"
        assert sink.stats().failures == 1

    def test_rotation_failure_rolls_back_prior_shifts(self, tmp_path: Path, monkeypatch) -> None:
        current = tmp_path / "events.jsonl"
        first = tmp_path / "events.jsonl.1"
        second = tmp_path / "events.jsonl.2"
        current.write_text("current-data", encoding="utf-8")
        first.write_text("history-1", encoding="utf-8")
        second.write_text("history-2", encoding="utf-8")
        max_bytes = len(serialize_event(_event()).encode("utf-8"))
        real_replace = os.replace
        call_count = 0

        def failing_replace(source, target, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 2:
                raise OSError("second shift failed")
            real_replace(source, target, **kwargs)

        monkeypatch.setattr(os, "replace", failing_replace)
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=max_bytes,
            max_retention=3,
        )
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "ROTATION_FAILED"
        assert current.read_text(encoding="utf-8") == "current-data"
        assert first.read_text(encoding="utf-8") == "history-1"
        assert second.read_text(encoding="utf-8") == "history-2"

    def test_rotation_never_unlinks(self, tmp_path: Path, monkeypatch) -> None:
        def fail_unlink(_path):
            raise AssertionError("rotation must never unlink")

        monkeypatch.setattr(os, "unlink", fail_unlink)
        line = serialize_event(_event("seed"))
        sink = LocalJsonlSink(
            log_root=tmp_path,
            filename="events.jsonl",
            max_bytes=len(line.encode("utf-8")) - 1,
            max_retention=3,
        )
        for index in range(8):
            assert sink.write(_event(f"e{index}")).ok is True

    def test_dir_fsync_failure_reported(self, tmp_path: Path, monkeypatch) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")

        def fail_fsync_dir():
            raise OSError("dir fsync failed")

        monkeypatch.setattr(sink, "_fsync_dir", fail_fsync_dir)
        result = sink.write(_event())
        assert result.ok is False
        assert result.error == "WRITE_FAILED"

    def test_hostile_event_write_is_generic(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        result = sink.write(object())  # type: ignore[arg-type]
        assert result.ok is False
        assert result.event_id == ""
        assert result.error == "WRITE_REJECTED"
        assert "object" not in str(result.error)

    def test_concurrent_writes_are_complete_and_truthful(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=100000)
        errors: list[Exception] = []

        def worker(offset: int) -> None:
            try:
                for index in range(10):
                    sink.write(_event(f"t{offset}-{index}"))
            except Exception as error:  # noqa: BLE001
                errors.append(error)

        threads = [threading.Thread(target=worker, args=(offset,)) for offset in range(4)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()
        assert errors == []
        messages = _messages(tmp_path / "events.jsonl")
        assert len(messages) == 40
        assert sink.stats().events_written == 40
        assert sink.stats().failures == 0


class TestCompositeAndDisabled:
    def test_composite_isolation(self, tmp_path: Path) -> None:
        memory = BoundedMemorySink(max_events=5, max_bytes=100000)
        outside = tmp_path / "outside.jsonl"
        outside.write_text("", encoding="utf-8")
        (tmp_path / "events.jsonl.1").symlink_to(outside)
        (tmp_path / "events.jsonl").write_text("x" * 900, encoding="utf-8")
        failing = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl", max_bytes=500)
        composite = CompositeSink([memory, failing])
        result = composite.write(_event())
        assert result.ok is False
        assert result.error == "COMPOSITE_CHILD_FAILED"
        assert memory.stats().events_written == 1
        assert failing.stats().failures == 1

    def test_composite_child_limit(self) -> None:
        children = [TelemetryDisabledSink() for _ in range(9)]
        with pytest.raises(ValueError):
            CompositeSink(children)  # type: ignore[arg-type]

    def test_composite_rejects_hostile_container_and_children(self) -> None:
        with pytest.raises(ValueError):
            CompositeSink({TelemetryDisabledSink()})  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            CompositeSink(iter([TelemetryDisabledSink()]))  # type: ignore[arg-type]

        class SinkSubclass(BoundedMemorySink):
            pass

        with pytest.raises(ValueError):
            CompositeSink([SinkSubclass(max_events=1, max_bytes=100)])  # type: ignore[list-item]

    def test_composite_accepts_exact_tuple(self) -> None:
        memory = BoundedMemorySink(max_events=1, max_bytes=100000)
        composite = CompositeSink((memory,))
        assert composite.write(_event()).ok is True
        assert composite.children == (memory,)

    def test_composite_isolates_exceptions(self) -> None:
        memory = BoundedMemorySink(max_events=1, max_bytes=100000)

        def fail_write(_event):
            raise RuntimeError("raw detail")

        memory.write = fail_write  # type: ignore[method-assign]
        composite = CompositeSink([memory, TelemetryDisabledSink()])
        result = composite.write(_event())
        assert result.ok is False
        assert result.error == "COMPOSITE_CHILD_FAILED"
        assert "raw detail" not in str(result)

    def test_telemetry_disabled_sink(self) -> None:
        sink = TelemetryDisabledSink()
        result = sink.write(_event())
        assert result.dropped is True
        assert sink.stats().events_written == 0

    def test_composite_children_are_immutable_copy(self) -> None:
        memory = BoundedMemorySink(max_events=2, max_bytes=100000)
        composite = CompositeSink([memory])
        assert composite.children[0] is memory
        assert composite.children == (memory,)


class TestCorruptedEventSinks:
    def test_hostile_tzinfo_hook_never_invoked(self, tmp_path: Path) -> None:
        from datetime import tzinfo

        class EvilTZ(tzinfo):
            def __init__(self) -> None:
                self.hooks: list[str] = []

            def utcoffset(self, dt):
                self.hooks.append("utcoffset")
                raise AssertionError("hostile utcoffset must not run")

            def dst(self, dt):
                self.hooks.append("dst")
                raise AssertionError("hostile dst must not run")

            def tzname(self, dt):
                self.hooks.append("tzname")
                return "secret"

            def __repr__(self):
                self.hooks.append("repr")
                return "secret"

        evil = EvilTZ()
        event = _event(payload={"token": "super-secret"})
        object.__setattr__(event, "timestamp", datetime(2026, 1, 1, tzinfo=evil))
        for sink in (
            BoundedMemorySink(max_events=2, max_bytes=100000),
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl"),
            TelemetryDisabledSink(),
        ):
            result = sink.write(event)
            assert result.ok is True
            assert result.event_id == ""
        assert evil.hooks == []

    def test_missing_operation_id_never_raises(self, tmp_path: Path) -> None:
        event = _event(operation_id="op-1")
        object.__delattr__(event, "operation_id")
        sinks: list[object] = [
            BoundedMemorySink(max_events=2, max_bytes=100000),
            LocalJsonlSink(log_root=tmp_path, filename="events.jsonl"),
            TelemetryDisabledSink(),
        ]
        composite = CompositeSink(
            [
                BoundedMemorySink(max_events=2, max_bytes=100000),
                LocalJsonlSink(log_root=tmp_path, filename="events-composite.jsonl"),
                TelemetryDisabledSink(),
            ]
        )
        sinks.append(composite)
        for sink in sinks:
            result = sink.write(event)
            assert isinstance(result, WriteResult)
            assert result.event_id == ""
        assert (tmp_path / "events.jsonl").exists()
        line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert json.loads(line.strip())["recovery_code"] == "EVENT_OVERSIZE"
        assert (tmp_path / "events-composite.jsonl").exists()
        line = (tmp_path / "events-composite.jsonl").read_text(encoding="utf-8")
        assert json.loads(line.strip())["recovery_code"] == "EVENT_OVERSIZE"

    def test_corrupt_operation_id_never_raises(self, tmp_path: Path) -> None:
        event = _event(operation_id="op-1")
        object.__setattr__(event, "operation_id", object())
        sink = BoundedMemorySink(max_events=2, max_bytes=100000)
        result = sink.write(event)
        assert result.ok is True
        assert result.event_id == ""

    def test_control_bearing_operation_id_never_echoed(self, tmp_path: Path) -> None:
        forged = ("safe\nforged", "x\x7fsecret", "tab\tid", "cr\rid", "nul\x00id")
        for bad in forged:
            event = _event()
            object.__setattr__(event, "operation_id", bad)
            for sink in (
                BoundedMemorySink(max_events=2, max_bytes=100000),
                LocalJsonlSink(log_root=tmp_path, filename=f"sink-{ord(bad[0])}.jsonl"),
                TelemetryDisabledSink(),
            ):
                result = sink.write(event)
                assert isinstance(result, WriteResult)
                assert result.event_id == ""

    def test_oversized_operation_id_never_echoed(self, tmp_path: Path) -> None:
        event = _event(operation_id="op-1")
        object.__setattr__(event, "operation_id", "é" * 200)
        sink = BoundedMemorySink(max_events=2, max_bytes=100000)
        result = sink.write(event)
        assert result.event_id == ""
        assert "é" * 10 not in result.event_id

    def test_valid_operation_id_preserved_across_public_sinks(self, tmp_path: Path) -> None:
        event = _event(operation_id="op-valid-123")
        for sink in (
            BoundedMemorySink(max_events=2, max_bytes=100000),
            LocalJsonlSink(log_root=tmp_path, filename="events-valid.jsonl"),
            TelemetryDisabledSink(),
        ):
            result = sink.write(event)
            assert result.event_id == "op-valid-123"

    def test_memory_sink_stores_only_fixed_dropped_record(self) -> None:
        sink = BoundedMemorySink(max_events=2, max_bytes=100000)
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="x",
            schema_version=0,
            payload={"token": "super-secret"},
        )
        result = sink.write(event)
        assert result.ok is True
        records = sink.snapshot()
        assert len(records) == 1
        parsed = json.loads(records[0].line)
        assert parsed["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in records[0].line

    def test_local_sink_writes_only_fixed_dropped_record(self, tmp_path: Path) -> None:
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        event = Event.model_construct(
            kind=EventKind.SYSTEM,
            severity=Severity.INFO,
            message="x",
            duration_ms=-1,
            payload={"token": "super-secret"},
        )
        result = sink.write(event)
        assert result.ok is True
        line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        parsed = json.loads(line.strip())
        assert parsed == {
            "schema_version": 1,
            "kind": "system",
            "severity": "warning",
            "message": "event dropped: serialization bound exceeded",
            "operation_id": "",
            "job_id": "",
            "phase": "",
            "recovery_code": "EVENT_OVERSIZE",
        }
        assert "super-secret" not in line

    def test_hostile_field_hooks_never_invoked(self, tmp_path: Path) -> None:
        class Evil:
            def __init__(self) -> None:
                self.hooks: list[str] = []

            def __index__(self):
                self.hooks.append("index")
                raise AssertionError("index hook")

            def __int__(self):
                self.hooks.append("int")
                raise AssertionError("int hook")

            def __repr__(self):
                self.hooks.append("repr")
                return "secret"

            def __hash__(self):
                self.hooks.append("hash")
                return 1

            def __eq__(self, other):
                self.hooks.append("eq")
                return False

        evil = Evil()
        event = _event(payload={"token": "super-secret"})
        object.__setattr__(event, "duration_ms", evil)
        sink = LocalJsonlSink(log_root=tmp_path, filename="events.jsonl")
        result = sink.write(event)
        assert result.ok is True
        line = (tmp_path / "events.jsonl").read_text(encoding="utf-8")
        assert json.loads(line.strip())["recovery_code"] == "EVENT_OVERSIZE"
        assert "super-secret" not in line
        assert evil.hooks == []
