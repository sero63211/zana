"""Typed validation issues for editable Capability Source packages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class CapabilityIssue:
    """One precise, recoverable validation finding."""

    code: str
    message: str
    file: str | None = None
    line: int | None = None

    def render(self) -> str:
        location = self.file or "capability source"
        if self.line is not None:
            location = f"{location}:{self.line}"
        return f"{location}: {self.code}: {self.message}"


class CapabilitySourceValidationError(Exception):
    """Raised when a Capability Source fails validation.

    Carries every collected issue so callers can recover file- and
    line-level details in one pass.
    """

    def __init__(self, issues: list[CapabilityIssue] | tuple[CapabilityIssue, ...]) -> None:
        self.issues = tuple(issues)
        super().__init__("\n".join(issue.render() for issue in self.issues))


def relative_label(root: Path, path: Path) -> str:
    """POSIX-style package-relative label used in diagnostics and provenance."""
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError:
        return path.as_posix()
