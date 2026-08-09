"""Import validation and atomic artifact-store registration plans."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from zana_core.artifacts.store import ArtifactStore
from zana_core.images.models import ImageRunnability
from zana_core.images.oci import ValidatedLayout, validate_oci_layout


class ImportValidationError(ValueError):
    """Raised when a layout cannot produce a registration plan."""


@dataclass(frozen=True)
class ImageRegistrationPlan:
    """Atomic registration inputs validated before any store mutation."""

    image_digest: str
    config_digest: str
    manifest_digest: str
    index_digest: str
    blob_digests: tuple[str, ...]
    runnability: ImageRunnability
    config_name: str
    config_version: str
    base_model_digest: str | None
    base_model_key: str
    total_size: int


@dataclass(frozen=True)
class RegistrationResult:
    """Outcome of registering a validated layout into an artifact store."""

    image_digest: str
    registered_blob_digests: tuple[str, ...]
    runnability: ImageRunnability


@dataclass(frozen=True)
class ImageImportResult:
    """Validation result combining the plan and a possible registration result."""

    plan: ImageRegistrationPlan
    registration: RegistrationResult | None = None


def plan_import(
    layout_root: Path,
    *,
    available_base_digests: set[str] | None = None,
) -> ImageRegistrationPlan:
    """Validate an extracted OCI layout and produce a registration plan."""

    layout = validate_oci_layout(layout_root)
    return _plan_from_layout(layout, available_base_digests)


def register_into_store(
    store: ArtifactStore,
    layout_root: Path,
    *,
    available_base_digests: set[str] | None = None,
) -> ImageImportResult:
    """Register a validated layout's immutable blobs into ``store``.

    All blobs are copied before any metadata is produced; registration returns
    the deterministic result without touching the database. Missing or weak
    base identity is preserved as ``not-runnable`` state.
    """

    layout = validate_oci_layout(layout_root)
    plan = _plan_from_layout(layout, available_base_digests)
    registered: list[str] = []
    for digest in layout.blob_digests:
        source = layout.root / "blobs" / "sha256" / digest.removeprefix("sha256:")
        store.put_file(source)
        registered.append(digest)
    config_digest = layout.config_digest
    config_source = layout.root / "blobs" / "sha256" / config_digest.removeprefix("sha256:")
    store.put_file(config_source)
    registered.append(config_digest)
    result = RegistrationResult(
        image_digest=plan.image_digest,
        registered_blob_digests=tuple(registered),
        runnability=plan.runnability,
    )
    return ImageImportResult(plan=plan, registration=result)


def _plan_from_layout(
    layout: ValidatedLayout,
    available_base_digests: set[str] | None,
) -> ImageRegistrationPlan:
    runnability = layout.config.runnability(available_base_digests)
    base_model_key = layout.config.base_model.display_name or "unknown"
    return ImageRegistrationPlan(
        image_digest=layout.index_digest,
        config_digest=layout.config_digest,
        manifest_digest=layout.manifest_digest,
        index_digest=layout.index_digest,
        blob_digests=layout.blob_digests,
        runnability=runnability,
        config_name=layout.config.name,
        config_version=layout.config.version,
        base_model_digest=layout.config.base_model.identity_digest,
        base_model_key=base_model_key,
        total_size=layout.total_size,
    )
