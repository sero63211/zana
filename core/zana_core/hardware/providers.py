"""Per-OS provider boundaries for the non-privileged hardware profile."""

from __future__ import annotations

import json
import os
import platform
import shutil
from pathlib import Path
from typing import Protocol

import psutil

from zana_core.hardware.commands import CommandRunner
from zana_core.hardware.models import (
    AcceleratorInfo,
    AcceleratorKind,
    CpuInfo,
    DiskInfo,
    MemoryInfo,
    OSType,
)


class PlatformProvider(Protocol):
    """Resource probes that differ by operating system."""

    os_type: OSType

    def cpu(self, runner: CommandRunner) -> CpuInfo: ...

    def memory(self) -> MemoryInfo: ...

    def disk(self, path: str | Path) -> DiskInfo: ...

    def platform_accelerators(
        self, runner: CommandRunner
    ) -> tuple[list[AcceleratorInfo], str | None]: ...


def detect_os_type(name: str) -> OSType:
    """Map platform/system names to the canonical OSType."""
    normalized = name.strip().lower()
    if normalized in {"darwin", "macos", "macosx", "osx"}:
        return OSType.MACOS
    if normalized == "linux":
        return OSType.LINUX
    if normalized in {"windows", "win32", "win64", "cygwin", "msys"}:
        return OSType.WINDOWS
    return OSType.UNKNOWN


def detect_arch() -> str:
    """Return a normalized machine architecture; never invents one."""
    machine = platform.machine().strip().lower()
    if machine in {"amd64", "x64"}:
        return "x86_64"
    return machine or "unknown"


def _psutil_memory() -> MemoryInfo:
    try:
        virtual = psutil.virtual_memory()
    except (OSError, psutil.Error):
        return MemoryInfo()
    return MemoryInfo(
        total_bytes=int(virtual.total),
        available_bytes=int(virtual.available),
    )


def _psutil_cpu_counts() -> tuple[int | None, int | None]:
    logical = psutil.cpu_count(logical=True)
    physical = psutil.cpu_count(logical=False)
    return (
        int(logical) if logical else None,
        int(physical) if physical else None,
    )


def _disk_usage(path: str | Path) -> DiskInfo:
    resolved = os.path.abspath(os.fspath(path))
    try:
        usage = shutil.disk_usage(resolved)
    except OSError as exc:
        return DiskInfo(path=resolved, error=f"disk_usage_failed:{exc}")
    return DiskInfo(
        path=resolved,
        total_bytes=int(usage.total),
        used_bytes=int(usage.used),
        free_bytes=int(usage.free),
    )


def _macos_brand_string(runner: CommandRunner) -> str | None:
    result = runner.run(["sysctl", "-n", "machdep.cpu.brand_string"], timeout=2.0)
    if result.error is None and not result.timed_out and result.stdout.strip():
        return result.stdout.strip()
    return None


def _macos_x86_metal(
    runner: CommandRunner,
) -> tuple[list[AcceleratorInfo], str | None]:
    result = runner.run(["system_profiler", "SPDisplaysDataType", "-json"], timeout=10.0)
    if result.error is not None or result.timed_out:
        return [], "system_profiler GPU probe failed; Metal status unknown"
    if result.returncode != 0:
        message = (
            f"system_profiler GPU probe failed (exit {result.returncode}); Metal status unknown"
        )
        return [], message
    try:
        payload = json.loads(result.stdout)
    except ValueError:
        return [], "system_profiler returned malformed JSON; Metal status unknown"
    displays = payload.get("SPDisplaysDataType", []) if isinstance(payload, dict) else []
    accelerators: list[AcceleratorInfo] = []
    for item in displays:
        if not isinstance(item, dict):
            continue
        raw_metal = item.get("spdisplays_metal") or ""
        if str(raw_metal).lower() not in {"spdisplays_supported", "supported"}:
            continue
        raw_name = item.get("sppci_model")
        name = raw_name if isinstance(raw_name, str) and raw_name.strip() else None
        accelerators.append(
            AcceleratorInfo(
                kind=AcceleratorKind.APPLE_METAL,
                name=name,
                shared_memory=None,
                detected_via="system_profiler",
            )
        )
    if not accelerators:
        return [], "system_profiler reported no Metal-supported GPU"
    return accelerators, None


