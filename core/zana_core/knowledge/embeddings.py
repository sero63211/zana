"""Embedding provider and vector index adapter boundary."""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from zana_core.runtimes.base import (
    HttpTransport,
    InvalidRuntimeResponseError,
    RuntimeProbeTimeoutError,
    parse_json_object,
    require_http_ok,
)
from zana_core.runtimes.transport import UrllibTransport


class EmbeddingError(Exception):
    """Base embedding failure."""


class MissingIdentityError(EmbeddingError):
    """Raised when embedding or index identity is missing/weak."""


class DimensionMismatchError(EmbeddingError):
    """Raised when vector dimensions drift from the declared identity."""


class EmptyVectorError(EmbeddingError):
    """Raised when an embedding is empty."""


class NonFiniteVectorError(EmbeddingError):
    """Raised when an embedding contains non-finite values."""


class MixedIdentityError(EmbeddingError):
    """Raised when an index contains vectors from mixed identities."""


class DuplicateChunkIdError(EmbeddingError):
    """Raised when a vector index contains duplicate chunk ids."""


class NormalizationMismatchError(EmbeddingError):
    """Raised when vectors violate the declared normalization behavior."""


class TransportError(EmbeddingError):
    """Raised for bounded transport/endpoint failures."""


class BackendUnavailableError(EmbeddingError):
    """Raised when a vector backend is not installed/available."""


class ResourceLimitError(EmbeddingError):
    """Raised when an embedding request exceeds configured low-resource limits."""


DEFAULT_MAX_BATCH_TEXTS = 64
DEFAULT_MAX_TEXT_CHARS = 32_768
DEFAULT_MAX_VECTOR_DIMENSIONS = 16_384


class NormalizationBehavior(str, Enum):
    """Explicit vector normalization contract."""

    L2 = "l2"
    NONE = "none"


class EmbeddingIdentity(BaseModel):
    """Exact immutable embedding identity."""

    model_config = ConfigDict(frozen=True)

    provider: str
    runtime_endpoint_identity: str
    model_name: str
    model_digest: str | None = None
    revision: str | None = None
    dimensions: int = Field(gt=0)
    normalization: NormalizationBehavior = NormalizationBehavior.L2
    batch_size: int = Field(gt=0)
    identity_strength: str = "exact"

    def identity_key(self) -> str:
        return ":".join(
            (
                self.provider,
                self.runtime_endpoint_identity,
                self.model_name,
                self.model_digest or "",
                self.revision or "",
                str(self.dimensions),
                self.normalization.value,
                str(self.batch_size),
            )
        )


class EmbeddingLimits(BaseModel):
    """Bounded low-resource embedding limits."""

    model_config = ConfigDict(frozen=True)

    max_batch_texts: int = Field(default=DEFAULT_MAX_BATCH_TEXTS, gt=0)
    max_text_chars: int = Field(default=DEFAULT_MAX_TEXT_CHARS, gt=0)
    max_vector_dimensions: int = Field(default=DEFAULT_MAX_VECTOR_DIMENSIONS, gt=0)


