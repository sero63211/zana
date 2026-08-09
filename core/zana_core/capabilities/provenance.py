"""Immutable source provenance capture with declared metadata and no rights inference."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from pathlib import Path
from types import MappingProxyType
from typing import Any, Literal


class SourceRole(str, Enum):
    """Role of a source file inside an editable capability package."""

    MANIFEST = "manifest"
    BEHAVIOR = "behavior"
    KNOWLEDGE = "knowledge"
    TRAINING = "training"
    VALIDATION = "validation"
    EVALUATION = "evaluation"
    TOOLS = "tools"
    PERMISSIONS = "permissions"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class SourceProvenance:
    """Immutable provenance record for one capability source file."""

    relative_path: str
    sha256: str
    size_bytes: int
    role: SourceRole
    title: str
    title_origin: Literal["manifest", "file_stem"]
    declared_license: str | None
    usage_metadata: Mapping[str, Any]
    ingested_at: datetime
    rights_inferred: bool = False


def sha256_of(path: Path) -> tuple[str, int]:
    """Return (sha256 hex digest, size in bytes) for a file, read in chunks."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
            size += len(chunk)
    return digest.hexdigest(), size


def make_provenance(
    *,
    relative_path: str,
    sha256: str,
    size_bytes: int,
    role: SourceRole,
    title: str,
    title_origin: Literal["manifest", "file_stem"],
    declared_license: str | None,
    usage_metadata: dict[str, Any],
    ingested_at: datetime,
) -> SourceProvenance:
    """Build an immutable provenance record with a frozen metadata snapshot."""
    frozen_usage = MappingProxyType(dict(sorted(usage_metadata.items())))
    return SourceProvenance(
        relative_path=relative_path,
        sha256=sha256,
        size_bytes=size_bytes,
        role=role,
        title=title,
        title_origin=title_origin,
        declared_license=declared_license,
        usage_metadata=frozen_usage,
        ingested_at=ingested_at,
        rights_inferred=False,
    )
