"""Tests for full hardware-profile composition."""

from datetime import UTC, datetime

from zana_core.hardware.commands import CommandResult
from zana_core.hardware.models import BackendKind, OSType
from zana_core.hardware.profile import collect_profile


def _no_backends(name: str) -> str | None:
    return None


class FakeRunner:
    """Bounded fake that records invocations and returns empty success."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, timeout: float) -> CommandResult:
        self.calls.append(list(args))
        return CommandResult(returncode=0, stdout="", stderr="")


def test_profile_has_typed_fields_on_real_host(tmp_path: object) -> None:
    profile = collect_profile(str(tmp_path))
    assert profile.os in {OSType.MACOS, OSType.LINUX, OSType.WINDOWS}
    assert profile.arch
    assert profile.cpu.logical_cores is None or profile.cpu.logical_cores > 0
    assert profile.memory.total_bytes is not None and profile.memory.total_bytes > 0
    assert profile.memory.available_bytes is not None
    assert profile.disk.path == str(tmp_path)
    assert profile.disk.free_bytes is not None and profile.disk.free_bytes > 0
    assert datetime.fromisoformat(profile.collected_at) is not None
    assert isinstance(profile.notes, list)


def test_linux_profile_uses_no_commands_without_executables(
    monkeypatch: object, tmp_path: object
) -> None:
    monkeypatch.setattr("zana_core.hardware.profile.platform.system", lambda: "Linux")
    runner = FakeRunner()
    profile = collect_profile(str(tmp_path), runner=runner, which=_no_backends)
    assert profile.os == OSType.LINUX
    assert profile.accelerators == []
    assert runner.calls == []
    assert all(not backend.installed for backend in profile.training_backends)
    assert all(not backend.installed for backend in profile.runtime_backends)
    assert all(backend.detected_via is None for backend in profile.runtime_backends)
    assert profile.notes == []
    assert profile.disk.error is None


def test_backend_reported_from_executable_without_start(
    monkeypatch: object, tmp_path: object
) -> None:
    monkeypatch.setattr("zana_core.hardware.profile.platform.system", lambda: "Linux")

    def which(name: str) -> str | None:
        return "/usr/local/bin/ollama" if name == "ollama" else None

    profile = collect_profile(str(tmp_path), runner=FakeRunner(), which=which)
    ollama = next(
        backend for backend in profile.runtime_backends if backend.backend == BackendKind.OLLAMA
    )
    assert ollama.installed is True
    assert ollama.detected_via == "executable:/usr/local/bin/ollama"


def test_nvidia_probe_wired_into_profile(monkeypatch: object, tmp_path: object) -> None:
    monkeypatch.setattr("zana_core.hardware.profile.platform.system", lambda: "Linux")

    class NvidiaRunner(FakeRunner):
        def run(self, args: list[str], *, timeout: float) -> CommandResult:
            self.calls.append(list(args))
            if args[0].endswith("nvidia-smi"):
                return CommandResult(
                    returncode=0,
                    stdout="NVIDIA GeForce RTX 4090,24576,8192,550.54.15\n",
                )
            return CommandResult(returncode=0, stdout="", stderr="")

    def which(name: str) -> str | None:
        return "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None

    runner = NvidiaRunner()
    profile = collect_profile(str(tmp_path), runner=runner, which=which)
    assert len(profile.accelerators) == 1
    accelerator = profile.accelerators[0]
    assert accelerator.detected_via == "nvidia-smi"
    assert accelerator.vram_total_bytes == 24576 * 1024 * 1024


def test_unknown_os_profile_is_honest(monkeypatch: object, tmp_path: object) -> None:
    monkeypatch.setattr("zana_core.hardware.profile.platform.system", lambda: "Haiku")
    profile = collect_profile(str(tmp_path), runner=FakeRunner(), which=_no_backends)
    assert profile.os == OSType.UNKNOWN
    assert profile.arch
    assert profile.memory.total_bytes is not None
    assert profile.disk.free_bytes is not None


def test_collected_at_uses_injected_clock(monkeypatch: object, tmp_path: object) -> None:
    monkeypatch.setattr("zana_core.hardware.profile.platform.system", lambda: "Linux")
    fixed = datetime(2026, 8, 9, 12, 30, tzinfo=UTC)
    profile = collect_profile(
        str(tmp_path), runner=FakeRunner(), which=_no_backends, now=lambda: fixed
    )
    assert profile.collected_at == fixed.isoformat()
