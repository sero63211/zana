"""Tests for the optional NVIDIA probe and CSV parser."""

from zana_core.hardware.commands import CommandResult
from zana_core.hardware.models import AcceleratorKind
from zana_core.hardware.nvidia import (
    parse_nvidia_accelerators,
    parse_size_bytes,
    probe_nvidia,
)

GOOD_CSV = "NVIDIA GeForce RTX 4090,24576,8192,550.54.15\n"


class FakeRunner:
    """Records invocations and returns a canned command result."""

    def __init__(self, result: CommandResult) -> None:
        self.result = result
        self.calls: list[list[str]] = []

    def run(self, args: list[str], *, timeout: float) -> CommandResult:
        self.calls.append(list(args))
        return self.result


def test_parse_size_bytes_plain_int() -> None:
    assert parse_size_bytes("24576") == 24576 * 1024 * 1024


def test_parse_size_bytes_with_units() -> None:
    assert parse_size_bytes("24576 MiB") == 24576 * 1024 * 1024
    assert parse_size_bytes("24 GiB") == 24 * 1024**3


def test_parse_size_bytes_unknown_is_none() -> None:
    assert parse_size_bytes("N/A") is None
    assert parse_size_bytes("") is None
    assert parse_size_bytes("unknown") is None


def test_parse_valid_csv_rows() -> None:
    accelerators = parse_nvidia_accelerators(GOOD_CSV)
    assert len(accelerators) == 1
    gpu = accelerators[0]
    assert gpu.kind == AcceleratorKind.NVIDIA_CUDA
    assert gpu.name == "NVIDIA GeForce RTX 4090"
    assert gpu.vram_total_bytes == 24576 * 1024 * 1024
    assert gpu.vram_free_bytes == 8192 * 1024 * 1024
    assert gpu.driver == "550.54.15"
    assert gpu.detected_via == "nvidia-smi"


def test_na_fields_stay_unknown() -> None:
    accelerators = parse_nvidia_accelerators("NVIDIA GeForce RTX 3060,N/A,N/A,N/A\n")
    gpu = accelerators[0]
    assert gpu.vram_total_bytes is None
    assert gpu.vram_free_bytes is None
    assert gpu.driver is None


def test_malformed_rows_are_ignored() -> None:
    assert parse_nvidia_accelerators("not enough fields\nname,total\n") == []


def test_garbage_output_has_no_accelerators() -> None:
    assert parse_nvidia_accelerators("<html>no gpu here</html>") == []


def test_absent_executable_skips_probe() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout=GOOD_CSV))
    accelerators, note = probe_nvidia(runner, which=lambda name: None)
    assert accelerators == []
    assert note is None
    assert runner.calls == []


def test_probe_success() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout=GOOD_CSV))
    accelerators, note = probe_nvidia(runner, nvidia_smi_path="/usr/bin/nvidia-smi")
    assert len(accelerators) == 1
    assert note is None
    assert runner.calls[0][0] == "/usr/bin/nvidia-smi"


def test_probe_failure_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=1, stdout="", stderr="driver error"))
    accelerators, note = probe_nvidia(runner, nvidia_smi_path="/usr/bin/nvidia-smi")
    assert accelerators == []
    assert "exit code 1" in (note or "")


def test_probe_timeout_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=None, timed_out=True, error="timeout"))
    accelerators, note = probe_nvidia(runner, nvidia_smi_path="/usr/bin/nvidia-smi")
    assert accelerators == []
    assert "timed out" in (note or "")


def test_probe_runner_error_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=None, error="os_error:denied"))
    accelerators, note = probe_nvidia(runner, nvidia_smi_path="/usr/bin/nvidia-smi")
    assert accelerators == []
    assert "failed" in (note or "")


def test_probe_unparseable_output_is_non_fatal() -> None:
    runner = FakeRunner(CommandResult(returncode=0, stdout="???\n"))
    accelerators, note = probe_nvidia(runner, nvidia_smi_path="/usr/bin/nvidia-smi")
    assert accelerators == []
    assert "no parseable" in (note or "")
