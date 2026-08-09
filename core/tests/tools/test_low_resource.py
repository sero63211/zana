"""Low-resource limits and deadline failure behavior tests."""

from __future__ import annotations

from zana_core.tools.calculator import MAX_DEADLINE_SECONDS, CalculatorTool
from zana_core.tools.models import ToolCall, ToolErrorCode, ToolStatus


class FakeClock:
    """Monotonic fake clock for deterministic deadline checks."""

    def __init__(self, now: float = 0.0) -> None:
        self._now = now

    def now(self) -> float:
        return self._now


class FakeMonotonicClock:
    """Fake clock that reports a very large monotonic timestamp."""

    def __init__(self) -> None:
        self._calls = 0

    def now(self) -> float:
        self._calls += 1
        return self._calls * 10**12


def _call(expression: str) -> ToolCall:
    return ToolCall(
        call_id="low-resource",
        tool_id="zana.calculator",
        arguments={"expression": expression},
    )


class TestLowResource:
    def test_deadline_exceeded_returns_error(self) -> None:
        clock = FakeMonotonicClock()
        result = CalculatorTool(clock=clock).invoke(_call("1 + 1"))
        assert result.status == ToolStatus.ERROR
        assert result.error is not None
        assert result.error.code == ToolErrorCode.DEADLINE_EXCEEDED
        assert "deadline" in result.error.message

    def test_deadline_boundary_allows_fast_work(self) -> None:
        clock = FakeClock(now=0.0)
        result = CalculatorTool(clock=clock).invoke(_call("1 + 1"))
        assert result.status == ToolStatus.SUCCESS
        assert result.output["value"] == 2

    def test_deadline_constant_is_bounded(self) -> None:
        assert 0 < MAX_DEADLINE_SECONDS <= 1.0

    def test_expression_length_and_node_limits(self) -> None:
        assert CalculatorTool().invoke(_call("1" * 500)).status == ToolStatus.ERROR
        assert CalculatorTool().invoke(_call("+".join(["1"] * 50))).status == ToolStatus.ERROR
