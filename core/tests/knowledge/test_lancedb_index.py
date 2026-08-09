"""Persistent LanceDB index orchestration tests via an exact tiny local store."""

from __future__ import annotations

import json
import os

import pytest

from zana_core.knowledge.embeddings import (
    BackendUnavailableError,
    EmbeddingIdentity,
    IndexIdentity,
    NormalizationBehavior,
    NormalizationMismatchError,
    VectorRecord,
)
from zana_core.knowledge.lancedb_index import (
    INDEX_MANIFEST_NAME,
    IndexCorruptionError,
    IndexIncompatibleError,
    IndexNotFoundError,
    LanceDBIndex,
    LanceDBRecordStore,
)
from zana_core.knowledge.limits import KnowledgeLimits, ResourceLimitError
from zana_core.knowledge.snapshots import build_index_identity


def embedding() -> EmbeddingIdentity:
    return EmbeddingIdentity(
        provider="ollama",
        runtime_endpoint_identity="http://127.0.0.1:11434",
        model_name="nomic-embed-text",
        model_digest="sha256:" + "2" * 64,
        dimensions=2,
        normalization=NormalizationBehavior.L2,
        batch_size=8,
    )


def index_identity() -> IndexIdentity:
    return IndexIdentity(
        snapshot_digest="sha256:" + "3" * 64,
        parser_version="docling.v1",
        chunker_identity="zana.heading-aware.v1",
        chunk_config_digest="sha256:" + "4" * 64,
        embedding=embedding(),
    )


def record(chunk_id: str, section: str = "s") -> VectorRecord:
    return VectorRecord(
        chunk_id=chunk_id,
        document_digest="sha256:" + "1" * 64,
        source_title="Policy Manual",
        page_start=1,
        page_end=2,
        heading_path=["Chapter 1"],
        section_id=section,
        text="text " + chunk_id,
        vector=[1.0, 0.0],
    )


