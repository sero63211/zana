"""Health check endpoint returning real version/process/runtime state."""

import os
import sys
import time

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from zana_core.api.deps import ServerConfig, verify_token

router = APIRouter(tags=["system"])

# Captured at import time, before uvicorn forks
_process_start: float = time.time()


class HealthResponse(BaseModel):
    """Real version, process, and runtime state."""

    status: str
    version: str
    python_version: str
    pid: int
    uptime_seconds: float


@router.get("/api/v1/health", response_model=HealthResponse)
async def health(config: ServerConfig = Depends(verify_token)) -> HealthResponse:  # noqa: B008
    """Return real server state. Requires valid bearer token."""
    return HealthResponse(
        status="ok",
        version=config.version,
        python_version=sys.version.split()[0],
        pid=os.getpid(),
        uptime_seconds=round(time.time() - _process_start, 3),
    )
