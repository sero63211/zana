"""Retrieval service, smoke tests, and index compatibility tests."""

from __future__ import annotations

import pytest

from zana_core.knowledge.embeddings import (
    EmbeddingIdentity,
    IndexIdentity,
    NormalizationBehavior,
    VectorRecord,
)
from zana_core.knowledge.retrieval import (
    ResourceLimitError,
    RetrievalQuery,
    RetrievalResult,
    RetrievalService,
    RetrievalSmokeRecord,
    index_compatible,
)


def identity() -> EmbeddingIdentity:
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
        parser_version="markdown-text.v1",
        chunker_identity="zana.heading-aware.v1",
        chunk_config_digest="sha256:" + "4" * 64,
        embedding=identity(),
    )


class FakeProvider:
    def __init__(self, identity: EmbeddingIdentity) -> None:
        self.identity = identity

    def embed(self, texts):  # noqa: ANN001
        from zana_core.knowledge.embeddings import EmbeddingBatch

        return EmbeddingBatch(
            identity=self.identity,
            texts=list(texts),
            vectors=[[1.0, 0.0] for _ in texts],
        )


class FakeIndex:
    def __init__(self, identity: IndexIdentity, records: list[VectorRecord]) -> None:
        self.identity = identity
        self.records = records

    def search(self, vector, limit):  # noqa: ANN001
        scored = []
        for record in self.records:
            score = sum(a * b for a, b in zip(vector, record.vector, strict=False))
            scored.append((record, score))
        return sorted(scored, key=lambda item: (-item[1], item[0].chunk_id))[:limit]

    def close(self) -> None:
        return


def record(chunk_id: str, section: str = "s", doc: str = "sha256:" + "1" * 64) -> VectorRecord:
    return VectorRecord(
        chunk_id=chunk_id,
        document_digest=doc,
        source_title="Policy Manual",
        page_start=1,
        page_end=2,
        heading_path=["Chapter 1"],
        section_id=section,
        text="text " + chunk_id,
        vector=[1.0, 0.0],
    )


class TestRetrievalService:
    def test_top_k_threshold_and_deterministic_order(self) -> None:
        provider = FakeProvider(identity())
        index = FakeIndex(
            index_identity(),
            [
                record("a", section="s1"),
                record("b", section="s2"),
                record("c", section="s3"),
                record("d", section="s4"),
            ],
        )
        service = RetrievalService(provider=provider, index=index)
        result = service.search(RetrievalQuery(text="q", top_k=2, min_score=0.5))
        assert isinstance(result, RetrievalResult)
        assert [hit.chunk_id for hit in result.hits] == ["a", "b"]
        assert [hit.rank for hit in result.hits] == [1, 2]
        assert list(result.hits[0].heading_path) == ["Chapter 1"]

    def test_dedup_by_document_and_section(self) -> None:
        provider = FakeProvider(identity())
        index = FakeIndex(
            index_identity(),
            [
                record("a", section="s1", doc="sha256:" + "b" * 64),
                record("b", section="s1", doc="sha256:" + "b" * 64),
                record("c", section="s2", doc="sha256:" + "b" * 64),
            ],
        )
        service = RetrievalService(provider=provider, index=index)
        by_document = service.search(RetrievalQuery(text="q", top_k=5, dedup_by="document"))
        assert len(by_document.hits) == 1
        by_section = service.search(RetrievalQuery(text="q", top_k=5, dedup_by="section"))
        assert [hit.section_id for hit in by_section.hits] == ["s1", "s2"]

    def test_identity_mismatch_rejected(self) -> None:
        other_data = identity().model_dump()
        other_data["model_name"] = "other"
        other = EmbeddingIdentity(**other_data)
        provider = FakeProvider(other)
        index = FakeIndex(index_identity(), [record("a")])
        service = RetrievalService(provider=provider, index=index)
        from zana_core.knowledge.retrieval import RetrievalError

        with pytest.raises(RetrievalError):
            service.search(RetrievalQuery(text="q"))

    def test_top_k_limit_fails_closed(self) -> None:
        provider = FakeProvider(identity())
        index = FakeIndex(index_identity(), [record("a")])
        service = RetrievalService(provider=provider, index=index, max_top_k=3)
        with pytest.raises(ResourceLimitError):
            service.search(RetrievalQuery(text="q", top_k=10))


class TestRetrievalSmoke:
    def test_honest_pass_and_fail_records(self) -> None:
        provider = FakeProvider(identity())
        index = FakeIndex(index_identity(), [record("a", doc="sha256:" + "1" * 64)])
        service = RetrievalService(provider=provider, index=index)
        passed = service.smoke_test(
            query_text="q",
            expected_chunk_ids=["a"],
            expected_source_ids=["sha256:" + "1" * 64],
        )
        assert isinstance(passed, RetrievalSmokeRecord)
        assert passed.passed is True
        failed = service.smoke_test(
            query_text="q",
            expected_chunk_ids=["missing"],
            expected_source_ids=["sha256:" + "a" * 64],
        )
        assert failed.passed is False
        assert "missing_chunk" in failed.failures
        assert "missing_source" in failed.failures


class TestIndexCompatibility:
    def test_all_inputs_must_match(self) -> None:
        assert index_compatible(
            index_identity(),
            expected_snapshot_digest="sha256:" + "3" * 64,
            parser_version="markdown-text.v1",
            chunker_identity="zana.heading-aware.v1",
            chunk_config_digest="sha256:" + "4" * 64,
            embedding=identity(),
        )
        assert not index_compatible(
            index_identity(),
            expected_snapshot_digest="sha256:" + "7" * 64,
            parser_version="markdown-text.v1",
            chunker_identity="zana.heading-aware.v1",
            chunk_config_digest="sha256:" + "4" * 64,
            embedding=identity(),
        )
        other_data = identity().model_dump()
        other_data["model_digest"] = "sha256:" + "8" * 64
        other = EmbeddingIdentity(**other_data)
        assert not index_compatible(
            index_identity(),
            expected_snapshot_digest="sha256:" + "3" * 64,
            parser_version="markdown-text.v1",
            chunker_identity="zana.heading-aware.v1",
            chunk_config_digest="sha256:" + "4" * 64,
            embedding=other,
        )