class TinyStore:
    """Real, durable, re-openable local record store used as an injected fixture."""

    def __init__(self, location, *, identity) -> None:  # noqa: ANN001
        self._file = location / "records.json"
        self._identity = identity
        self._fail_upsert = False

    def load_records(self):  # noqa: ANN201
        if not self._file.exists():
            return []
        try:
            data = json.loads(self._file.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            raise IndexCorruptionError("Tiny store records are corrupt.") from None
        return [VectorRecord(**row) for row in data]

    def upsert(self, records) -> None:  # noqa: ANN001
        if self._fail_upsert:
            raise OSError("injected store failure")
        merged = {item.chunk_id: item for item in self.load_records() + list(records)}
        rows = [item.model_dump() for item in merged.values()]
        tmp = self._file.with_name(".records.tmp")
        tmp.write_text(json.dumps(rows), encoding="utf-8")
        os.replace(tmp, self._file)

    def search(self, vector, limit):  # noqa: ANN001, ANN201
        scored = []
        for item in self.load_records():
            score = sum(a * b for a, b in zip(vector, item.vector, strict=False))
            scored.append((item, score))
        scored.sort(key=lambda entry: (-entry[1], entry[0].chunk_id))
        return scored[:limit]

    def close(self) -> None:  # noqa: D102
        return None


def make_index(tmp_path, *, identity=None, records=None, name="idx"):  # noqa: ANN001
    identity = identity or index_identity()
    records = list(records) if records is not None else [record("a"), record("b")]
    location = tmp_path / name
    store = TinyStore(location, identity=identity)
    index = LanceDBIndex.create(
        location,
        identity=identity,
        records=records,
        store=store,
    )
    return index, location


class TestLanceDBCreateOpen:
    def test_create_search_roundtrip(self, tmp_path) -> None:  # noqa: ANN001
        index, location = make_index(tmp_path)
        assert index.identity.identity_key() == index_identity().identity_key()
        results = index.search([1.0, 0.0], limit=2)
        assert [item.chunk_id for item, _ in results] == ["a", "b"]
        reopened = LanceDBIndex.open(location, store=TinyStore(location, identity=index_identity()))
        assert reopened.identity.identity_key() == index_identity().identity_key()
        assert [item.chunk_id for item, _ in reopened.search([1.0, 0.0], limit=2)] == [
            "a",
            "b",
        ]

    def test_create_refuses_existing_published_index(self, tmp_path) -> None:  # noqa: ANN001
        _, location = make_index(tmp_path)
        store = TinyStore(location, identity=index_identity())
        with pytest.raises(IndexCorruptionError):
            LanceDBIndex.create(
                location,
                identity=index_identity(),
                records=[record("z")],
                store=store,
            )

    def test_open_expected_identity_compatible(self, tmp_path) -> None:  # noqa: ANN001
        _, location = make_index(tmp_path)
        reopened = LanceDBIndex.open(
            location,
            expected_identity=index_identity(),
            store=TinyStore(location, identity=index_identity()),
        )
        assert reopened is not None

    def test_open_expected_identity_incompatible(self, tmp_path) -> None:  # noqa: ANN001
        _, location = make_index(tmp_path)
        other = index_identity().model_dump()
        other["snapshot_digest"] = "sha256:" + "9" * 64
        with pytest.raises(IndexIncompatibleError):
            LanceDBIndex.open(
                location,
                expected_identity=IndexIdentity(**other),
                store=TinyStore(location, identity=index_identity()),
            )

    def test_open_missing_location(self, tmp_path) -> None:  # noqa: ANN001
        missing = tmp_path / "missing"
        with pytest.raises(IndexNotFoundError):
            LanceDBIndex.open(missing, store=TinyStore(missing, identity=index_identity()))

    def test_corrupt_manifest_rejected(self, tmp_path) -> None:  # noqa: ANN001
        _, location = make_index(tmp_path)
        manifest = location / INDEX_MANIFEST_NAME
        manifest.write_text("{not valid json", encoding="utf-8")
        with pytest.raises(IndexCorruptionError):
            LanceDBIndex.open(location, store=TinyStore(location, identity=index_identity()))

    def test_tampered_identity_key_rejected(self, tmp_path) -> None:  # noqa: ANN001
        _, location = make_index(tmp_path)
        manifest = location / INDEX_MANIFEST_NAME
        payload = json.loads(manifest.read_text(encoding="utf-8"))
        payload["identity_key"] = payload["identity_key"][:-1] + "0"
        manifest.write_text(json.dumps(payload), encoding="utf-8")
        with pytest.raises(IndexCorruptionError):
            LanceDBIndex.open(location, store=TinyStore(location, identity=index_identity()))


class TestLanceDBUpsertAndBounds:
    def test_upsert_adds_and_dedups(self, tmp_path) -> None:  # noqa: ANN001
        index, location = make_index(tmp_path)
        count = index.upsert([record("a"), record("c")])
        assert count == 2
        reopened = LanceDBIndex.open(location, store=TinyStore(location, identity=index_identity()))
        ids = {item.chunk_id for item, _ in reopened.search([1.0, 0.0], limit=10)}
        assert ids == {"a", "b", "c"}

    def test_upsert_rejects_dimension_drift(self, tmp_path) -> None:  # noqa: ANN001
        index, _ = make_index(tmp_path)
        from zana_core.knowledge.embeddings import DimensionMismatchError

        drifted = VectorRecord(
            chunk_id="drift",
            document_digest="sha256:" + "1" * 64,
            vector=[1.0, 0.0, 0.0],
            text="drift",
        )
        with pytest.raises(DimensionMismatchError):
            index.upsert([drifted])

    def test_create_rejects_non_normalized_vector(self, tmp_path) -> None:  # noqa: ANN001
        from zana_core.knowledge.embeddings import NormalizationMismatchError

        bad = VectorRecord(
            chunk_id="x",
            document_digest="sha256:" + "1" * 64,
            vector=[2.0, 0.0],
            text="x",
        )
        location = tmp_path / "bad"
        store = TinyStore(location, identity=index_identity())
        with pytest.raises(NormalizationMismatchError):
            LanceDBIndex.create(
                location,
                identity=index_identity(),
                records=[bad],
                store=store,
            )

    def test_search_limit_bounds(self, tmp_path) -> None:  # noqa: ANN001
        index, _ = make_index(tmp_path)
        with pytest.raises(ResourceLimitError):
            index.search([1.0, 0.0], limit=0)
        with pytest.raises(ResourceLimitError):
            index.search([1.0, 0.0], limit=10_000)

    def test_create_exceeds_index_records(self, tmp_path) -> None:  # noqa: ANN001
        limits = KnowledgeLimits(max_index_records=2)
        location = tmp_path / "big"
        store = TinyStore(location, identity=index_identity())
        with pytest.raises(ResourceLimitError):
            LanceDBIndex.create(
                location,
                identity=index_identity(),
                records=[record(i, section=str(i)) for i in ("a", "b", "c")],
                store=store,
                limits=limits,
            )


class TestLanceDBAtomicityAndAvailability:
    def test_atomic_failure_leaves_no_published_manifest(self, tmp_path) -> None:  # noqa: ANN001
        location = tmp_path / "atomic"
        store = TinyStore(location, identity=index_identity())
        store._fail_upsert = True
        with pytest.raises(OSError):
            LanceDBIndex.create(
                location,
                identity=index_identity(),
                records=[record("a")],
                store=store,
            )
        assert not (location / INDEX_MANIFEST_NAME).exists()
        with pytest.raises(IndexNotFoundError):
            LanceDBIndex.open(location, store=TinyStore(location, identity=index_identity()))

    def test_lancedb_unavailable_is_honest(self, tmp_path) -> None:  # noqa: ANN001
        location = tmp_path / "unavail"
        with pytest.raises(BackendUnavailableError):
            LanceDBIndex.create(
                location,
                identity=index_identity(),
                records=[record("a")],
            )

    def test_lancedb_record_store_unavailable_is_honest(self, tmp_path) -> None:  # noqa: ANN001
        location = tmp_path / "store"
        with pytest.raises(BackendUnavailableError):
            LanceDBRecordStore(location, identity=index_identity())

    def test_deterministic_identity_manifest(self, tmp_path) -> None:  # noqa: ANN001
        _, first = make_index(tmp_path, name="one")
        _, second = make_index(tmp_path, name="two")
        first_manifest = (first / INDEX_MANIFEST_NAME).read_text(encoding="utf-8")
        second_manifest = (second / INDEX_MANIFEST_NAME).read_text(encoding="utf-8")
        assert first_manifest == second_manifest

    def test_symlink_location_rejected(self, tmp_path) -> None:  # noqa: ANN001
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")
        with pytest.raises(IndexCorruptionError):
            LanceDBIndex.create(
                link,
                identity=index_identity(),
                records=[record("a")],
                store=TinyStore(link, identity=index_identity()),
            )


class _FakeLanceTable:
    def __init__(self, rows=None, count=None) -> None:  # noqa: ANN001
        self.rows = rows if rows is not None else []
        self.count = len(self.rows) if count is None else count
        self.metrics: list[str] = []
        self.merge_called = False
        self.merge_raise = None
        self.materialized = False

    def count_rows(self):  # noqa: ANN201
        return self.count

    def to_lance(self):  # noqa: ANN201
        return self

    def to_table(self):  # noqa: ANN201
        return self

    def to_pylist(self):  # noqa: ANN201
        self.materialized = True
        return self.rows

    def search(self, vector, *, metric):  # noqa: ANN001, ARG002
        self.metrics.append(metric)
        return self

    def limit(self, limit):  # noqa: ANN001, ARG002
        return self

    def to_list(self):  # noqa: ANN201
        return self.rows

    def merge_insert(self, key):  # noqa: ANN001, ARG002
        self.merge_called = True
        return self

    def when_matched_update_all(self):  # noqa: ANN201
        return self

    def when_not_matched_insert_all(self):  # noqa: ANN201
        return self

    def execute(self, rows):  # noqa: ANN001
        if self.merge_raise is not None:
            raise self.merge_raise


class _FakeLanceBackend:
    def __init__(self, tables: dict | None = None) -> None:  # noqa: ANN001
        self.tables = dict(tables or {})
        self.create_calls: list[tuple[str, list]] = []

    def table_names(self):  # noqa: ANN201
        return list(self.tables)

    def open_table(self, name):  # noqa: ANN001
        return self.tables[name]

    def create_table(self, name, data):  # noqa: ANN001
        self.create_calls.append((name, data))
        table = _FakeLanceTable(rows=data)
        self.tables[name] = table
        return table


def _row(chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "document_digest": "sha256:" + "1" * 64,
        "source_title": "Policy Manual",
        "page_start": 1,
        "page_end": 2,
        "heading_path": ["Chapter 1"],
        "section_id": "s",
        "text": "text " + chunk_id,
        "vector": [1.0, 0.0],
        "metadata_json": {},
    }


class TestLanceDBRecordStoreAdapter:
    def test_create_table_uses_create_not_overwrite(self, tmp_path) -> None:  # noqa: ANN001
        backend = _FakeLanceBackend()
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        store.upsert([record("a")])
        assert [name for name, _ in backend.create_calls] == ["vectors"]
        assert backend.tables["vectors"].rows[0]["chunk_id"] == "a"

    def test_merge_update_by_chunk_id(self, tmp_path) -> None:  # noqa: ANN001
        table = _FakeLanceTable(rows=[_row("a")], count=1)
        backend = _FakeLanceBackend({"vectors": table})
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        store.upsert([record("a"), record("b")])
        assert table.merge_called is True
        assert backend.create_calls == []

    def test_update_failure_never_overwrites(self, tmp_path) -> None:  # noqa: ANN001
        table = _FakeLanceTable(rows=[_row("a")], count=1)
        table.merge_raise = RuntimeError("merge backend failure")
        backend = _FakeLanceBackend({"vectors": table})
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        with pytest.raises(IndexCorruptionError):
            store.upsert([record("b")])
        assert backend.create_calls == []

    def test_load_rejects_cap_plus_one_before_materialization(self, tmp_path) -> None:  # noqa: ANN001
        table = _FakeLanceTable(rows=[], count=KnowledgeLimits().max_index_records + 1)
        backend = _FakeLanceBackend({"vectors": table})
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        with pytest.raises(ResourceLimitError):
            store.load_records()
        assert table.materialized is False

    def test_search_explicit_cosine_and_distance_mapping(self, tmp_path) -> None:  # noqa: ANN001
        rows = [{**_row("a"), "_distance": 0.25}]
        table = _FakeLanceTable(rows=rows, count=1)
        backend = _FakeLanceBackend({"vectors": table})
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        results = store.search([1.0, 0.0], limit=5)
        assert table.metrics == ["cosine"]
        assert results == [(record("a"), 0.75)]

    def test_search_out_of_range_distance_rejected(self, tmp_path) -> None:  # noqa: ANN001
        rows = [{**_row("a"), "_distance": 3.0}]
        table = _FakeLanceTable(rows=rows, count=1)
        backend = _FakeLanceBackend({"vectors": table})
        store = LanceDBRecordStore(
            tmp_path,
            identity=index_identity(),
            backend=backend,
        )
        with pytest.raises(IndexCorruptionError):
            store.search([1.0, 0.0], limit=5)


class TestLanceDBQueryVectorValidation:
    def test_wrong_dimensions_rejected(self, tmp_path) -> None:  # noqa: ANN001
        index, _ = make_index(tmp_path)
        with pytest.raises(ResourceLimitError):
            index.search([1.0, 0.0, 0.0], limit=2)

    def test_nan_query_rejected(self, tmp_path) -> None:  # noqa: ANN001
        index, _ = make_index(tmp_path)
        with pytest.raises(ResourceLimitError):
            index.search([float("nan"), 0.0], limit=2)

    def test_non_normalized_query_rejected(self, tmp_path) -> None:  # noqa: ANN001
        index, _ = make_index(tmp_path)
        with pytest.raises(NormalizationMismatchError):
            index.search([2.0, 0.0], limit=2)

    def test_valid_query_maps_scores(self, tmp_path) -> None:  # noqa: ANN001
        other = VectorRecord(
            chunk_id="b",
            document_digest="sha256:" + "1" * 64,
            vector=[0.0, 1.0],
            text="text b",
        )
        index, _ = make_index(tmp_path, records=[record("a"), other])
        results = index.search([1.0, 0.0], limit=2)
        assert [item.chunk_id for item, score in results] == ["a", "b"]
        assert [score for _, score in results] == [1.0, 0.0]


class TestSnapshotAndRetrievalIntegration:
    def test_build_index_identity_from_snapshot(self, tmp_path) -> None:  # noqa: ANN001
        from zana_core.knowledge.models import (
            ChunkConfiguration,
            SourceMetadata,
        )
        from zana_core.knowledge.snapshots import (
            build_snapshot_manifest,
            read_snapshot,
            write_snapshot,
        )

        source = SourceMetadata(
            original_path="/approved/doc.md",
            display_name="doc.md",
            kind="markdown",
            size_bytes=10,
            sha256="sha256:" + "1" * 64,
        )
        manifest = build_snapshot_manifest(
            sources=[source],
            chunks=[],
            chunk_config=ChunkConfiguration(),
            embedding_identity_required="embedding:v1",
        )
        identity = build_index_identity(manifest=manifest, embedding=embedding())
        assert identity.snapshot_digest == manifest.snapshot_id
        assert identity.parser_version == manifest.parser_version

        location = tmp_path / "snap"
        write_snapshot(location, manifest)
        loaded = read_snapshot(location)
        assert loaded.snapshot_id == manifest.snapshot_id

    def test_open_persistent_retrieval(self, tmp_path) -> None:  # noqa: ANN001
        from zana_core.knowledge.embeddings import EmbeddingBatch
        from zana_core.knowledge.retrieval import RetrievalQuery, RetrievalService

        class FakeProvider:
            def __init__(self, identity: EmbeddingIdentity) -> None:  # noqa: ANN001
                self.identity = identity

            def embed(self, texts):  # noqa: ANN001
                return EmbeddingBatch(
                    identity=self.identity,
                    texts=list(texts),
                    vectors=[[1.0, 0.0] for _ in texts],
                )

        _, location = make_index(tmp_path, records=[record("a"), record("b", section="s2")])
        store = TinyStore(location, identity=index_identity())
        service = RetrievalService.open_persistent(
            location,
            provider=FakeProvider(embedding()),
            store=store,
        )
        result = service.search(RetrievalQuery(text="query", top_k=2))
        assert [hit.chunk_id for hit in result.hits] == ["a", "b"]
        assert result.hits[0].source_title == "Policy Manual"

    def test_open_persistent_without_store_is_honestly_unavailable(self, tmp_path) -> None:  # noqa: ANN001
        from zana_core.knowledge.embeddings import EmbeddingBatch
        from zana_core.knowledge.retrieval import RetrievalService

        class FakeProvider:
            def embed(self, texts):  # noqa: ANN001
                return EmbeddingBatch(
                    identity=embedding(),
                    texts=list(texts),
                    vectors=[[1.0, 0.0] for _ in texts],
                )

        _, location = make_index(tmp_path)
        with pytest.raises(BackendUnavailableError):
            RetrievalService.open_persistent(location, provider=FakeProvider())
