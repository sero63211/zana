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
from zana_core.images.secrets import (
    ExclusionError,
    ExclusionScanner,
    SecretScanLimits,
    scan_layout_payloads,
    scan_payload_for_secrets,
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
        payload = config.model_dump(mode="json", by_alias=True)
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

    def test_tool_and_capability_list_caps(self) -> None:
        from zana_core.images.models import Tool

        with pytest.raises(ValidationError):
            ZanaImageConfig(
                name="x",
                version="1",
                base_model=BaseModelReference(identity_digest=BASE_DIGEST),
                tools=[Tool(id=f"tool-{index}") for index in range(200)],
            )
        with pytest.raises(ValidationError):
            BaseModelReference(
                identity_digest=BASE_DIGEST,
                runtime_compatibility=[f"runtime-{index}" for index in range(100)],
            )

    def test_empty_permission_arrays_serialize_as_arrays_not_null(self) -> None:
        from zana_core.images.oci import canonical_json_bytes

        config = make_config()
        payload = config.model_dump(mode="json", by_alias=True)
        permissions = payload["permissions"]
        assert permissions["filesystem_read"] == []
        assert permissions["filesystem_write"] == []
        assert permissions["tools_allow"] == []
        assert permissions["secrets_allow"] == []
        assert ZanaImageConfig.model_validate(payload) == config
        encoded = canonical_json_bytes(config)
        assert b'"filesystem_read":[]' in encoded
        assert b"null" not in encoded


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

    def test_scan_payload_detects_sensitive_keys(self) -> None:
        from zana_core.images.secrets import scan_payload_for_secrets

        hits = scan_payload_for_secrets(
            {
                "runtime": {"endpoint_token": "sk-live"},
                "permissions": {"digest": "sha256:" + "a" * 64},
            }
        )
        assert hits == ["runtime.endpoint_token"]

    def test_classify_member_name(self) -> None:
        from zana_core.images.secrets import ExclusionScanner

        scanner = ExclusionScanner()
        assert scanner.classify_member_name("blobs/sha256/abc") == "safe"
        assert scanner.classify_member_name("secrets/token.txt") == "secret"
        assert scanner.classify_member_name("instances/inst-1/memories.json") == "mutable-state"

    def test_scan_member_names_rejects_unsafe(self) -> None:
        from zana_core.images.secrets import ExclusionError, ExclusionScanner

        scanner = ExclusionScanner()
        assert scanner.scan_member_names(["oci-layout", "index.json"]) == [
            "oci-layout",
            "index.json",
        ]
        with pytest.raises(ExclusionError):
            scanner.scan_member_names(["instances/inst-1/state.json"])

    def test_scan_member_names_enforces_input_cap(self) -> None:
        scanner = ExclusionScanner()
        with pytest.raises(ExclusionError, match="count exceeds"):
            scanner.scan_member_names([f"f{i}.txt" for i in range(9000)])

    def test_payload_scan_depth_budget(self) -> None:
        payload = {"a": {"b": {"c": {"d": {"e": {"f": {"g": {"h": {"i": {"j": {}}}}}}}}}}}
        with pytest.raises(ExclusionError, match="depth"):
            scan_payload_for_secrets(payload, limits=SecretScanLimits(max_depth=4))

    def test_payload_scan_item_budget(self) -> None:
        payload = {"items": list(range(9000))}
        with pytest.raises(ExclusionError, match="item"):
            scan_payload_for_secrets(payload, limits=SecretScanLimits(max_items=100))

    def test_payload_scan_hit_budget(self) -> None:
        payload = {f"api_token_{i}": "secret" for i in range(100)}
        with pytest.raises(ExclusionError, match="hit"):
            scan_payload_for_secrets(payload, limits=SecretScanLimits(max_hits=8))

    def test_payload_scan_key_and_path_budgets(self) -> None:
        with pytest.raises(ExclusionError, match="key"):
            scan_payload_for_secrets(
                {"x" * 300: "value"}, limits=SecretScanLimits(max_key_chars=10)
            )
        with pytest.raises(ExclusionError, match="path"):
            scan_payload_for_secrets(
                {"a": {"b": {"c": "v"}}},
                limits=SecretScanLimits(max_path_chars=3),
            )

    def test_payload_scan_value_budget(self) -> None:
        with pytest.raises(ExclusionError, match="value"):
            scan_payload_for_secrets(
                {"api_token": "x" * 5000},
                limits=SecretScanLimits(max_value_chars=32),
            )

    def test_layout_scan_uses_bounded_read_and_safe_errors(self, tmp_path: Path) -> None:
        layout = tmp_path / "layout"
        layout.mkdir()
        (layout / "oci-layout").write_bytes(b"x" * 300)
        with pytest.raises(ExclusionError, match="size limit"):
            scan_layout_payloads(layout, max_json_bytes=64)
        (layout / "oci-layout").write_bytes(b"{bad json")
        try:
            scan_layout_payloads(layout, max_json_bytes=128)
        except ExclusionError as error:
            assert "could not parse" in str(error)
            assert str(layout) not in str(error)
        else:
            raise AssertionError("malformed JSON must raise")

    def test_layout_scan_respects_deadline(self, tmp_path: Path) -> None:
        class FakeDeadline:
            def check(self, stage: object) -> None:
                raise ExclusionError("deadline exceeded")

        layout = tmp_path / "layout"
        layout.mkdir()
        (layout / "oci-layout").write_text('{"imageLayoutVersion":"1.0.0"}')
        with pytest.raises(ExclusionError, match="deadline"):
            scan_layout_payloads(layout, deadline=FakeDeadline())

    def test_scanner_walk_is_bounded(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        root = tmp_path / "tree"
        (root / "a").mkdir(parents=True)
        for index in range(20):
            (root / "a" / f"f{index:04d}.txt").write_bytes(b"x")
        from pathlib import Path as _Path

        real_iterdir = _Path.iterdir
        pulled: list[str] = []

        def guarded_iterdir(self: _Path):
            for item in real_iterdir(self):
                pulled.append(item.name)
                if len(pulled) > 5:
                    raise AssertionError("scanner materialized more than cap+1 entries")
                yield item

        monkeypatch.setattr(_Path, "iterdir", guarded_iterdir)
        with pytest.raises(ExclusionError, match="member count"):
            ExclusionScanner(max_entries=3).scan(root)
        assert len(pulled) <= 5

    def test_scanner_rejects_excessive_depth(self, tmp_path: Path) -> None:
        deep = tmp_path / "tree"
        current = deep
        for _ in range(40):
            current = current / "d"
        current.mkdir(parents=True)
        (current / "f.txt").write_bytes(b"x")
        with pytest.raises(ExclusionError, match="depth"):
            ExclusionScanner(max_depth=8).scan(deep)

    def test_payload_frontier_accounted_before_stack_growth(self) -> None:
        payload = {"items": list(range(1000))}
        with pytest.raises(ExclusionError, match="item"):
            scan_payload_for_secrets(payload, limits=SecretScanLimits(max_items=2))

    def test_custom_policy_bounds_are_hard(self) -> None:
        with pytest.raises(ExclusionError, match="invalid item"):
            ExclusionScanner(secret_name_markers=frozenset({"x" * 300}))
        with pytest.raises(ExclusionError, match="entry hard limit"):
            ExclusionScanner(secret_suffixes=frozenset({f".s{index}" for index in range(100)}))
