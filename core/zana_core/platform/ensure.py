"""Explicit shallow idempotent root creation; never recursive."""

from __future__ import annotations

from pathlib import Path

from zana_core.platform.models import PathRoot, PlatformPathError, PlatformPaths


def ensure_roots(
    paths: PlatformPaths,
    kinds: tuple[PathRoot, ...] | None = None,
) -> tuple[Path, ...]:
    """Create only the exact approved roots, idempotently and shallowly."""
    selected = kinds if kinds is not None else tuple(PathRoot)
    created: list[Path] = []
    for kind in selected:
        root = paths.root(kind)
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise PlatformPathError(
                f"cannot create {kind.value} root {str(root)!r}: {exc}"
            ) from exc
        created.append(root)
    return tuple(created)
