"""Recursive exclusion of secrets and mutable instance state."""

from __future__ import annotations

import stat
from collections.abc import Iterator
from pathlib import Path

SECRET_NAME_MARKERS = frozenset(
    {
        "secret",
        "secrets",
        "token",
        "tokens",
        "key",
        "keys",
        "credential",
        "credentials",
        "password",
        "passwords",
        "authorization",
        "auth",
        "id_rsa",
        "id_ed25519",
    }
)
SECRET_SUFFIXES = frozenset({".pem", ".key", ".crt", ".p12", ".pfx", ".kdbx", ".env", ".gpg"})
MUTABLE_STATE_DIRS = frozenset(
    {
        "conversations",
        "messages",
        "memories",
        "state",
        "states",
        "snapshots",
        "cache",
        "caches",
        "tmp",
        "temp",
        "runtime-cache",
    }
)


class ExclusionError(ValueError):
    """Raised when an exclusion scan cannot be performed safely."""


class ExclusionScanner:
    """Recursively classify export candidates as safe, secret, or mutable state."""

    def __init__(
        self,
        *,
        secret_name_markers: frozenset[str] = SECRET_NAME_MARKERS,
        secret_suffixes: frozenset[str] = SECRET_SUFFIXES,
        mutable_state_dirs: frozenset[str] = MUTABLE_STATE_DIRS,
        max_depth: int = 64,
        max_entries: int = 100_000,
    ) -> None:
        self.secret_name_markers = secret_name_markers
        self.secret_suffixes = secret_suffixes
        self.mutable_state_dirs = mutable_state_dirs
        self.max_depth = max_depth
        self.max_entries = max_entries

    def scan(self, root: Path) -> list[Path]:
        """Return regular files under ``root`` that are safe to export."""

        root = Path(root).resolve()
        if not root.is_dir():
            raise ExclusionError(f"Export root is not a directory: {root}")
        safe: list[Path] = []
        entries = 0
        for path in self._walk(root):
            entries += 1
            if entries > self.max_entries:
                raise ExclusionError("Exclusion scan exceeded the entry limit.")
            if not stat.S_ISREG(path.stat().st_mode):
                continue
            if self._is_secret(path) or self._is_mutable_state(path):
                continue
            safe.append(path)
        return safe

    def classify(self, path: Path) -> str:
        """Return ``safe``, ``secret``, or ``mutable-state`` for one path."""

        if self._is_secret(path):
            return "secret"
        if self._is_mutable_state(path):
            return "mutable-state"
        return "safe"

    def _walk(self, root: Path) -> Iterator[Path]:
        for path in root.rglob("*"):
            if path.is_symlink():
                raise ExclusionError(f"Symlink inside export root is not allowed: {path}")
            yield path

    def _is_secret(self, path: Path) -> bool:
        name = path.name.lower()
        if path.suffix.lower() in self.secret_suffixes:
            return True
        if any(marker in name for marker in self.secret_name_markers):
            return True
        return any(
            marker in part.lower() for part in path.parts for marker in self.secret_name_markers
        )

    def _is_mutable_state(self, path: Path) -> bool:
        return any(part.lower() in self.mutable_state_dirs for part in path.parts)
