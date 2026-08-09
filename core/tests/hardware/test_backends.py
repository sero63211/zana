"""Tests for executable/module-based backend availability."""

from zana_core.hardware.backends import RUNTIME_BACKENDS, TRAINING_BACKENDS, probe_backends
from zana_core.hardware.models import BackendKind, BackendRole


def test_ollama_found_via_executable() -> None:
    def which(name: str) -> str | None:
        return "/usr/local/bin/ollama" if name == "ollama" else None

    results = probe_backends(
        RUNTIME_BACKENDS,
        which=which,
        module_available=lambda name: None,
    )
    ollama = next(result for result in results if result.backend == BackendKind.OLLAMA)
    assert ollama.installed is True
    assert ollama.detected_via == "executable:/usr/local/bin/ollama"
    assert ollama.role == BackendRole.RUNTIME


def test_missing_backends_stay_unknown() -> None:
    results = probe_backends(
        RUNTIME_BACKENDS,
        which=lambda name: None,
        module_available=lambda name: None,
    )
    assert all(result.installed is False for result in results)
    assert all(result.detected_via is None for result in results)


def test_llama_cpp_checks_second_executable() -> None:
    def which(name: str) -> str | None:
        return "/opt/llama/llama-cli" if name == "llama-cli" else None

    results = probe_backends(
        RUNTIME_BACKENDS,
        which=which,
        module_available=lambda name: None,
    )
    llama = next(result for result in results if result.backend == BackendKind.LLAMA_CPP)
    assert llama.installed is True
    assert llama.detected_via == "executable:/opt/llama/llama-cli"


def test_mlx_module_present_for_both_roles() -> None:
    def module_available(name: str) -> object | None:
        return object() if name == "mlx_lm" else None

    training = probe_backends(TRAINING_BACKENDS, module_available=module_available)
    mlx_training = next(result for result in training if result.backend == BackendKind.MLX_LM)
    assert mlx_training.installed is True
    assert mlx_training.detected_via == "python_module:mlx_lm"
    assert mlx_training.role == BackendRole.TRAINING

    runtime = probe_backends(RUNTIME_BACKENDS, module_available=module_available)
    mlx_runtime = next(result for result in runtime if result.backend == BackendKind.MLX_LM)
    assert mlx_runtime.installed is True
    assert mlx_runtime.detected_via == "python_module:mlx_lm"


def test_hf_peft_requires_all_modules() -> None:
    results = probe_backends(
        TRAINING_BACKENDS,
        module_available=lambda name: object() if name == "peft" else None,
    )
    hf = next(result for result in results if result.backend == BackendKind.HF_PEFT)
    assert hf.installed is False


def test_default_probe_reports_bools_without_starting() -> None:
    results = probe_backends(RUNTIME_BACKENDS)
    assert len(results) == len(RUNTIME_BACKENDS)
    assert all(isinstance(result.installed, bool) for result in results)
