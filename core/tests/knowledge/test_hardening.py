"""Focused regression tests for knowledge pipeline hardening."""

from __future__ import annotations

import itertools
import math
import os
import stat
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from pydantic import ValidationError

from zana_core.knowledge.chunker import (
    ChunkLimitError,
    HeadingAwareChunker,
    TextEstimator,
    estimate_tokens,
)
from zana_core.knowledge.embeddings import (
    EmbeddingBatch,
    EmbeddingIdentity,
    EmptyVectorError,
    IndexIdentity,
    MissingIdentityError,
    NonFiniteVectorError,
    NormalizationBehavior,
    OllamaEmbeddingProvider,
    OllamaEmbedRequest,
    TransportError,
    VectorRecord,
    cosine_similarity,
    normalize_l2,
    validate_vector_index,
)
from zana_core.knowledge.evidence import (
    EvidenceBlock,
    fit_context,
    render_evidence_block,
)
from zana_core.knowledge.intake import (
    ApprovedPathResolver,
    OversizeFileError,
    PathEscapeError,
    UnreadableFileError,
    UnsupportedTypeError,
    check_size,
    detect_kind,
    digest_stream,
    prepare_source,
    prepare_sources,
)
from zana_core.knowledge.limits import (
    HARD_MAX_SOURCE_BYTES,
    HARD_MAX_STREAM_CHUNK_SIZE,
    HARD_MAX_TIMEOUT_SECONDS,
    DeadlineExceededError,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    VectorBudget,
    check_deadline,
    make_deadline,
    remaining_seconds,
    require_strict_number,
    safe_monotonic,
)
from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    ContextPackage,
    DocumentKind,
    FrozenMetadata,
    FrozenMetadataList,
    NormalizedDocument,
    NormalizedSection,
    ParserError,
    SnapshotManifest,
    SourceMetadata,
    canonical_identity_key,
    validate_bounded_metadata,
)
from zana_core.knowledge.normalizers import (
    NormalizationLimitError,
    normalize_source,
    normalize_text,
)
from zana_core.knowledge.parsers import parse_sources
from zana_core.knowledge.retrieval import (
    HostileIndexError,
    RetrievalError,
    RetrievalHit,
    RetrievalQuery,
    RetrievalService,
    RetrievalSmokeRecord,
    index_compatible,
)
from zana_core.knowledge.snapshots import build_snapshot_manifest


def identity(dimensions: int = 2, batch_size: int = 8) -> EmbeddingIdentity:
    return EmbeddingIdentity(
        provider="ollama",
        runtime_endpoint_identity="http://127.0.0.1:11434",
        model_name="nomic-embed-text",
        model_digest="sha256:" + "2" * 64,
        dimensions=dimensions,
        normalization=NormalizationBehavior.L2,
        batch_size=batch_size,
    )


def index_identity() -> IndexIdentity:
    return IndexIdentity(
        snapshot_digest="sha256:" + "3" * 64,
        parser_version="markdown-text.v1",
        chunker_identity="zana.heading-aware.v1",
        chunk_config_digest="sha256:" + "4" * 64,
        embedding=identity(),
    )


def record(chunk_id: str = "a", *, text: str = "policy text") -> VectorRecord:
    return VectorRecord(
        chunk_id=chunk_id,
        document_digest="sha256:" + "1" * 64,
        source_title="Policy Manual",
        page_start=1,
        page_end=2,
        heading_path=["Chapter 1"],
        section_id="s1",
        text=text,
        vector=[1.0, 0.0],
    )


def source_marker() -> SourceMetadata:
    return SourceMetadata(
        original_path="/approved/doc.md",
        display_name="doc.md",
        kind=DocumentKind.MARKDOWN,
        size_bytes=10,
        sha256="sha256:" + "0" * 64,
    )


class FakeProvider:
    def __init__(self, embedding_identity: EmbeddingIdentity) -> None:
        self.identity = embedding_identity
        self.calls = 0

    def embed(self, texts):
        self.calls += 1
        return EmbeddingBatch(
            identity=self.identity,
            texts=list(texts),
            vectors=[[1.0, 0.0] for _ in texts],
        )


class FakeIndex:
    def __init__(self, index_identity_: IndexIdentity, records: list[VectorRecord]) -> None:
        self.identity = index_identity_
        self.records = records

    def search(self, vector, limit):  # noqa: ANN001
        scored = [(record, 1.0) for record in self.records]
        return sorted(scored, key=lambda item: item[0].chunk_id)[:limit]

    def close(self) -> None:
        return


class InfiniteIndex:
    def __init__(self, index_identity_: IndexIdentity) -> None:
        self.identity = index_identity_
        self.consumed = 0

    def search(self, vector, limit):  # noqa: ANN001
        while True:
            self.consumed += 1
            yield (record(), 1.0)

    def close(self) -> None:
        return


class MarkdownParser:
    parser_version = "markdown-text.v1"
    supported_kinds = frozenset({DocumentKind.MARKDOWN, DocumentKind.TEXT})

    def parse(
        self,
        source: SourceMetadata,
        *,
        deadline: float | None = None,
    ) -> NormalizedDocument:
        return NormalizedDocument(
            document_id=source.sha256,
            title=source.display_name,
            sections=[],
            warnings=[],
        )


class TestKnowledgeLimits:
    def test_hard_caps_cannot_be_raised(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_source_bytes=HARD_MAX_SOURCE_BYTES + 1)
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_top_k=1_001)

    def test_every_hard_field_cannot_be_raised(self) -> None:
        import importlib

        module = importlib.import_module("zana_core.knowledge.limits")
        checked: list[str] = []
        for field in KnowledgeLimits.model_fields:
            hard = getattr(module, f"HARD_MAX_{field[4:].upper()}")
            assert hard is not None, f"missing hard cap for {field}"
            with pytest.raises(ValidationError):
                KnowledgeLimits(**{field: hard + 1})
            checked.append(field)
        assert len(checked) == len(KnowledgeLimits.model_fields)

    def test_cross_field_and_finite_constraints(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_top_k=10, max_candidate_count=5)
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_text_bytes=100, max_source_bytes=50)
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_timeout_seconds=float("nan"))

    def test_frozen_and_extra_forbid(self) -> None:
        limits = KnowledgeLimits()
        with pytest.raises(ValidationError):
            KnowledgeLimits(unknown=1)
        with pytest.raises(ValidationError):
            limits.max_source_bytes = 1

    def test_make_deadline_is_finite_and_hard_capped(self) -> None:
        deadline = make_deadline(None)
        assert math.isfinite(deadline)
        assert deadline > time.monotonic()
        assert deadline <= time.monotonic() + HARD_MAX_TIMEOUT_SECONDS
        with pytest.raises(ResourceLimitError):
            make_deadline(1000, hard_max=60)
        with pytest.raises(ResourceLimitError):
            make_deadline(float("nan"))
        with pytest.raises(ResourceLimitError):
            make_deadline(0)


