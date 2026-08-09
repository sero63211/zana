"""Minimal trusted built-in tool boundary for ZANA.

This package never executes shell, Python, filesystem, network, MCP, plugin,
or user code. It contains only code-owned adapters and a permission-gated
executor.
"""

from zana_core.tools.calculator import CalculatorTool
from zana_core.tools.executor import PermissionGatedToolExecutor
from zana_core.tools.models import (
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolExecutionProvenance,
    ToolResult,
    ToolStatus,
)
from zana_core.tools.registry import ToolRegistry

__all__ = [
    "CalculatorTool",
    "PermissionGatedToolExecutor",
    "ToolCall",
    "ToolDefinition",
    "ToolError",
    "ToolErrorCode",
    "ToolExecutionProvenance",
    "ToolRegistry",
    "ToolResult",
    "ToolStatus",
]
