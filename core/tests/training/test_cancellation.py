"""Cancellation and partial-artifact state machine tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.training.cancellation import (
    InvalidCancellationTransitionError,
    mark_cancelled,
    promote_adapter,
    transition_run,
)
from zana_core.training.contracts import AdapterState, CancellationState, InvocationSpec, RunRecord


def _spec() -> InvocationSpec:
    return InvocationSpec(
        provider="mlx_lm",
        executable="mlx_lm.lora",
        args=("--seed", "7", "--train", "--iters", "3"),
        env={},
        provider_version="0.5.0",
        package_version="0.5.0",
        seed=7,
        dataset_digest="sha256:train",
        config_digest="sha256:config",
        output_path=Path("/data/out"),
    )


def _run() -> RunRecord:
    return RunRecord(
        run_id="run-1",
        provider="mlx_lm",
        invocation=_spec(),
        state=CancellationState.RUNNING,
        partial_outputs=(Path("/data/tmp/partial.bin"),),
    )


class TestCancellation:
    def test_cancel_retains_logs_and_partial_outputs_unusable(self) -> None:
        run = mark_cancelled(_run(), log_path=Path("/data/logs/run-1.log"))
        assert run.state == CancellationState.CANCELLED
        assert run.log_path == Path("/data/logs/run-1.log")
        assert run.adapter is None
        assert run.partial_outputs == (Path("/data/tmp/partial.bin"),)

    def test_terminal_states_fail_closed(self) -> None:
        run = mark_cancelled(_run())
        with pytest.raises(InvalidCancellationTransitionError):
            transition_run(run, CancellationState.RUNNING)

    def test_partial_adapter_never_promoted(self) -> None:
        completed = transition_run(_run(), CancellationState.COMPLETED)
        with pytest.raises(InvalidCancellationTransitionError):
            promote_adapter(completed, AdapterState.PARTIAL)

    def test_only_completed_run_can_promote(self) -> None:
        with pytest.raises(InvalidCancellationTransitionError):
            promote_adapter(_run(), AdapterState.COMPLETE)
