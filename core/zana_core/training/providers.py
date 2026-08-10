"""Metadata-only provider probes and registry for MLX-LM and HF PEFT."""

from __future__ import annotations

import importlib.metadata
import importlib.util
import platform
import shutil
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from zana_core.training.contracts import ProviderProbe, ProviderProbeStatus

ModuleProbe = Callable[[str], object | None]
VersionProbe = Callable[[str], str | None]
ExecutableProbe = Callable[[str], str | None]


def _default_module_available(name: str) -> object | None:
    try:
        return importlib.util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None


def _default_version(name: str) -> str | None:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return None


def _default_which(name: str) -> str | None:
    return shutil.which(name)


@dataclass(frozen=True, slots=True)
class ProviderEnvironment:
    """Injected metadata inspectors so tests never touch real machines."""

    module_available: ModuleProbe = _default_module_available
    version: VersionProbe = _default_version
    which: ExecutableProbe = _default_which
    system: str | None = None
    machine: str | None = None

    def _platform_ok(self) -> bool:
        return self.system is not None and self.machine is not None


class MLXLMProviderProbe:
    """Probes mlx_lm on Apple Silicon without importing it."""

    provider = "mlx_lm"

    def __init__(self, env: ProviderEnvironment | None = None) -> None:
        self.env = env or ProviderEnvironment()

    def resolve_executable(self) -> str | None:
        """Return the resolved mlx_lm.lora path; never exposed in probe evidence."""
        return self.env.which("mlx_lm.lora")

    def probe(self) -> ProviderProbe:
        evidence: list[str] = []
        system = self.env.system or platform.system()
        machine = self.env.machine or platform.machine()
        platform_ok = system.lower() == "darwin" and machine.lower() in ("arm64", "aarch64")
        module_present = self.env.module_available("mlx_lm") is not None
        version = self.env.version("mlx-lm")
        executable = self.env.which("mlx_lm.lora")
        if module_present:
            evidence.append("module:mlx_lm")
        if version:
            evidence.append(f"version:{version}")
        if executable is not None:
            # Basename only: resolved paths stay internal to the executor.
            evidence.append("executable:mlx_lm.lora")
        evidence.append(f"platform:{system}-{machine}")
        available = (
            platform_ok and module_present and version is not None and executable is not None
        )
        status = ProviderProbeStatus.AVAILABLE if available else ProviderProbeStatus.UNAVAILABLE
        return ProviderProbe(
            provider=self.provider,
            status=status,
            version=version,
            platform_ok=platform_ok,
            evidence=evidence,
            error=(
                None
                if available
                else "MLX-LM training requires Apple Silicon, installed mlx_lm, "
                "the mlx-lm package version, and a resolvable mlx_lm.lora executable"
            ),
        )


class HfPeftProviderProbe:
    """Explicitly unavailable; ZANA v1 does not implement HF PEFT execution."""

    provider = "hf_peft"

    def __init__(self, env: ProviderEnvironment | None = None) -> None:
        self.env = env or ProviderEnvironment()

    def probe(self) -> ProviderProbe:
        return ProviderProbe(
            provider=self.provider,
            status=ProviderProbeStatus.UNAVAILABLE,
            version=None,
            platform_ok=False,
            evidence=["hf_peft:not_implemented"],
            error="HF PEFT training execution is not implemented in ZANA v1",
        )


class ProviderRegistry:
    """Registry of metadata-only training provider probes."""

    def __init__(
        self,
        probes: Sequence[MLXLMProviderProbe | HfPeftProviderProbe] | None = None,
    ) -> None:
        self._probes = (
            list(probes)
            if probes is not None
            else [
                MLXLMProviderProbe(),
                HfPeftProviderProbe(),
            ]
        )

    def probe_all(self) -> list[ProviderProbe]:
        return [probe.probe() for probe in self._probes]

    def probe(self, provider: str) -> ProviderProbe | None:
        for probe in self._probes:
            if getattr(probe, "provider", None) == provider:
                return probe.probe()
        return None
