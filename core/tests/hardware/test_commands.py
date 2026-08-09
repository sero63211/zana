"""Tests for the bounded subprocess command boundary."""

import sys

from zana_core.hardware.commands import CommandRunner, SubprocessCommandRunner


def test_echo_success() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run([sys.executable, "-c", "print('hello')"], timeout=5.0)
    assert result.error is None
    assert result.timed_out is False
    assert result.returncode == 0
    assert result.stdout.strip() == "hello"


def test_nonzero_exit_reported() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=5.0)
    assert result.error is None
    assert result.returncode == 3


def test_stderr_captured() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run(
        [sys.executable, "-c", "import sys; print('boom', file=sys.stderr); sys.exit(1)"],
        timeout=5.0,
    )
    assert result.returncode == 1
    assert result.stderr.strip() == "boom"


def test_missing_executable_is_not_fatal() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run(["zana-no-such-binary-xyz"], timeout=1.0)
    assert result.error == "executable_not_found"
    assert result.returncode is None


def test_timeout_is_bounded_and_reported() -> None:
    runner = SubprocessCommandRunner()
    result = runner.run(
        [sys.executable, "-c", "import time; time.sleep(10)"],
        timeout=0.2,
    )
    assert result.timed_out is True
    assert result.error == "timeout"
    assert result.returncode is None


def test_runner_satisfies_protocol() -> None:
    runner: CommandRunner = SubprocessCommandRunner()
    assert runner is not None
