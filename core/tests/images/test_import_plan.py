"""Import planning, atomic registration, and not-runnable behavior tests."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from zana_core.artifacts import ArtifactStore
from zana_core.images.import_plan import (
    MAX_IMPORT_BLOB_BYTES,
    ImportLimits,
    ImportValidationError,
    _BoundedSource,
    register_into_store,
)
from zana_core.images.models import (
    BaseModelReference,
    RunnableState,
    ZanaImageConfig,
)
from zana_core.images.oci import assemble_oci_layout

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
        with pytest.raises(ImportValidationError):
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

    def test_register_uses_base_available_probe(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import, register_into_store

        root = _make_layout(tmp_path)
        store = ArtifactStore(tmp_path / "artifacts")
        probe_digest = BASE_DIGEST
        plan = plan_import(root, base_available=lambda digest: digest == probe_digest)
        assert plan.runnability.state == RunnableState.RUNNABLE
        result = register_into_store(
            store, root, base_available=lambda digest: digest == probe_digest
        )
        assert result.registration is not None
        assert result.registration.runnability.state == RunnableState.RUNNABLE

    def test_import_limits_reject_absurd_values(self) -> None:
        for kwargs in (
            {"max_json_bytes": 0},
            {"max_json_bytes": 2**30},
            {"max_blob_bytes": 2**40},
            {"chunk_size": 0},
            {"chunk_size": 2**31},
            {"deadline_seconds": float("inf")},
            {"deadline_seconds": 1e9},
            {"max_json_bytes": 1.5},
            {"chunk_size": True},
        ):
            with pytest.raises(ImportValidationError):
                ImportLimits(**kwargs).validated()

    def test_store_copy_uses_truthful_cumulative_budget(self, tmp_path: Path) -> None:
        root = _make_layout(tmp_path)
        blob_dir = root / "blobs" / "sha256"
        total = sum(path.stat().st_size for path in blob_dir.iterdir())
        store = ArtifactStore(tmp_path / "artifacts")
        result = register_into_store(
            store,
            root,
            limits=ImportLimits(
                max_json_bytes=4096,
                max_blob_bytes=MAX_IMPORT_BLOB_BYTES,
                max_total_bytes=max(total + 1, 4096),
                chunk_size=64,
                deadline_seconds=30.0,
            ),
            deadline_seconds=30.0,
        )
        assert result.registration is not None
        assert len(result.registration.registered_blob_digests) >= 2

    def test_store_copy_cumulative_budget_fails_at_boundary(self, tmp_path: Path) -> None:
        root = _make_layout(tmp_path)
        blob_dir = root / "blobs" / "sha256"
        total = sum(path.stat().st_size for path in blob_dir.iterdir())
        store = ArtifactStore(tmp_path / "artifacts")
        from zana_core.images.oci import OciValidationError

        with pytest.raises((ImportValidationError, OciValidationError)):
            register_into_store(
                store,
                root,
                limits=ImportLimits(
                    max_json_bytes=4096,
                    max_blob_bytes=MAX_IMPORT_BLOB_BYTES,
                    max_total_bytes=max(total - 1, 1),
                    chunk_size=64,
                    deadline_seconds=30.0,
                ),
                deadline_seconds=30.0,
            )

    def test_available_base_digests_exact_set_before_any_hook(self, tmp_path: Path) -> None:
        root = _make_layout(tmp_path)
        from zana_core.images.import_plan import plan_import

        class HostileSet(set):
            def __len__(self):
                raise AssertionError("len must not be trusted")

            def __iter__(self):
                raise AssertionError("iteration must not be trusted")

            def __contains__(self, item):
                raise AssertionError("membership must not be trusted")

            def __hash__(self):
                raise AssertionError("hash must not be trusted")

        with pytest.raises(ImportValidationError):
            plan_import(root, available_base_digests=HostileSet())  # type: ignore[arg-type]

    def test_available_base_digests_never_uses_truthiness_or_set_fallback(
        self, tmp_path: Path
    ) -> None:
        import inspect

        from zana_core.images import import_plan as ip
        from zana_core.images.import_plan import plan_import

        source = inspect.getsource(ip)
        assert "available_base_digests or set()" not in source
        root = _make_layout(tmp_path)
        plan = plan_import(root, available_base_digests=set())
        assert plan.runnability.state == RunnableState.NOT_RUNNABLE_MISSING_BASE

    def test_available_base_digests_cap_before_iteration(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import MAX_AVAILABLE_BASE_DIGESTS, plan_import

        root = _make_layout(tmp_path)
        with pytest.raises(ImportValidationError, match="hard limit"):
            plan_import(
                root,
                available_base_digests={
                    f"sha256:{index:064x}" for index in range(MAX_AVAILABLE_BASE_DIGESTS + 1)
                },
            )

    def test_available_base_digests_member_exact_digest(self, tmp_path: Path) -> None:
        from zana_core.images.import_plan import plan_import

        root = _make_layout(tmp_path)
        with pytest.raises(ImportValidationError, match="digest"):
            plan_import(root, available_base_digests={"not-a-digest"})

    def test_deadline_value_rejects_hostile_float_hook(self) -> None:
        from zana_core.images.import_plan import _deadline_value

        class Hostile:
            def __float__(self):
                raise AssertionError("float hook must not be invoked")

        with pytest.raises(ImportValidationError, match="exact builtin"):
            _deadline_value(Hostile(), 30.0)  # type: ignore[arg-type]

    def test_register_into_store_deadline_is_enforced(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _make_layout(tmp_path)
        calls = {"count": 0}

        def clock() -> float:
            calls["count"] += 1
            return 0.0 if calls["count"] == 1 else 1000.0

        monkeypatch.setattr("zana_core.images.import_plan.time.monotonic", clock)
        store = ArtifactStore(tmp_path / "artifacts")
        with pytest.raises(ImportValidationError, match="deadline"):
            register_into_store(
                store,
                root,
                limits=ImportLimits(deadline_seconds=0.5),
                deadline_seconds=0.5,
            )

    def test_bounded_source_enforces_deadline_and_bytes(self, tmp_path: Path) -> None:
        source = tmp_path / "blob.bin"
        source.write_bytes(b"x" * 4096)
        reader = _BoundedSource(
            source,
            chunk_size=64,
            max_bytes=128,
            max_total_bytes=4096,
            total_so_far=0,
            start=time.monotonic(),
            deadline_seconds=30.0,
            expected_size=4096,
        )
        try:
            with pytest.raises(ImportValidationError, match="byte limit"):
                while reader.read(64):
                    pass
        finally:
            reader.close()
        short = _BoundedSource(
            source,
            chunk_size=64,
            max_bytes=4096,
            max_total_bytes=4096,
            total_so_far=0,
            start=time.monotonic(),
            deadline_seconds=0.0,
            expected_size=4096,
        )
        try:
            with pytest.raises(ImportValidationError, match="deadline"):
                short.read(64)
        finally:
            short.close()
