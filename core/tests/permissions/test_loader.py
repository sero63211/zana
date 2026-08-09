"""Safe policy loading: defaults, explicit allows, and fail-closed errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.permissions.loader import (
    PolicyLoadError,
    UnsupportedSchemaVersionError,
    load_policy,
    load_policy_file,
)


class TestDefaultDeny:
    def test_omission_resolves_to_default_deny(self) -> None:
        policy = load_policy("schemaVersion: 1\n")
        assert policy.schema_version == 1
        assert policy.network.mode.value == "offline"
        assert policy.network.outbound is False
        assert policy.filesystem.read == []
        assert policy.filesystem.write == []
        assert policy.tools.allow == []
        assert policy.secrets.allow == []
        assert policy.experimental.mcp.endpoints == []

    def test_empty_document_defaults_schema_version(self) -> None:
        policy = load_policy("")
        assert policy.schema_version == 1
        assert policy.network.outbound is False


class TestExplicitPolicy:
    def test_dict_loading_with_explicit_allows(self) -> None:
        policy = load_policy(
            {
                "schemaVersion": 1,
                "network": {"mode": "ask", "outbound": True},
                "filesystem": {
                    "read": ["/data/read"],
                    "write": ["/data/write"],
                },
                "tools": {"allow": ["zana.calculator"]},
                "secrets": {"allow": ["api_key"]},
                "experimental": {
                    "mcp": {
                        "endpoints": [
                            {
                                "id": "local-tools",
                                "endpoint": "http://127.0.0.1:9000",
                                "scopes": ["calculator"],
                                "enabled": True,
                            }
                        ]
                    }
                },
            }
        )
        assert policy.network.mode.value == "ask"
        assert policy.network.outbound is True
        assert policy.filesystem.read == ["/data/read"]
        assert policy.tools.allow == ["zana.calculator"]
        assert policy.secrets.allow == ["api_key"]
        endpoint = policy.experimental.mcp.endpoints[0]
        assert endpoint.id == "local-tools"
        assert endpoint.enabled is True
        assert endpoint.scopes == ["calculator"]

    def test_mcp_endpoint_defaults_disabled(self) -> None:
        policy = load_policy(
            {
                "schemaVersion": 1,
                "experimental": {
                    "mcp": {"endpoints": [{"id": "mcp-a", "endpoint": "http://127.0.0.1:9000"}]}
                },
            }
        )
        assert policy.experimental.mcp.endpoints[0].enabled is False

    def test_load_policy_file(self, tmp_path: Path) -> None:
        path = tmp_path / "policy.yaml"
        path.write_text("schemaVersion: 1\nsecrets:\n  allow: [db_password]\n", encoding="utf-8")
        policy = load_policy_file(path)
        assert policy.secrets.allow == ["db_password"]


class TestFailClosed:
    def test_unknown_top_level_field_rejected(self) -> None:
        with pytest.raises(PolicyLoadError):
            load_policy("schemaVersion: 1\nallowEverything: true\n")

    def test_unknown_nested_field_rejected(self) -> None:
        with pytest.raises(PolicyLoadError):
            load_policy("schemaVersion: 1\nnetwork:\n  outbound: false\n  telemetry: true\n")

    def test_unknown_schema_version_rejected(self) -> None:
        with pytest.raises(UnsupportedSchemaVersionError):
            load_policy("schemaVersion: 2\n")

    def test_invalid_yaml_has_recovery_error(self) -> None:
        with pytest.raises(PolicyLoadError, match="not valid YAML"):
            load_policy("network: [unclosed\n")

    def test_duplicate_mapping_keys_rejected(self) -> None:
        with pytest.raises(PolicyLoadError, match="duplicate key"):
            load_policy("schemaVersion: 1\nschemaVersion: 1\n")

    def test_non_mapping_document_rejected(self) -> None:
        with pytest.raises(PolicyLoadError, match="must be a mapping"):
            load_policy("- a\n- b\n")

    def test_invalid_utf8_bytes_rejected(self) -> None:
        with pytest.raises(PolicyLoadError, match="UTF-8"):
            load_policy(b"\xff\xfe")

    def test_forbidden_shell_tool_rejected(self) -> None:
        with pytest.raises(PolicyLoadError, match="Forbidden tool"):
            load_policy("schemaVersion: 1\ntools:\n  allow: [shell]\n")

    def test_forbidden_python_and_install_hook_rejected(self) -> None:
        for tool in ("python_exec", "post_install_hook", "install_script"):
            with pytest.raises(PolicyLoadError, match="Forbidden tool"):
                load_policy(f"schemaVersion: 1\ntools:\n  allow: [{tool}]\n")

    def test_unknown_non_builtin_tool_rejected(self) -> None:
        with pytest.raises(PolicyLoadError, match="Unknown tool"):
            load_policy("schemaVersion: 1\ntools:\n  allow: [arbitrary_code]\n")
