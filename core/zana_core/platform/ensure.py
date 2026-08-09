"""Explicit idempotent root creation with validate-first semantics."""

from __future__ import annotations

from pathlib import Path

from zana_core.platform.models import (
    PathPolicy,
    PathRoot,
    PlatformPathError,
    PlatformPaths,
    PlatformPathValidationError,
)
from zana_core.platform.resolve import validate_root_set


def ensure_roots(
    paths: PlatformPaths,
    kinds: tuple[PathRoot, ...] | None = None,
    *,
    policy: PathPolicy | None = None,
) -> tuple[Path, ...]:
    """Validate first, then create only the exact approved roots idempotently.

    The full root set is validated before any filesystem mutation, so an
    invalid, unsafe, symlinked, or non-directory set produces zero partial
    creation. ``mkdir(parents=True)`` is intentional: it creates the exact
    platformdirs-style parent chains for approved roots; it is not a
    recursive scan of existing content and never visits subdirectories.
    """
    resolved_policy = policy or PathPolicy()
    validate_root_set(paths, resolved_policy)
    selected = kinds if kinds is not None else tuple(PathRoot)
    for kind in selected:
        root = paths.root(kind)
        if root.is_symlink():
            raise PlatformPathValidationError(
                f"{kind.value} root {str(root)!r} is a symlink; refusing creation",
                code="PATH_SYMLINK_ROOT",
            )
        if root.exists() and not root.is_dir():
            raise PlatformPathValidationError(
                f"{kind.value} root {str(root)!r} exists and is not a directory",
                code="PATH_TYPE",
            )
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
