"""FastAPI auth dependency for per-launch bearer token."""

import hmac
from dataclasses import dataclass
from typing import Annotated

from fastapi import Header, HTTPException, Request
from starlette.status import HTTP_401_UNAUTHORIZED

from zana_core.api.errors import error_401


@dataclass(frozen=True)
class ServerConfig:
    """Per-launch server configuration injected into request scope."""

    token: str
    version: str


def _extract_bearer_token(authorization: str | None) -> str | None:
    """Extract token from Authorization: Bearer <token> header."""
    if authorization is None:
        return None
    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    token = parts[1]
    if not token:
        return None
    return token


def verify_token(
    request: Request,
    authorization: Annotated[str | None, Header(alias="Authorization")] = None,
) -> ServerConfig:
    """FastAPI dependency: validate bearer token with constant-time comparison.

    Raises 401 on missing, malformed, or wrong token.
    """
    config: ServerConfig = request.app.state.server_config
    provided = _extract_bearer_token(authorization)

    if provided is None:
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=error_401().model_dump(),
        )

    if not hmac.compare_digest(config.token, provided):
        raise HTTPException(
            status_code=HTTP_401_UNAUTHORIZED,
            detail=error_401().model_dump(),
        )

    return config
