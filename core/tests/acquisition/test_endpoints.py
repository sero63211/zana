"""Explicit endpoint validation and remote-policy tests."""

from __future__ import annotations

import pytest

from zana_core.acquisition.endpoints import EndpointError, validate_endpoint
from zana_core.acquisition.models import AcquisitionPolicy


class TestEndpointValidation:
    def test_loopback_default_allowed(self) -> None:
        assert (
            validate_endpoint("http://127.0.0.1:11434", AcquisitionPolicy.LOCAL_ONLY)
            == "http://127.0.0.1:11434"
        )

    def test_non_http_rejected(self) -> None:
        with pytest.raises(EndpointError):
            validate_endpoint("file:///etc/passwd", AcquisitionPolicy.LOCAL_ONLY)

    def test_credentials_and_fragments_rejected(self) -> None:
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://user:pass@127.0.0.1:11434",
                AcquisitionPolicy.LOCAL_ONLY,
            )
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:11434#frag",
                AcquisitionPolicy.LOCAL_ONLY,
            )

    def test_remote_denied_without_explicit_policy(self) -> None:
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://example.com:11434",
                AcquisitionPolicy.LOCAL_ONLY,
            )

    def test_remote_allowed_with_explicit_policy(self) -> None:
        assert (
            validate_endpoint(
                "http://example.com:11434",
                AcquisitionPolicy.EXPLICIT_REMOTE_ALLOWED,
            )
            == "http://example.com:11434"
        )

    def test_trailing_slash_normalizes_to_origin(self) -> None:
        assert (
            validate_endpoint(
                "http://127.0.0.1:11434/",
                AcquisitionPolicy.LOCAL_ONLY,
            )
            == "http://127.0.0.1:11434"
        )

    def test_path_query_and_oversized_endpoints_rejected(self) -> None:
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:11434/api/pull",
                AcquisitionPolicy.LOCAL_ONLY,
            )
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:11434?x=1",
                AcquisitionPolicy.LOCAL_ONLY,
            )
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:11434/" + "é" * 1000,
                AcquisitionPolicy.LOCAL_ONLY,
            )

    def test_dangling_port_delimiter_rejected(self) -> None:
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:",
                AcquisitionPolicy.LOCAL_ONLY,
            )
        with pytest.raises(EndpointError):
            validate_endpoint(
                "http://127.0.0.1:/",
                AcquisitionPolicy.LOCAL_ONLY,
            )

    def test_ipv6_origin_preserved(self) -> None:
        assert (
            validate_endpoint(
                "http://[::1]:11434",
                AcquisitionPolicy.LOCAL_ONLY,
            )
            == "http://[::1]:11434"
        )

    def test_normalized_origin_never_reuses_raw_netloc(self) -> None:
        assert (
            validate_endpoint(
                "HTTP://127.0.0.1:11434/",
                AcquisitionPolicy.LOCAL_ONLY,
            )
            == "http://127.0.0.1:11434"
        )
        assert (
            validate_endpoint(
                "http://127.0.0.1:11434",
                AcquisitionPolicy.LOCAL_ONLY,
            )
            == "http://127.0.0.1:11434"
        )
