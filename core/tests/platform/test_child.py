"""Safe child-path derivation tests."""

from __future__ import annotations

import pytest

from zana_core.platform.models import PathPolicy, PlatformPathValidationError
from zana_core.platform.resolve import derive_child, is_within


def test_valid_child_path(tmp_path):
    child = derive_child(tmp_path, "builds", "job-1", "blob.bin")
    assert child == tmp_path / "builds" / "job-1" / "blob.bin"
    assert is_within(tmp_path, child)
    assert not child.exists()


def test_traversal_components_rejected(tmp_path):
    for component in ("..", ".", ""):
        with pytest.raises(PlatformPathValidationError) as exc:
            derive_child(tmp_path, component)
        assert exc.value.code == "PATH_TRAVERSAL"


def test_separator_components_rejected(tmp_path):
    for component in ("a/b", "a\\b"):
        with pytest.raises(PlatformPathValidationError) as exc:
            derive_child(tmp_path, component)
        assert exc.value.code == "PATH_SEPARATOR"


def test_nul_component_rejected(tmp_path):
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child(tmp_path, "a\x00b")
    assert exc.value.code == "PATH_NUL"


def test_depth_budget_enforced(tmp_path):
    policy = PathPolicy(max_child_depth=3)
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child(tmp_path, "a", "b", "c", "d", policy=policy)
    assert exc.value.code == "PATH_TOO_DEEP"


def test_component_length_budget_enforced(tmp_path):
    policy = PathPolicy(max_component_length=8)
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child(tmp_path, "way-too-long-name", policy=policy)
    assert exc.value.code == "PATH_TOO_LONG"


def test_relative_root_rejected():
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child("relative/root", "child")
    assert exc.value.code == "PATH_RELATIVE"


def test_symlink_escape_rejected(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("secret")
    root = tmp_path / "root"
    root.mkdir()
    (root / "link").symlink_to(outside, target_is_directory=True)
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child(root, "link", "secret.txt")
    assert exc.value.code == "PATH_SYMLINK_ESCAPE"


def test_symlink_inside_root_is_allowed(tmp_path):
    root = tmp_path / "root"
    target = root / "real"
    target.mkdir(parents=True)
    (target / "ok.txt").write_text("ok")
    (root / "link").symlink_to(target, target_is_directory=True)
    child = derive_child(root, "link", "ok.txt")
    assert child == root / "link" / "ok.txt"


def test_lexical_escape_rejected(tmp_path):
    with pytest.raises(PlatformPathValidationError) as exc:
        derive_child(tmp_path / "root", "..", "escape")
    assert exc.value.code in ("PATH_TRAVERSAL", "PATH_ESCAPE")
