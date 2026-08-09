"""Injectable command boundary for bounded, non-privileged probes."""

from __future__ import annotations

import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class CommandResult:
    """Outcome of one bounded subprocess probe."""

    returncode: int | None
    stdout: str = ""
    stderr: str = ""
    timed_out: bool = False
    error: str | None = None


class CommandRunner(Protocol):
    """Executes bounded read-only commands without privileged access."""

    def run(self, args: Sequence[str], *, timeout: float) -> CommandResult: ...


def _decode_output(value: str | bytes | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode(errors="replace")
    return value


class SubprocessCommandRunner:
    """Default bounded runner; never elevates privileges."""

    def run(self, args: Sequence[str], *, timeout: float) -> CommandResult:
        try:
            completed = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
        except FileNotFoundError:
            return CommandResult(returncode=None, error="executable_not_found")
        except subprocess.TimeoutExpired as exc:
            return CommandResult(
                returncode=None,
                stdout=_decode_output(exc.stdout),
                stderr=_decode_output(exc.stderr),
                timed_out=True,
                error="timeout",
            )
        except OSError as exc:
            return CommandResult(returncode=None, error=f"os_error:{exc}")
        return CommandResult(
            returncode=completed.returncode,
            stdout=completed.stdout,
            stderr=completed.stderr,
        )
