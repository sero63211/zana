"""Drift-proof tests: portability must use the canonical images vocabulary."""

from __future__ import annotations

from importlib import util

from zana_core.images import archive as images_archive
from zana_core.images import models as images_models
from zana_core.images import oci as images_oci
from zana_core.images import secrets as images_secrets
from zana_core.portability import models as portability_models


def test_codec_kind_is_the_canonical_archive_format() -> None:
    assert portability_models.CodecKind is images_archive.ArchiveFormat


def test_runnable_state_is_the_canonical_image_state() -> None:
    assert portability_models.RunnableState is images_models.RunnableState


def test_portability_has_no_parallel_codec_or_oci_module() -> None:
    spec = util.find_spec("zana_core.portability.codec")
    assert spec is None
    spec = util.find_spec("zana_core.portability.oci")
    assert spec is None


def test_portability_imports_canonical_validation_and_exclusion() -> None:
    from zana_core.portability import export as portability_export
    from zana_core.portability import import_ as portability_import

    assert portability_export._images_archive is images_archive
    assert portability_export._images_secrets is images_secrets
    assert portability_import._images_archive is images_archive
    assert portability_import._images_import_plan is not None
    assert images_oci.validate_oci_layout is not None
