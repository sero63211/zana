"""Runtime materialization compatibility decisions; no runtime contact."""

from __future__ import annotations

from zana_core.training.contracts import (
    AdapterBaseIdentity,
    AdapterMaterializationCompatibility,
    AdapterMetadata,
)


def decide_materialization(
    runtime_id: str,
    base: AdapterBaseIdentity,
    adapter: AdapterMetadata,
) -> AdapterMaterializationCompatibility:
    """Decide compatibility only; this task never contacts a runtime."""
    if adapter.base_model_digest != base.base_model_digest:
        return AdapterMaterializationCompatibility(
            runtime_id=runtime_id,
            compatible=False,
            reason="adapter base digest does not match the declared base",
            adapter=adapter,
        )
    if adapter.training_provider not in ("mlx_lm", "hf_peft"):
        return AdapterMaterializationCompatibility(
            runtime_id=runtime_id,
            compatible=False,
            reason=f"unsupported adapter provider {adapter.training_provider!r}",
            adapter=adapter,
        )
    if runtime_id == "ollama" and adapter.training_provider == "hf_peft":
        return AdapterMaterializationCompatibility(
            runtime_id=runtime_id,
            compatible=False,
            reason="HF PEFT adapter cannot be materialized by the Ollama runtime",
            adapter=adapter,
        )
    if runtime_id not in ("ollama", "mlx_lm", "openai-compatible"):
        return AdapterMaterializationCompatibility(
            runtime_id=runtime_id,
            compatible=False,
            reason=f"runtime {runtime_id!r} cannot load this adapter",
            adapter=adapter,
        )
    return AdapterMaterializationCompatibility(
        runtime_id=runtime_id,
        compatible=True,
        reason="exact base digest and provider compatibility proven",
        adapter=adapter,
    )
