"""Trusted code-owned tool registry; unknown tools fail closed."""

from __future__ import annotations

from collections.abc import Sequence

from zana_core.tools.calculator import CalculatorTool
from zana_core.tools.models import (
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolResult,
    ToolStatus,
)


class ToolRegistry:
    """Registry containing only trusted code-owned adapters."""

    def __init__(self, adapters: Sequence[CalculatorTool] | None = None) -> None:
        self._adapters = list(adapters) if adapters is not None else [CalculatorTool()]

    def definitions(self) -> list[ToolDefinition]:
        return [adapter.definition() for adapter in self._adapters]

    def definition(self, tool_id: str) -> ToolDefinition | None:
        for adapter in self._adapters:
            if adapter.id == tool_id:
                return adapter.definition()
        return None

    def resolve(self, tool_id: str) -> CalculatorTool | None:
        for adapter in self._adapters:
            if adapter.id == tool_id:
                return adapter
        return None

    def execute_unchecked(self, call: ToolCall) -> ToolResult:
        adapter = self.resolve(call.tool_id)
        if adapter is None:
            return ToolResult(
                call_id=call.call_id,
                tool_id=call.tool_id,
                status=ToolStatus.ERROR,
                error=ToolError(
                    code=ToolErrorCode.UNKNOWN_TOOL,
                    message=f"unknown tool {call.tool_id!r}; tools fail closed",
                ),
            )
        return adapter.invoke(call)
