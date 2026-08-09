"""Embedding identity, normalization, cosine, and Ollama adapter tests."""

from __future__ import annotations

import math
from collections.abc import Mapping

import pytest
from pydantic import ValidationError

from zana_core.knowledge.embeddings import (
    BackendUnavailableError,
    DimensionMismatchError,
    DuplicateChunkIdError,
    EmbeddingBatch,
    EmbeddingIdentity,
    EmptyVectorError,
    IndexIdentity,
    MixedIdentityError,
    NormalizationBehavior,
    NormalizationMismatchError,
    OllamaEmbeddingProvider,
    ResourceLimitError,
    TransportError,
    VectorRecord,
    cosine_similarity,
    normalize_l2,
    validate_embedding_batch,
    validate_vector_index,
)
from zana_core.knowledge.limits import KnowledgeLimits
from zana_core.runtimes.base import HttpResponse


def identity(dimensions: int = 3) -> EmbeddingIdentity:
    return EmbeddingIdentity(
        provider="ollama",
        runtime_endpoint_identity="http://127.0.0.1:11434",
        model_name="nomic-embed-text",
        model_digest="sha256:" + "2" * 64,
        dimensions=dimensions,
        normalization=NormalizationBehavior.L2,
        batch_size=8,
    )


def l2(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(value * value for value in values))
    return [value / norm for value in values]


class FakeTransport:
    def __init__(self, routes: Mapping[tuple[str, str], HttpResponse] | None = None) -> None:
        self.routes = dict(routes or {})
        self.calls: list[tuple[str, str, bytes | None]] = []

    def request(
        self,
        method: str,
        url: str,
        *,
        headers=None,  # noqa: ANN001
        body: bytes | None = None,
        timeout: float,
    ) -> HttpResponse:
        self.calls.append((method, url, body))
        response = self.routes.get((method, url))
        if response is None:
            raise TimeoutError("bounded transport timeout")
        return response


class TestVectorMath:
    def test_normalize_l2_and_cosine(self) -> None:
        vector = normalize_l2([3.0, 4.0])
        assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0)
        assert math.isclose(cosine_similarity([1.0, 0.0], [1.0, 0.0]), 1.0)
        assert math.isclose(cosine_similarity([1.0, 0.0], [0.0, 1.0]), 0.0)

    def test_zero_vector_rejected(self) -> None:
        with pytest.raises(EmptyVectorError):
            normalize_l2([0.0, 0.0])
        with pytest.raises(EmptyVectorError):
            cosine_similarity([0.0, 0.0], [1.0, 0.0])


class TestBatchValidation:
    def test_missing_identity_fails_closed(self) -> None:
        other_data = identity().model_dump()
        other_data["model_name"] = "different"
        other = EmbeddingIdentity(**other_data)
        batch = EmbeddingBatch(identity=other, texts=["x"], vectors=[l2([1.0, 2.0, 3.0])])
        with pytest.raises(MixedIdentityError):
            validate_embedding_batch(
                batch,
                expected_identity=identity(),
                expected_dimensions=3,
            )

    def test_dimension_drift_rejected(self) -> None:
        batch = EmbeddingBatch(identity=identity(), texts=["x"], vectors=[[1.0, 2.0]])
        with pytest.raises(DimensionMismatchError):
            validate_embedding_batch(
                batch,
                expected_identity=identity(),
                expected_dimensions=3,
            )

    def test_nonfinite_rejected(self) -> None:
        with pytest.raises(ValidationError):
            EmbeddingBatch(
                identity=identity(),
                texts=["x"],
                vectors=[[float("nan"), 0.0, 1.0]],
            )

    def test_l2_mismatch_rejected(self) -> None:
        batch = EmbeddingBatch(identity=identity(), texts=["x"], vectors=[[10.0, 0.0, 0.0]])
        with pytest.raises(NormalizationMismatchError):
            validate_embedding_batch(
                batch,
                expected_identity=identity(),
                expected_dimensions=3,
            )


