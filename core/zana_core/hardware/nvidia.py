"""Optional NVIDIA probing through nvidia-smi only when the executable exists."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from zana_core.hardware.commands import CommandRunner
from zana_core.hardware.models import AcceleratorInfo, AcceleratorKind

_MEMORY_UNITS = {
    "kb": 10**3,
    "mb": 10**6,
    "gb": 10**9,
    "kib": 2**10,
    "mib": 2**20,
    "gib": 2**30,
}

NVIDIA_SMI_QUERY_ARGS = (
    "--query-gpu=name,memory.total,memory.free,driver_version",
    "--format=csv,noheader,nounits",
)


def parse_size_bytes(value: str) -> int | None:
    """Parse a GPU memory value such as '24576', '24576MiB', or 'N/A'."""
    token = value.strip()
    if not token or token.upper() == "N/A":
        return None
    number = ""
    suffix = ""
    for char in token:
        if char.isdigit() or char == ".":
            number += char
        else:
            suffix += char
    if not number:
        return None
    try:
        amount = float(number)
    except ValueError:
        return None
    normalized_suffix = suffix.strip().lower()
    if not normalized_suffix:
        # nvidia-smi --format=csv,nounits reports memory in MiB.
        return int(amount * 2**20)
    multiplier = _MEMORY_UNITS.get(normalized_suffix)
    if multiplier is None:
        return None
    return int(amount * multiplier)


def parse_nvidia_rows(stdout: str) -> list[dict[str, str | None]]:
    """Parse nvidia-smi CSV rows; malformed lines are dropped."""
    rows: list[dict[str, str | None]] = []
    for line in stdout.splitlines():
        fields = [field.strip() for field in line.split(",")]
        if len(fields) < 4:
            continue
        rows.append(
            {
                "name": fields[0],
                "memory_total": fields[1],
                "memory_free": fields[2],
                "driver": fields[3],
            }
        )
    return rows


def parse_nvidia_accelerators(stdout: str) -> list[AcceleratorInfo]:
    """Convert nvidia-smi CSV output into typed accelerator records."""
    accelerators: list[AcceleratorInfo] = []
    for row in parse_nvidia_rows(stdout):
        name = row["name"]
        if name and name.upper() == "N/A":
            name = None
        driver = row["driver"]
        if driver and driver.upper() == "N/A":
            driver = None
        accelerators.append(
            AcceleratorInfo(
                kind=AcceleratorKind.NVIDIA_CUDA,
                name=name,
                vram_total_bytes=parse_size_bytes(row["memory_total"] or ""),
                vram_free_bytes=parse_size_bytes(row["memory_free"] or ""),
                driver=driver,
                detected_via="nvidia-smi",
            )
        )
    return accelerators


def probe_nvidia(
    runner: CommandRunner,
    *,
    which: Callable[[str], str | None] | None = None,
    nvidia_smi_path: str | None = None,
    timeout: float = 5.0,
) -> tuple[list[AcceleratorInfo], str | None]:
    """Probe NVIDIA GPUs; absent, failed, or malformed probes are non-fatal."""
    path = nvidia_smi_path or (which or shutil.which)("nvidia-smi")
    if not path:
        return [], None
    result = runner.run([path, *NVIDIA_SMI_QUERY_ARGS], timeout=timeout)
    if result.timed_out:
        return [], "nvidia-smi probe timed out"
    if result.error is not None:
        return [], f"nvidia-smi probe failed: {result.error}"
    if result.returncode != 0:
        return [], f"nvidia-smi probe failed with exit code {result.returncode}"
    accelerators = parse_nvidia_accelerators(result.stdout)
    if not accelerators:
        return [], "nvidia-smi returned no parseable GPU rows"
    return accelerators, None
