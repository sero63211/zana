"""Metadata-only provider probe tests with injected inspectors."""

from __future__ import annotations

from zana_core.training.contracts import ProviderProbeStatus
from zana_core.training.providers import (
    HfPeftProviderProbe,
    MLXLMProviderProbe,
    ProviderEnvironment,
    ProviderRegistry,
)


class TestMLXLMProviderProbe:
    def test_apple_silicon_with_modules_is_available(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object() if name == "mlx_lm" else None,
            version=lambda name: "0.5.0" if name == "mlx-lm" else None,
            which=lambda name: None,
            system="Darwin",
            machine="arm64",
        )
        result = MLXLMProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.AVAILABLE
        assert result.version == "0.5.0"
        assert result.platform_ok is True

    def test_non_apple_platform_is_unavailable(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object(),
            version=lambda name: "0.5.0",
            which=lambda name: None,
            system="Linux",
            machine="x86_64",
        )
        result = MLXLMProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE
        assert result.platform_ok is False

    def test_missing_module_is_unavailable_not_error(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: None,
            version=lambda name: None,
            which=lambda name: None,
            system="Darwin",
            machine="arm64",
        )
        result = MLXLMProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE
        assert result.error is not None


class TestHfPeftProviderProbe:
    def test_linux_cuda_with_peft_is_available(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object() if name in ("peft", "transformers") else None,
            version=lambda name: "0.10.0" if name == "peft" else None,
            which=lambda name: "/usr/bin/nvidia-smi" if name == "nvidia-smi" else None,
            system="Linux",
            machine="x86_64",
        )
        result = HfPeftProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.AVAILABLE
        assert result.version == "0.10.0"

    def test_macos_is_unavailable(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object(),
            version=lambda name: "0.10.0",
            which=lambda name: None,
            system="Darwin",
            machine="arm64",
        )
        result = HfPeftProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE

    def test_missing_cuda_is_unavailable(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object(),
            version=lambda name: "0.10.0",
            which=lambda name: None,
            system="Linux",
            machine="x86_64",
        )
        result = HfPeftProviderProbe(env).probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE


class TestProviderRegistry:
    def test_probe_all_returns_structured_results(self) -> None:
        registry = ProviderRegistry(
            [
                MLXLMProviderProbe(
                    ProviderEnvironment(
                        module_available=lambda name: object(),
                        version=lambda name: "0.5.0",
                        which=lambda name: None,
                        system="Darwin",
                        machine="arm64",
                    )
                ),
                HfPeftProviderProbe(
                    ProviderEnvironment(
                        module_available=lambda name: None,
                        version=lambda name: None,
                        which=lambda name: None,
                        system="Linux",
                        machine="x86_64",
                    )
                ),
            ]
        )
        results = registry.probe_all()
        assert len(results) == 2
        assert {result.provider for result in results} == {"mlx_lm", "hf_peft"}

    def test_unknown_provider_returns_none(self) -> None:
        registry = ProviderRegistry([])
        assert registry.probe("unknown") is None
