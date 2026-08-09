"""Resolver tests: OS layouts, overrides, unsafe roots, no mutation."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.platform.helpers import (
    linux_layout,
    mac_layout,
    resolver_for,
    windows_layout,
)
from zana_core.platform.models import (
    PathPolicy,
    PathRoot,
    PlatformPathValidationError,
)
from zana_core.platform.resolve import FixedPathLocator, PathResolver, is_within


@pytest.mark.parametrize(
    ("layout_name", "layout_builder"),
    [
        ("macos", mac_layout),
        ("linux", linux_layout),
        ("windows", windows_layout),
    ],
)
def test_three_os_layouts_resolve_without_mutation(tmp_path, layout_name, layout_builder):
    layout = layout_builder(tmp_path)
    paths = resolver_for(layout).resolve()
    for kind, expected in layout.items():
        assert paths.root(kind) == Path(expected)
    # Resolution must never create directories.
    created = [path for path in tmp_path.rglob("*") if path.is_dir()]
    assert created == []


def test_explicit_overrides_replace_locator_paths(tmp_path):
    layout = mac_layout(tmp_path)
    override = tmp_path / "custom" / "data"
    paths = resolver_for(layout, data=str(override)).resolve()
    assert paths.data_root == override
    assert paths.config_root == layout[PathRoot.CONFIG]
    # No directory created.
    assert not override.exists()


def test_relative_override_rejected(tmp_path):
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(mac_layout(tmp_path), data="relative/path")
    assert exc.value.code == "PATH_RELATIVE"


def test_filesystem_home_and_cwd_roots_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    for raw in (Path("/"), Path.home(), Path.cwd()):
        with pytest.raises(PlatformPathValidationError) as exc:
            resolver_for(layout, data=str(raw))
        assert exc.value.code == "PATH_UNSAFE_ROOT"


def test_traversal_override_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout, data=str(tmp_path / ".." / "escape"))
    assert exc.value.code == "PATH_TRAVERSAL"
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout, data=f"{tmp_path}/./data")
    assert exc.value.code == "PATH_TRAVERSAL"


def test_nul_override_rejected(tmp_path):
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(mac_layout(tmp_path), data=str(tmp_path) + "\x00evil")
    assert exc.value.code == "PATH_NUL"


def test_alias_collision_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    shared = tmp_path / "shared"
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout, data=str(shared), cache=str(shared))
    assert exc.value.code == "PATH_ALIAS_COLLISION"


def test_workspace_under_data_override_allowed(tmp_path):
    layout = mac_layout(tmp_path)
    parent = tmp_path / "parent"
    child = parent / "child"
    paths = resolver_for(layout, data=str(parent), workspace=str(child)).resolve()
    assert paths.workspace_root == child
    assert paths.data_root == parent


def test_reversed_containment_override_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    parent = tmp_path / "parent"
    child = parent / "child"
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout, data=str(child), workspace=str(parent))
    assert exc.value.code == "PATH_PARENT_CHILD"


def test_confinement_roots_enforced(tmp_path):
    layout = mac_layout(tmp_path)
    confine = tmp_path / "confine"
    outside = tmp_path / "outside"
    policy = PathPolicy(confinement_roots=(tmp_path,))
    PathResolver(
        FixedPathLocator(layout),
        policy,
        overrides={"data": tmp_path / "custom" / "data"},
    ).resolve()
    with pytest.raises(PlatformPathValidationError) as exc:
        PathResolver(
            FixedPathLocator(layout),
            PathPolicy(confinement_roots=(confine,)),
            overrides={"data": outside},
        )
    assert exc.value.code == "PATH_UNCONFINED"


def test_defaults_do_not_require_actual_home(tmp_path):
    layout = mac_layout(tmp_path / "fake-home")
    paths = resolver_for(layout).resolve()
    assert paths.config_root == tmp_path / "fake-home" / "Library" / "Preferences" / "zana"


def test_locator_default_filesystem_root_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    layout[PathRoot.DATA] = Path("/")
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_UNSAFE_ROOT"


def test_locator_default_home_root_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    layout[PathRoot.DATA] = Path.home()
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_UNSAFE_ROOT"


def test_locator_default_cwd_root_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    layout[PathRoot.DATA] = Path.cwd()
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_UNSAFE_ROOT"


def test_locator_default_duplicate_roots_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    shared = layout[PathRoot.CONFIG]
    layout[PathRoot.DATA] = shared
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_ALIAS_COLLISION"


def test_locator_default_unsafe_parent_child_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    layout[PathRoot.CACHE] = layout[PathRoot.CONFIG] / "nested"
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_PARENT_CHILD"


def test_locator_default_reversed_containment_rejected(tmp_path):
    layout = mac_layout(tmp_path)
    workspace = layout[PathRoot.WORKSPACE]
    layout[PathRoot.DATA] = workspace / "data"
    with pytest.raises(PlatformPathValidationError) as exc:
        resolver_for(layout).resolve()
    assert exc.value.code == "PATH_PARENT_CHILD"


def test_allowed_workspace_under_data_is_preserved(tmp_path):
    layout = mac_layout(tmp_path)
    paths = resolver_for(layout).resolve()
    assert is_within(paths.data_root, paths.workspace_root)
    assert paths.workspace_root != paths.data_root
