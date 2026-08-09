"""Real safe calculator tool with a deliberately small parsed grammar."""

from __future__ import annotations

import ast
import math
import operator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from zana_core.tools.models import (
    ToolCall,
    ToolDefinition,
    ToolError,
    ToolErrorCode,
    ToolResult,
    ToolStatus,
)

ALLOWED_BINARY_OPERATORS: dict[type[ast.operator], object] = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
}

MAX_EXPRESSION_LENGTH = 200
MAX_DEPTH = 8
MAX_NODES = 32
MAX_INTEGER_DIGITS = 18
MAX_RESULT_MAGNITUDE = 1e12
ZERO = 1e-12
MAX_DEADLINE_SECONDS = 1.0


class MonotonicClock(Protocol):
    def now(self) -> float: ...


class DefaultClock:
    def now(self) -> float:
        return datetime.now(UTC).timestamp()


@dataclass(frozen=True, slots=True)
class CalculatorOutput:
    value: float
    rendered: str
    expression_digest: str


def _render_number(value: float) -> str:
    if math.isinf(value) or math.isnan(value):
        raise ValueError("non-finite result")
    if abs(value) >= 1e12:
        raise ValueError("result magnitude too large")
    rendered = f"{value:.12f}".rstrip("0").rstrip(".")
    if rendered in ("-0", ""):
        rendered = "0"
    return rendered


def _digest(payload: str) -> str:
    import hashlib

    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_limits(
    tree: ast.AST,
    *,
    max_depth: int,
    max_nodes: int,
) -> None:
    if _node_count(tree) > max_nodes:
        raise ValueError("expression has too many nodes")
    if _depth(tree) > max_depth:
        raise ValueError("expression is too deeply nested")


def _node_count(node: ast.AST) -> int:
    return 1 + sum(_node_count(child) for child in ast.iter_child_nodes(node))


def _depth(node: ast.AST) -> int:
    children = list(ast.iter_child_nodes(node))
    if not children:
        return 1
    return 1 + max(_depth(child) for child in children)


def _check_numeric(node: ast.AST) -> None:
    if isinstance(node, ast.Constant) and isinstance(node.value, int):
        digits = len(str(abs(node.value)))
        if digits > MAX_INTEGER_DIGITS:
            raise ValueError("integer literal too large")


def _parse_expression(expression: str) -> ast.AST:
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise ValueError(f"invalid expression: {error.msg}") from error
    _ensure_limits(tree, max_depth=MAX_DEPTH, max_nodes=MAX_NODES)
    _walk_validate(tree)
    return tree


def _walk_validate(node: ast.AST) -> None:
    _check_numeric(node)
    if isinstance(node, ast.Expression):
        _walk_validate(node.body)
        return
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError("only numeric literals are allowed")
        if isinstance(node.value, float) and not math.isfinite(node.value):
            raise ValueError("non-finite literal")
        return
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.UAdd | ast.USub):
        _walk_validate(node.operand)
        return
    if isinstance(node, ast.BinOp) and type(node.op) in ALLOWED_BINARY_OPERATORS:
        _walk_validate(node.left)
        _walk_validate(node.right)
        return
    raise ValueError(f"disallowed syntax: {type(node).__name__}")


def _evaluate(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _evaluate(node.body)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, int | float):
            raise ValueError("only numeric literals are allowed")
        return float(node.value)
    if isinstance(node, ast.UnaryOp):
        value = _evaluate(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value
    if isinstance(node, ast.BinOp):
        left = _evaluate(node.left)
        right = _evaluate(node.right)
        operation = ALLOWED_BINARY_OPERATORS[type(node.op)]
        if operation is operator.add:
            result = left + right
        elif operation is operator.sub:
            result = left - right
        elif operation is operator.mul:
            result = left * right
        else:
            if abs(right) < ZERO:
                raise ValueError("division by zero")
            result = left / right
        return float(result)
    raise ValueError(f"disallowed syntax: {type(node).__name__}")


class CalculatorTool:
    """Trusted V1 calculator tool."""

    id = "zana.calculator"
    version = "1.0.0"

    def __init__(self, clock: MonotonicClock | None = None) -> None:
        self.clock = clock or DefaultClock()

    def definition(self) -> ToolDefinition:
        return ToolDefinition(
            id=self.id,
            version=self.version,
            description="Evaluate a bounded arithmetic expression.",
            input_schema={
                "type": "object",
                "properties": {"expression": {"type": "string"}},
                "required": ["expression"],
            },
        )

    def invoke(self, call: ToolCall) -> ToolResult:
        started = self.clock.now()
        elapsed = self.clock.now() - started
        if elapsed >= MAX_DEADLINE_SECONDS:
            return self._error_result(
                call,
                ToolErrorCode.DEADLINE_EXCEEDED,
                "calculator deadline was already exceeded",
                started,
            )
        expression = call.arguments.get("expression")
        if not isinstance(expression, str):
            return self._error_result(
                call,
                ToolErrorCode.MALFORMED_INPUT,
                "calculator requires a string expression",
                started,
            )
        if len(expression) > MAX_EXPRESSION_LENGTH:
            return self._error_result(
                call,
                ToolErrorCode.LIMIT_EXCEEDED,
                "expression exceeds the maximum length",
                started,
            )
        try:
            if self.clock.now() - started >= MAX_DEADLINE_SECONDS:
                raise ValueError("expression deadline exceeded")
            tree = _parse_expression(expression)
            if self.clock.now() - started >= MAX_DEADLINE_SECONDS:
                raise ValueError("expression deadline exceeded")
            value = _evaluate(tree)
            rendered = _render_number(value)
            expression_digest = _digest(expression)
        except ValueError as error:
            code = (
                ToolErrorCode.LIMIT_EXCEEDED
                if any(
                    marker in str(error)
                    for marker in (
                        "too many",
                        "too deeply",
                        "too large",
                        "magnitude",
                        "deadline",
                    )
                )
                else ToolErrorCode.CALCULATION_ERROR
            )
            return self._error_result(call, code, str(error), started)
        completed = self.clock.now()
        duration_ms = int(max(0.0, (completed - started) * 1000))
        return ToolResult(
            call_id=call.call_id,
            tool_id=self.id,
            status=ToolStatus.SUCCESS,
            output={
                "value": value,
                "rendered": rendered,
                "expression_digest": expression_digest,
                "duration_ms": duration_ms,
            },
        )

    @staticmethod
    def _error_result(
        call: ToolCall,
        code: ToolErrorCode,
        message: str,
        started: float,
    ) -> ToolResult:
        return ToolResult(
            call_id=call.call_id,
            tool_id=call.tool_id,
            status=ToolStatus.ERROR,
            error=ToolError(code=code, message=message),
            output={"duration_ms": int(max(0.0, (datetime.now(UTC).timestamp() - started) * 1000))},
        )
