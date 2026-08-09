"""Evaluation model and reproducibility configuration capture tests."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.evaluation.models import (
    EvaluationSuiteResult,
    ReproducibilitySettings,
    ScorerConfig,
    ScorerType,
)


class TestModels:
    def test_reproducibility_settings_capture_without_runtime_call(self) -> None:
        settings = ReproducibilitySettings(
            temperature_expectation=0.0,
            seed=42,
            max_tokens=256,
            runtime_identity="ollama:1.0",
            model_identity="model-x",
            model_version="v1",
        )
        assert settings.temperature_expectation == 0.0
        assert settings.seed == 42
        assert settings.max_tokens == 256
        assert settings.model_identity == "model-x"

    def test_scorer_config_roundtrip_preserves_raw_values(self) -> None:
        config = ScorerConfig(
            type=ScorerType.NUMERIC_TOLERANCE,
            expected=391,
            tolerance=0.1,
        )
        assert config.expected == 391
        assert config.tolerance == 0.1

    def test_json_schema_alias_roundtrip(self) -> None:
        config = ScorerConfig(
            type=ScorerType.JSON_SCHEMA_VALID,
            schema={"type": "object"},
        )
        assert config.json_schema == {"type": "object"}

    def test_suite_result_requires_reproducibility(self) -> None:
        with pytest.raises(ValidationError):
            EvaluationSuiteResult(
                suite_id="suite",
                reproducibility=ReproducibilitySettings(temperature_expectation=0.2),
                results=[],
                metrics=None,
            )
