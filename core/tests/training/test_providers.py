"""Metadata-only provider probe tests with injected inspectors."""

from __future__ import annotations

from zana_core.training.contracts import ProviderProbeStatus
from zana_core.training.providers import (
    HfPeftProviderProbe,
    MLXLMProviderProbe,
    ProviderEnvironment,
    ProviderRegistry,
)


def _mlx_env(*, which: str | None = "/private/bin/mlx_lm.lora") -> ProviderEnvironment:
    return ProviderEnvironment(
        module_available=lambda name: object() if name == "mlx_lm" else None,
        version=lambda name: "0.5.0" if name == "mlx-lm" else None,
        which=lambda name: which if name == "mlx_lm.lora" else None,
        system="Darwin",
        machine="arm64",
    )


class TestMLXLMProviderProbe:
    def test_apple_silicon_with_modules_and_executable_is_available(self) -> None:
        result = MLXLMProviderProbe(_mlx_env()).probe()
        assert result.status == ProviderProbeStatus.AVAILABLE
        assert result.version == "0.5.0"
        assert result.platform_ok is True

    def test_evidence_never_leaks_full_executable_paths(self) -> None:
        result = MLXLMProviderProbe(_mlx_env()).probe()
        for item in result.evidence:
            assert "/" not in item
        assert "executable:mlx_lm.lora" in result.evidence

    def test_missing_executable_is_unavailable(self) -> None:
        result = MLXLMProviderProbe(_mlx_env(which=None)).probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE
        assert result.error is not None
        assert "mlx_lm.lora" in result.error

    def test_non_apple_platform_is_unavailable(self) -> None:
        env = ProviderEnvironment(
            module_available=lambda name: object(),
            version=lambda name: "0.5.0",
            which=lambda name: "/usr/bin/mlx_lm.lora",
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
    def test_hf_peft_is_explicitly_unavailable(self) -> None:
        result = HfPeftProviderProbe().probe()
        assert result.status == ProviderProbeStatus.UNAVAILABLE
        assert "not implemented" in (result.error or "")


class TestProviderRegistry:
    def test_probe_all_returns_structured_results(self) -> None:
        registry = ProviderRegistry(
            [
                MLXLMProviderProbe(_mlx_env()),
                HfPeftProviderProbe(),
            ]
        )
        results = registry.probe_all()
        assert len(results) == 2
        assert {result.provider for result in results} == {"mlx_lm", "hf_peft"}
        assert results[0].status == ProviderProbeStatus.AVAILABLE
        assert results[1].status == ProviderProbeStatus.UNAVAILABLE

    def test_unknown_provider_returns_none(self) -> None:
        registry = ProviderRegistry([])
        assert registry.probe("unknown") is None