class TestCapPlusOneEarlyStop:
    def test_vector_index_infinite_iterable_stops_at_cap_plus_one(self) -> None:
        def records() -> Iterator[VectorRecord]:
            index = 0
            while True:
                yield record(chunk_id=f"c{index}")
                index += 1

        consumed = itertools.count()
        with pytest.raises(ResourceLimitError):
            validate_vector_index(
                index_identity(),
                _counted(records(), consumed),
                limits=KnowledgeLimits(max_index_records=3),
            )
        assert next(consumed) == 4

    def test_vector_index_sequence_length_checked_before_copy(self) -> None:
        with pytest.raises(ResourceLimitError):
            validate_vector_index(
                index_identity(),
                [record(chunk_id=f"c{index}") for index in range(4)],
                limits=KnowledgeLimits(max_index_records=3),
            )

    def test_parse_sources_infinite_iterable_stops_at_cap_plus_one(self) -> None:
        def sources() -> Iterator[SourceMetadata]:
            while True:
                yield source_marker()

        consumed = itertools.count()
        with pytest.raises(ResourceLimitError):
            parse_sources(
                _counted(sources(), consumed),
                MarkdownParser(),
                limits=KnowledgeLimits(max_source_count=2),
            )
        assert next(consumed) == 3

    def test_prepare_sources_infinite_paths_stop_at_cap_plus_one(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def paths() -> Iterator[Path]:
            while True:
                yield doc

        consumed = itertools.count()
        with pytest.raises(ResourceLimitError):
            list(
                prepare_sources(
                    _counted(paths(), consumed),
                    approved_root=root,
                    limits=KnowledgeLimits(max_source_count=2),
                )
            )
        assert next(consumed) == 3

    def test_retrieval_hostile_infinite_candidates_stop_at_candidate_cap(self) -> None:
        index = InfiniteIndex(index_identity())
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=index,
            limits=KnowledgeLimits(max_candidate_count=5, max_top_k=3),
        )
        with pytest.raises(HostileIndexError):
            service.search(RetrievalQuery(text="q", top_k=2))
        assert index.consumed == 5 + 1

    def test_smoke_expected_ids_stop_at_cap_plus_one(self) -> None:
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=FakeIndex(index_identity(), [record("a")]),
            limits=KnowledgeLimits(max_smoke_expectations=3),
        )

        def expected_ids() -> Iterator[str]:
            index = 0
            while True:
                yield f"id{index}"
                index += 1

        consumed = itertools.count()
        with pytest.raises(ResourceLimitError):
            service.smoke_test(
                query_text="q",
                expected_chunk_ids=_counted(expected_ids(), consumed),
                expected_source_ids=[],
            )
        assert next(consumed) == 4

    def test_normalize_rejects_over_line_cap_at_boundary(self, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import normalizers

        original = normalizers._iter_lines
        seen: list[str] = []

        def counting(text: str, max_lines: int) -> Iterator[str]:
            for line in original(text, max_lines):
                seen.append(line)
                yield line

        monkeypatch.setattr(normalizers, "_iter_lines", counting)
        with pytest.raises(NormalizationLimitError):
            normalize_text(
                "a\nb\nc\nd\ne\n",
                limits=KnowledgeLimits(max_lines=2),
            )
        assert len(seen) == 2


def _counted(items: Iterator[object], counter: Iterator[int]) -> Iterator[object]:
    for item in items:
        next(counter)
        yield item


class TestUnicodeByteCaps:
    def test_embedding_rejects_multibyte_overflow_before_request(self) -> None:
        class Transport:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                self.calls += 1
                raise AssertionError("transport must not be reached")

        transport = Transport()
        provider = OllamaEmbeddingProvider(
            identity=identity(batch_size=1),
            transport=transport,
            limits=KnowledgeLimits(
                max_text_bytes=2_048,
                max_batch_total_bytes=4_096,
                max_query_bytes=2_048,
                max_chunk_text_bytes=2_048,
            ),
        )
        with pytest.raises(ResourceLimitError):
            provider.embed(["€" * 1_000])
        assert transport.calls == 0

    def test_query_rejects_multibyte_overflow_before_embedding(self) -> None:
        provider = FakeProvider(identity())
        service = RetrievalService(
            provider=provider,
            index=FakeIndex(index_identity(), [record("a")]),
            limits=KnowledgeLimits(
                max_query_bytes=256,
                max_text_bytes=512,
                max_chunk_text_bytes=512,
            ),
        )
        with pytest.raises(ResourceLimitError):
            service.search(RetrievalQuery(text="€" * 300))
        assert provider.calls == 0

    def test_normalization_rejects_multibyte_overflow(self) -> None:
        with pytest.raises(NormalizationLimitError):
            normalize_text(
                "€" * 1_000,
                limits=KnowledgeLimits(
                    max_text_bytes=2_048,
                    max_query_bytes=2_048,
                    max_chunk_text_bytes=2_048,
                ),
            )

    def test_chunking_rejects_multibyte_section_overflow(self) -> None:
        section = NormalizedSection(
            section_id="s1",
            heading_path=["A"],
            text="€" * 1_000,
            start_offset=0,
            end_offset=1_000,
        )
        document = NormalizedDocument(
            document_id="sha256:" + "0" * 64,
            title="T",
            sections=[section],
        )
        chunker = HeadingAwareChunker(
            ChunkConfiguration(target_tokens=40),
            limits=KnowledgeLimits(
                max_text_bytes=2_048,
                max_query_bytes=2_048,
                max_chunk_text_bytes=2_048,
            ),
        )
        with pytest.raises(ChunkLimitError):
            chunker.chunk_document(document)


class TestHostileOutputs:
    def test_provider_rejects_dimension_drift(self) -> None:
        class Transport:
            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                return self.response  # type: ignore[attr-defined]

        transport = Transport()
        transport.response = type(
            "Response",
            (),
            {
                "status": 200,
                "text": '{"embeddings":[[1.0,0.0,0.0,0.0]]}',
                "content_type": "application/json",
            },
        )()
        provider = OllamaEmbeddingProvider(identity=identity(dimensions=3), transport=transport)
        with pytest.raises(TransportError):
            provider.embed(["x"])

    def test_provider_rejects_non_finite_response(self) -> None:
        class Transport:
            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                return self.response  # type: ignore[attr-defined]

        transport = Transport()
        transport.response = type(
            "Response",
            (),
            {
                "status": 200,
                "text": '{"embeddings":[[1.0, NaN, 0.0]]}',
                "content_type": "application/json",
            },
        )()
        provider = OllamaEmbeddingProvider(identity=identity(dimensions=3), transport=transport)
        with pytest.raises(TransportError):
            provider.embed(["x"])

    def test_retrieval_rejects_non_finite_score(self) -> None:
        class BadIndex(FakeIndex):
            def search(self, vector, limit):  # noqa: ANN001
                return [(record("a"), float("nan"))]

        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=BadIndex(index_identity(), []),
        )
        with pytest.raises(HostileIndexError):
            service.search(RetrievalQuery(text="q"))

    def test_retrieval_rejects_oversized_candidate_text(self) -> None:
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=FakeIndex(
                index_identity(),
                [record("a", text="x" * 100)],
            ),
            limits=KnowledgeLimits(
                max_text_bytes=16,
                max_query_bytes=16,
                max_chunk_text_bytes=16,
            ),
        )
        with pytest.raises(HostileIndexError):
            service.search(RetrievalQuery(text="q"))


class TestAbsurdLimitsAndBudgets:
    def test_context_budget_must_be_positive_and_bounded(self) -> None:
        blocks = [_evidence("alpha")]
        with pytest.raises(ResourceLimitError):
            fit_context(blocks, budget_tokens=0)
        with pytest.raises(ResourceLimitError):
            fit_context(blocks, budget_tokens=float("nan"))
        with pytest.raises(ResourceLimitError):
            fit_context(
                blocks,
                budget_tokens=100,
                limits=KnowledgeLimits(max_evidence_tokens=10),
            )

    def test_context_block_count_is_bounded(self) -> None:
        blocks = [_evidence(f"alpha{index}") for index in range(4)]
        with pytest.raises(ResourceLimitError):
            fit_context(
                blocks,
                budget_tokens=1_000,
                limits=KnowledgeLimits(max_evidence_count=3),
            )

    def test_chunker_rejects_over_budget_chunk_count(self) -> None:
        section = NormalizedSection(
            section_id="s1",
            heading_path=["A"],
            text="a b c d e f g h i j k",
            start_offset=0,
            end_offset=21,
        )
        document = NormalizedDocument(
            document_id="sha256:" + "0" * 64,
            title="T",
            sections=[section],
        )
        chunker = HeadingAwareChunker(
            ChunkConfiguration(target_tokens=1, max_tokens=1, overlap_tokens=0),
            limits=KnowledgeLimits(max_chunk_count=3),
        )
        with pytest.raises(ChunkLimitError):
            chunker.chunk_document(document)

    def test_snapshot_rejects_over_budget_counts(self) -> None:
        config = ChunkConfiguration()
        with pytest.raises(ResourceLimitError):
            build_snapshot_manifest(
                sources=[source_marker() for _ in range(4)],
                chunks=[],
                chunk_config=config,
                embedding_identity_required="e",
                limits=KnowledgeLimits(max_source_count=3),
            )
        with pytest.raises(ResourceLimitError):
            build_snapshot_manifest(
                sources=[],
                chunks=[_chunk(f"c{index}") for index in range(4)],
                chunk_config=config,
                embedding_identity_required="e",
                limits=KnowledgeLimits(max_chunk_count=3),
            )


class TestPreflightDenial:
    def test_endpoint_credentials_rejected_before_transport(self) -> None:
        with pytest.raises(MissingIdentityError):
            OllamaEmbeddingProvider(
                identity=identity(),
                endpoint="http://user:secret@127.0.0.1:11434",
            )

    def test_endpoint_must_match_identity(self) -> None:
        with pytest.raises(MissingIdentityError):
            OllamaEmbeddingProvider(
                identity=identity(),
                endpoint="http://127.0.0.1:11435",
            )

    def test_oversized_bearer_rejected_before_transport(self) -> None:
        class Transport:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                self.calls += 1
                raise AssertionError("transport must not be reached")

        transport = Transport()
        with pytest.raises(ResourceLimitError):
            OllamaEmbeddingProvider(
                identity=identity(),
                transport=transport,
                bearer_token="s" * 100,
                limits=KnowledgeLimits(max_credential_bytes=8),
            )
        assert transport.calls == 0

    def test_non_finite_timeout_rejected_before_transport(self) -> None:
        with pytest.raises(ResourceLimitError):
            OllamaEmbeddingProvider(identity=identity(), timeout=float("nan"))

    def test_total_batch_bytes_rejected_before_transport(self) -> None:
        class Transport:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                self.calls += 1
                raise AssertionError("transport must not be reached")

        transport = Transport()
        provider = OllamaEmbeddingProvider(
            identity=identity(batch_size=1),
            transport=transport,
            limits=KnowledgeLimits(
                max_batch_text_count=2,
                max_batch_total_bytes=8,
                max_text_bytes=100,
                max_query_bytes=8,
                max_chunk_text_bytes=100,
            ),
        )
        with pytest.raises(ResourceLimitError):
            provider.embed(["abcdefghi"])
        assert transport.calls == 0

    def test_request_bytes_rejected_before_transport(self) -> None:
        class Transport:
            def __init__(self) -> None:
                self.calls = 0

            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                self.calls += 1
                raise AssertionError("transport must not be reached")

        transport = Transport()
        provider = OllamaEmbeddingProvider(
            identity=identity(batch_size=1),
            transport=transport,
            limits=KnowledgeLimits(
                max_batch_text_count=2,
                max_batch_total_bytes=64,
                max_request_bytes=64,
                max_text_bytes=64,
                max_query_bytes=64,
                max_chunk_text_bytes=64,
            ),
        )
        with pytest.raises(ResourceLimitError):
            provider.embed(["hello"])
        assert transport.calls == 0


