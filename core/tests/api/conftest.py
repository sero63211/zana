"""Shared fixtures for API tests."""

import pytest
from fastapi.testclient import TestClient

from zana_core.main import create_app


@pytest.fixture
def valid_token() -> str:
    return "test-token-abc123"


@pytest.fixture
def client(valid_token: str) -> TestClient:
    app = create_app(token=valid_token)
    return TestClient(app)


@pytest.fixture
def auth_header(valid_token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {valid_token}"}
