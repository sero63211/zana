"""Canonical cross-platform path and storage boundary for ZANA Core.

This package resolves platform roots, probes exact roots, and derives safe
child paths without mutating the filesystem during resolution, without
recursive scans, and without background work.
"""

from zana_core.platform.ensure import ensure_roots
from zana_core.platform.models import (
    FilesystemCapability,
    PathPolicy,
    PathRoot,
    PlatformPathError,
    PlatformPaths,
    PlatformPathValidationError,
)
from zana_core.platform.probe import (
    DefaultFilesystemProbe,
    FilesystemProbe,
    probe_roots,
)
from zana_core.platform.resolve import (
    FixedPathLocator,
    PathLocator,
    PathResolver,
    PlatformdirsLocator,
    derive_child,
    is_within,
    validate_override,
    validate_overrides,
    validate_root_set,
)

__all__ = [
    "DefaultFilesystemProbe",
    "FilesystemCapability",
    "FilesystemProbe",
    "FixedPathLocator",
    "PathLocator",
    "PathPolicy",
    "PathResolver",
    "PathRoot",
    "PlatformPaths",
    "PlatformPathError",
    "PlatformPathValidationError",
    "PlatformdirsLocator",
    "derive_child",
    "ensure_roots",
    "is_within",
    "probe_roots",
    "validate_override",
    "validate_overrides",
    "validate_root_set",
]
