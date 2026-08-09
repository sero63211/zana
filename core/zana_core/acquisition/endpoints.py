"""Explicit endpoint validation with remote-policy fail-closed behavior."""

from __future__ import annotations

from urllib.parse import urlparse

from zana_core.acquisition.models import AcquisitionPolicy


class EndpointError(ValueError):
    """Raised when an acquisition endpoint is invalid or disallowed."""


def validate_endpoint(endpoint: str, policy: AcquisitionPolicy) -> str:
    """Validate and normalize an explicit origin; never scans the network."""
    if len(endpoint) > 2000:
        raise EndpointError("Acquisition endpoint is too long.")
    if len(endpoint.encode("utf-8")) > 2000:
        raise EndpointError("Acquisition endpoint exceeds the UTF-8 byte limit.")
    try:
        parsed = urlparse(endpoint)
    except ValueError:
        raise EndpointError("Acquisition endpoint is malformed.") from None
    if parsed.scheme not in ("http", "https"):
        raise EndpointError("Acquisition endpoint must be http(s).")
    if not parsed.hostname:
        raise EndpointError("Acquisition endpoint must include a host.")
    if parsed.username is not None or parsed.password is not None:
        raise EndpointError("Do not embed credentials in acquisition endpoints.")
    if parsed.fragment:
        raise EndpointError("Acquisition endpoints must not contain a URL fragment.")
    if parsed.query:
        raise EndpointError("Acquisition endpoints must not contain a query string.")
    if parsed.path not in ("", "/"):
        raise EndpointError("Acquisition endpoints must be an origin without a path.")
    try:
        port = parsed.port
    except ValueError:
        raise EndpointError("Acquisition endpoint port is invalid.") from None
    if port is not None and not (1 <= port <= 65535):
        raise EndpointError("Acquisition endpoint port is out of range.")
    if _has_dangling_port_delimiter(endpoint):
        raise EndpointError("Acquisition endpoint port is incomplete.")
    host = parsed.hostname
    is_loopback = host in {"127.0.0.1", "localhost", "::1"}
    if policy == AcquisitionPolicy.LOCAL_ONLY and not is_loopback:
        raise EndpointError(
            "Remote acquisition is denied by local-only policy; "
            "explicit remote approval is required."
        )
    display_host = f"[{host}]" if ":" in host else host
    origin = f"{parsed.scheme}://{display_host}"
    if port is not None:
        origin = f"{origin}:{port}"
    return origin


def _has_dangling_port_delimiter(endpoint: str) -> bool:
    authority_start = endpoint.find("://")
    if authority_start == -1:
        return False
    after_authority = endpoint[authority_start + 3 :]
    before_path = after_authority.split("/", 1)[0]
    return before_path.endswith(":")
