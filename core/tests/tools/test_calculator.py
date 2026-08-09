"""Calculator tool grammar, precedence, limits, and deterministic output tests."""

from __future__ import annotations

import pytest

from zana_core.tools.calculator import CalculatorTool
from zana_core.tools.models import ToolCall, ToolErrorCode, ToolStatus


def _result(expression: str):
    return CalculatorTool().invoke(
        ToolCall(call_id="c1", tool_id="zana.calculator", arguments={"expression": expression})
    )


class TestCalculator:
    def test_basic_addition(self) -> None:
        result = _result("1 + 2")
        assert result.status == ToolStatus.SUCCESS
        assert result.output["value"] == 3
        assert result.output["rendered"] == "3"

    def test_precedence(self) -> None:
        result = _result("2 + 3 * 4")
        assert result.output["value"] == 14

    def test_parentheses(self) -> None:
        result = _result("(2 + 3) * 4")
        assert result.output["value"] == 20

    def test_unary_signs(self) -> None:
        result = _result("-5 + +3")
        assert result.output["value"] == -2

    def test_decimal_rendering(self) -> None:
        result = _result("0.1 + 0.2")
        assert result.output["rendered"] == "0.3"

    def test_division_by_zero(self) -> None:
        result = _result("1 / 0")
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert "division by zero" in result.error.message

    def test_non_finite_result_rejected(self) -> None:
        result = _result("1e309")
        assert result.status == ToolStatus.ERROR
        assert "non-finite" in (result.error.message if result.error else "")

    def test_magnitude_limit(self) -> None:
        result = _result("999999999999999999999999999999999999999")
        assert result.status == ToolStatus.ERROR
        assert result.error is not None

    def test_too_many_nodes(self) -> None:
        result = _result("+".join(["1"] * 50))
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == ToolErrorCode.LIMIT_EXCEEDED

    def test_too_deep(self) -> None:
        result = _result("+".join(["(1)"] * 12))
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == ToolErrorCode.LIMIT_EXCEEDED

    def test_expression_length_limit(self) -> None:
        result = _result("1" * 500)
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == ToolErrorCode.LIMIT_EXCEEDED

    def test_malformed_input(self) -> None:
        result = _result(123)  # type: ignore[arg-type]
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == ToolErrorCode.MALFORMED_INPUT

    def test_deterministic_output_and_digest(self) -> None:
        first = _result("12 + 7 * 3")
        second = _result("12 + 7 * 3")
        assert first.output["expression_digest"] == second.output["expression_digest"]
        assert first.output["value"] == second.output["value"]


class TestProhibitedSyntax:
    @pytest.mark.parametrize(
        "expression",
        [
            "__import__('os')",
            "1; import os",
            "os.system('echo hi')",
            "sys.exit()",
            "[1, 2]",
            "{'a': 1}",
            "'string'",
            "lambda x: x",
            "1 ** 2",
            "1 << 2",
            "x",
            "a[0]",
            "range(10)",
            "1 if True else 2",
            "True",
        ],
    )
    def test_prohibited_syntax_rejected(self, expression: str) -> None:
        result = _result(expression)
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code in (ToolErrorCode.CALCULATION_ERROR, ToolErrorCode.LIMIT_EXCEEDED)
