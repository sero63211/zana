"""Tests for deterministic exclusive operation guards."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.portability.guards import (
    MAX_LIVE_TARGET_LOCKS,
    ConcurrentOperationError,
    OperationGuard,
    in_process_lock_count,
)


def test_same_target_concurrent_acquisition_fails(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    with (
        guard.acquire("op-1", "target://one"),
        pytest.raises(ConcurrentOperationError) as exc,
        guard.acquire("op-2", "target://one"),
    ):
        pytest.fail("second acquisition should not succeed")
    assert exc.value.code in {"CONCURRENT_OPERATION", "GUARD_FILE_EXISTS"}
    assert list((tmp_path / "portability" / "locks").iterdir()) == []


def test_different_targets_can_run_sequentially(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    with guard.acquire("op-1", "target://a"):
        pass
    with guard.acquire("op-2", "target://b"):
        pass
    assert list((tmp_path / "portability" / "locks").iterdir()) == []


def test_guard_cleaned_on_exception(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    with pytest.raises(RuntimeError), guard.acquire("op-1", "target://one"):
        raise RuntimeError("boom")
    assert list((tmp_path / "portability" / "locks").iterdir()) == []


def test_guard_target_keys_are_hashed_and_confined(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    locks = tmp_path / "portability" / "locks"
    with guard.acquire("op-1", "../../escape"):
        names = [path.name for path in locks.iterdir()]
        assert len(names) == 1
        assert "../../escape" not in names[0]
        assert ".lock" in names[0]
    assert list(locks.iterdir()) == []


def test_unique_target_registry_returns_to_zero(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    for index in range(500):
        with guard.acquire(f"op-{index}", f"target://unique-{index}"):
            pass
    assert in_process_lock_count() == 0
    assert list((tmp_path / "portability" / "locks").iterdir()) == []


def test_guard_construction_has_no_filesystem_side_effect(tmp_path: Path) -> None:
    OperationGuard(tmp_path)
    assert not (tmp_path / "portability").exists()


def test_guard_lock_dir_created_lazily(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    with guard.acquire("op-1", "target://one"):
        assert (tmp_path / "portability" / "locks").is_dir()
    assert list((tmp_path / "portability" / "locks").iterdir()) == []


def test_live_lock_registry_has_hard_cap(tmp_path: Path) -> None:
    guard = OperationGuard(tmp_path)
    contexts = []
    try:
        for index in range(MAX_LIVE_TARGET_LOCKS):
            contexts.append(guard.acquire(f"op-{index}", f"target://{index}"))
            contexts[-1].__enter__()
        with (
            pytest.raises(ConcurrentOperationError) as exc,
            guard.acquire("op-over", "target://overflow"),
        ):
            pass
        assert exc.value.code == "LOCK_REGISTRY_FULL"
    finally:
        for context in reversed(contexts):
            context.__exit__(None, None, None)
    assert in_process_lock_count() == 0


def test_guard_file_mode_is_0600(tmp_path: Path) -> None:
    import hashlib

    guard = OperationGuard(tmp_path)
    target = "target://mode"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    with guard.acquire("op-1", target):
        guard_path = tmp_path / "portability" / "locks" / f"{digest}.lock"
        assert guard_path.is_file()
        assert (guard_path.stat().st_mode & 0o777) == 0o600


def test_symlink_guard_file_is_rejected(tmp_path: Path) -> None:
    import hashlib

    guard = OperationGuard(tmp_path)
    target = "target://symlink"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    locks_dir = tmp_path / "portability" / "locks"
    locks_dir.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.write_bytes(b"x")
    guard_path = locks_dir / f"{digest}.lock"
    guard_path.symlink_to(outside)
    with (
        pytest.raises(ConcurrentOperationError) as exc,
        guard.acquire("op-1", target),
    ):
        pass
    assert exc.value.code == "GUARD_FILE_EXISTS"
    assert outside.read_bytes() == b"x"


def test_guard_cleaned_when_write_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    def failing_fdopen(fd, mode="r", *args, **kwargs):
        os.close(fd)
        raise OSError("simulated fdopen failure")

    monkeypatch.setattr(os, "fdopen", failing_fdopen)
    guard = OperationGuard(tmp_path)
    with (
        pytest.raises(OSError, match="fdopen failure"),
        guard.acquire("op-1", "target://one"),
    ):
        pass
    monkeypatch.undo()
    assert in_process_lock_count() == 0
    locks = tmp_path / "portability" / "locks"
    assert not locks.exists() or list(locks.iterdir()) == []


def test_guard_cleaned_when_fsync_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os

    real_fsync = os.fsync

    def failing_fsync(fd):
        real_fsync(fd)
        raise OSError("simulated fsync failure")

    monkeypatch.setattr(os, "fsync", failing_fsync)
    guard = OperationGuard(tmp_path)
    with (
        pytest.raises(OSError, match="fsync failure"),
        guard.acquire("op-1", "target://one"),
    ):
        pass
    monkeypatch.undo()
    assert in_process_lock_count() == 0
    locks = tmp_path / "portability" / "locks"
    assert not locks.exists() or list(locks.iterdir()) == []


def test_unlink_failure_leaves_stale_guard_but_registry_zero(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from zana_core.portability import guards as guards_module

    def failing_unlink(fd, name):
        raise OSError("simulated unlink failure")

    monkeypatch.setattr(guards_module, "_unlink_guard_dirfd", failing_unlink)
    guard = OperationGuard(tmp_path)
    with (
        pytest.raises(OSError, match="unlink failure"),
        guard.acquire("op-1", "target://one"),
    ):
        pass
    monkeypatch.undo()
    assert in_process_lock_count() == 0
    locks = tmp_path / "portability" / "locks"
    stale = list(locks.iterdir())
    assert len(stale) == 1


def test_guard_filename_is_digest_only(tmp_path: Path) -> None:
    import hashlib

    guard = OperationGuard(tmp_path)
    target = "target://shared"
    digest = hashlib.sha256(target.encode("utf-8")).hexdigest()
    with guard.acquire("op-one", target):
        locks = list((tmp_path / "portability" / "locks").iterdir())
        assert len(locks) == 1
        assert locks[0].name == f"{digest}.lock"
    with guard.acquire("op-two", target):
        locks = list((tmp_path / "portability" / "locks").iterdir())
        assert len(locks) == 1
        assert locks[0].name == f"{digest}.lock"
        assert locks[0].read_text() == "op-two"
    assert in_process_lock_count() == 0


def test_guard_rejects_hostile_path_subclass(tmp_path: Path) -> None:
    from zana_core.portability.models import PortabilityError

    class EvilPath(type(tmp_path)):
        def __fspath__(self):
            raise AssertionError("fspath hook must not be invoked")

        def resolve(self, strict=False):
            raise AssertionError("resolve hook must not be invoked")

    with pytest.raises(PortabilityError):
        OperationGuard(EvilPath(tmp_path))


def test_guard_parent_replacement_never_touches_outside(tmp_path: Path, monkeypatch) -> None:
    import os

    from zana_core.portability import guards as guards_module

    real_open = os.open
    data_root = tmp_path / "data"
    data_root.mkdir()
    identity = (data_root.stat().st_dev, data_root.stat().st_ino)
    swapped = False

    def swapping_open(path, flags, mode=0o777, dir_fd=None):
        nonlocal swapped
        if dir_fd is not None and not swapped:
            try:
                info = os.fstat(dir_fd)
            except OSError:
                info = None
            if info is not None and (info.st_dev, info.st_ino) == identity:
                data_root.rename(tmp_path / "old-data")
                new_root = tmp_path / "data"
                new_root.mkdir()
                (new_root / "portability" / "locks").mkdir(parents=True)
                (new_root / "portability" / "locks" / "outside.lock").write_bytes(b"keep")
                swapped = True
        if dir_fd is not None:
            return real_open(path, flags, mode, dir_fd=dir_fd)
        return real_open(path, flags, mode)

    monkeypatch.setattr(guards_module.os, "open", swapping_open)
    guard = OperationGuard(data_root)
    with guard.acquire("op-1", "target://one"):
        pass
    monkeypatch.undo()
    assert (tmp_path / "data" / "portability" / "locks" / "outside.lock").read_text() == "keep"


def test_prohibited_guard_absolute_unlink_helper_is_absent() -> None:
    import zana_core.portability.guards as guards_module

    assert not hasattr(guards_module, "_unlink_guard")
    assert hasattr(guards_module, "_unlink_guard_dirfd")
