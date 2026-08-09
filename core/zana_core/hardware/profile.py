"""Compose the cross-platform, non-privileged hardware profile."""

from __future__ import annotations

import platform
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from zana_core.hardware.backends import RUNTIME_BACKENDS, TRAINING_BACKENDS, probe_backends
from zana_core.hardware.commands import CommandRunner, SubprocessCommandRunner
from zana_core.hardware.models import HardwareProfile
from zana_core.hardware.nvidia import probe_nvidia
from zana_core.hardware.providers import detect_arch, detect_os_type, provider_for

ExecutableProbe = Callable[[str], str | None]


def collect_profile(
    workspace_path: str | Path,
    *,
    runner: CommandRunner | None = None,
    which: ExecutableProbe | None = None,
    now: Callable[[], datetime] | None = None,
) -> HardwareProfile:
    """Collect an honest host snapshot without privileges or process starts.

    Only bounded read-only commands and filesystem queries are used. Backend
    availability comes from installed executables/modules; nothing is started.
    Unknown values stay ``None`` and are never replaced by invented numbers.
    """
    active_runner = runner or SubprocessCommandRunner()
    active_which = which or shutil.which
    os_type = detect_os_type(platform.system())
    provider = provider_for(os_type)

    notes: list[str] = []
    accelerators, accelerator_note = provider.platform_accelerators(active_runner)
    if accelerator_note:
        notes.append(accelerator_note)
    nvidia_accelerators, nvidia_note = probe_nvidia(active_runner, which=active_which)
    accelerators.extend(nvidia_accelerators)
    if nvidia_note:
        notes.append(nvidia_note)

    collected_at = (now() if now else datetime.now(UTC)).isoformat()

    return HardwareProfile(
        os=os_type,
        arch=detect_arch(),
        cpu=provider.cpu(active_runner),
        memory=provider.memory(),
        disk=provider.disk(workspace_path),
        accelerators=accelerators,
        training_backends=probe_backends(TRAINING_BACKENDS, which=active_which),
        runtime_backends=probe_backends(RUNTIME_BACKENDS, which=active_which),
        collected_at=collected_at,
        notes=notes,
    )