class TestVectorIndexValidation:
    def test_duplicate_chunk_rejected(self) -> None:
        index_identity = IndexIdentity(
            snapshot_digest="sha256:" + "5" * 64,
            parser_version="p",
            chunker_identity="c",
            chunk_config_digest="sha256:" + "6" * 64,
            embedding=identity(),
        )
        records = [
            VectorRecord(
                chunk_id="c1",
                document_digest="sha256:" + "1" * 64,
                vector=l2([1.0, 2.0, 3.0]),
            ),
            VectorRecord(
                chunk_id="c1",
                document_digest="sha256:" + "1" * 64,
                vector=l2([1.0, 2.0, 3.0]),
            ),
        ]
        with pytest.raises(DuplicateChunkIdError):
            validate_vector_index(index_identity, records)

    def test_empty_and_dimension_mismatch_rejected(self) -> None:
        index_identity = IndexIdentity(
            snapshot_digest="sha256:" + "5" * 64,
            parser_version="p",
            chunker_identity="c",
            chunk_config_digest="sha256:" + "6" * 64,
            embedding=identity(dimensions=2),
        )
        with pytest.raises(EmptyVectorError):
            validate_vector_index(
                index_identity,
                [
                    VectorRecord(
                        chunk_id="c1",
                        document_digest="sha256:" + "5" * 64,
                        vector=[],
                    )
                ],
            )
        with pytest.raises(DimensionMismatchError):
            validate_vector_index(
                index_identity,
                [
                    VectorRecord(
                        chunk_id="c1",
                        document_digest="sha256:" + "5" * 64,
                        vector=[1.0, 2.0, 3.0],
                    )
                ],
            )


class TestOllamaEmbeddingProvider:
    def test_request_and_response_contract(self) -> None:
        transport = FakeTransport(
            {
                ("POST", "http://127.0.0.1:11434/api/embed"): HttpResponse(
                    status=200,
                    text='{"embeddings":[[1.0,0.0,0.0],[0.0,1.0,0.0]]}',
                    content_type="application/json",
                )
            }
        )
        provider = OllamaEmbeddingProvider(identity=identity(), transport=transport)
        batch = provider.embed(["a", "b"])
        assert len(batch.vectors) == 2
        assert list(batch.texts) == ["a", "b"]
        body = transport.calls[0][2]
        assert b'"model":"nomic-embed-text"' in body
        assert b'"input":["a","b"]' in body

    def test_malformed_response_is_structured_error(self) -> None:
        transport = FakeTransport(
            {
                ("POST", "http://127.0.0.1:11434/api/embed"): HttpResponse(
                    status=200,
                    text='{"unexpected":true}',
                    content_type="application/json",
                )
            }
        )
        provider = OllamaEmbeddingProvider(identity=identity(), transport=transport)
        with pytest.raises(TransportError):
            provider.embed(["a"])

    def test_transport_failure_is_structured_error(self) -> None:
        provider = OllamaEmbeddingProvider(
            identity=identity(),
            transport=FakeTransport(
                {
                    ("POST", "http://127.0.0.1:11434/api/embed"): HttpResponse(
                        status=500,
                        text="unavailable",
                        content_type="text/plain",
                    )
                }
            ),
        )
        with pytest.raises(TransportError):
            provider.embed(["a"])

    def test_cardinality_drift_rejected(self) -> None:
        transport = FakeTransport(
            {
                ("POST", "http://127.0.0.1:11434/api/embed"): HttpResponse(
                    status=200,
                    text='{"embeddings":[[1.0,0.0,0.0]]}',
                    content_type="application/json",
                )
            }
        )
        provider = OllamaEmbeddingProvider(identity=identity(), transport=transport)
        with pytest.raises(TransportError):
            provider.embed(["a", "b"])

    def test_batch_limit_rejects_before_request(self) -> None:
        transport = FakeTransport()
        small_batch_identity = EmbeddingIdentity(**{**identity().model_dump(), "batch_size": 1})
        provider = OllamaEmbeddingProvider(
            identity=small_batch_identity,
            transport=transport,
            limits=KnowledgeLimits(max_batch_text_count=2),
        )
        with pytest.raises(ResourceLimitError):
            provider.embed(["a", "b", "c"])
        assert transport.calls == []

    def test_text_and_dimension_limits_reject_before_request(self) -> None:
        transport = FakeTransport()
        provider = OllamaEmbeddingProvider(
            identity=identity(),
            transport=transport,
            limits=KnowledgeLimits(
                max_text_bytes=4,
                max_query_bytes=4,
                max_chunk_text_bytes=4,
            ),
        )
        with pytest.raises(ResourceLimitError):
            provider.embed(["too-long"])
        big_data = identity().model_dump()
        big_data["dimensions"] = 4_096
        big_identity = EmbeddingIdentity(**big_data)
        with pytest.raises(ResourceLimitError):
            OllamaEmbeddingProvider(
                identity=big_identity,
                transport=transport,
                limits=KnowledgeLimits(max_vector_dimensions=2_048),
            )
        assert transport.calls == []

    def test_empty_query_is_structured_error(self) -> None:
        provider = OllamaEmbeddingProvider(
            identity=identity(),
            transport=FakeTransport(),
        )
        with pytest.raises(EmptyVectorError):
            provider.embed([])


class TestBackendUnavailable:
    def test_placeholder_is_explicit(self) -> None:
        with pytest.raises(BackendUnavailableError):
            raise BackendUnavailableError("LanceDB backend is not installed.")
