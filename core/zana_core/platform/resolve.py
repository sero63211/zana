"""Path resolution, override validation, and safe child derivation."""

from __future__ import annotations

import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Protocol

from zana_core.platform.models import (
    PathPolicy,
    PathRoot,
    PlatformPaths,
    PlatformPathValidationError,
)

_NUL = "\x00"


class PathLocator(Protocol):
    """Injection-friendly locator for platform base directories."""

    def base_dir(self, kind: PathRoot) -> Path: ...


class PlatformdirsLocator:
    """Default locator backed only by platformdirs."""

    def __init__(self, app_name: str = "zana") -> None:
        if not app_name or app_name != app_name.strip() or _NUL in app_name:
            raise PlatformPathValidationError("app_name must be non-empty and NUL-free")
        self._app_name = app_name

    def base_dir(self, kind: PathRoot) -> Path:
        import platformdirs

        if kind == PathRoot.CONFIG:
            return Path(platformdirs.user_config_dir(self._app_name))
        if kind == PathRoot.DATA:
            return Path(platformdirs.user_data_dir(self._app_name))
        if kind == PathRoot.CACHE:
            return Path(platformdirs.user_cache_dir(self._app_name))
        if kind == PathRoot.LOG:
            return Path(platformdirs.user_log_dir(self._app_name))
        if kind == PathRoot.TEMP:
            return Path(tempfile.gettempdir()) / self._app_name
        if kind == PathRoot.WORKSPACE:
            return Path(platformdirs.user_data_dir(self._app_name, version="workspaces"))
        raise PlatformPathValidationError(f"unsupported root kind {kind!r}")


class FixedPathLocator:
    """Deterministic locator for tests; never touches the real machine."""

    def __init__(self, mapping: Mapping[PathRoot, Path]) -> None:
        self._mapping = dict(mapping)

    def base_dir(self, kind: PathRoot) -> Path:
        try:
            return self._mapping[kind]
        except KeyError:
            raise PlatformPathValidationError(f"no fixed path for {kind.value}") from None


def _normalize(path: Path) -> Path:
    return Path(os.path.normpath(str(path)))


def _components(path: Path) -> tuple[str, ...]:
    normalized = _normalize(path)
    if not normalized.is_absolute():
        raise PlatformPathValidationError(
            f"path {str(path)!r} must be absolute", code="PATH_RELATIVE"
        )
    parts = normalized.parts
    return parts[1:] if parts and parts[0] in ("/", "\\") else parts


def _base_checks(
    kind: PathRoot,
    text: str,
    policy: PathPolicy,
) -> Path:
    if _NUL in text:
        raise PlatformPathValidationError(
            f"override for {kind.value} contains a NUL byte", code="PATH_NUL"
        )
    path = Path(text)
    if not path.is_absolute():
        raise PlatformPathValidationError(
            f"override for {kind.value} must be absolute", code="PATH_RELATIVE"
        )
    raw_components = [part for part in text.replace("\\", "/").split("/") if part]
    for component in raw_components:
        if component in (".", ".."):
            raise PlatformPathValidationError(
                f"override for {kind.value} contains traversal component {component!r}",
                code="PATH_TRAVERSAL",
            )
    normalized = _normalize(path)
    if not normalized.is_absolute():
        raise PlatformPathValidationError(
            f"override for {kind.value} is not absolute after normalization",
            code="PATH_RELATIVE",
        )
    components = _components(normalized)
    if len(components) > policy.max_components_per_root:
        raise PlatformPathValidationError(
            f"override for {kind.value} exceeds component budget {policy.max_components_per_root}",
            code="PATH_TOO_DEEP",
        )
    if len(text) > policy.max_path_length:
        raise PlatformPathValidationError(
            f"override for {kind.value} exceeds path length budget",
            code="PATH_TOO_LONG",
        )
    return normalized


def validate_override(
    kind: PathRoot,
    raw: str | Path,
    policy: PathPolicy,
) -> Path:
    """Validate one explicit override: absolute, normalized, NUL-free, safe."""
    text = str(raw)
    normalized = _base_checks(kind, text, policy)
    if policy.forbid_filesystem_root and normalized == Path(os.path.sep):
        raise PlatformPathValidationError(
            f"override for {kind.value} is the filesystem root",
            code="PATH_UNSAFE_ROOT",
        )
    if policy.forbid_home_root and normalized == Path.home():
        raise PlatformPathValidationError(
            f"override for {kind.value} is the home root",
            code="PATH_UNSAFE_ROOT",
        )
    if policy.forbid_cwd_root and normalized == Path.cwd():
        raise PlatformPathValidationError(
            f"override for {kind.value} is the working directory root",
            code="PATH_UNSAFE_ROOT",
        )
    if policy.confinement_roots:
        confined = any(is_within(Path(confine), normalized) for confine in policy.confinement_roots)
        if not confined:
            raise PlatformPathValidationError(
                f"override for {kind.value} is outside the allowed confinement roots",
                code="PATH_UNCONFINED",
            )
    return normalized


