"""Strictness of platform path models."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.platform.models import (
    FilesystemCapability,
    PathPolicy,
    PathRoot,
    PlatformPaths,
)


def test_models_are_frozen_and_forbid_unknown_fields(tmp_path):
    paths = PlatformPaths(
        config_root=tmp_path / "config",
        data_root=tmp_path / "data",
        cache_root=tmp_path / "cache",
        log_root=tmp_path / "log",
        temp_root=tmp_path / "temp",
        workspace_root=tmp_path / "workspace",
    )
    with pytest.raises(ValidationError):
        paths.data_root = tmp_path / "other"
    with pytest.raises(ValidationError):
        PlatformPaths(
            config_root=tmp_path,
            data_root=tmp_path,
            cache_root=tmp_path,
            log_root=tmp_path,
            temp_root=tmp_path,
            workspace_root=tmp_path,
            invented=True,
        )
    policy = PathPolicy()
    with pytest.raises(ValidationError):
        policy.max_child_depth = 0
    with pytest.raises(ValidationError):
        PathPolicy(max_component_length=0)


def test_capability_unknown_values_are_honest(tmp_path):
    capability = FilesystemCapability(
        root=tmp_path / "missing",
        available=False,
        writable=None,
        free_bytes=None,
        error="stat failed",
    )
    assert capability.free_bytes is None
    assert capability.writable is None


def test_root_helper_roundtrip(tmp_path):
    values = {
        "config_root": tmp_path / "a",
        "data_root": tmp_path / "b",
        "cache_root": tmp_path / "c",
        "log_root": tmp_path / "d",
        "temp_root": tmp_path / "e",
        "workspace_root": tmp_path / "f",
    }
    paths = PlatformPaths(**values)
    assert paths.all_roots() == tuple(Path(v) for v in values.values())
    assert paths.root(PathRoot.CONFIG) == tmp_path / "a"
