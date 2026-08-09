"""Explicit shallow idempotent root creation tests."""

from __future__ import annotations

from tests.platform.helpers import mac_layout, resolver_for
from zana_core.platform.ensure import ensure_roots
from zana_core.platform.models import PathRoot, PlatformPathError


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
    paths = paths.model_copy(update={"data_root": blocker / "nested"})
    try:
        ensure_roots(paths)
        raise AssertionError("expected PlatformPathError")
    except PlatformPathError:
        pass
