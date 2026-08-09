"""Authenticated system profile and diagnostic endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from zana_core.api.deps import ServerConfig, verify_token
from zana_core.diagnostics.doctor import DoctorService
from zana_core.diagnostics.models import DiagnosticReport, ProbeBudget
from zana_core.diagnostics.probes import (
    LoopbackAuthProbe,
    MemoryDiskProbe,
    OptionalDependencyProbe,
    PlatformProbe,
    RuntimeDiscoveryProbe,
    SqliteReachabilityProbe,
    StorageRootProbe,
)
from zana_core.hardware.models import HardwareProfile
from zana_core.hardware.profile import collect_profile

router = APIRouter(
    prefix="/api/v1/system",
    tags=["system"],
    dependencies=[Depends(verify_token)],
)


@router.get("/profile", response_model=HardwareProfile)
def system_profile(request: Request) -> HardwareProfile:
    """Return a real, bounded hardware snapshot for build planning.

    The disk report is captured relative to the Core data root so it reflects
    the same volume used for durable artifacts.
    """
    return collect_profile(request.app.state.data_root)


@router.get("/doctor", response_model=DiagnosticReport)
def system_doctor(
    request: Request,
    config: Annotated[ServerConfig, Depends(verify_token)],
) -> DiagnosticReport:
    """Run the bounded read-only diagnostic probe set."""
    database = request.app.state.database
    data_root = request.app.state.data_root
    sqlite_checker = database.pragma_state
    probes = [
        PlatformProbe(),
        MemoryDiskProbe(path=data_root),
        SqliteReachabilityProbe(checker=sqlite_checker),
        RuntimeDiscoveryProbe(request.app.state.runtime_registry),
        StorageRootProbe(
            artifact_root=data_root / "artifacts",
            image_root=data_root / "images",
        ),
        OptionalDependencyProbe(),
        LoopbackAuthProbe(base_url="http://127.0.0.1", token_present=bool(config.token)),
    ]
    return DoctorService(budget=ProbeBudget()).run(probes)