class DarwinProvider:
    """macOS probes: architecture-backed Metal plus bounded sysctl/system_profiler."""

    os_type: OSType = OSType.MACOS

    def cpu(self, runner: CommandRunner) -> CpuInfo:
        logical, physical = _psutil_cpu_counts()
        return CpuInfo(
            name=_macos_brand_string(runner) or platform.processor() or None,
            logical_cores=logical,
            physical_cores=physical,
        )

    def memory(self) -> MemoryInfo:
        return _psutil_memory()

    def disk(self, path: str | Path) -> DiskInfo:
        return _disk_usage(path)

    def platform_accelerators(
        self, runner: CommandRunner
    ) -> tuple[list[AcceleratorInfo], str | None]:
        if platform.machine().lower() == "arm64":
            return (
                [
                    AcceleratorInfo(
                        kind=AcceleratorKind.APPLE_METAL,
                        name=_macos_brand_string(runner),
                        shared_memory=True,
                        detected_via="apple_silicon_platform",
                    )
                ],
                None,
            )
        return _macos_x86_metal(runner)


def _linux_cpu_model_name() -> str | None:
    try:
        with open("/proc/cpuinfo", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if line.lower().startswith("model name"):
                    return line.split(":", 1)[1].strip() or None
    except OSError:
        return None
    return None


class LinuxProvider:
    """Linux probes: psutil, disk usage, and a read-only /proc/cpuinfo lookup."""

    os_type: OSType = OSType.LINUX

    def cpu(self, runner: CommandRunner) -> CpuInfo:
        logical, physical = _psutil_cpu_counts()
        return CpuInfo(
            name=_linux_cpu_model_name(),
            logical_cores=logical,
            physical_cores=physical,
        )

    def memory(self) -> MemoryInfo:
        return _psutil_memory()

    def disk(self, path: str | Path) -> DiskInfo:
        return _disk_usage(path)

    def platform_accelerators(
        self, runner: CommandRunner
    ) -> tuple[list[AcceleratorInfo], str | None]:
        return [], None


def _parse_wmic_names(stdout: str) -> list[str]:
    names: list[str] = []
    for line in stdout.splitlines():
        name = line.strip()
        if name and name.lower() != "name" and "," not in name:
            names.append(name)
    return names


class WindowsProvider:
    """Windows probes: psutil, disk usage, and a bounded read-only wmic lookup."""

    os_type: OSType = OSType.WINDOWS

    def cpu(self, runner: CommandRunner) -> CpuInfo:
        logical, physical = _psutil_cpu_counts()
        return CpuInfo(
            name=platform.processor() or None,
            logical_cores=logical,
            physical_cores=physical,
        )

    def memory(self) -> MemoryInfo:
        return _psutil_memory()

    def disk(self, path: str | Path) -> DiskInfo:
        return _disk_usage(path)

    def platform_accelerators(
        self, runner: CommandRunner
    ) -> tuple[list[AcceleratorInfo], str | None]:
        result = runner.run(
            ["wmic", "path", "win32_VideoController", "get", "Name"],
            timeout=5.0,
        )
        if result.error is not None or result.timed_out:
            return [], "wmic GPU probe failed; Windows GPUs unknown"
        if result.returncode != 0:
            return [], f"wmic GPU probe failed (exit {result.returncode}); Windows GPUs unknown"
        names = _parse_wmic_names(result.stdout)
        if not names:
            return [], "wmic reported no display controllers"
        return (
            [
                AcceleratorInfo(
                    kind=AcceleratorKind.UNKNOWN,
                    name=name,
                    detected_via="wmic",
                )
                for name in names
            ],
            None,
        )


class UnknownProvider:
    """Fallback probes that never claim platform-specific facts."""

    os_type: OSType = OSType.UNKNOWN

    def cpu(self, runner: CommandRunner) -> CpuInfo:
        logical, physical = _psutil_cpu_counts()
        return CpuInfo(logical_cores=logical, physical_cores=physical)

    def memory(self) -> MemoryInfo:
        return _psutil_memory()

    def disk(self, path: str | Path) -> DiskInfo:
        return _disk_usage(path)

    def platform_accelerators(
        self, runner: CommandRunner
    ) -> tuple[list[AcceleratorInfo], str | None]:
        return [], None


def provider_for(os_type: OSType) -> PlatformProvider:
    """Return the provider matching the detected OS; unknown is honest."""
    if os_type == OSType.MACOS:
        return DarwinProvider()
    if os_type == OSType.LINUX:
        return LinuxProvider()
    if os_type == OSType.WINDOWS:
        return WindowsProvider()
    return UnknownProvider()