def validate_overrides(
    overrides: Mapping[PathRoot, str | Path],
    policy: PathPolicy,
) -> dict[PathRoot, Path]:
    """Validate all overrides and reject alias/parent/child collisions."""
    validated = {kind: validate_override(kind, raw, policy) for kind, raw in overrides.items()}
    if policy.require_disjoint_roots:
        ordered = sorted(validated.items(), key=lambda item: str(item[1]))
        for index, (kind, path) in enumerate(ordered):
            for other_kind, other in ordered[index + 1 :]:
                if path == other:
                    raise PlatformPathValidationError(
                        f"overrides for {kind.value} and {other_kind.value} collide "
                        "on the same absolute path",
                        code="PATH_ALIAS_COLLISION",
                    )
                if is_within(path, other) or is_within(other, path):
                    raise PlatformPathValidationError(
                        f"overrides for {kind.value} and {other_kind.value} form a "
                        "parent/child configuration that could make cleanup broad",
                        code="PATH_PARENT_CHILD",
                    )
    return validated


def is_within(root: Path, candidate: Path) -> bool:
    """True when ``candidate`` equals or is inside ``root`` (pure lexical)."""
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


class PathResolver:
    """Resolves canonical platform roots; resolution never mutates the FS."""

    def __init__(
        self,
        locator: PathLocator | None = None,
        policy: PathPolicy | None = None,
        overrides: Mapping[PathRoot, str | Path] | None = None,
        *,
        app_name: str = "zana",
    ) -> None:
        self._locator = locator or PlatformdirsLocator(app_name)
        self._policy = policy or PathPolicy()
        raw_overrides = {PathRoot(kind): raw for kind, raw in (overrides or {}).items()}
        self._overrides = validate_overrides(raw_overrides, self._policy)

    def resolve(self) -> PlatformPaths:
        values: dict[str, Path] = {}
        for kind in PathRoot:
            base = self._locator.base_dir(kind)
            if not base.is_absolute():
                raise PlatformPathValidationError(
                    f"locator returned a relative {kind.value} root {str(base)!r}",
                    code="PATH_RELATIVE",
                )
            values[f"{kind.value}_root"] = _normalize(self._overrides.get(kind, base))
        paths = PlatformPaths(**values)
        for kind in PathRoot:
            _base_checks(kind, str(paths.root(kind)), self._policy)
        return paths


def derive_child(
    root: Path,
    *components: str,
    policy: PathPolicy | None = None,
    resolve_symlinks: bool = True,
) -> Path:
    """Derive a safe child path under ``root`` with component validation.

    No directory is created. When ``resolve_symlinks`` is true and the root
    exists, symlink escapes are rejected by resolving the candidate and
    comparing against the resolved root.
    """
    policy = policy or PathPolicy()
    root = Path(root)
    if not root.is_absolute():
        raise PlatformPathValidationError("root must be absolute", code="PATH_RELATIVE")
    if not components:
        raise PlatformPathValidationError(
            "at least one child component is required", code="PATH_EMPTY"
        )
    if len(components) > policy.max_child_depth:
        raise PlatformPathValidationError(
            f"child path exceeds depth budget {policy.max_child_depth}",
            code="PATH_TOO_DEEP",
        )
    for component in components:
        if _NUL in component:
            raise PlatformPathValidationError(
                "child component contains a NUL byte", code="PATH_NUL"
            )
        if not component or component in (".", ".."):
            raise PlatformPathValidationError(
                f"child component {component!r} is not allowed",
                code="PATH_TRAVERSAL",
            )
        if "/" in component or "\\" in component:
            raise PlatformPathValidationError(
                f"child component {component!r} must not contain separators",
                code="PATH_SEPARATOR",
            )
        if len(component) > policy.max_component_length:
            raise PlatformPathValidationError(
                f"child component exceeds length budget {policy.max_component_length}",
                code="PATH_TOO_LONG",
            )
    candidate = _normalize(Path(root, *components))
    if not is_within(root, candidate):
        raise PlatformPathValidationError("derived child path escapes its root", code="PATH_ESCAPE")
    if resolve_symlinks and root.exists():
        resolved_root = root.resolve(strict=False)
        resolved_candidate = candidate.resolve(strict=False)
        if not is_within(resolved_root, resolved_candidate):
            raise PlatformPathValidationError(
                "derived child path escapes its root via a symlink",
                code="PATH_SYMLINK_ESCAPE",
            )
    return candidate
