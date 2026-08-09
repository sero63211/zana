"""ZANA Image config model and runnability tests."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.images.models import (
    BaseModelReference,
    RunnableState,
    ZanaImageConfig,
)

BASE_DIGEST = "sha256:" + "a" * 64


def make_config(identity_digest: str | None = BASE_DIGEST) -> ZanaImageConfig:
    return ZanaImageConfig(
        name="math-tutor",
        version="1.0.0",
        base_model=BaseModelReference(
            display_name="example-model",
            identity_digest=identity_digest,
            runtime_compatibility=["ollama"],
            required_capabilities=["completion"],
        ),
    )


class TestConfigValidation:
    def test_valid_config_round_trips(self) -> None:
        config = make_config()
        payload = config.model_dump(mode="json")
        payload["schemaVersion"] = 1
        payload["kind"] = "ZanaImage"
        assert payload["schemaVersion"] == 1
        assert payload["kind"] == "ZanaImage"
        assert ZanaImageConfig.model_validate(payload) == config

    def test_invalid_digest_is_rejected(self) -> None:
        with pytest.raises(ValidationError):
            make_config(identity_digest="sha256:not-a-digest")

    def test_weak_identity_is_allowed_as_explicit_state(self) -> None:
        config = make_config(identity_digest=None)
        runnability = config.runnability()
        assert runnability.state == RunnableState.NOT_RUNNABLE_WEAK_IDENTITY
        assert "no exact digest" in runnability.reason

    def test_missing_base_is_not_runnable_without_substitution(self) -> None:
        config = make_config()
        runnability = config.runnability(available_base_digests=set())
        assert runnability.state == RunnableState.NOT_RUNNABLE_MISSING_BASE
        assert runnability.exact_base_digest == BASE_DIGEST

    def test_exact_base_available_is_runnable(self) -> None:
        config = make_config()
        runnability = config.runnability(available_base_digests={BASE_DIGEST})
        assert runnability.state == RunnableState.RUNNABLE

    def test_schema_version_and_kind_are_locked(self) -> None:
        with pytest.raises(ValidationError):
            ZanaImageConfig(
                schema_version=2,
                kind="ZanaImage",
                name="x",
                version="1",
                base_model=BaseModelReference(identity_digest=BASE_DIGEST),
            )


class TestSecretExclusion:
    def test_exclusion_scanner_skips_secrets_and_state(
        self,
        tmp_path: Path,
    ) -> None:
        from zana_core.images.secrets import ExclusionScanner

        root = tmp_path / "capability"
        (root / "behavior").mkdir(parents=True)
        (root / "secrets").mkdir()
        (root / "state" / "conversations").mkdir(parents=True)
        (root / "behavior" / "system.md").write_text("policy")
        (root / "secrets" / "token.txt").write_text("sensitive")
        (root / "state" / "conversations" / "chat.json").write_text("{}")

        scanner = ExclusionScanner()
        safe = scanner.scan(root)
        assert [path.name for path in safe] == ["system.md"]
        assert scanner.classify(root / "secrets" / "token.txt") == "secret"
        assert scanner.classify(root / "state" / "conversations" / "chat.json") == "mutable-state"

    def test_symlink_in_export_root_is_rejected(self, tmp_path: Path) -> None:
        from zana_core.images.secrets import ExclusionError, ExclusionScanner

        root = tmp_path / "capability"
        root.mkdir()
        (root / "behavior.md").write_text("ok")
        (root / "link").symlink_to(tmp_path / "outside")
        with pytest.raises(ExclusionError):
            ExclusionScanner().scan(root)
