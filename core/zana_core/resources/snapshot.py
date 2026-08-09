"""Cheap cross-platform resource snapshot provider."""

from __future__ import annotations

import os
import platform
import shutil
from pathlib import Path
from typing import Protocol

from zana_core.resources.models import PlatformLabel, ResourceSnapshot


class SnapshotProvider(Protocol):
    """Protocol for synchronous resource snapshots."""

    def capture(self) -> ResourceSnapshot: ...


def platform_label(system: str | None) -> PlatformLabel:
    normalized = (system or "").lower()
    if normalized == "darwin":
        return PlatformLabel.MACOS
    if normalized == "linux":
        return PlatformLabel.LINUX
    if normalized in {"windows", "win32", "cygwin"}:
        return PlatformLabel.WINDOWS
    return PlatformLabel.UNKNOWN


class DefaultSnapshotProvider:
    """Real snapshot using psutil/shutil/platform only.

    Every probe is wrapped: failures produce None fields plus a probe error,
    never fabricated zero or success values. No admin commands, GPU
    allocation, process scanning, polling, daemon, or telemetry.
    """

    def __init__(self, workspace_path: str | Path = ".") -> None:
        self._workspace = str(Path(workspace_path).resolve())

    def capture(self) -> ResourceSnapshot:
        memory_total: int | None = None
        memory_available: int | None = None
        disk_free: int | None = None
        probe_error: str | None = None

        try:
            import psutil

            virtual = psutil.virtual_memory()
            memory_total = int(virtual.total)
            memory_available = int(virtual.available)
        except Exception as exc:  # noqa: BLE001 - probe failure must be non-fatal
            probe_error = f"memory probe failed: {exc}"

        try:
            usage = shutil.disk_usage(self._workspace)
            disk_free = int(usage.free)
        except Exception as exc:  # noqa: BLE001
            try:
                import psutil

                disk = psutil.disk_usage(self._workspace)
                disk_free = int(disk.free)
            except Exception as inner:  # noqa: BLE001
                disk_free = None
                probe_error = (
                    f"{probe_error or 'disk probe failed'}; shutil: {exc}; psutil: {inner}"
                    if probe_error
                    else f"disk probe failed; shutil: {exc}; psutil: {inner}"
                )

        try:
            cores = os.cpu_count()
        except Exception:  # noqa: BLE001
            cores = None

        system = platform.system()
        arch = platform.machine() or ""
        notes: list[str] = []
        if probe_error:
            notes.append(probe_error)
        return ResourceSnapshot(
            revision=0,
            platform=platform_label(system),
            os_name=system or "",
            arch=arch,
            logical_cores=cores,
            memory_total_bytes=memory_total,
            memory_available_bytes=memory_available,
            disk_path=self._workspace,
            disk_free_bytes=disk_free,
            probe_error=probe_error,
            notes=tuple(notes),
        )
