"""Bounded synchronous capability probe for exact platform roots."""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Protocol

from zana_core.platform.models import FilesystemCapability, PlatformPaths


class FilesystemProbe(Protocol):
    """Protocol for bounded per-root capability probes."""

    def probe(self, root: Path) -> FilesystemCapability: ...


class DefaultFilesystemProbe:
    """Constant-operation probe using stat/access/disk_usage only."""

    def probe(self, root: Path) -> FilesystemCapability:
        available = False
        writable: bool | None = None
        free_bytes: int | None = None
        error: str | None = None
        try:
            available = root.is_dir()
            if available:
                writable = os.access(root, os.W_OK)
        except OSError as exc:
            error = f"stat failed: {exc}"
        try:
            usage = shutil.disk_usage(root)
            free_bytes = int(usage.free)
        except OSError as exc:
            free_bytes = None
            error = f"{error}; disk_usage failed: {exc}" if error else f"disk_usage failed: {exc}"
        return FilesystemCapability(
            root=root,
            available=available,
            writable=writable,
            free_bytes=free_bytes,
            error=error,
        )


def probe_roots(
    paths: PlatformPaths,
    probe: FilesystemProbe | None = None,
) -> tuple[FilesystemCapability, ...]:
    """Probe every exact root with a constant number of operations per root."""
    engine = probe or DefaultFilesystemProbe()
    return tuple(engine.probe(root) for root in paths.all_roots())
