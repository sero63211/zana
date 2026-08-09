"""Import planning, atomic registration, and not-runnable behavior tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.artifacts import ArtifactStore
from zana_core.images.import_plan import register_into_store
from zana_core.images.models import (
    BaseModelReference,
    RunnableState,
    ZanaImageConfig,
)
from zana_core.images.oci import OciValidationError, assemble_oci_layout

BASE_DIGEST = "sha256:" + "c" * 64
OTHER_BASE_DIGEST = "sha256:" + "d" * 64


def _make_layout(tmp_path: Path, base_digest: str | None = BASE_DIGEST) -> Path:
    config = ZanaImageConfig(
        name="tutor",
        version="1.0.0",
        base_model=BaseModelReference(
            display_name="example-model",
            identity_digest=base_digest,
        ),
    )
    behavior = tmp_path / "behavior.json"
    behavior.write_bytes(b'{"policy":"helpful"}')
    root = tmp_path / "layout"
    root.mkdir()
    assemble_oci_layout(config, {"behavior": behavior}, root)
    return root


class TestImportPlanning:
    def test_plan_records_real_digests_and_sizes(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        root = _make_layout(tmp_path)
        plan = plan_import(root)
        assert plan.image_digest.startswith("sha256:")
        assert plan.config_name == "tutor"
        assert plan.config_version == "1.0.0"
        assert plan.base_model_digest == BASE_DIGEST
        assert len(plan.blob_digests) == 1
        assert plan.total_size > 0

    def test_missing_base_plan_is_not_runnable(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        plan = plan_import(_make_layout(tmp_path), available_base_digests=set())
        assert plan.runnability.state == RunnableState.NOT_RUNNABLE_MISSING_BASE
        assert plan.runnability.exact_base_digest == BASE_DIGEST

    def test_weak_identity_plan_is_not_runnable(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        plan = plan_import(_make_layout(tmp_path, base_digest=None))
        assert plan.runnability.state == RunnableState.NOT_RUNNABLE_WEAK_IDENTITY

    def test_exact_base_available_plan_is_runnable(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        plan = plan_import(
            _make_layout(tmp_path),
            available_base_digests={BASE_DIGEST},
        )
        assert plan.runnability.state == RunnableState.RUNNABLE

    def test_different_exact_digest_is_not_substituted(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        plan = plan_import(
            _make_layout(tmp_path),
            available_base_digests={OTHER_BASE_DIGEST},
        )
        assert plan.runnability.state == RunnableState.NOT_RUNNABLE_MISSING_BASE
        assert plan.runnability.exact_base_digest == BASE_DIGEST

    def test_corrupted_layout_never_produces_plan(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        root = _make_layout(tmp_path)
        (root / "index.json").write_text("{bad")
        with pytest.raises(OciValidationError):
            plan_import(root)


class TestAtomicRegistration:
    def test_register_into_store_registers_immutable_blobs(
        self,
        tmp_path: Path,
    ) -> None:
        root = _make_layout(tmp_path)
        store = ArtifactStore(tmp_path / "artifacts")
        result = register_into_store(store, root, available_base_digests={BASE_DIGEST})

        assert result.registration is not None
        assert result.registration.image_digest == result.plan.image_digest
        assert result.registration.runnability.state == RunnableState.RUNNABLE
        assert len(result.registration.registered_blob_digests) == 2
        for digest in result.registration.registered_blob_digests:
            assert store.verify(digest) > 0

    def test_registration_preserves_missing_base_state(self, tmp_path: Path) -> None:
        root = _make_layout(tmp_path)
        store = ArtifactStore(tmp_path / "artifacts")
        result = register_into_store(store, root, available_base_digests=set())
        assert result.registration is not None
        assert result.registration.runnability.state == RunnableState.NOT_RUNNABLE_MISSING_BASE

    def test_registration_does_not_touch_database(self, tmp_path: Path) -> None:
        root = _make_layout(tmp_path)
        store = ArtifactStore(tmp_path / "artifacts")
        result = register_into_store(store, root)
        assert result.plan.config_name == "tutor"
        assert store.root.is_dir()
        # No DB/API wiring exists: only the blob store and plan are produced.
        assert not (tmp_path / "zana.sqlite3").exists()