class EmbeddingBatch(BaseModel):
    """Immutable embedding batch with exact identity."""

    model_config = ConfigDict(frozen=True)

    identity: EmbeddingIdentity
    texts: list[str] = Field(default_factory=list)
    vectors: list[list[float]] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class VectorRecord(BaseModel):
    """One stored vector with stable provenance."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_digest: str
    source_title: str = ""
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    section_id: str = ""
    text: str = ""
    vector: list[float] = Field(default_factory=list)
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class IndexIdentity(BaseModel):
    """Immutable snapshot/index identity with exact embedding binding."""

    model_config = ConfigDict(frozen=True)

    snapshot_digest: str
    parser_version: str
    chunker_identity: str
    chunk_config_digest: str
    embedding: EmbeddingIdentity

    def identity_key(self) -> str:
        return ":".join(
            (
                self.snapshot_digest,
                self.parser_version,
                self.chunker_identity,
                self.chunk_config_digest,
                self.embedding.identity_key(),
            )
        )


class EmbeddingProvider(Protocol):
    """Provider contract for exact-identity embedding generation."""

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class VectorIndex(Protocol):
    """Local vector index contract; no backend is implied or claimed."""

    identity: IndexIdentity

    def search(self, vector: list[float], limit: int) -> list[tuple[VectorRecord, float]]: ...

    def close(self) -> None: ...


def validate_embedding_batch(
    batch: EmbeddingBatch,
    *,
    expected_identity: EmbeddingIdentity,
    expected_dimensions: int,
) -> EmbeddingBatch:
    if batch.identity.identity_key() != expected_identity.identity_key():
        raise MixedIdentityError("Embedding batch identity does not match the expected identity.")
    if len(batch.texts) != len(batch.vectors):
        raise DimensionMismatchError("Embedding batch text/vector cardinality drifted.")
    for vector in batch.vectors:
        _validate_vector(vector, expected_dimensions=expected_dimensions)
    if expected_identity.normalization == NormalizationBehavior.L2:
        for vector in batch.vectors:
            if not _is_l2_normalized(vector):
                raise NormalizationMismatchError(
                    "Embedding vectors are not L2 normalized as declared."
                )
    return batch


def validate_vector_index(
    identity: IndexIdentity,
    records: Sequence[VectorRecord],
) -> list[VectorRecord]:
    """Validate one immutable index identity and its vectors fail-closed."""
    seen: set[str] = set()
    normalized = list(records)
    for record in normalized:
        if record.chunk_id in seen:
            raise DuplicateChunkIdError(f"Duplicate chunk id {record.chunk_id}.")
        seen.add(record.chunk_id)
        if not record.vector:
            raise EmptyVectorError(f"Chunk {record.chunk_id} has an empty vector.")
        if len(record.vector) != identity.embedding.dimensions:
            raise DimensionMismatchError(
                f"Chunk {record.chunk_id} dimension does not match index identity."
            )
        if any(not math.isfinite(value) for value in record.vector):
            raise NonFiniteVectorError(f"Chunk {record.chunk_id} has non-finite vector values.")
        if identity.embedding.normalization == NormalizationBehavior.L2 and not _is_l2_normalized(
            record.vector
        ):
            raise NormalizationMismatchError(f"Chunk {record.chunk_id} is not L2 normalized.")
    return normalized


def _validate_vector(vector: list[float], *, expected_dimensions: int) -> None:
    if not vector:
        raise EmptyVectorError("Embedding vector is empty.")
    if len(vector) != expected_dimensions:
        raise DimensionMismatchError(
            f"Expected {expected_dimensions} dimensions, got {len(vector)}."
        )
    if any(not math.isfinite(value) for value in vector):
        raise NonFiniteVectorError("Embedding vector contains non-finite values.")


def _is_l2_normalized(vector: list[float]) -> bool:
    norm = math.sqrt(sum(value * value for value in vector))
    return math.isclose(norm, 1.0, rel_tol=1e-6, abs_tol=1e-6)


def normalize_l2(vector: list[float]) -> list[float]:
    """Deterministic L2 normalization with zero-vector rejection."""
    _validate_vector(vector, expected_dimensions=len(vector))
    norm = math.sqrt(sum(value * value for value in vector))
    if norm == 0.0:
        raise EmptyVectorError("Cannot normalize a zero vector.")
    return [value / norm for value in vector]


def cosine_similarity(left: list[float], right: list[float]) -> float:
    """Deterministic cosine similarity with zero-vector rejection."""
    if len(left) != len(right):
        raise DimensionMismatchError("Cosine inputs have different dimensions.")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if left_norm == 0.0 or right_norm == 0.0:
        raise EmptyVectorError("Cosine similarity rejects zero vectors.")
    dot = sum(a * b for a, b in zip(left, right, strict=False))
    return dot / (left_norm * right_norm)


@dataclass(frozen=True)
class OllamaEmbedRequest:
    """Exact /api/embed request payload."""

    model: str
    input: list[str]

    def to_bytes(self) -> bytes:
        import json

        return json.dumps(
            {"model": self.model, "input": self.input},
            separators=(",", ":"),
        ).encode("utf-8")


class OllamaEmbeddingProvider:
    """Real Ollama /api/embed provider over injected bounded transport.

    No model is started, pulled, loaded, or queried by this class itself.
    """

    provider_name = "ollama"

    def __init__(
        self,
        *,
        identity: EmbeddingIdentity,
        endpoint: str = "http://127.0.0.1:11434",
        transport: HttpTransport | None = None,
        timeout: float = 5.0,
        bearer_token: str | None = None,
        limits: EmbeddingLimits | None = None,
    ) -> None:
        if identity.provider != self.provider_name:
            raise MissingIdentityError("Ollama provider requires an ollama embedding identity.")
        if identity.identity_strength != "exact":
            raise MissingIdentityError("Ollama embedding requires an exact model identity.")
        self.identity = identity
        self.endpoint = endpoint.rstrip("/")
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.bearer_token = bearer_token
        self.limits = limits or EmbeddingLimits()

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        self._validate_input(texts)
        payload = OllamaEmbedRequest(model=self.identity.model_name, input=list(texts)).to_bytes()
        try:
            response = self.transport.request(
                "POST",
                f"{self.endpoint}/api/embed",
                headers=self._headers(),
                body=payload,
                timeout=self.timeout,
            )
            require_http_ok(response, "Ollama /api/embed")
            parsed = parse_json_object(response, "Ollama /api/embed")
            vectors = _parse_ollama_embedding_response(parsed, expected_count=len(texts))
        except (RuntimeProbeTimeoutError, InvalidRuntimeResponseError) as error:
            raise TransportError(str(error)) from error
        except (ValueError, KeyError, TypeError) as error:
            raise TransportError("Ollama /api/embed returned a malformed response.") from error
        batch = EmbeddingBatch(
            identity=self.identity,
            texts=list(texts),
            vectors=vectors,
        )
        return validate_embedding_batch(
            batch,
            expected_identity=self.identity,
            expected_dimensions=self.identity.dimensions,
        )

    def _validate_input(self, texts: Sequence[str]) -> None:
        if not texts:
            raise EmptyVectorError("At least one text is required for embedding.")
        if len(texts) > self.limits.max_batch_texts:
            raise ResourceLimitError(
                f"Embedding batch exceeds the {self.limits.max_batch_texts}-text limit."
            )
        for text in texts:
            if len(text) > self.limits.max_text_chars:
                raise ResourceLimitError(
                    "A text exceeds the configured character limit; no request was sent."
                )
        if self.identity.dimensions > self.limits.max_vector_dimensions:
            raise ResourceLimitError("Embedding dimensions exceed the configured vector limit.")

    def _headers(self) -> dict[str, str] | None:
        if not self.bearer_token:
            return None
        return {"Authorization": f"Bearer {self.bearer_token}"}


def _parse_ollama_embedding_response(
    payload: dict[str, Any],
    *,
    expected_count: int,
) -> list[list[float]]:
    embeddings = payload.get("embeddings")
    if not isinstance(embeddings, list) or len(embeddings) != expected_count:
        raise InvalidRuntimeResponseError("Ollama /api/embed returned a malformed embeddings list.")
    vectors: list[list[float]] = []
    for entry in embeddings:
        if not isinstance(entry, list) or not all(
            isinstance(value, int | float) for value in entry
        ):
            raise InvalidRuntimeResponseError(
                "Ollama /api/embed returned malformed vector entries."
            )
        vectors.append([float(value) for value in entry])
    return vectors
