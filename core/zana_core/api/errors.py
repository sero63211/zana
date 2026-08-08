"""Canonical error models matching the ZANA API contract."""

from typing import Any

from pydantic import BaseModel


class ErrorDetail(BaseModel):
    """Canonical error envelope for all API error responses."""

    code: str
    message: str
    details: dict[str, Any] = {}
    recoverable: bool = False
    actions: list[str] = []


class ErrorResponse(BaseModel):
    """Wrapper matching the contract error shape: {"error": {...}}."""

    error: ErrorDetail


def error_401(
    code: str = "UNAUTHORIZED",
    message: str = "Missing or invalid bearer token.",
) -> ErrorResponse:
    return ErrorResponse(
        error=ErrorDetail(
            code=code,
            message=message,
            recoverable=False,
            actions=["provide_valid_token"],
        )
    )
