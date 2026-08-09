"""Focused integration tests for canonical platform wiring in main.py."""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from tests.platform.helpers import mac_layout, resolver_for
from zana_core.db.database import Database
from zana_core.main import create_app
from zana_core.platform.models import (
    PlatformPaths,
    PlatformPathValidationError,
)
from zana_core.runtimes.registry import RuntimeProbeRegistry


def test_explicit_database_path_bypasses_resolver_and_ensure(tmp_path):
    db_path = tmp_path / "explicit" / "zana.sqlite3"
    app = create_app(token="tok", database_path=db_path)
    assert app.state.database.path == db_path
    assert app.state.data_root == db_path.parent
    assert db_path.exists()
    # No platform roots were created.
    assert not (tmp_path / "Library").exists()
    assert not (tmp_path / "config").exists()


def test_injected_platform_paths_creates_only_data_and_db(tmp_path):
    paths = resolver_for(mac_layout(tmp_path)).resolve()
    app = create_app(token="tok", platform_paths=paths)
    database: Database = app.state.database
    assert database.path == paths.data_root / "db" / "zana.sqlite3"
    assert app.state.data_root == paths.data_root
    assert database.path.exists()
    # Only the data root was ensured; config/cache/log/temp/workspace stay absent.
    assert paths.data_root.is_dir()
    assert not paths.config_root.exists()
    assert not paths.cache_root.exists()
    assert not paths.log_root.exists()
    assert not paths.workspace_root.exists()
    with TestClient(app) as client:
        response = client.get("/api/v1/health")
    assert response.status_code == 401


def test_injected_path_resolver_factory_is_used(tmp_path):
    class Factory:
        def __init__(self) -> None:
            self._paths = resolver_for(mac_layout(tmp_path))

        def resolve(self):
            return self._paths.resolve()

    app = create_app(token="tok", path_resolver_factory=Factory)
    expected = tmp_path / "Library" / "Application Support" / "zana" / "db" / "zana.sqlite3"
    assert app.state.database.path == expected


def test_unsafe_alias_root_set_fails_before_mutation(tmp_path):
    unsafe = PlatformPaths(
        config_root=tmp_path / "a",
        data_root=tmp_path / "shared",
        cache_root=tmp_path / "shared",
        log_root=tmp_path / "d",
        temp_root=tmp_path / "e",
        workspace_root=tmp_path / "f",
    )
    with pytest.raises(PlatformPathValidationError):
        create_app(token="tok", platform_paths=unsafe)
    assert not unsafe.data_root.exists()
    assert not unsafe.cache_root.exists()
    assert not unsafe.workspace_root.exists()


def test_unsafe_root_set_fails_before_mutation(tmp_path):
    unsafe = PlatformPaths(
        config_root=tmp_path / "a",
        data_root=tmp_path / "b",
        cache_root=tmp_path / "c",
        log_root=tmp_path / "d",
        temp_root=tmp_path / "e",
        workspace_root=tmp_path / "b" / "nested",
    )
    # workspace under data is the allowed relation; data under workspace is not.
    unsafe = PlatformPaths(
        config_root=tmp_path / "a",
        data_root=tmp_path / "b" / "nested",
        cache_root=tmp_path / "c",
        log_root=tmp_path / "d",
        temp_root=tmp_path / "e",
        workspace_root=tmp_path / "b",
    )
    with pytest.raises(PlatformPathValidationError):
        create_app(token="tok", platform_paths=unsafe)
    assert not unsafe.config_root.exists()
    assert not unsafe.data_root.exists()
    assert not unsafe.workspace_root.exists()


def test_main_no_longer_imports_platformdirs():
    module = importlib.import_module("zana_core.main")
    assert "platformdirs" not in sys.modules.get("zana_core.main", module).__dict__
    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "import platformdirs" not in source
    assert "platformdirs" not in source


def test_system_and_refresh_routes_require_auth_and_registry_is_injected(tmp_path):
    db_path = tmp_path / "db" / "zana.sqlite3"
    app = create_app(token="tok", database_path=db_path, runtime_registry=RuntimeProbeRegistry())
    assert isinstance(app.state.runtime_registry, RuntimeProbeRegistry)
    with TestClient(app) as client:
        assert client.get("/api/v1/system/profile").status_code == 401
        assert client.get("/api/v1/system/doctor").status_code == 401
        assert client.post("/api/v1/runtimes/refresh").status_code == 401
        assert (
            client.post(
                "/api/v1/models/pull", json={"runtime_id": 1, "model_reference": "x"}
            ).status_code
            == 401
        )