class TestDeterminismAndModelStrictness:
    def test_retrieval_is_deterministic(self) -> None:
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=FakeIndex(
                index_identity(),
                [
                    record("a", text="alpha"),
                    record("b", text="beta"),
                    record("c", text="gamma"),
                ],
            ),
        )
        first = service.search(RetrievalQuery(text="q", top_k=3))
        second = service.search(RetrievalQuery(text="q", top_k=3))
        assert [hit.chunk_id for hit in first.hits] == [hit.chunk_id for hit in second.hits]
        assert [hit.rank for hit in first.hits] == [1, 2, 3]

    def test_public_models_reject_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            RetrievalQuery(text="q", unexpected=1)
        with pytest.raises(ValidationError):
            RetrievalHit(
                chunk_id="c",
                document_digest="sha256:" + "5" * 64,
                source_title="t",
                text="x",
                score=0.5,
                rank=1,
                unexpected=1,
            )

    def test_metadata_is_bounded(self) -> None:
        limits = KnowledgeLimits(max_metadata_items=2, max_metadata_depth=2)
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": 1, "b": 2, "c": 3}, limits=limits)
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": {"b": {"c": 1}}}, limits=limits)
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": float("nan")}, limits=limits)

    def test_intake_errors_do_not_leak_private_paths(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.docx"
        doc.write_text("x", encoding="utf-8")
        with pytest.raises(UnsupportedTypeError) as error:
            prepare_source(doc, approved_root=root)
        message = str(error.value)
        assert str(root) not in message
        assert str(doc) not in message
        outside = tmp_path / "outside.md"
        outside.write_text("x", encoding="utf-8")
        link = root / "link.md"
        link.symlink_to(outside)
        with pytest.raises(PathEscapeError) as escape:
            prepare_source(link, approved_root=root)
        assert str(root) not in str(escape.value)

    def test_invalid_deadline_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        with pytest.raises(ResourceLimitError):
            prepare_source(doc, approved_root=root, deadline_seconds=0)
        with pytest.raises(ResourceLimitError):
            prepare_source(doc, approved_root=root, deadline_seconds=float("nan"))


class TestMetadataAdversarial:
    def test_cycle_rejected_before_copy(self) -> None:
        metadata: dict[str, object] = {}
        metadata["self"] = metadata
        with pytest.raises(ValueError, match="cycle|alias"):
            validate_bounded_metadata(metadata)

    def test_alias_rejected(self) -> None:
        shared: list[object] = [1, 2]
        with pytest.raises(ValueError, match="cycle|alias"):
            validate_bounded_metadata({"a": shared, "b": shared})

    def test_deep_graph_rejected_before_copy(self) -> None:
        node: dict[str, object] = {"leaf": 1}
        for _ in range(12):
            node = {"next": node}
        with pytest.raises(ValueError, match="depth"):
            validate_bounded_metadata(node)

    def test_hostile_and_oversized_values_rejected(self) -> None:
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": 2**63})
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": object()})
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": (1, 2)})
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": float("nan")})

    def test_defensive_copy_is_not_the_original_graph(self) -> None:
        original: dict[str, object] = {"nested": {"value": 1}}
        copied = validate_bounded_metadata(original)
        assert copied["nested"] is not original["nested"]
        assert copied["nested"] == original["nested"]


class TestAggregateRetainedBudgets:
    def test_document_aggregate_bytes(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.models as models

        monkeypatch.setattr(models, "HARD_MAX_DOCUMENT_RETAINED_BYTES", 32)
        sections = tuple(
            NormalizedSection(
                section_id=f"s{index}",
                heading_path=["A"],
                text="x" * 16,
                start_offset=0,
                end_offset=16,
            )
            for index in range(4)
        )
        with pytest.raises(ResourceLimitError):
            NormalizedDocument(
                document_id="sha256:" + "0" * 64,
                title="T",
                sections=sections,
                warnings=(),
            )

    def test_snapshot_aggregate_bytes(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.models as models

        monkeypatch.setattr(models, "HARD_MAX_SNAPSHOT_RETAINED_BYTES", 32)
        with pytest.raises(ResourceLimitError):
            SnapshotManifest(
                snapshot_id="sha256:" + "5" * 64,
                parser_version="p",
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
                sources=(),
                chunks=(_chunk("a", text="x" * 16), _chunk("b", text="x" * 16)),
                created_at=datetime.now(UTC),
            )

    def test_context_totals_must_match_rendered_context(self) -> None:
        block = _evidence("alpha")
        with pytest.raises(ValidationError):
            ContextPackage(
                evidence=(block,),
                total_tokens=1,
                total_bytes=1,
            )

    def test_retrieval_result_aggregate_bytes(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.retrieval as retrieval

        monkeypatch.setattr(retrieval, "HARD_MAX_RESULT_RETAINED_BYTES", 32)
        with pytest.raises(ResourceLimitError):
            RetrievalSmokeRecord(
                query="q",
                expected_chunk_ids=("a" * 40,),
                expected_source_ids=(),
                observed_chunk_ids=(),
                observed_source_ids=(),
                passed=True,
                failures=(),
            )

    def test_vector_index_aggregate_stops_before_append(self) -> None:
        records = [
            record("a", text="x" * 40),
            record("b", text="y" * 40),
        ]
        with pytest.raises(ResourceLimitError):
            validate_vector_index(
                index_identity(),
                records,
                limits=KnowledgeLimits(max_index_retained_bytes=64),
            )


class TestStrictNumericTypes:
    def test_bool_and_wrong_numerics_rejected(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeLimits(max_top_k=True)
        with pytest.raises(ResourceLimitError):
            make_deadline(True)
        with pytest.raises(ResourceLimitError):
            make_deadline(1, hard_max=True)
        with pytest.raises(ResourceLimitError):
            digest_stream(_FixedStream(b"x"), chunk_size=True)
        with pytest.raises(ResourceLimitError):
            OllamaEmbeddingProvider(identity=identity(), timeout=True)
        with pytest.raises(ResourceLimitError):
            RetrievalService(
                provider=FakeProvider(identity()),
                index=FakeIndex(index_identity(), []),
                max_top_k=True,
            )
        with pytest.raises(ResourceLimitError):
            fit_context([], budget_tokens=True)
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=("a",),
                vectors=((True, 0.0),),
            )

    def test_intake_public_numeric_arguments_reject_bool(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        with pytest.raises(ResourceLimitError):
            prepare_source(doc, approved_root=root, max_bytes=True)
        with pytest.raises(ResourceLimitError):
            check_size(doc, max_bytes=True)

    def test_norm_overflow_rejected(self) -> None:
        huge = [1e308, 1e308]
        with pytest.raises(NonFiniteVectorError):
            normalize_l2(huge)
        with pytest.raises(NonFiniteVectorError):
            cosine_similarity(huge, huge)


class TestExactNumericAndDatetimeAudit:
    class EvilInt(int):
        def bit_length(self):
            raise RuntimeError("hostile bit_length")

    class EvilFloat(float):
        pass

    class EvilDatetime(datetime):
        @property
        def tzinfo(self):
            raise RuntimeError("hostile tzinfo")

    def test_subclass_numeric_hooks_do_not_leak(self) -> None:
        with pytest.raises(ResourceLimitError) as error:
            require_strict_number(self.EvilInt(1), label="x")
        assert "hostile bit_length" not in str(error.value)
        with pytest.raises(ResourceLimitError):
            require_strict_number(self.EvilFloat(1.0), label="x")

    def test_datetime_subclass_hooks_do_not_leak(self) -> None:
        with pytest.raises(ValidationError) as error:
            SnapshotManifest(
                snapshot_id="sha256:" + "0" * 64,
                parser_version="p",
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
                sources=(),
                chunks=(),
                created_at=self.EvilDatetime(2026, 8, 9, tzinfo=UTC),
            )
        assert "hostile tzinfo" not in str(error.value)


class TestUnionAggregateBudgetAudit:
    def test_union_revalidates_aggregate_item_budget(self) -> None:
        left = FrozenMetadata({f"a{index}": index for index in range(200)})
        right = FrozenMetadata({f"b{index}": index for index in range(200)})
        with pytest.raises(ValueError):
            left | right

    def test_union_revalidates_byte_and_depth_budgets(self) -> None:
        left = FrozenMetadata({f"a{index}": "x" for index in range(200)})
        right = FrozenMetadata({f"b{index}": "y" for index in range(200)})
        with pytest.raises(ValueError):
            left | right
        nested = FrozenMetadata({"n": {"m": 1}})
        deep = {"d": {"e": {"f": {"g": {"h": {"i": {"j": {"k": {"l": 1}}}}}}}}}
        with pytest.raises(ValueError):
            nested | deep


class TestTextEstimatorBoundaryAudit:
    def test_identity_must_be_exact_bounded_string(self) -> None:
        with pytest.raises(ValueError):
            TextEstimator(identity=object())
        with pytest.raises(ValueError):
            TextEstimator(identity="x" * 100_000)

    def test_direct_estimate_rejects_hostile_inputs(self) -> None:
        estimator = TextEstimator()
        with pytest.raises(ValueError):
            estimator.estimate(object())  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            estimator.estimate("€" * 2_000_000)


class TestIndexCompatibleAudit:
    def test_wrong_types_rejected_before_attribute_access(self) -> None:
        with pytest.raises(RetrievalError):
            index_compatible(
                object(),  # type: ignore[arg-type]
                expected_snapshot_digest="sha256:" + "0" * 64,
                parser_version="p",
                chunker_identity="c",
                chunk_config_digest="sha256:" + "1" * 64,
                embedding=identity(),
            )
        with pytest.raises(RetrievalError):
            index_compatible(
                index_identity(),
                expected_snapshot_digest=123,  # type: ignore[arg-type]
                parser_version="p",
                chunker_identity="c",
                chunk_config_digest="sha256:" + "1" * 64,
                embedding=identity(),
            )


class TestFromPlainAudit:
    def test_unsafe_constructor_calls_rejected(self) -> None:
        with pytest.raises(ValueError):
            FrozenMetadata._build_from_plain([1])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            FrozenMetadataList._build_from_plain([1])  # type: ignore[arg-type]
        with pytest.raises(ValueError):
            FrozenMetadata._from_plain({"bad": object()})
        with pytest.raises(ValueError):
            FrozenMetadataList._from_plain((object(),))
        with pytest.raises(ValueError):
            FrozenMetadata._from_plain_entries((("bad", object()),))
        with pytest.raises(ValueError):
            FrozenMetadataList._from_plain_entries((object(),))
        with pytest.raises(ValueError):
            FrozenMetadata._from_plain({"bad": object()})
        with pytest.raises(ValueError):
            FrozenMetadataList._from_plain((object(),))
        with pytest.raises(ValueError):
            FrozenMetadata._from_plain_entries((("bad", object()),))
        with pytest.raises(ValueError):
            FrozenMetadataList._from_plain_entries((object(),))


class TestIntakeHardening:
    def test_root_symlink_rejected(self, tmp_path: Path) -> None:
        real_root = tmp_path / "real"
        real_root.mkdir()
        link_root = tmp_path / "root-link"
        link_root.symlink_to(real_root, target_is_directory=True)
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([link_root])

    def test_duplicate_and_overlapping_roots_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([root, root])
        nested = root / "nested"
        nested.mkdir()
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([root, nested])

    def test_intake_copy_cannot_be_selected_again(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        metadata = prepare_source(doc, approved_root=root)
        copy = Path(metadata.extra["copy_path"])
        assert copy.exists()
        with pytest.raises(PathEscapeError):
            prepare_source(copy, approved_root=root)

    def test_completed_copy_is_read_only(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        metadata = prepare_source(doc, approved_root=root)
        mode = stat.S_IMODE(os.stat(metadata.extra["copy_path"]).st_mode)
        assert mode & 0o222 == 0

    def test_mutation_cleans_partial_copy(self, tmp_path: Path, monkeypatch) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("x" * 20, encoding="utf-8")
        real_fstat = intake.os.fstat
        calls = {"count": 0}

        def changed_fstat(fd):
            result = real_fstat(fd)
            calls["count"] += 1
            if calls["count"] == 2:
                result = result._replace(st_size=result.st_size + 1)
            return result

        monkeypatch.setattr(intake.os, "fstat", changed_fstat)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []

    def test_short_write_cleans_partial_copy(self, tmp_path: Path, monkeypatch) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("x" * 20, encoding="utf-8")

        def failing_write(fd, payload):  # noqa: ANN001
            raise UnreadableFileError("short write")

        monkeypatch.setattr(intake, "_write_all", failing_write)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []


class TestEndpointAndVectorBoundaries:
    def test_ipv6_canonical_origin(self) -> None:
        ipv6_identity = EmbeddingIdentity(
            provider="ollama",
            runtime_endpoint_identity="http://[::1]:11434",
            model_name="m",
            dimensions=2,
            normalization=NormalizationBehavior.L2,
            batch_size=1,
        )
        provider = OllamaEmbeddingProvider(
            identity=ipv6_identity,
            endpoint="http://[::1]:11434/",
        )
        assert provider.endpoint == "http://[::1]:11434"

    def test_malformed_endpoints_rejected(self) -> None:
        for endpoint in (
            "http://127.0.0.1:99999",
            r"http://127.0.0.1\:11434",
            "http://[::1",
        ):
            with pytest.raises(MissingIdentityError):
                OllamaEmbeddingProvider(identity=identity(), endpoint=endpoint)


class TestEstimatorAndBoundarySemantics:
    def test_normalize_source_rejects_unsupported_kinds(self) -> None:
        for kind in (DocumentKind.PDF, DocumentKind.UNSUPPORTED):
            with pytest.raises(NormalizationLimitError):
                normalize_source(
                    "text",
                    kind=kind,
                    title="t",
                    document_id="sha256:" + "0" * 64,
                )

    def test_text_estimator_words_per_token_is_honored(self) -> None:
        estimator = TextEstimator(words_per_token=2.0)
        assert estimator.estimate("a b c d") == 2
        assert estimator.estimate("a") == 1
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=0)
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=True)
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=float("nan"))

    def test_overlap_is_token_aligned_and_bounded(self) -> None:
        section = NormalizedSection(
            section_id="s1",
            heading_path=["A"],
            text="one two three four five six seven eight nine ten",
            start_offset=0,
            end_offset=49,
        )
        document = NormalizedDocument(
            document_id="sha256:" + "0" * 64,
            title="T",
            sections=(section,),
        )
        estimator = TextEstimator()
        chunker = HeadingAwareChunker(
            ChunkConfiguration(target_tokens=3, max_tokens=4, overlap_tokens=2),
            estimator=estimator,
        )
        chunks = chunker.chunk_document(document)
        assert len(chunks) > 1
        for chunk in chunks[1:]:
            assert chunk.overlap_prefix is not None
            prefix = chunk.overlap_prefix or ""
            assert prefix.endswith("ten") or prefix.split()[-1] in {
                "one",
                "two",
                "three",
                "four",
                "five",
                "six",
                "seven",
                "eight",
                "nine",
            }
            assert estimator.estimate(prefix) <= 2

    def test_snapshot_identity_strings_are_bounded(self) -> None:
        from zana_core.knowledge.snapshots import digest_invalidation_inputs

        with pytest.raises(ResourceLimitError):
            digest_invalidation_inputs(
                sources=[source_marker()],
                parser_version="p" * 100_000,
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
            )
        with pytest.raises(ResourceLimitError):
            digest_invalidation_inputs(
                sources=[source_marker()],
                parser_version="p",
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e" * 100_000,
            )

    def test_evidence_heading_join_is_bounded(self) -> None:
        long_chunk = Chunk(
            chunk_id="c",
            document_digest="sha256:" + "0" * 64,
            section_id="s1",
            heading_path=tuple(f"heading-{index}-" + "x" * 1_100 for index in range(16)),
            start_offset=0,
            end_offset=3,
            text="abc",
            token_estimate=1,
            tokenizer_identity="zana.text-estimator.v1",
        )
        from zana_core.knowledge.evidence import evidence_block

        with pytest.raises(ResourceLimitError):
            evidence_block(long_chunk)


class TestTightenedResourceBoundaries:
    def test_digest_stream_chunk_and_byte_caps(self) -> None:
        with pytest.raises(ResourceLimitError):
            digest_stream(_FixedStream(b"x"), chunk_size=HARD_MAX_STREAM_CHUNK_SIZE + 1)
        with pytest.raises(OversizeFileError):
            digest_stream(_FixedStream(b"x" * 10), max_bytes=5)
        digest = digest_stream(_FixedStream(b"hello"), max_bytes=10)
        assert digest.startswith("sha256:")

    def test_resolver_bounds_and_generic_errors(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([])
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([tmp_path / f"r{index}" for index in range(17)])
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([Path("/")])
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([Path.home()])
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([Path.cwd()])
        resolver = ApprovedPathResolver([root])
        assert len(resolver.root_paths) == 1
        with pytest.raises(PathEscapeError):
            resolver.resolve(root / ".." / "outside.md")

    def test_resolver_rejects_intermediate_symlink(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "doc.md").write_text("x", encoding="utf-8")
        linkdir = root / "linkdir"
        linkdir.symlink_to(outside, target_is_directory=True)
        resolver = ApprovedPathResolver([root])
        with pytest.raises(PathEscapeError):
            resolver.resolve(linkdir / "doc.md")

    def test_prepare_source_cleans_partial_copy_on_deadline(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("x" * 10, encoding="utf-8")
        original = intake.check_deadline
        calls = {"count": 0}

        def exploding(deadline, *, label="knowledge phase"):
            calls["count"] += 1
            if calls["count"] > 1:
                raise DeadlineExceededError("intake copy exceeded its total deadline.")
            original(deadline, label=label)

        monkeypatch.setattr(intake, "check_deadline", exploding)
        with pytest.raises(DeadlineExceededError):
            prepare_source(doc, approved_root=root, deadline_seconds=60)
        assert list((root / ".zana-intake-copy").glob("*")) == []

    def test_prepare_sources_passes_one_absolute_deadline(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        seen: list[float] = []

        def spy(path, *, approved_root, max_bytes, limits, deadline):  # noqa: ANN001
            seen.append(deadline)
            return source_marker()

        monkeypatch.setattr(intake, "_prepare_source_absolute", spy)
        list(prepare_sources([doc, doc], approved_root=root))
        assert len(seen) == 2
        assert seen[0] == seen[1]

    def test_parser_receives_shared_absolute_deadline(self) -> None:
        seen: list[float] = []

        class SpyParser:
            parser_version = "spy.v1"
            supported_kinds = frozenset({DocumentKind.MARKDOWN, DocumentKind.TEXT})

            def parse(
                self,
                source: SourceMetadata,
                *,
                deadline: float | None = None,
            ) -> NormalizedDocument:
                seen.append(deadline or 0.0)
                return NormalizedDocument(
                    document_id=source.sha256,
                    title=source.display_name,
                    sections=(),
                    warnings=(),
                )

        parse_sources(
            [source_marker(), source_marker()],
            SpyParser(),
            limits=KnowledgeLimits(max_source_count=2),
        )
        assert len(seen) == 2
        assert seen[0] == seen[1]

    def test_parser_deadline_exceeded_propagates(self) -> None:
        class ExpiredParser:
            parser_version = "expired.v1"
            supported_kinds = frozenset({DocumentKind.MARKDOWN, DocumentKind.TEXT})

            def parse(
                self,
                source: SourceMetadata,
                *,
                deadline: float | None = None,
            ) -> NormalizedDocument:
                raise DeadlineExceededError("parser exceeded its total deadline.")

        with pytest.raises(DeadlineExceededError):
            parse_sources([source_marker()], ExpiredParser())


class TestEndpointAndResponseBoundaries:
    def test_endpoint_path_and_dangling_port_rejected(self) -> None:
        with pytest.raises(MissingIdentityError):
            OllamaEmbeddingProvider(
                identity=identity(),
                endpoint="http://127.0.0.1:11434/api/embed",
            )
        with pytest.raises(MissingIdentityError):
            OllamaEmbeddingProvider(
                identity=identity(),
                endpoint="http://127.0.0.1:",
            )

    def test_endpoint_normalized_origin_must_match_identity(self) -> None:
        provider = OllamaEmbeddingProvider(
            identity=identity(),
            endpoint="http://127.0.0.1:11434/",
        )
        assert provider.endpoint == "http://127.0.0.1:11434"

    def test_oversized_response_rejected_as_generic_transport_error(self) -> None:
        class Transport:
            def request(self, method, url, *, headers=None, body=None, timeout):  # noqa: ANN001
                return self.response  # type: ignore[attr-defined]

        transport = Transport()
        transport.response = type(
            "Response",
            (),
            {
                "status": 200,
                "text": "x" * 5_000,
                "content_type": "application/json",
            },
        )()
        provider = OllamaEmbeddingProvider(
            identity=identity(),
            transport=transport,
            limits=KnowledgeLimits(
                max_request_bytes=4_096,
                max_batch_total_bytes=4_096,
            ),
        )
        with pytest.raises(TransportError) as error:
            provider.embed(["q"])
        assert "5_000" not in str(error.value)
        assert "http" not in str(error.value).lower()

    def test_retrieval_sequence_overflow_rejected_before_iteration(self) -> None:
        class OverIndex(FakeIndex):
            def search(self, vector, limit):  # noqa: ANN001
                return [(record(f"c{index}"), 1.0) for index in range(4)]

        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=OverIndex(index_identity(), []),
            limits=KnowledgeLimits(max_candidate_count=3, max_top_k=2),
        )
        with pytest.raises(HostileIndexError):
            service.search(RetrievalQuery(text="q", top_k=2))


class TestAggregateAndImmutabilityGuards:
    def test_embedding_batch_cardinality_and_finite_guards(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=["a", "b"],
                vectors=[[1.0, 0.0]],
            )
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=["a"],
                vectors=[[float("nan"), 0.0]],
            )
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=["a"],
                vectors=[[0.0] * 9_000],
            )

    def test_context_package_total_bytes_cannot_exceed_hard_cap(self) -> None:
        with pytest.raises(ValidationError):
            ContextPackage(total_tokens=1, total_bytes=8 * 1024 * 1024 + 1)

    def test_fit_context_enforces_byte_budget(self) -> None:
        blocks = [_evidence("x" * 200)]
        package = fit_context(
            blocks,
            budget_tokens=10_000,
            limits=KnowledgeLimits(
                max_context_bytes=100,
                max_evidence_tokens=10_000,
            ),
        )
        assert package.total_bytes <= 100
        assert package.total_bytes == 0

    def test_durable_list_fields_are_tuples(self) -> None:
        section = NormalizedSection(
            section_id="s1",
            heading_path=["A"],
            text="body",
            start_offset=0,
            end_offset=4,
        )
        document = NormalizedDocument(
            document_id="sha256:" + "0" * 64,
            title="T",
            sections=[section],
            warnings=[],
        )
        hit = RetrievalHit(
            chunk_id="c",
            document_digest="sha256:" + "1" * 64,
            source_title="t",
            heading_path=["A"],
            text="x",
            score=0.5,
            rank=1,
        )
        assert isinstance(document.sections, tuple)
        assert isinstance(document.warnings, tuple)
        assert isinstance(section.heading_path, tuple)
        assert isinstance(hit.heading_path, tuple)
        with pytest.raises(TypeError):
            document.sections[0].heading_path[0] = "B"  # type: ignore[index]


class TestStrictCoercionAudit:
    def test_text_estimator_rejects_string_float_and_huge_int(self) -> None:
        with pytest.raises(ValueError):
            TextEstimator(words_per_token="1")
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=10**400)
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=True)
        with pytest.raises(ValueError):
            TextEstimator(words_per_token=float("nan"))

    def test_estimate_word_count_rejects_invalid_inputs(self) -> None:
        estimator = TextEstimator()
        with pytest.raises(ValueError):
            estimator.estimate_word_count(True)
        with pytest.raises(ValueError):
            estimator.estimate_word_count("1")
        with pytest.raises(ValueError):
            estimator.estimate_word_count(-1)
        assert estimator.estimate_word_count(0) == 1

    def test_fit_context_rejects_float_and_bool_budgets(self) -> None:
        with pytest.raises(ResourceLimitError):
            fit_context([], budget_tokens=10.5)
        with pytest.raises(ResourceLimitError):
            fit_context([], budget_tokens=True)

    def test_public_boolean_fields_reject_coercion(self) -> None:
        with pytest.raises(ValidationError):
            ParserError(code="c", message="m", recoverable="true")
        with pytest.raises(ValidationError):
            SourceMetadata(
                original_path="/approved/doc.md",
                display_name="doc.md",
                kind=DocumentKind.MARKDOWN,
                size_bytes=1,
                sha256="sha256:" + "0" * 64,
                approved=0,
            )
        with pytest.raises(ValidationError):
            ContextPackage(evidence=(), total_tokens=0, total_bytes=0, fitted=1)
        with pytest.raises(ValidationError):
            RetrievalSmokeRecord(
                query="q",
                expected_chunk_ids=(),
                expected_source_ids=(),
                observed_chunk_ids=(),
                observed_source_ids=(),
                passed="true",
                failures=(),
            )

    def test_huge_int_float_conversion_rejected(self) -> None:
        with pytest.raises(ResourceLimitError):
            require_strict_number(10**400, label="Huge int")
        with pytest.raises(NonFiniteVectorError):
            normalize_l2([10**400, 1.0])

    def test_datetime_requires_timezone_and_bounds(self) -> None:
        from datetime import datetime

        with pytest.raises(ValidationError):
            SnapshotManifest(
                snapshot_id="sha256:" + "0" * 64,
                parser_version="p",
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
                sources=(),
                chunks=(),
                created_at=datetime(2026, 8, 9),
            )
        with pytest.raises(ValidationError):
            SnapshotManifest(
                snapshot_id="sha256:" + "0" * 64,
                parser_version="p",
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
                sources=(),
                chunks=(),
                created_at=datetime(1999, 1, 1, tzinfo=UTC),
            )


class TestClockAudit:
    def test_safe_monotonic_rejects_invalid_clock(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.limits as limits

        monkeypatch.setattr(limits.time, "monotonic", lambda: float("nan"))
        with pytest.raises(ResourceLimitError):
            safe_monotonic()

    def test_check_deadline_rejects_wrong_types(self) -> None:
        from zana_core.knowledge.limits import check_deadline, remaining_seconds

        with pytest.raises(ResourceLimitError):
            check_deadline(True)
        with pytest.raises(ResourceLimitError):
            check_deadline("1")
        with pytest.raises(ResourceLimitError):
            remaining_seconds(float("nan"))

    def test_deadline_hard_max_validated_before_use(self) -> None:
        with pytest.raises(ResourceLimitError):
            make_deadline(None, hard_max=True)
        with pytest.raises(ResourceLimitError):
            make_deadline(None, hard_max=10**400)
        with pytest.raises(ResourceLimitError):
            make_deadline(10**400)


class TestHostileContainersAudit:
    def test_metadata_rejects_dict_and_list_subclasses(self) -> None:
        class HostileDict(dict):
            pass

        class HostileList(list):
            pass

        with pytest.raises(ValueError):
            validate_bounded_metadata(HostileDict({"a": 1}))
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": HostileList([1])})

    def test_metadata_retained_byte_budget(self) -> None:
        limits = KnowledgeLimits(
            max_metadata_retained_bytes=10,
            max_metadata_items=4,
        )
        with pytest.raises(ValueError):
            validate_bounded_metadata({"a": "x" * 12}, limits=limits)

    def test_bounded_collection_inputs_do_not_trust_sequence_len(self) -> None:
        class LyingSequence(list):
            def __len__(self) -> int:
                raise AssertionError("hostile __len__ must not be called")

        records = LyingSequence([record("a"), record("b")])
        validated = validate_vector_index(
            index_identity(),
            records,
            limits=KnowledgeLimits(max_index_records=3),
        )
        assert [item.chunk_id for item in validated] == ["a", "b"]
        documents, errors = parse_sources(
            LyingSequence([source_marker(), source_marker()]),
            MarkdownParser(),
            limits=KnowledgeLimits(max_source_count=3),
        )
        assert len(documents) == 2
        assert errors == []

    def test_resolver_caps_hostile_iteration(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()

        def many() -> Iterator[Path]:
            while True:
                yield root

        with pytest.raises(PathEscapeError):
            ApprovedPathResolver(many())


class TestMetadataImmutabilityAudit:
    def test_durable_metadata_is_deeply_immutable(self) -> None:
        chunk = _chunk("c", text="abc")
        with pytest.raises(TypeError):
            chunk.metadata_json["x"] = 1
        source = SourceMetadata(
            original_path="/approved/doc.md",
            display_name="doc.md",
            kind=DocumentKind.MARKDOWN,
            size_bytes=1,
            sha256="sha256:" + "0" * 64,
            extra={"nested": {"value": 1}},
        )
        with pytest.raises(TypeError):
            source.extra["nested"]["value"] = 2
        with pytest.raises(TypeError):
            source.extra["new"] = 1

    def test_budget_helpers_validate_inputs_and_overflow(self) -> None:
        with pytest.raises(ResourceLimitError):
            RetainedByteBudget(True)
        with pytest.raises(ResourceLimitError):
            RetainedByteBudget(10).add("x", max_bytes=True)
        budget = RetainedByteBudget(4)
        budget.add("ab")
        with pytest.raises(ResourceLimitError):
            budget.add("abcd")
        with pytest.raises(ResourceLimitError):
            VectorBudget(max_cells=True, max_bytes=100)
        with pytest.raises(ResourceLimitError):
            VectorBudget(max_cells=2, max_bytes=2).add([1.0, 2.0], dimensions=2)
        vector_budget = VectorBudget(max_cells=2, max_bytes=100)
        with pytest.raises(ResourceLimitError):
            vector_budget.add([1.0, 2.0, 3.0], dimensions=3)
        assert vector_budget.cells == 0


class TestIdentityKeyAudit:
    def test_identity_key_is_delimiter_independent(self) -> None:
        left = EmbeddingIdentity(
            provider="a:b",
            runtime_endpoint_identity="http://127.0.0.1:11434",
            model_name="c",
            dimensions=2,
            normalization=NormalizationBehavior.L2,
            batch_size=1,
        )
        right = EmbeddingIdentity(
            provider="a",
            runtime_endpoint_identity="http://127.0.0.1:11434",
            model_name="b:c",
            dimensions=2,
            normalization=NormalizationBehavior.L2,
            batch_size=1,
        )
        assert left.identity_key() != right.identity_key()

    def test_identity_key_is_canonical_digest(self) -> None:
        key = identity().identity_key()
        assert len(key) == 7 + 64
        assert key.startswith("sha256:")


class TestIntakeWorkspaceAudit:
    def test_workspace_symlink_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (root / ".zana-intake-copy").symlink_to(outside, target_is_directory=True)
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list(outside.iterdir()) == []

    def test_workspace_non_private_mode_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        workspace = root / ".zana-intake-copy"
        workspace.mkdir(mode=0o755)
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)

    def test_same_size_mutation_cleans_copy(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("xxxx", encoding="utf-8")
        real_fstat = intake.os.fstat
        calls = {"count": 0}

        def changed_fstat(fd):
            result = real_fstat(fd)
            calls["count"] += 1
            if calls["count"] == 2:
                result = result._replace(st_mtime_ns=result.st_mtime_ns + 1)
            return result

        monkeypatch.setattr(intake.os, "fstat", changed_fstat)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []

    def test_metadata_failure_cleans_copy(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def exploding_metadata(*args, **kwargs):  # noqa: ANN001
            raise ValueError("metadata rejected")

        monkeypatch.setattr(intake, "SourceMetadata", exploding_metadata)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []

    def test_fsync_failure_cleans_copy(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        real_fsync = intake.os.fsync

        def failing_fsync(fd):  # noqa: ANN001
            if isinstance(fd, int):
                raise OSError("fsync failed")
            return real_fsync(fd)

        monkeypatch.setattr(intake.os, "fsync", failing_fsync)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []

    def test_pdf_rejected_before_copy(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.pdf"
        doc.write_bytes(b"%PDF-1.4")
        with pytest.raises(UnsupportedTypeError):
            prepare_source(doc, approved_root=root)
        assert not (root / ".zana-intake-copy").exists()

    def test_to_parser_error_uses_generic_messages(self) -> None:
        from zana_core.knowledge.intake import to_parser_error

        error = to_parser_error(UnsupportedTypeError("secret path"))
        assert "secret path" not in error.message


class TestProviderBoundaryAudit:
    def test_provider_wrong_result_type_maps_to_retrieval_error(self) -> None:
        class BadProvider:
            def embed(self, texts):
                return "not-a-batch"

        service = RetrievalService(
            provider=BadProvider(),
            index=FakeIndex(index_identity(), [record("a")]),
        )
        with pytest.raises(RetrievalError):
            service.search(RetrievalQuery(text="q"))

    def test_index_wrong_identity_type_maps_to_corruption_error(self) -> None:
        class BadIndex:
            identity = "not-an-identity"

            def search(self, vector, limit):  # noqa: ANN001
                return []

            def close(self) -> None:
                return

        with pytest.raises(RetrievalError):
            RetrievalService(
                provider=FakeProvider(identity()),
                index=BadIndex(),
            )

    def test_bool_score_rejected(self) -> None:
        class BoolIndex(FakeIndex):
            def search(self, vector, limit):  # noqa: ANN001
                return [(record("a"), True)]

        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=BoolIndex(index_identity(), []),
        )
        with pytest.raises(HostileIndexError):
            service.search(RetrievalQuery(text="q"))

    def test_vector_list_subclass_rejected(self) -> None:
        class VectorList(list):
            pass

        with pytest.raises(EmptyVectorError):
            normalize_l2(VectorList([1.0, 0.0]))

    def test_request_object_caps_direct_construction(self) -> None:
        with pytest.raises(ResourceLimitError):
            OllamaEmbedRequest(
                model="m",
                input=("x" * 100,),
                limits=KnowledgeLimits(
                    max_text_bytes=16,
                    max_query_bytes=16,
                    max_chunk_text_bytes=16,
                ),
            )
        with pytest.raises(ResourceLimitError):
            OllamaEmbedRequest(
                model="m",
                input=("a", "b", "c"),
                limits=KnowledgeLimits(max_batch_text_count=2),
            )

    def test_parser_wrong_return_type_is_generic_error(self) -> None:
        class BadParser:
            parser_version = "bad.v1"
            supported_kinds = frozenset({DocumentKind.MARKDOWN, DocumentKind.TEXT})

            def parse(self, source, *, deadline=None):  # noqa: ANN001
                return "not-a-document"

        documents, errors = parse_sources([source_marker()], BadParser())
        assert documents == []
        assert len(errors) == 1
        assert errors[0].code == "PARSE_FAILED"


class TestRetrievalFunctionalAudit:
    def test_min_score_is_applied_before_dedup_and_rank(self) -> None:
        class ScoredIndex(FakeIndex):
            def __init__(self, index_identity_, records, scores):  # noqa: ANN001
                super().__init__(index_identity_, records)
                self.scores = scores

            def search(self, vector, limit):  # noqa: ANN001
                return list(zip(self.records, self.scores, strict=True))[:limit]

        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=ScoredIndex(
                index_identity(),
                [record("a"), record("b"), record("c")],
                [0.9, 0.4, 0.7],
            ),
        )
        result = service.search(RetrievalQuery(text="q", top_k=3, min_score=0.6))
        assert [hit.chunk_id for hit in result.hits] == ["a", "c"]
        assert [hit.rank for hit in result.hits] == [1, 2]


class TestEvidenceInjectionAudit:
    def test_render_escapes_control_and_closing_marker(self) -> None:
        block = _evidence("drop \n[/Source " + "0" * 12 + "] [x]")
        rendered = render_evidence_block(block)
        closing = f"[/Source {block.source_id[:12]}]"
        assert rendered.count(closing) == 1
        assert "\\[x\\]" in rendered
        assert "\\[/Source" in rendered
        assert "[/Source 000000000000]" not in rendered

    def test_render_escapes_source_title(self) -> None:
        block = EvidenceBlock(
            source_id="sha256:" + "0" * 64,
            source_title="Title\n[/Source x]",
            text="body",
            token_estimate=1,
        )
        rendered = render_evidence_block(block)
        assert "\\x0a" in rendered
        closing = f"[/Source {block.source_id[:12]}]"
        assert rendered.count(closing) == 1


class TestRemainingSecondsAudit:
    def test_before_equal_and_after_deadline(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.limits as limits

        monkeypatch.setattr(limits, "safe_monotonic", lambda: 100.0)
        assert remaining_seconds(110.0) == 10.0
        assert remaining_seconds(100.0) == 0.0
        assert remaining_seconds(90.0) == 0.0

    def test_hostile_clock_and_inputs(self, monkeypatch) -> None:  # noqa: ANN001
        import zana_core.knowledge.limits as limits

        monkeypatch.setattr(limits, "safe_monotonic", lambda: float("nan"))
        with pytest.raises(ResourceLimitError):
            remaining_seconds(100.0)
        monkeypatch.setattr(limits, "safe_monotonic", lambda: 100.0)
        with pytest.raises(ResourceLimitError):
            remaining_seconds(True)
        with pytest.raises(ResourceLimitError):
            remaining_seconds(float("nan"))
        with pytest.raises(ResourceLimitError):
            check_deadline("1")


class TestFrozenGraphMutationAudit:
    def test_ior_or_iadd_imul_and_base_calls_rejected(self) -> None:
        metadata = FrozenMetadata({"a": {"b": [1, 2]}})
        with pytest.raises(TypeError):
            metadata.__ior__({"x": 1})
        merged = metadata | {"x": 1}
        assert "x" not in metadata
        assert merged["x"] == 1
        with pytest.raises(TypeError):
            metadata["a"]["b"].__iadd__([3])
        with pytest.raises(TypeError):
            metadata["a"]["b"].__imul__(2)
        with pytest.raises(TypeError):
            dict.__setitem__(metadata, "x", 1)
        with pytest.raises(TypeError):
            list.append(metadata["a"]["b"], 3)

    def test_nested_alias_mutation_impossible(self) -> None:
        metadata = FrozenMetadata({"a": {"b": [1]}})
        nested = metadata["a"]
        with pytest.raises((AttributeError, TypeError)):
            nested.__setitem__("b", [2])
        with pytest.raises((AttributeError, TypeError)):
            metadata["a"]["b"].__setitem__(0, 9)
        assert metadata["a"]["b"][0] == 1

    def test_frozen_list_sequences_are_tuple_backed(self) -> None:
        values = FrozenMetadataList([1, {"x": 2}, [3]])
        assert len(values) == 3
        with pytest.raises(TypeError):
            values.__iadd__([4])
        with pytest.raises(TypeError):
            values.__imul__(2)
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(values, "_data", (object(),))
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(values, "_entries", (object(),))
        assert list(values)[1]["x"] == 2

    def test_list_equality_fails_closed_on_hostile_elements(self) -> None:
        class Hostile:
            def __eq__(self, other):
                raise AssertionError("hostile eq called")

            def __hash__(self):
                raise AssertionError("hostile hash called")

        values = FrozenMetadataList([1])
        assert (values == [Hostile()]) is False
        assert (values == (Hostile(),)) is False


class TestHostileModelInputAudit:
    class HostileDict(dict):
        def items(self):
            raise AssertionError("hostile items called")

        def __iter__(self):
            raise AssertionError("hostile iter called")

        def __len__(self):
            raise AssertionError("hostile len called")

        def __str__(self):
            raise AssertionError("hostile str called")

    class HostileList(list):
        def __iter__(self):
            raise AssertionError("hostile iter called")

        def __len__(self):
            raise AssertionError("hostile len called")

    def test_hostile_metadata_through_durable_models_rejected(self) -> None:
        base = {
            "original_path": "/approved/doc.md",
            "display_name": "doc.md",
            "kind": DocumentKind.MARKDOWN,
            "size_bytes": 1,
            "sha256": "sha256:" + "0" * 64,
            "approved": True,
        }
        with pytest.raises(ValidationError):
            SourceMetadata(**base, extra=self.HostileDict({"a": 1}))
        chunk = _chunk("c")
        with pytest.raises(ValidationError):
            Chunk.model_validate(
                {
                    **chunk.model_dump(),
                    "metadata_json": self.HostileDict({"a": 1}),
                }
            )
        with pytest.raises(ValidationError):
            VectorRecord(
                chunk_id="c",
                document_digest="sha256:" + "1" * 64,
                heading_path=self.HostileList(["A"]),
                vector=[1.0, 0.0],
            )

    def test_hostile_batch_sequences_rejected_before_materialization(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=self.HostileList(["a"]),
                vectors=[[1.0, 0.0]],
            )
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=["a"],
                vectors=self.HostileList([[1.0, 0.0]]),
            )

    def test_hostile_identity_key_mapping_rejected(self) -> None:
        with pytest.raises(ValueError):
            canonical_identity_key(parts=self.HostileDict({"a": 1}))
        with pytest.raises(ValueError):
            canonical_identity_key(parts={"a": 10**400})
        with pytest.raises(ValueError):
            canonical_identity_key(parts={"a": float("nan")})
        key = canonical_identity_key(parts={"a": "b", "c": 1})
        assert key.startswith("sha256:")


class TestPathSubclassAudit:
    class HostileStr(str):
        def __str__(self) -> str:
            raise AssertionError("hostile str called")

    class HostilePath(Path):
        pass

    def test_str_and_path_subclasses_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([self.HostileStr(str(root))])
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver([self.HostilePath(root)])
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        with pytest.raises(PathEscapeError):
            prepare_source(self.HostilePath(doc), approved_root=root)

    def test_arbitrary_roots_iterable_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()

        def many() -> Iterator[Path]:
            while True:
                yield root

        with pytest.raises(PathEscapeError):
            ApprovedPathResolver(many())
        with pytest.raises(PathEscapeError):
            ApprovedPathResolver({"not-a-sequence": root})


class TestRootWorkspaceSwapAudit:
    def test_root_open_failure_cleans_and_does_not_create_workspace(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def fail_open(*args, **kwargs):  # noqa: ANN001
            raise UnreadableFileError("root identity changed")

        monkeypatch.setattr(intake, "_open_root_fd", fail_open)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert not (root / ".zana-intake-copy").exists()

    def test_workspace_prepare_failure_cleans(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def fail_workspace(*args, **kwargs):  # noqa: ANN001
            raise UnreadableFileError("workspace validation failed")

        monkeypatch.setattr(intake, "_prepare_workspace", fail_workspace)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert not (root / ".zana-intake-copy").exists()

    def test_root_identity_mismatch_after_resolve(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def mismatched(*args, **kwargs):  # noqa: ANN001
            raise UnreadableFileError("approved root identity mismatch")

        monkeypatch.setattr(intake, "_open_root_fd", mismatched)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert not (root / ".zana-intake-copy").exists()


class TestChunkerConstructorAudit:
    def test_hostile_constructor_types_rejected(self) -> None:
        with pytest.raises(ChunkLimitError):
            HeadingAwareChunker(config="bad")
        with pytest.raises(ChunkLimitError):
            HeadingAwareChunker(estimator=object())
        with pytest.raises(ChunkLimitError):
            HeadingAwareChunker(limits=object())

    def test_public_methods_validate_exact_types(self) -> None:
        chunker = HeadingAwareChunker()
        with pytest.raises(ChunkLimitError):
            chunker.chunk_document(object())  # type: ignore[arg-type]
        with pytest.raises(ChunkLimitError):
            chunker.chunk_section("digest", object())  # type: ignore[arg-type]
        with pytest.raises(ChunkLimitError):
            chunker.chunk_section(123, _section())
        with pytest.raises(ChunkLimitError):
            estimate_tokens(123)  # type: ignore[arg-type]
        with pytest.raises(ChunkLimitError):
            estimate_tokens("x", estimator=object())


class TestLazyIterationAudit:
    class ExplodingProvider:
        def embed(self, texts):
            raise RuntimeError("provider exploded")

    class ExplodingIndex:
        def __init__(self, index_identity_: IndexIdentity) -> None:
            self.identity = index_identity_

        def search(self, vector, limit):  # noqa: ANN001
            def iterate():
                yield (record("a"), 1.0)
                raise RuntimeError("index iterator exploded")

            return iterate()

        def close(self) -> None:
            return

    def test_provider_failure_maps_to_generic_retrieval_error(self) -> None:
        service = RetrievalService(
            provider=self.ExplodingProvider(),
            index=FakeIndex(index_identity(), []),
        )
        with pytest.raises(RetrievalError):
            service.search(RetrievalQuery(text="q"))

    def test_lazy_candidate_iteration_failure_maps_to_generic_error(self) -> None:
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=self.ExplodingIndex(index_identity()),
        )
        with pytest.raises(RetrievalError):
            service.search(RetrievalQuery(text="q"))

    def test_smoke_iteration_failure_maps_to_generic_error(self) -> None:
        service = RetrievalService(
            provider=FakeProvider(identity()),
            index=FakeIndex(index_identity(), []),
        )

        def exploding() -> Iterator[str]:
            yield "a"
            raise RuntimeError("hostile iteration")

        with pytest.raises(ResourceLimitError):
            service.smoke_test(
                query_text="q",
                expected_chunk_ids=exploding(),
                expected_source_ids=[],
            )


class TestDigestStreamAudit:
    class BadRead:
        def read(self, size: int) -> str:
            return "not-bytes"

    class RaisingRead:
        def read(self, size: int) -> bytes:
            raise OSError("stream exploded")

    def test_non_bytes_reads_rejected_generically(self) -> None:
        with pytest.raises(UnreadableFileError) as error:
            digest_stream(self.BadRead())
        assert "not-bytes" not in str(error.value)

    def test_raising_reads_rejected_generically(self) -> None:
        with pytest.raises(UnreadableFileError) as error:
            digest_stream(self.RaisingRead())
        assert "stream exploded" not in str(error.value)


class TestModelNameAndLimitsAudit:
    def test_request_requires_exact_limits(self) -> None:
        with pytest.raises(ResourceLimitError):
            OllamaEmbedRequest(model="m", input=("a",), limits=None)
        with pytest.raises(ResourceLimitError):
            OllamaEmbedRequest(model="m", input=("a",), limits="bad")

    def test_control_and_whitespace_model_names_rejected(self) -> None:
        for name in ("bad\nname", "bad\tname", "bad name"):
            with pytest.raises(ResourceLimitError):
                OllamaEmbedRequest(
                    model=name,
                    input=("a",),
                    limits=KnowledgeLimits(),
                )


class TestCheckHelperSymlinkAudit:
    def test_check_helpers_reject_symlink_ancestors(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "doc.md").write_text("ok", encoding="utf-8")
        linkdir = root / "linkdir"
        linkdir.symlink_to(outside, target_is_directory=True)
        target = linkdir / "doc.md"
        with pytest.raises(UnreadableFileError):
            check_size(target, max_bytes=100)
        with pytest.raises(UnreadableFileError):
            _check_readable_public(target)


class TestCheckHelperRaceAudit:
    def test_ancestor_rename_and_symlink_race_proves_no_outside_open(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        outside = tmp_path / "outside.md"
        outside.write_text("outside", encoding="utf-8")
        real_open = intake.os.open

        def swapped_open(*args, **kwargs):
            if "doc.md" in str(args):
                raise UnreadableFileError("outside file was not opened")
            return real_open(*args, **kwargs)

        monkeypatch.setattr(intake.os, "open", swapped_open)
        # Component-walk opens never pass a full path containing the leaf,
        # so a hostile path open would be detected immediately.
        with pytest.raises(UnreadableFileError):
            check_size(doc, max_bytes=100)


def _check_readable_public(path: object) -> None:
    from zana_core.knowledge import intake

    intake.check_readable(path)


class TestBackingStorageAndGrammarAudit:
    def test_backing_storage_cannot_be_mutated(self) -> None:
        metadata = FrozenMetadata({"a": 1})
        with pytest.raises((AttributeError, TypeError)):
            metadata._data["mutated"] = 2  # type: ignore[attr-defined]
        with pytest.raises((AttributeError, TypeError)):
            metadata._entries = ()
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(metadata, "_data", (("bad", object()),))
        with pytest.raises((AttributeError, TypeError)):
            object.__setattr__(metadata, "_entries", (("bad", object()),))
        assert dict(metadata) == {"a": 1}

    def test_arbitrary_objects_rejected_in_all_construction_paths(self) -> None:
        with pytest.raises(ValueError):
            FrozenMetadata({"bad": object()})
        with pytest.raises(ValueError):
            FrozenMetadataList([object()])
        with pytest.raises(ValueError):
            FrozenMetadata({"a": {"b": object()}})
        with pytest.raises(ValueError):
            FrozenMetadataList([{"bad": object()}])

    def test_mutable_and_custom_subclass_values_rejected(self) -> None:
        class CustomList(list):
            pass

        class CustomDict(dict):
            pass

        with pytest.raises(ValueError):
            FrozenMetadata({"a": CustomList([1])})
        with pytest.raises(ValueError):
            FrozenMetadataList([CustomDict({"a": 1})])

    def test_cycles_and_oversize_depth_rejected(self) -> None:
        cyclic: dict[str, object] = {}
        cyclic["self"] = cyclic
        with pytest.raises(ValueError):
            FrozenMetadata(cyclic)
        node: dict[str, object] = {"leaf": 1}
        for _ in range(12):
            node = {"next": node}
        with pytest.raises(ValueError):
            FrozenMetadata(node)

    def test_union_cannot_bypass_validation(self) -> None:
        metadata = FrozenMetadata({"a": 1})
        with pytest.raises(ValueError):
            metadata | {"bad": object()}
        with pytest.raises((TypeError, ValueError)):
            metadata | self.HostileDict({"x": 1})

    class HostileDict(dict):
        def items(self):
            raise AssertionError("hostile items called")

    def test_deep_immutability_through_views(self) -> None:
        metadata = FrozenMetadata({"a": {"b": [1, 2]}})
        with pytest.raises(TypeError):
            metadata["a"]["b"].__iadd__([3])
        with pytest.raises(TypeError):
            metadata["a"]["b"].__imul__(2)
        plain = dict(metadata)
        assert isinstance(plain["a"]["b"], FrozenMetadataList)
        with pytest.raises(TypeError):
            plain["a"]["b"].append(3)
        assert metadata["a"]["b"] == [1, 2]


class TestWrongLimitsAudit:
    def test_optional_limits_reject_truthy_lookalikes(self) -> None:
        with pytest.raises(ResourceLimitError):
            fit_context([], budget_tokens=10, limits={})
        with pytest.raises(ResourceLimitError):
            parse_sources([], MarkdownParser(), limits=1)
        with pytest.raises(ResourceLimitError):
            normalize_text("x", limits=object())
        with pytest.raises(ResourceLimitError):
            HeadingAwareChunker(limits={})
        with pytest.raises(ResourceLimitError):
            RetrievalService(
                provider=FakeProvider(identity()),
                index=FakeIndex(index_identity(), []),
                limits={},
            )
        with pytest.raises(ResourceLimitError):
            OllamaEmbeddingProvider(identity=identity(), limits={})
        with pytest.raises(ResourceLimitError):
            validate_vector_index(index_identity(), [], limits={})
        with pytest.raises(ResourceLimitError):
            build_snapshot_manifest(
                sources=[],
                chunks=[],
                chunk_config=ChunkConfiguration(),
                embedding_identity_required="e",
                limits={},
            )


class TestSafePrimitiveAudit:
    def test_missing_required_flag_fails_closed(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")
        monkeypatch.delattr(intake.os, "O_NOFOLLOW")
        with pytest.raises(UnreadableFileError) as error:
            prepare_source(doc, approved_root=root)
        assert "O_NOFOLLOW" in str(error.value)
        assert not (root / ".zana-intake-copy").exists()

    def test_component_walk_failure_cleans(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from zana_core.knowledge import intake

        root = tmp_path / "approved"
        root.mkdir()
        doc = root / "doc.md"
        doc.write_text("ok", encoding="utf-8")

        def swapped(*args, **kwargs):  # noqa: ANN001
            raise UnreadableFileError("source component swapped")

        monkeypatch.setattr(intake, "_open_relative_verified", swapped)
        with pytest.raises(UnreadableFileError):
            prepare_source(doc, approved_root=root)
        assert list((root / ".zana-intake-copy").glob("*")) == []


class TestPrepareSourcesHostileAudit:
    class ExplodingIterator:
        def __iter__(self):
            return self

        def __next__(self):
            raise RuntimeError("hostile next")

    def test_iterator_creation_failure_maps_to_generic_error(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()

        class ExplodingIterable:
            def __iter__(self):
                raise RuntimeError("hostile iter")

        with pytest.raises(UnreadableFileError) as error:
            list(prepare_sources(ExplodingIterable(), approved_root=root))
        assert "hostile iter" not in str(error.value)

    def test_next_failure_maps_to_generic_error(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        with pytest.raises(UnreadableFileError) as error:
            list(prepare_sources(self.ExplodingIterator(), approved_root=root))
        assert "hostile next" not in str(error.value)


class TestDetectKindAudit:
    def test_hostile_and_non_exact_inputs_rejected(self) -> None:
        with pytest.raises(UnsupportedTypeError):
            detect_kind(object())  # type: ignore[arg-type]
        with pytest.raises(UnsupportedTypeError):
            detect_kind("doc.md")
        assert detect_kind(Path("doc.md")) is DocumentKind.MARKDOWN


class TestCanonicalDigestChunkAudit:
    def test_chunker_rejects_non_canonical_document_digest(self) -> None:
        section = _section()
        document = NormalizedDocument(
            document_id="not-a-digest",
            title="T",
            sections=(section,),
        )
        with pytest.raises(ChunkLimitError):
            HeadingAwareChunker().chunk_document(document)
        with pytest.raises(ChunkLimitError):
            HeadingAwareChunker().chunk_section("not-a-digest", section)


class TestTransportBoundaryAudit:
    class HostileTransport:
        @property
        def request(self):
            raise RuntimeError("hostile request property")

    def test_hostile_transport_attribute_maps_to_generic_error(self) -> None:
        with pytest.raises(TransportError) as error:
            OllamaEmbeddingProvider(
                identity=identity(),
                transport=self.HostileTransport(),
            )
        assert "hostile request property" not in str(error.value)


def _section() -> NormalizedSection:
    return NormalizedSection(
        section_id="s1",
        heading_path=["A"],
        text="body",
        start_offset=0,
        end_offset=4,
    )


class _FixedStream:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, size: int) -> bytes:
        if not self.data:
            return b""
        chunk = self.data[:size]
        self.data = self.data[size:]
        return chunk


def _evidence(text: str) -> EvidenceBlock:
    return EvidenceBlock(
        source_id="sha256:" + "0" * 64,
        source_title="T",
        text=text,
        token_estimate=1,
    )


def _chunk(chunk_id: str, *, text: str = "abc") -> Chunk:
    return Chunk(
        chunk_id=chunk_id,
        document_digest="sha256:" + "0" * 64,
        section_id="s1",
        start_offset=0,
        end_offset=len(text),
        text=text,
        token_estimate=1,
        tokenizer_identity="zana.text-estimator.v1",
    )
