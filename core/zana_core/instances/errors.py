"""Typed instance and chat error records."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class InstanceError(Exception):
    """Base class for fail-closed instance orchestration failures."""


class InstanceErrorRecord(BaseModel):
    """Durable typed error with an explicit recovery action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    code: str
    message: str
    recovery_action: str
    recoverable: bool = True
