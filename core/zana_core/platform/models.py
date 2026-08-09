"""Frozen strict models for the platform path boundary."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class PlatformPathError(Exception):
    """Base error for platform path operations."""


class PlatformPathValidationError(PlatformPathError, ValueError):
    """Raised when a root override or child derivation is unsafe."""

    def __init__(self, message: str, *, code: str = "PATH_INVALID") -> None:
        self.code = code
        super().__init__(message)


class PathRoot(str, Enum):
    """Canonical root kinds managed by the platform boundary."""

    CONFIG = "config"
    DATA = "data"
    CACHE = "cache"
    LOG = "log"
    TEMP = "temp"
    WORKSPACE = "workspace"


class PathPolicy(BaseModel):
    """Strict bounds for root validation and child derivation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    max_components_per_root: int = Field(default=64, ge=1, le=1024)
    max_path_length: int = Field(default=4096, ge=1, le=1 << 20)
    max_child_depth: int = Field(default=16, ge=1, le=256)
    max_component_length: int = Field(default=255, ge=1, le=4096)
    forbid_home_root: bool = True
    forbid_cwd_root: bool = True
    forbid_filesystem_root: bool = True
    require_disjoint_roots: bool = True
    confinement_roots: tuple[Path, ...] = ()


class PlatformPaths(BaseModel):
    """Canonical resolved roots; never mutated during simple resolution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    config_root: Path
    data_root: Path
    cache_root: Path
    log_root: Path
    temp_root: Path
    workspace_root: Path

    def root(self, kind: PathRoot) -> Path:
        return getattr(self, f"{kind.value}_root")

    def all_roots(self) -> tuple[Path, ...]:
        return (
            self.config_root,
            self.data_root,
            self.cache_root,
            self.log_root,
            self.temp_root,
            self.workspace_root,
        )


class FilesystemCapability(BaseModel):
    """Bounded probe result for one exact root; unknown values stay honest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: Path
    available: bool
    writable: bool | None = None
    free_bytes: int | None = Field(default=None, ge=0)
    error: str | None = Field(default=None, max_length=2000)
