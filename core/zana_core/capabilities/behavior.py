"""Behavior file loading and hashing without executing content."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zana_core.capabilities.errors import CapabilityIssue, CapabilitySourceValidationError
from zana_core.capabilities.provenance import sha256_of


@dataclass(frozen=True, slots=True)
class BehaviorSource:
    """Hashed behavior file metadata; the content itself is never executed."""

    relative_path: str
    sha256: str
    size_bytes: int
    line_count: int


def load_behavior(root: Path, path: Path) -> BehaviorSource:
    """Read and hash ``path`` as UTF-8 text, rejecting anything unreadable."""
    label = path.resolve().relative_to(root.resolve()).as_posix()
    try:
        digest, size = sha256_of(path)
        text = path.read_bytes().decode("utf-8")
    except UnicodeDecodeError:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("BEHAVIOR_UTF8", "behavior file is not valid UTF-8", label)]
        ) from None
    except OSError as exc:
        raise CapabilitySourceValidationError(
            [CapabilityIssue("BEHAVIOR_READ", f"cannot read behavior file: {exc}", label)]
        ) from exc
    return BehaviorSource(
        relative_path=label,
        sha256=digest,
        size_bytes=size,
        line_count=len(text.splitlines()),
    )
