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
    modules = ("mlx_lm",)
    executables = ("mlx_lm.train",)

    def __init__(self, env: ProviderEnvironment | None = None) -> None:
        self.env = env or ProviderEnvironment()

    def probe(self) -> ProviderProbe:
        evidence: list[str] = []
        system = self.env.system or platform.system()
        machine = self.env.machine or platform.machine()
        platform_ok = system.lower() == "darwin" and machine.lower() in ("arm64", "aarch64")
        present = [
            module for module in self.modules if self.env.module_available(module) is not None
        ]
        version = self.env.version("mlx-lm")
        if present:
            evidence.append("module:" + ",".join(present))
        for executable in self.executables:
            path = self.env.which(executable)
            if path:
                evidence.append(f"executable:{path}")
        if version:
            evidence.append(f"version:{version}")
        if not platform_ok:
            evidence.append(f"platform:{system}/{machine}")
        available = platform_ok and bool(present or evidence) and version is not None
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
                else "MLX-LM training is unavailable on this platform or package set"
            ),
        )


class HfPeftProviderProbe:
    """Probes PEFT/Transformers on CUDA hosts without importing frameworks."""

    provider = "hf_peft"
    modules = ("peft", "transformers")

    def __init__(self, env: ProviderEnvironment | None = None) -> None:
        self.env = env or ProviderEnvironment()

    def probe(self) -> ProviderProbe:
        evidence: list[str] = []
        system = self.env.system or platform.system()
        machine = self.env.machine or platform.machine()
        present = [
            module for module in self.modules if self.env.module_available(module) is not None
        ]
        version = self.env.version("peft")
        if present:
            evidence.append("module:" + ",".join(present))
        if version:
            evidence.append(f"version:{version}")
        cuda = self.env.which("nvidia-smi")
        if cuda:
            evidence.append("executable:nvidia-smi")
        platform_ok = system.lower() == "linux" and machine.lower() in (
            "x86_64",
            "amd64",
            "aarch64",
            "arm64",
        )
        available = (
            platform_ok
            and len(present) == len(self.modules)
            and cuda is not None
            and version is not None
        )
        status = ProviderProbeStatus.AVAILABLE if available else ProviderProbeStatus.UNAVAILABLE
        return ProviderProbe(
            provider=self.provider,
            status=status,
            version=version,
            platform_ok=platform_ok,
            evidence=evidence,
            error=(
                None if available else "HF PEFT training requires Linux CUDA plus PEFT/Transformers"
            ),
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
