"""Permission-gated executor tests: policy first, default deny, redaction."""

from __future__ import annotations

from zana_core.permissions.decisions import PermissionDecisionEngine
from zana_core.permissions.loader import load_policy
from zana_core.tools.executor import PermissionGatedToolExecutor
from zana_core.tools.models import ToolCall, ToolStatus
from zana_core.tools.registry import ToolRegistry


def _call(tool_id: str = "zana.calculator") -> ToolCall:
    return ToolCall(
        call_id="call-1",
        tool_id=tool_id,
        arguments={"expression": "2 + 2"},
        image_digest="sha256:image",
        instance_id="instance-1",
    )


class TestExecutor:
    def test_permission_check_happens_before_adapter(self) -> None:
        engine = PermissionDecisionEngine(load_policy("schemaVersion: 1\n"))
        registry = ToolRegistry()
        result, provenance = PermissionGatedToolExecutor(engine, registry).execute(_call())
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code.value == "PERMISSION_DENIED"
        assert provenance.permission_decision == "deny"
        assert provenance.error_code == "PERMISSION_DENIED"

    def test_default_deny_without_policy_allow(self) -> None:
        engine = PermissionDecisionEngine(load_policy("schemaVersion: 1\n"))
        registry = ToolRegistry()
        _, provenance = PermissionGatedToolExecutor(engine, registry).execute(_call())
        assert provenance.permission_decision == "deny"

    def test_allowed_calculator_runs(self) -> None:
        policy = load_policy("schemaVersion: 1\ntools:\n  allow: [zana.calculator]\n")
        engine = PermissionDecisionEngine(policy)
        registry = ToolRegistry()
        result, provenance = PermissionGatedToolExecutor(engine, registry).execute(_call())
        assert result.status == ToolStatus.SUCCESS
        assert provenance.permission_decision == "allow"
        assert provenance.result_digest is not None
        assert provenance.image_digest == "sha256:image"
        assert provenance.instance_id == "instance-1"

    def test_unknown_tool_fails_closed_even_when_allowed(self) -> None:
        policy = load_policy("schemaVersion: 1\ntools:\n  allow: [zana.calculator]\n")
        engine = PermissionDecisionEngine(policy)
        registry = ToolRegistry()
        result, provenance = PermissionGatedToolExecutor(engine, registry).execute(
            _call(tool_id="zana.unknown")
        )
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code.value == "UNKNOWN_TOOL"
        assert provenance.error_code == "UNKNOWN_TOOL"

    def test_provenance_is_redacted(self) -> None:
        policy = load_policy("schemaVersion: 1\ntools:\n  allow: [zana.calculator]\n")
        engine = PermissionDecisionEngine(policy)
        registry = ToolRegistry()
        _, provenance = PermissionGatedToolExecutor(engine, registry).execute(
            ToolCall(
                call_id="call-secret",
                tool_id="zana.calculator",
                arguments={"expression": "secret-calculator-input"},
            )
        )
        serialized = provenance.model_dump_json()
        assert "secret-calculator-input" not in serialized
        assert provenance.input_digest
