"""Test GET /api/v1/health authentication and response shape."""

from fastapi.testclient import TestClient


class TestHealthAuth:
    """Authentication: correct token → 200, missing/wrong/empty → 401."""

    PATH = "/api/v1/health"

    def test_correct_token_returns_200(
        self, client: TestClient, auth_header: dict[str, str]
    ) -> None:
        response = client.get(self.PATH, headers=auth_header)
        assert response.status_code == 200

    def test_missing_auth_header_returns_401(self, client: TestClient) -> None:
        response = client.get(self.PATH)
        assert response.status_code == 401
        body = response.json()
        assert body["error"]["code"] == "UNAUTHORIZED"

    def test_wrong_token_returns_401(
        self, client: TestClient
    ) -> None:
        response = client.get(self.PATH, headers={"Authorization": "Bearer wrong-token"})
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_empty_token_returns_401(
        self, client: TestClient
    ) -> None:
        # No token after Bearer
        response = client.get(self.PATH, headers={"Authorization": "Bearer "})
        assert response.status_code == 401

    def test_malformed_auth_header_returns_401(
        self, client: TestClient
    ) -> None:
        # Not a Bearer scheme
        response = client.get(self.PATH, headers={"Authorization": "Basic xyz"})
        assert response.status_code == 401


class TestHealthResponseShape:
    """Payload validation: typed fields present with correct types."""

    PATH = "/api/v1/health"

    def test_response_has_typed_fields(
        self, client: TestClient, auth_header: dict[str, str]
    ) -> None:
        response = client.get(self.PATH, headers=auth_header)
        assert response.status_code == 200
        body = response.json()

        assert body["status"] == "ok"
        assert isinstance(body["version"], str)
        assert body["version"] == "0.1.0"
        assert isinstance(body["python_version"], str)
        assert "." in body["python_version"]
        assert isinstance(body["pid"], int)
        assert body["pid"] > 0
        assert isinstance(body["uptime_seconds"], float)
        assert body["uptime_seconds"] >= 0.0

    def test_error_response_shape_on_401(self, client: TestClient) -> None:
        response = client.get(self.PATH)
        assert response.status_code == 401
        body = response.json()

        assert "error" in body
        err = body["error"]
        assert err["code"] == "UNAUTHORIZED"
        assert isinstance(err["message"], str)
        assert isinstance(err["details"], dict)
        assert isinstance(err["recoverable"], bool)
        assert isinstance(err["actions"], list)
        assert "provide_valid_token" in err["actions"]
