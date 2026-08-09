"""Shared test fixtures for the ZANA Core suite."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from zana_core.db.database import Database
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.main import create_app


@pytest.fixture
def valid_token() -> str:
    return "test-token-abc123"


@pytest.fixture
def db_path(tmp_path_factory) -> Path:  # noqa: ANN001
    return tmp_path_factory.mktemp("zana-db") / "zana.sqlite3"


@pytest.fixture
def database(db_path: Path) -> Database:
    db = Database(db_path)
    db.upgrade()
    yield db
    db.close()


@pytest.fixture
def session_factory(database: Database) -> sessionmaker:
    return database.session_factory


@pytest.fixture
def uow(session_factory: sessionmaker) -> UnitOfWork:
    unit = UnitOfWork(session_factory)
    yield unit
    unit.close()


@pytest.fixture
def client(database: Database, valid_token: str) -> TestClient:
    app = create_app(token=valid_token, database_path=database.path)
    return TestClient(app)


@pytest.fixture
def auth_header(valid_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {valid_token}"}
