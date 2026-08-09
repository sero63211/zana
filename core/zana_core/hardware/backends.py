"""Training/runtime backend availability from installed executables or modules."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from importlib import util

from zana_core.hardware.models import BackendAvailability, BackendKind, BackendRole

ExecutableProbe = Callable[[str], str | None]
ModuleProbe = Callable[[str], object | None]


@dataclass(frozen=True)
class BackendProbeSpec:
    """Which installed artifacts prove a backend is available."""

    backend: BackendKind
    role: BackendRole
    executables: tuple[str, ...] = ()
    modules: tuple[str, ...] = ()


RUNTIME_BACKENDS: tuple[BackendProbeSpec, ...] = (
    BackendProbeSpec(BackendKind.OLLAMA, BackendRole.RUNTIME, executables=("ollama",)),
    BackendProbeSpec(BackendKind.LM_STUDIO, BackendRole.RUNTIME, executables=("lms",)),
    BackendProbeSpec(
        BackendKind.LLAMA_CPP,
        BackendRole.RUNTIME,
        executables=("llama-server", "llama-cli"),
    ),
    BackendProbeSpec(BackendKind.MLX_LM, BackendRole.RUNTIME, modules=("mlx_lm",)),
)

TRAINING_BACKENDS: tuple[BackendProbeSpec, ...] = (
    BackendProbeSpec(BackendKind.MLX_LM, BackendRole.TRAINING, modules=("mlx_lm",)),
    BackendProbeSpec(
        BackendKind.HF_PEFT,
        BackendRole.TRAINING,
        modules=("peft", "transformers"),
    ),
)


def _default_module_available(name: str) -> object | None:
    try:
        return util.find_spec(name)
    except (ImportError, AttributeError, ValueError):
        return None


def probe_backends(
    specs: Sequence[BackendProbeSpec],
    *,
    which: ExecutableProbe | None = None,
    module_available: ModuleProbe | None = None,
) -> list[BackendAvailability]:
    """Report installed status without importing or starting any backend."""
    import shutil

    find_executable = which or shutil.which
    find_module = module_available or _default_module_available
    results: list[BackendAvailability] = []
    for spec in specs:
        detected_via: str | None = None
        for executable in spec.executables:
            path = find_executable(executable)
            if path:
                detected_via = f"executable:{path}"
                break
        if detected_via is None:
            present_modules = [module for module in spec.modules if find_module(module) is not None]
            if spec.modules and len(present_modules) == len(spec.modules):
                detected_via = f"python_module:{','.join(spec.modules)}"
        results.append(
            BackendAvailability(
                backend=spec.backend,
                role=spec.role,
                installed=detected_via is not None,
                detected_via=detected_via,
            )
        )
    return results
