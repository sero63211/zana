"""Focused tests for the production loopback native streaming transport."""

from __future__ import annotations

import io
import urllib.error

import pytest

from zana_core.acquisition import transport as transport_module
from zana_core.acquisition.transport import (
    NativeTransportCleanupError,
    NativeTransportHTTPError,
    NativeTransportProtocolError,
    NativeTransportTimeoutError,
    UrllibNativeStreamTransport,
)


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes] | None = None,
        *,
        close_error: Exception | None = None,
    ) -> None:
        self.chunks = list(chunks or [])
        self.closed = False
        self.close_error = close_error

    def read(self, size: int) -> bytes:  # noqa: ARG001
        if not self.chunks:
            return b""
        return self.chunks.pop(0)

    def close(self) -> None:
        self.closed = True
        if self.close_error is not None:
            raise self.close_error


def _transport(monkeypatch, responses: list[FakeResponse] | None = None):
    calls: list[object] = []

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        calls.append(request)
        if not responses:
            raise RuntimeError("no canned response")
        return responses.pop(0)

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", fake_urlopen)
    return UrllibNativeStreamTransport(), calls


def _request(*, url: str = "http://127.0.0.1:11434/api/pull") -> dict[str, object]:
    return {
        "method": "POST",
        "url": url,
        "headers": {"Content-Type": "application/json"},
        "body": b'{"model":"qwen2:1.5b","stream":true}',
        "timeout": 5.0,
    }


def test_transport_posts_bounded_pull_and_closes(monkeypatch) -> None:
    response = FakeResponse([b'{"status":"success"}\n'])
    transport, _ = _transport(monkeypatch, [response])
    stream = transport.open_stream(**_request())
    payload = b"".join(stream)
    assert payload == b'{"status":"success"}\n'
    assert response.closed is True
    transport.close()


def test_transport_rejects_unsafe_urls_before_network(monkeypatch) -> None:
    transport, calls = _transport(monkeypatch)
    unsafe = [
        "http://example.com:11434/api/pull",
        "http://user:pass@127.0.0.1:11434/api/pull",
        "http://127.0.0.1:11434/api/pull?token=secret",
        "http://127.0.0.1:11434/api/pull#fragment",
        "http://127.0.0.1:11434/api/tags",
        "http://127.0.0.1:11434",
        "ftp://127.0.0.1:11434/api/pull",
        "http://127.0.0.1:0/api/pull",
        "http://evil.localhost/api/pull",
    ]
    for url in unsafe:
        with pytest.raises(NativeTransportProtocolError):
            transport.open_stream(**_request(url=url))
    assert calls == []


def test_transport_rejects_method_headers_body_and_timeout(monkeypatch) -> None:
    transport, calls = _transport(monkeypatch)
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(**{**_request(), "method": "GET"})
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(**{**_request(), "body": None})
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(
            **{
                **_request(),
                "headers": {"Authorization": "Bearer secret"},
            }
        )
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(**{**_request(), "timeout": 0.0})
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(**{**_request(), "timeout": float("nan")})
    assert calls == []


def test_transport_http_error_is_sanitized(monkeypatch) -> None:
    error = urllib.error.HTTPError(
        "http://127.0.0.1:11434/api/pull",
        404,
        "Not Found",
        {},
        io.BytesIO(b"super-secret-body"),
    )

    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        raise error

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(NativeTransportHTTPError) as raised:
        UrllibNativeStreamTransport().open_stream(**_request())
    assert "super-secret-body" not in str(raised.value)


def test_transport_timeout_is_sanitized(monkeypatch) -> None:
    def fake_urlopen(request, timeout):  # noqa: ANN001, ARG001
        raise TimeoutError("local runtime timed out with secret detail")

    monkeypatch.setattr(transport_module.urllib.request, "urlopen", fake_urlopen)
    with pytest.raises(NativeTransportTimeoutError) as raised:
        UrllibNativeStreamTransport().open_stream(**_request())
    assert "secret detail" not in str(raised.value)


def test_transport_cleanup_failure_is_not_silent(monkeypatch) -> None:
    response = FakeResponse(
        [b"x"],
        close_error=RuntimeError("close boom with secret"),
    )
    transport, _ = _transport(monkeypatch, [response])
    stream = transport.open_stream(**_request())
    with pytest.raises(NativeTransportCleanupError) as raised:
        stream.close()
    assert "close boom" not in str(raised.value)
    assert "secret" not in str(raised.value)


def test_transport_allows_only_one_open_stream(monkeypatch) -> None:
    first = FakeResponse([b"x"])
    transport, calls = _transport(monkeypatch, [first])
    first_stream = transport.open_stream(**_request())
    with pytest.raises(NativeTransportProtocolError):
        transport.open_stream(**_request())
    assert len(calls) == 1
    first_stream.close()
