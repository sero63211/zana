"""Authenticated real system diagnostics."""

from __future__ import annotations

import os
import time

from fastapi import APIRouter, Depends, Request
from sqlalchemy import text

from zana_core.api.deps import verify_token
from zana_core.api.schemas import SystemDoctorRead
from zana_core.db.database import Database

router = APIRouter(
    prefix="/api/v1/system",
    tags=["system"],
    dependencies=[Depends(verify_token)],
)

_process_start: float = time.time()


@router.get("/doctor", response_model=SystemDoctorRead)
def system_doctor(request: Request) -> SystemDoctorRead:
    database: Database = request.app.state.database
    pragma = database.pragma_state()
    healthy = pragma["journal_mode"] == "wal" and pragma["foreign_keys"] == 1
    with database.engine.connect() as connection:
        sqlite_version = connection.execute(text("SELECT sqlite_version()")).scalar_one()
    return SystemDoctorRead(
        status="ok" if healthy else "degraded",
        sqlite_version=str(sqlite_version),
        journal_mode=str(pragma["journal_mode"]),
        foreign_keys=bool(pragma["foreign_keys"]),
        busy_timeout_ms=int(pragma["busy_timeout_ms"]),
        migration_revision=database.revision(),
        db_path=str(database.path),
        table_counts=database.table_counts(),
        core_pid=os.getpid(),
        core_uptime_seconds=round(time.time() - _process_start, 3),
    )
