"""Shared fake transports for bounded runtime probe tests."""

from __future__ import annotations

from collections.abc import Callable, Mapping

import pytest

from zana_core.runtimes.base import HttpResponse, RuntimeProbeTimeoutError


class FakeTransport:
    """Injected transport returning canned responses or raising timeouts."""

    def __init__(
        self,
        routes: Mapping[tuple[str, str], HttpResponse] | None = None,
        *,
        default_timeout: bool = False,
    ) -> None:
        self.routes = dict(routes or {})
        self.default_timeout = default_timeout
        self.calls: list[tuple[str, str, Mapping[str, str] | None, bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append((method, url, headers, body))
        if self.default_timeout:
            raise RuntimeProbeTimeoutError("bounded test timeout")
        response = self.routes.get((method, url))
        if response is None:
            raise RuntimeProbeTimeoutError(f"no canned response for {method} {url}")
        return response


def json_response(payload: object, *, status: int = 200) -> HttpResponse:
    import json

    return HttpResponse(
        status=status,
        text=json.dumps(payload),
        content_type="application/json",
    )


@pytest.fixture
def fake_transport() -> Callable[..., FakeTransport]:
    return FakeTransport
