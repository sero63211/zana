"""Explicit shallow idempotent root creation tests."""

from __future__ import annotations

import pytest

from tests.platform.helpers import mac_layout, resolver_for
from zana_core.platform.ensure import ensure_roots
from zana_core.platform.models import (
    PathPolicy,
    PathRoot,
    PlatformPaths,
    PlatformPathValidationError,
)
from zana_core.platform.resolve import validate_root_set


def test_ensure_creates_only_exact_roots_idempotently(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    created = ensure_roots(paths)
    assert set(created) == set(paths.all_roots())
    for root in paths.all_roots():
        assert root.is_dir()
    second = ensure_roots(paths)
    assert set(second) == set(paths.all_roots())


def test_ensure_selected_kinds_only(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    created = ensure_roots(paths, kinds=(PathRoot.CONFIG, PathRoot.LOG))
    assert set(created) == {paths.config_root, paths.log_root}
    assert not paths.data_root.exists()


def test_ensure_never_recursively_scans(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    ensure_roots(paths)
    assert paths.workspace_root.is_dir()
    # Parent/child defaults are created exactly as roots; no extra children.
    children = list(paths.data_root.iterdir())
    assert children == [paths.workspace_root]


def test_ensure_failure_is_explicit(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    blocker = paths.data_root
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("file")
    with pytest.raises(PlatformPathValidationError) as exc:
        ensure_roots(paths)
    assert exc.value.code == "PATH_TYPE"
    # Zero partial creation: nothing else was created before the refusal.
    assert not paths.config_root.exists()
    assert not paths.cache_root.exists()


def test_ensure_rejects_existing_symlink_root_with_zero_partial_creation(tmp_path):
    layout = mac_layout(tmp_path)
    real = tmp_path / "real-data"
    real.mkdir()
    symlink = tmp_path / "data-link"
    symlink.symlink_to(real, target_is_directory=True)
    paths = resolver_for(layout, data=str(symlink)).resolve()
    with pytest.raises(PlatformPathValidationError) as exc:
        ensure_roots(paths)
    assert exc.value.code == "PATH_SYMLINK_ROOT"
    assert not paths.config_root.exists()
    assert not paths.cache_root.exists()


def test_ensure_rejects_non_directory_root_with_zero_partial_creation(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    blocker = tmp_path / "file-root"
    blocker.write_text("not a directory")
    paths = paths.model_copy(update={"workspace_root": blocker})
    with pytest.raises(PlatformPathValidationError) as exc:
        ensure_roots(paths)
    assert exc.value.code == "PATH_TYPE"
    assert not paths.config_root.exists()
    assert not paths.data_root.exists()


def test_ensure_invalid_root_set_creates_nothing(tmp_path):
    unsafe = PlatformPaths(
        config_root=tmp_path / "a",
        data_root=tmp_path / "b",
        cache_root=tmp_path / "b" / "nested",
        log_root=tmp_path / "d",
        temp_root=tmp_path / "e",
        workspace_root=tmp_path / "f",
    )
    policy = PathPolicy()
    with pytest.raises(PlatformPathValidationError) as exc:
        validate_root_set(unsafe, policy)
    assert exc.value.code == "PATH_PARENT_CHILD"
    with pytest.raises(PlatformPathValidationError):
        ensure_roots(unsafe, policy=policy)
    assert not unsafe.config_root.exists()
    assert not unsafe.data_root.exists()


def test_ensure_allows_declared_workspace_under_data(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    created = ensure_roots(paths)
    assert paths.workspace_root in created
    assert paths.data_root.is_dir()
    assert paths.workspace_root.is_dir()
