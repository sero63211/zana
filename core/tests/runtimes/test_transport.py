"""Real bounded localhost transport tests against a threaded protocol server."""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

import pytest

from zana_core.runtimes.base import (
    InvalidRuntimeResponseError,
    RuntimeProbeTimeoutError,
)
from zana_core.runtimes.transport import UrllibTransport


class _Handler(BaseHTTPRequestHandler):
    routes: dict[tuple[str, str], Any] = {}

    def do_GET(self) -> None:  # noqa: N802
        self._handle_route("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle_route("POST")

    def _handle_route(self, method: str) -> None:
        route = self.routes.get((method, self.path))
        if route is None:
            self.send_response(404)
            self.end_headers()
            return
        status, payload, delay = route
        if delay:
            time.sleep(delay)
        body = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return


@pytest.fixture
def protocol_server() -> Any:
    _Handler.routes = {
        ("GET", "/health"): (200, {"status": "ok"}, 0),
        ("GET", "/timeout"): (200, {"status": "slow"}, 0.4),
        ("GET", "/oversized"): (200, b"x" * (1_500_000), 0),
        ("POST", "/echo"): (200, {"echoed": True}, 0),
    }
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    yield f"http://{host}:{port}"
    server.shutdown()
    server.server_close()
    thread.join(timeout=2)


class TestUrllibTransport:
    def test_get_json_response(self, protocol_server: str) -> None:
        response = UrllibTransport().request(
            "GET",
            f"{protocol_server}/health",
            timeout=0.5,
        )
        assert response.status == 200
        assert json.loads(response.text) == {"status": "ok"}

    def test_bounded_timeout(self, protocol_server: str) -> None:
        with pytest.raises(RuntimeProbeTimeoutError):
            UrllibTransport().request(
                "GET",
                f"{protocol_server}/timeout",
                timeout=0.05,
            )

    def test_oversized_response_is_rejected(self, protocol_server: str) -> None:
        with pytest.raises(InvalidRuntimeResponseError):
            UrllibTransport().request(
                "GET",
                f"{protocol_server}/oversized",
                timeout=0.5,
            )

    def test_post_body_is_sent(self, protocol_server: str) -> None:
        transport = UrllibTransport()
        response = transport.request(
            "POST",
            f"{protocol_server}/echo",
            body=transport.json_body({"model": "test"}),
            timeout=0.5,
        )
        assert response.status == 200
        assert json.loads(response.text) == {"echoed": True}
