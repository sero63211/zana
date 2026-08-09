"""Manual runtime endpoint validation without automatic discovery."""

from __future__ import annotations

from urllib.parse import urlparse

from zana_core.runtimes.base import AdapterType, ManualEndpointError

ALLOWED_SCHEMES = {"http", "https"}


def validate_manual_endpoint(endpoint: str, adapter_type: AdapterType) -> str:
    """Validate a user-supplied endpoint and return a normalized URL.

    Remote hosts are allowed only because this function is invoked for an
    explicit manual endpoint entry; it never probes or discovers endpoints.
    Embedded credentials are always rejected.
    """
    if adapter_type == AdapterType.AUTO:
        raise ManualEndpointError(
            "Auto adapter selection is not allowed for manual endpoints.",
            code="ADAPTER_REQUIRED",
            actions=["select_adapter"],
        )
    parsed = urlparse(endpoint)
    if parsed.scheme not in ALLOWED_SCHEMES or not parsed.hostname:
        raise ManualEndpointError(
            "A manual runtime endpoint must be an absolute http(s) URL.",
            code="INVALID_ENDPOINT",
            actions=["fix_endpoint"],
        )
    if parsed.username is not None or parsed.password is not None:
        raise ManualEndpointError(
            "Do not embed credentials in runtime endpoints.",
            code="ENDPOINT_CREDENTIALS_NOT_ALLOWED",
            actions=["store_credentials_separately"],
        )
    if parsed.fragment:
        raise ManualEndpointError(
            "Runtime endpoints must not contain a URL fragment.",
            code="INVALID_ENDPOINT",
            actions=["fix_endpoint"],
        )
    normalized = endpoint.rstrip("/")
    return normalized
