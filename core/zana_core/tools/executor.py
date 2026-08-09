"""Permission-gated tool executor using the integrated permission engine."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime

from zana_core.permissions.decisions import Decision, PermissionDecisionEngine
from zana_core.tools.models import (
    ToolCall,
    ToolError,
    ToolErrorCode,
    ToolExecutionProvenance,
    ToolResult,
    ToolStatus,
)
from zana_core.tools.registry import ToolRegistry


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class PermissionGatedToolExecutor:
    """Evaluate policy before resolving or invoking any adapter."""

    def __init__(
        self,
        policy_engine: PermissionDecisionEngine,
        registry: ToolRegistry,
    ) -> None:
        self.policy_engine = policy_engine
        self.registry = registry

    def execute(self, call: ToolCall) -> tuple[ToolResult, ToolExecutionProvenance]:
        started = datetime.now(UTC)
        unknown_tool = self.registry.definition(call.tool_id) is None
        if unknown_tool:
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=ToolError(
                    code=ToolErrorCode.UNKNOWN_TOOL,
                    message=f"unknown tool {call.tool_id!r}; tools fail closed",
                ),
            )
            return result, self._provenance(
                call,
                started,
                _digest(call.tool_id),
                result,
                permission_decision="deny",
            )
        input_digest = _digest(
            json.dumps(
                {"tool_id": call.tool_id, "arguments": call.arguments},
                sort_keys=True,
                default=str,
            )
        )
        permission = self.policy_engine.tool_allowed(call.tool_id)
        if permission != Decision.ALLOW:
            denial = self.policy_engine.tool_denial(call.tool_id)
            result = ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=ToolError(
                    code=ToolErrorCode.PERMISSION_DENIED,
                    message=denial.message,
                ),
            )
            return result, self._provenance(
                call,
                started,
                input_digest,
                result,
                permission_decision="deny",
            )
        result = self.registry.execute_unchecked(call)
        return result, self._provenance(
            call,
            started,
            input_digest,
            result,
            permission_decision="allow",
        )

    @staticmethod
    def _provenance(
        call: ToolCall,
        started: datetime,
        input_digest: str,
        result: ToolResult,
        *,
        permission_decision: str,
    ) -> ToolExecutionProvenance:
        completed = datetime.now(UTC)
        duration_ms = int(max(0.0, (completed - started).total_seconds() * 1000))
        result_digest = (
            _digest(
                json.dumps(
                    result.model_dump(),
                    sort_keys=True,
                    default=str,
                )
            )
            if result.status == ToolStatus.SUCCESS
            else None
        )
        return ToolExecutionProvenance(
            call_id=call.call_id,
            tool_id=call.tool_id,
            tool_version="1.0.0",
            permission_decision=permission_decision,
            input_digest=input_digest,
            status=result.status,
            result_digest=result_digest,
            error_code=result.error.code.value if result.error is not None else None,
            started_at=started,
            completed_at=completed,
            duration_ms=duration_ms,
            image_digest=call.image_digest,
            instance_id=call.instance_id,
        )
