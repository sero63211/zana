"""Tests for per-OS provider boundaries."""

import os
import platform

import pytest

from zana_core.hardware.commands import CommandResult
from zana_core.hardware.models import AcceleratorKind, OSType
from zana_core.hardware.providers import (
    DarwinProvider,
    LinuxProvider,
    UnknownProvider,
    WindowsProvider,
    detect_os_type,
    provider_for,
)

SYSTEM_PROFILER_JSON = (
    '{"SPDisplaysDataType": [{"sppci_model": "AMD Radeon Pro", '
    '"spdisplays_metal": "spdisplays_supported"}]}'
)


class FakeRunner:
    """Records invocations and returns a canned command result."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, timeout: float) -> CommandResult:
        self.calls.append(list(args))
        return self.result


@pytest.mark.parametrize(
    ("system", "expected"),
    [
        ("Darwin", OSType.MACOS),
        ("macos", OSType.MACOS),
        ("Linux", OSType.LINUX),
        ("Windows", OSType.WINDOWS),
        ("win32", OSType.WINDOWS),
        ("Haiku", OSType.UNKNOWN),
        ("", OSType.UNKNOWN),
    ],
)
def test_detect_os_type(system: str, expected: OSType) -> None:
    assert detect_os_type(system) == expected


def test_provider_for_known_platforms() -> None:
    assert isinstance(provider_for(OSType.MACOS), DarwinProvider)
    assert isinstance(provider_for(OSType.LINUX), LinuxProvider)
    assert isinstance(provider_for(OSType.WINDOWS), WindowsProvider)
    assert isinstance(provider_for(OSType.UNKNOWN), UnknownProvider)


def test_cpu_counts_are_real_ints() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout=""))
    cpu = LinuxProvider().cpu(runner)
    assert cpu.logical_cores is None or cpu.logical_cores > 0
    assert cpu.physical_cores is None or cpu.physical_cores > 0


def test_memory_values_are_positive() -> None:
    memory = LinuxProvider().memory()
    assert memory.total_bytes is not None and memory.total_bytes > 0
    assert memory.available_bytes is not None and memory.available_bytes > 0


def test_disk_free_at_explicit_path(tmp_path: object) -> None:
    disk = WindowsProvider().disk(str(tmp_path))
    assert disk.path == str(tmp_path)
    assert disk.free_bytes is not None and disk.free_bytes > 0
    assert disk.total_bytes is not None and disk.total_bytes > 0
    assert disk.error is None


def test_disk_path_is_absolute(tmp_path: object) -> None:
    disk = LinuxProvider().disk("some/relative/path")
    assert os.path.isabs(disk.path)
    assert disk.error is None or disk.free_bytes is None


def test_linux_has_no_platform_accelerator_claims() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout=""))
    accelerators, note = LinuxProvider().platform_accelerators(runner)
    assert accelerators == []
    assert note is None
    assert runner.calls == []


def test_unknown_provider_has_no_platform_accelerator_claims() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout=""))
    accelerators, note = UnknownProvider().platform_accelerators(runner)
    assert accelerators == []
    assert note is None
    assert runner.calls == []


def test_windows_gpu_probe_parses_controller_names() -> None:
    runner = FakeRunner(
        CommandResult(returncode=0, stdout="Name\nNVIDIA GeForce RTX 4090\nIntel UHD Graphics\n")
    )
    accelerators, note = WindowsProvider().platform_accelerators(runner)
    assert len(accelerators) == 2
    assert accelerators[0].kind == AcceleratorKind.UNKNOWN
    assert accelerators[0].name == "NVIDIA GeForce RTX 4090"
    assert accelerators[0].detected_via == "wmic"
    assert accelerators[1].name == "Intel UHD Graphics"
    assert note is None
    assert runner.calls[0][0] == "wmic"


def test_windows_gpu_probe_failure_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=1, stderr="denied"))
    accelerators, note = WindowsProvider().platform_accelerators(runner)
    assert accelerators == []
    assert "failed" in (note or "")


def test_windows_gpu_probe_not_found_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=None, error="executable_not_found"))
    accelerators, note = WindowsProvider().platform_accelerators(runner)
    assert accelerators == []
    assert note is not None


def test_darwin_cpu_name_from_bounded_sysctl() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="Apple M2 Pro\n"))
    cpu = DarwinProvider().cpu(runner)
    assert cpu.name == "Apple M2 Pro"
    assert runner.calls[0][0] == "sysctl"


def test_darwin_arm64_metal_without_brand_name() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("zana_core.hardware.providers.platform.machine", lambda: "arm64")
    runner = FakeRunner(CommandResult(returncode=None, error="executable_not_found"))
    accelerators, note = DarwinProvider().platform_accelerators(runner)
    assert len(accelerators) == 1
    assert accelerators[0].kind == AcceleratorKind.APPLE_METAL
    assert accelerators[0].shared_memory is True
    assert accelerators[0].name is None
    assert note is None


def test_darwin_x86_metal_parses_system_profiler() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("zana_core.hardware.providers.platform.machine", lambda: "x86_64")
    runner = FakeRunner(CommandResult(returncode=0, stdout=SYSTEM_PROFILER_JSON))
    accelerators, note = DarwinProvider().platform_accelerators(runner)
    assert len(accelerators) == 1
    assert accelerators[0].kind == AcceleratorKind.APPLE_METAL
    assert accelerators[0].name == "AMD Radeon Pro"
    assert accelerators[0].shared_memory is None
    assert note is None
    assert runner.calls[0][0] == "system_profiler"


def test_darwin_x86_metal_profiler_failure_is_unknown() -> None:
    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr("zana_core.hardware.providers.platform.machine", lambda: "x86_64")
    runner = FakeRunner(CommandResult(returncode=None, timed_out=True, error="timeout"))
    accelerators, note = DarwinProvider().platform_accelerators(runner)
    assert accelerators == []
    assert "unknown" in (note or "")


def test_linux_cpu_model_name_reads_proc_cpuinfo() -> None:
    monkeypatch = pytest.MonkeyPatch()
    real_open = open

    class FakeCPUInfo:
        def __enter__(self) -> "FakeCPUInfo":
            return self

        def __exit__(self, *args: object) -> bool:
            return False

        def __iter__(self):
            return iter(["model name\t: AMD Ryzen 9 7950X\n", "cpu cores\t: 16\n"])

    def scoped_open(path: str, *args: object, **kwargs: object) -> object:
        if path == "/proc/cpuinfo":
            return FakeCPUInfo()
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", scoped_open)
    cpu = LinuxProvider().cpu(FakeRunner(CommandResult(returncode=0, stdout="")))
    assert cpu.name == "AMD Ryzen 9 7950X"


@pytest.mark.skipif(
    platform.system() != "Darwin" or platform.machine().lower() != "arm64",
    reason="Apple Silicon Metal probe requires macOS ARM64",
)
def test_darwin_metal_real_probe() -> None:
    from zana_core.hardware.commands import SubprocessCommandRunner

    accelerators, note = DarwinProvider().platform_accelerators(SubprocessCommandRunner())
    assert len(accelerators) == 1
    assert accelerators[0].kind == AcceleratorKind.APPLE_METAL
    assert accelerators[0].detected_via in {"apple_silicon_platform", "system_profiler"}
    if accelerators[0].detected_via == "apple_silicon_platform":
        assert accelerators[0].shared_memory is True
    assert note is None
