"""Embedding provider and vector index adapter boundary."""

from __future__ import annotations

import json
import math
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Annotated, Any, Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from zana_core.knowledge.limits import (
    HARD_MAX_BATCH_TEXT_COUNT,
    HARD_MAX_BATCH_TOTAL_BYTES,
    HARD_MAX_HEADING_DEPTH,
    HARD_MAX_PAGE_NUMBER,
    HARD_MAX_TEXT_BYTES,
    HARD_MAX_VECTOR_CELL_BYTES,
    HARD_MAX_VECTOR_CELLS,
    HARD_MAX_VECTOR_COUNT,
    HARD_MAX_VECTOR_DIMENSIONS,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    StrictInt,
    StrictUtcDatetime,
    VectorBudget,
    check_utf8_bytes,
    require_finite_number,
    require_strict_int,
    require_strict_number,
    resolve_limits,
    utf8_byte_length,
)
from zana_core.knowledge.models import (
    BoundedMetadata,
    BoundedVector,
    CanonicalSha256,
    FrozenMetadata,
    FrozenMetadataList,
    HeadingPath,
    Utf8ChunkText,
    Utf8Identifier,
    Utf8Key,
    Utf8Path,
    Utf8String,
    Utf8Text,
    bounded_tuple,
    canonical_identity_key,
)
from zana_core.runtimes.base import (
    HttpResponse,
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


class NormalizationBehavior(str, Enum):
    """Explicit vector normalization contract."""

    L2 = "l2"
    NONE = "none"


class EmbeddingIdentity(BaseModel):
    """Exact immutable embedding identity."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    provider: Utf8Identifier
    runtime_endpoint_identity: Utf8Path
    model_name: Utf8Identifier
    model_digest: CanonicalSha256 | None = None
    revision: Utf8Identifier | None = None
    dimensions: StrictInt = Field(gt=0, le=HARD_MAX_VECTOR_DIMENSIONS)
    normalization: NormalizationBehavior = NormalizationBehavior.L2
    batch_size: StrictInt = Field(gt=0, le=HARD_MAX_BATCH_TEXT_COUNT)
    identity_strength: Utf8Identifier = "exact"

    def identity_key(self) -> str:
        return canonical_identity_key(
            parts={
                "provider": self.provider,
                "runtime_endpoint_identity": self.runtime_endpoint_identity,
                "model_name": self.model_name,
                "model_digest": self.model_digest,
                "revision": self.revision,
                "dimensions": self.dimensions,
                "normalization": self.normalization.value,
                "batch_size": self.batch_size,
            }
        )


class EmbeddingBatch(BaseModel):
    """Immutable embedding batch with exact identity and aggregate caps."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    identity: EmbeddingIdentity
    texts: EmbeddingTexts = Field(default_factory=tuple, max_length=HARD_MAX_BATCH_TEXT_COUNT)
    vectors: EmbeddingVectors = Field(default_factory=tuple, max_length=HARD_MAX_VECTOR_COUNT)
    created_at: StrictUtcDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _bounded_aggregate(self) -> EmbeddingBatch:
        if len(self.texts) != len(self.vectors):
            raise ValueError("EmbeddingBatch texts and vectors must have equal cardinality.")
        total_text_bytes = 0
        for text in self.texts:
            total_text_bytes += utf8_byte_length(
                text,
                max_bytes=HARD_MAX_TEXT_BYTES,
                label="Embedding text",
            )
            if total_text_bytes > HARD_MAX_BATCH_TOTAL_BYTES:
                raise ValueError("EmbeddingBatch exceeds the aggregate text byte limit.")
        cells = 0
        for vector in self.vectors:
            for value in vector:
                require_finite_number(value, label="Embedding vector cell")
            cells += len(vector)
            if cells > HARD_MAX_VECTOR_CELLS:
                raise ValueError("EmbeddingBatch exceeds the aggregate vector cell limit.")
            if cells * 8 > HARD_MAX_VECTOR_CELL_BYTES:
                raise ValueError("EmbeddingBatch exceeds the aggregate vector byte limit.")
        return self


class VectorRecord(BaseModel):
    """One stored vector with stable provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: Utf8Key
    document_digest: CanonicalSha256
    source_title: Utf8String = ""
    page_start: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    page_end: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    heading_path: HeadingPath = Field(default_factory=tuple, max_length=HARD_MAX_HEADING_DEPTH)
    section_id: Utf8String = ""
    text: Utf8ChunkText = ""
    vector: BoundedVector = Field(default_factory=tuple)
    metadata_json: BoundedMetadata = Field(default_factory=FrozenMetadata)

    @field_validator("vector")
    @classmethod
    def _vector_finite(cls, value: tuple[float, ...]) -> tuple[float, ...]:
        for item in value:
            require_finite_number(item, label="Vector cell")
        return value


class IndexIdentity(BaseModel):
    """Immutable snapshot/index identity with exact embedding binding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    snapshot_digest: CanonicalSha256
    parser_version: Utf8String
    chunker_identity: Utf8String
    chunk_config_digest: CanonicalSha256
    embedding: EmbeddingIdentity

    def identity_key(self) -> str:
        return canonical_identity_key(
            parts={
                "snapshot_digest": self.snapshot_digest,
                "parser_version": self.parser_version,
                "chunker_identity": self.chunker_identity,
                "chunk_config_digest": self.chunk_config_digest,
                "embedding": self.embedding.identity_key(),
            }
        )


EmbeddingTexts = Annotated[
    tuple[Utf8Text, ...],
    bounded_tuple(HARD_MAX_BATCH_TEXT_COUNT, "Embedding texts"),
]
EmbeddingVectors = Annotated[
    tuple[BoundedVector, ...],
    bounded_tuple(HARD_MAX_VECTOR_COUNT, "Embedding vectors"),
]


class EmbeddingProvider(Protocol):
    """Provider contract for exact-identity embedding generation."""

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch: ...


class VectorIndex(Protocol):
    """Local vector index contract; candidates are a bounded iterable."""

    identity: IndexIdentity

    def search(
        self, vector: list[float] | tuple[float, ...], limit: int
    ) -> Iterable[tuple[VectorRecord, float]]: ...

    def close(self) -> None: ...


def _collect_texts(
    texts: Sequence[str] | Iterable[str],
    *,
    max_count: int,
    max_total_bytes: int,
    max_text_bytes: int,
) -> list[str]:
    collected: list[str] = []
    total = 0
    for text in texts:
        if len(collected) >= max_count:
            raise ResourceLimitError(f"Embedding batch exceeds the {max_count}-text limit.")
        if type(text) is not str:
            raise ResourceLimitError("Embedding texts must be exact strings.")
        size = utf8_byte_length(
            text,
            max_bytes=max_text_bytes,
            label="Embedding text",
        )
        total += size
        if total > max_total_bytes:
            raise ResourceLimitError("Embedding batch exceeds the total UTF-8 byte limit.")
        collected.append(text)
    if not collected:
        raise EmptyVectorError("At least one text is required for embedding.")
    return collected


def validate_embedding_batch(
    batch: EmbeddingBatch,
    *,
    expected_identity: EmbeddingIdentity,
    expected_dimensions: int,
    limits: KnowledgeLimits | None = None,
) -> EmbeddingBatch:
    active = resolve_limits(limits)
    if type(batch) is not EmbeddingBatch:
        raise MixedIdentityError("Embedding batch must be an EmbeddingBatch instance.")
    if type(expected_identity) is not EmbeddingIdentity:
        raise MixedIdentityError("Expected identity must be an EmbeddingIdentity instance.")
    validated_dimensions = require_strict_int(expected_dimensions, label="Expected dimensions")
    if validated_dimensions <= 0:
        raise ResourceLimitError("Expected dimensions must be positive.")
    if validated_dimensions != expected_identity.dimensions:
        raise MixedIdentityError("Expected dimensions must match the expected identity dimensions.")
    if batch.identity.identity_key() != expected_identity.identity_key():
        raise MixedIdentityError("Embedding batch identity does not match the expected identity.")
    if len(batch.texts) > active.max_batch_text_count:
        raise ResourceLimitError(
            f"Embedding batch exceeds the {active.max_batch_text_count}-text limit."
        )
    total_bytes = 0
    for text in batch.texts:
        total_bytes += utf8_byte_length(
            text,
            max_bytes=active.max_text_bytes,
            label="Embedding text",
        )
        if total_bytes > active.max_batch_total_bytes:
            raise ResourceLimitError("Embedding batch exceeds the total UTF-8 byte limit.")
    if len(batch.texts) != len(batch.vectors):
        raise DimensionMismatchError("Embedding batch text/vector cardinality drifted.")
    if len(batch.vectors) > active.max_vector_count:
        raise ResourceLimitError(
            f"Embedding batch exceeds the {active.max_vector_count}-vector limit."
        )
    vector_budget = VectorBudget(
        max_cells=active.max_vector_cells,
        max_bytes=active.max_vector_cell_bytes,
        label="Embedding batch vectors",
    )
    for vector in batch.vectors:
        _validate_vector(vector, expected_dimensions=validated_dimensions)
        vector_budget.add(vector, dimensions=len(vector))
    if expected_identity.normalization == NormalizationBehavior.L2:
        for vector in batch.vectors:
            if not _is_l2_normalized(vector):
                raise NormalizationMismatchError(
                    "Embedding vectors are not L2 normalized as declared."
                )
    return batch


def validate_vector_index(
    identity: IndexIdentity,
    records: Sequence[VectorRecord] | Iterable[VectorRecord],
    *,
    limits: KnowledgeLimits | None = None,
) -> list[VectorRecord]:
    """Validate one immutable index identity and its vectors fail-closed."""
    active = resolve_limits(limits)
    if type(identity) is not IndexIdentity:
        raise MixedIdentityError("Vector index identity must be an IndexIdentity instance.")
    seen: set[str] = set()
    validated: list[VectorRecord] = []
    retained = RetainedByteBudget(
        active.max_index_retained_bytes, label="Vector index retained content"
    )
    vectors = VectorBudget(
        max_cells=active.max_vector_cells,
        max_bytes=active.max_vector_cell_bytes,
        label="Vector index vectors",
    )
    try:
        for index, record in enumerate(records, start=1):
            if index > active.max_index_records:
                raise ResourceLimitError(
                    f"Vector index exceeds the {active.max_index_records}-record limit."
                )
            _validate_vector_record(record, limits=active)
            if record.chunk_id in seen:
                raise DuplicateChunkIdError("Vector index contains a duplicate chunk id.")
            seen.add(record.chunk_id)
            if not record.vector:
                raise EmptyVectorError("Vector index contains an empty vector.")
            if len(record.vector) != identity.embedding.dimensions:
                raise DimensionMismatchError(
                    "Vector index record dimensions do not match the index identity."
                )
            _account_vector_record(retained, record, limits=active)
            vectors.add(record.vector, dimensions=len(record.vector))
            if (
                identity.embedding.normalization == NormalizationBehavior.L2
                and not _is_l2_normalized(record.vector)
            ):
                raise NormalizationMismatchError(
                    "Vector index record is not L2 normalized as declared."
                )
            validated.append(record)
    except (EmbeddingError, ResourceLimitError):
        raise
    except Exception:
        raise ResourceLimitError("Vector index records could not be iterated safely.") from None
    return validated


def _account_vector_record(
    budget: RetainedByteBudget,
    record: VectorRecord,
    *,
    limits: KnowledgeLimits,
) -> None:
    budget.add(record.chunk_id, max_bytes=limits.max_key_bytes, label="chunk_id")
    budget.add(
        record.document_digest,
        max_bytes=limits.max_string_bytes,
        label="document_digest",
    )
    budget.add(
        record.source_title,
        max_bytes=limits.max_string_bytes,
        label="source_title",
    )
    budget.add(record.section_id, max_bytes=limits.max_string_bytes, label="section_id")
    budget.add(record.text, max_bytes=limits.max_chunk_text_bytes, label="vector text")
    for heading in record.heading_path:
        budget.add(heading, max_bytes=limits.max_string_bytes, label="heading")
    _account_metadata_strings(budget, record.metadata_json, limits=limits)


def _account_metadata_strings(
    budget: RetainedByteBudget,
    metadata: dict[str, Any],
    *,
    limits: KnowledgeLimits,
) -> None:
    stack: list[Any] = [metadata]
    while stack:
        node = stack.pop()
        if type(node) is FrozenMetadata:
            validated = FrozenMetadata._validated_wrapper(node)
            for key, child in tuple.__getitem__(validated, slice(None)):
                budget.add(key, max_bytes=limits.max_key_bytes, label="metadata key")
                if type(child) in (FrozenMetadata, FrozenMetadataList):
                    stack.append(child)
                elif type(child) is str:
                    budget.add(child, max_bytes=limits.max_string_bytes, label="metadata string")
        elif type(node) is FrozenMetadataList:
            validated = FrozenMetadataList._validated_wrapper(node)
            for child in tuple.__getitem__(validated, slice(None)):
                if type(child) in (FrozenMetadata, FrozenMetadataList):
                    stack.append(child)
                elif type(child) is str:
                    budget.add(child, max_bytes=limits.max_string_bytes, label="metadata string")


def _validate_vector_record(record: VectorRecord, *, limits: KnowledgeLimits) -> None:
    if type(record) is not VectorRecord:
        raise MixedIdentityError("Vector index entries must be VectorRecord instances.")
    check_utf8_bytes(record.chunk_id, max_bytes=limits.max_key_bytes, label="chunk_id")
    check_utf8_bytes(
        record.document_digest,
        max_bytes=limits.max_string_bytes,
        label="document_digest",
    )
    check_utf8_bytes(
        record.source_title,
        max_bytes=limits.max_string_bytes,
        label="source_title",
    )
    check_utf8_bytes(record.section_id, max_bytes=limits.max_string_bytes, label="section_id")
    check_utf8_bytes(
        record.text,
        max_bytes=limits.max_chunk_text_bytes,
        label="vector text",
    )
    if len(record.heading_path) > limits.max_heading_depth:
        raise ResourceLimitError("Vector record heading path exceeds the configured depth limit.")
    if len(record.vector) > limits.max_vector_dimensions:
        raise ResourceLimitError("Vector record dimensions exceed the configured vector limit.")
    if type(record.metadata_json) is not FrozenMetadata:
        raise ResourceLimitError("Vector record metadata must be a validated FrozenMetadata value.")


def _validate_vector(vector: list[float] | tuple[float, ...], *, expected_dimensions: int) -> None:
    if type(vector) not in (tuple, list):
        raise EmptyVectorError("Embedding vector must be an exact builtin tuple or list.")
    if not vector:
        raise EmptyVectorError("Embedding vector is empty.")
    if len(vector) > HARD_MAX_VECTOR_DIMENSIONS:
        raise DimensionMismatchError(
            f"Vector exceeds the {HARD_MAX_VECTOR_DIMENSIONS}-dimension hard cap."
        )
    if len(vector) != expected_dimensions:
        raise DimensionMismatchError(
            f"Expected {expected_dimensions} dimensions, got {len(vector)}."
        )
    for value in vector:
        try:
            require_finite_number(value, label="Embedding vector cell")
        except ResourceLimitError:
            raise NonFiniteVectorError(
                "Embedding vector contains a non-finite or non-numeric cell."
            ) from None


def _stable_norm(vector: list[float] | tuple[float, ...]) -> float:
    """Return a finite stable L2 norm, rejecting overflow."""
    max_abs = 0.0
    for value in vector:
        magnitude = abs(require_finite_number(value, label="Vector cell"))
        if magnitude > max_abs:
            max_abs = magnitude
    if max_abs == 0.0:
        return 0.0
    threshold = math.sqrt(sys.float_info.max / max(1, len(vector)))
    if max_abs > threshold:
        raise NonFiniteVectorError("Vector norm overflows; values are too large to square safely.")
    scaled_sum = 0.0
    for value in vector:
        scaled = require_finite_number(value, label="Vector cell") / max_abs
        scaled_sum += scaled * scaled
    result = max_abs * math.sqrt(scaled_sum)
    if not math.isfinite(result):
        raise NonFiniteVectorError("Vector norm overflowed; values are too large.")
    return result


def _is_l2_normalized(vector: list[float] | tuple[float, ...]) -> bool:
    if type(vector) not in (tuple, list) or not vector:
        return False
    if len(vector) > HARD_MAX_VECTOR_DIMENSIONS:
        return False
    return math.isclose(_stable_norm(vector), 1.0, rel_tol=1e-6, abs_tol=1e-6)


def normalize_l2(vector: list[float] | tuple[float, ...]) -> list[float]:
    """Deterministic L2 normalization with zero-vector rejection."""
    _validate_vector(vector, expected_dimensions=len(vector))
    norm = _stable_norm(vector)
    if norm == 0.0:
        raise EmptyVectorError("Cannot normalize a zero vector.")
    result = [require_finite_number(value, label="Vector cell") / norm for value in vector]
    if any(not math.isfinite(value) for value in result):
        raise NonFiniteVectorError("L2 normalization produced non-finite values.")
    return result


def cosine_similarity(
    left: list[float] | tuple[float, ...],
    right: list[float] | tuple[float, ...],
) -> float:
    """Deterministic cosine similarity with zero-vector rejection."""
    _validate_vector(left, expected_dimensions=len(left))
    _validate_vector(right, expected_dimensions=len(right))
    if len(left) != len(right):
        raise DimensionMismatchError("Cosine inputs have different dimensions.")
    left_norm = _stable_norm(left)
    right_norm = _stable_norm(right)
    if left_norm == 0.0 or right_norm == 0.0:
        raise EmptyVectorError("Cosine similarity rejects zero vectors.")
    dot = 0.0
    for left_value, right_value in zip(left, right, strict=False):
        dot += require_finite_number(left_value, label="Vector cell") * require_finite_number(
            right_value, label="Vector cell"
        )
    result = dot / (left_norm * right_norm)
    if not math.isfinite(result):
        raise NonFiniteVectorError("Cosine similarity produced a non-finite value.")
    return result


def _normalize_origin(endpoint: str, *, max_bytes: int) -> str:
    """Validate and reconstruct a bounded origin without credentials."""
    if type(endpoint) is not str:
        raise MissingIdentityError("Embedding endpoint must be a string.")
    utf8_byte_length(endpoint, max_bytes=max_bytes, label="Embedding endpoint")
    if any(char.isspace() or ord(char) < 0x20 or ord(char) == 0x7F for char in endpoint):
        raise MissingIdentityError(
            "Embedding endpoint must not contain whitespace or control characters."
        )
    if "\\" in endpoint:
        raise MissingIdentityError("Embedding endpoint must not contain backslashes.")
    try:
        parsed = urlsplit(endpoint)
    except ValueError:
        raise MissingIdentityError("Embedding endpoint is malformed.") from None
    if parsed.scheme not in {"http", "https"}:
        raise MissingIdentityError("Embedding endpoint must use http or https.")
    if parsed.hostname is None:
        raise MissingIdentityError("Embedding endpoint requires a host.")
    if parsed.username is not None or parsed.password is not None:
        raise MissingIdentityError("Embedding endpoint must not contain credentials.")
    if parsed.query or parsed.fragment or parsed.path not in ("", "/"):
        raise MissingIdentityError("Embedding endpoint must be an origin without a path.")
    try:
        port = parsed.port
    except ValueError:
        raise MissingIdentityError("Embedding endpoint has an invalid port.") from None
    if port is not None and not (1 <= port <= 65535):
        raise MissingIdentityError("Embedding endpoint has an invalid port.")
    host = parsed.hostname.lower()
    display_host = f"[{host}]" if ":" in host else host
    if port is None:
        return f"{parsed.scheme}://{display_host}"
    return f"{parsed.scheme}://{display_host}:{port}"


def _validate_timeout(timeout: float, limits: KnowledgeLimits) -> None:
    validated = require_strict_number(
        timeout,
        label="Embedding timeout",
        hard_max=limits.max_timeout_seconds,
    )
    if validated <= 0:
        raise ResourceLimitError("Embedding timeout must be positive.")


@dataclass(frozen=True)
class OllamaEmbedRequest:
    """Exact /api/embed request payload with hard caps before serialization."""

    model: str
    input: tuple[str, ...]
    limits: KnowledgeLimits | None = None

    def __post_init__(self) -> None:
        if self.limits is None or type(self.limits) is not KnowledgeLimits:
            raise ResourceLimitError(
                "Embedding request limits must be an exact KnowledgeLimits instance."
            )
        active: KnowledgeLimits = self.limits
        if type(self.model) is not str or not self.model:
            raise ResourceLimitError("Embedding request model must be a non-empty exact string.")
        if any(ord(char) < 0x20 or ord(char) == 0x7F or char.isspace() for char in self.model):
            raise ResourceLimitError(
                "Embedding request model must not contain control or whitespace characters."
            )
        check_utf8_bytes(
            self.model,
            max_bytes=active.max_string_bytes,
            label="Embedding model",
        )
        if type(self.input) is not tuple or not self.input:
            raise ResourceLimitError("Embedding request requires a non-empty exact tuple of texts.")
        if len(self.input) > active.max_batch_text_count:
            raise ResourceLimitError(
                f"Embedding request exceeds the {active.max_batch_text_count}-text limit."
            )
        total = 0
        for text in self.input:
            if type(text) is not str:
                raise ResourceLimitError("Embedding request texts must be exact strings.")
            size = utf8_byte_length(
                text,
                max_bytes=active.max_text_bytes,
                label="Embedding text",
            )
            total += size
            if total > active.max_batch_total_bytes:
                raise ResourceLimitError("Embedding request exceeds the total UTF-8 byte limit.")
        estimate = _json_byte_estimate(self.model, self.input)
        if estimate > active.max_request_bytes:
            raise ResourceLimitError(
                "Embedding request exceeds the bounded request byte limit before construction."
            )

    def to_bytes(self) -> bytes:
        payload = json.dumps(
            {"model": self.model, "input": list(self.input)},
            separators=(",", ":"),
        ).encode("utf-8")
        if self.limits is None or type(self.limits) is not KnowledgeLimits:
            raise ResourceLimitError(
                "Embedding request limits must be an exact KnowledgeLimits instance."
            )
        active: KnowledgeLimits = self.limits
        if len(payload) > active.max_request_bytes:
            raise ResourceLimitError("Embedding request exceeds the bounded request byte limit.")
        return payload


def _json_byte_estimate(model: str, texts: Sequence[str]) -> int:
    """Upper-bound the exact UTF-8 JSON payload before allocating it."""
    total = 64 + len(model.encode("utf-8")) + 2 * len(texts)
    for text in texts:
        total += len(text.encode("utf-8"))
        for char in text:
            codepoint = ord(char)
            if codepoint > 0xFFFF:
                total += 11
            elif codepoint > 0x7F or codepoint < 0x20:
                total += 5
            elif char in ('"', "\\"):
                total += 1
    return total


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
        limits: KnowledgeLimits | None = None,
    ) -> None:
        self.limits = resolve_limits(limits)
        if type(identity) is not EmbeddingIdentity:
            raise MissingIdentityError("Ollama provider requires an EmbeddingIdentity instance.")
        if endpoint is not None and type(endpoint) is not str:
            raise MissingIdentityError("Ollama endpoint must be an exact string.")
        if bearer_token is not None and type(bearer_token) is not str:
            raise MissingIdentityError("Ollama bearer token must be an exact string or None.")
        if transport is not None:
            try:
                request = transport.request
            except Exception:
                raise TransportError(
                    "Ollama transport request could not be inspected safely."
                ) from None
            if not callable(request):
                raise TransportError("Ollama transport must expose a callable request method.")
            self._transport_request = request
        else:
            self._transport_request = UrllibTransport().request
        if identity.provider != self.provider_name:
            raise MissingIdentityError("Ollama provider requires an ollama embedding identity.")
        if identity.identity_strength != "exact":
            raise MissingIdentityError("Ollama embedding requires an exact model identity.")
        normalized_origin = _normalize_origin(
            endpoint,
            max_bytes=self.limits.max_endpoint_bytes,
        )
        if identity.runtime_endpoint_identity != normalized_origin:
            raise MissingIdentityError(
                "Ollama endpoint does not match the embedding identity endpoint."
            )
        _validate_timeout(timeout, self.limits)
        if bearer_token is not None:
            check_utf8_bytes(
                bearer_token,
                max_bytes=self.limits.max_credential_bytes,
                label="Bearer token",
            )
        if identity.dimensions > self.limits.max_vector_dimensions:
            raise ResourceLimitError("Embedding dimensions exceed the configured vector limit.")
        if identity.batch_size > self.limits.max_batch_text_count:
            raise ResourceLimitError("Embedding batch size exceeds the configured batch limit.")
        self.identity = identity
        self.endpoint = normalized_origin
        self.transport = transport
        self.timeout = timeout
        self.bearer_token = bearer_token

    def embed(self, texts: Sequence[str]) -> EmbeddingBatch:
        endpoint = self.endpoint
        timeout = self.timeout
        try:
            collected = _collect_texts(
                texts,
                max_count=self.limits.max_batch_text_count,
                max_total_bytes=self.limits.max_batch_total_bytes,
                max_text_bytes=self.limits.max_text_bytes,
            )
        except (EmbeddingError, ResourceLimitError):
            raise
        except Exception:
            raise TransportError("Embedding input could not be collected safely.") from None
        payload = OllamaEmbedRequest(
            model=self.identity.model_name,
            input=tuple(collected),
            limits=self.limits,
        ).to_bytes()
        try:
            response = self._transport_request(
                "POST",
                f"{endpoint}/api/embed",
                headers=self._headers(),
                body=payload,
                timeout=timeout,
            )
            if type(response) is not HttpResponse or type(response.text) is not str:
                raise TransportError("Ollama /api/embed returned an unexpected response type.")
            try:
                utf8_byte_length(
                    response.text,
                    max_bytes=self.limits.max_request_bytes,
                    label="Ollama /api/embed response",
                )
            except ResourceLimitError:
                raise TransportError(
                    "Ollama /api/embed response exceeded the bounded byte limit."
                ) from None
            require_http_ok(response, "Ollama /api/embed")
            parsed = parse_json_object(response, "Ollama /api/embed")
            vectors = _parse_ollama_embedding_response(
                parsed,
                expected_count=len(collected),
                expected_dimensions=self.identity.dimensions,
                limits=self.limits,
            )
        except (RuntimeProbeTimeoutError, InvalidRuntimeResponseError):
            raise TransportError(
                "Ollama /api/embed failed: the runtime returned an invalid response."
            ) from None
        except (TimeoutError, OSError):
            raise TransportError("Ollama /api/embed could not be reached.") from None
        except (ValueError, KeyError, TypeError):
            raise TransportError("Ollama /api/embed returned a malformed response.") from None
        batch = EmbeddingBatch(
            identity=self.identity,
            texts=tuple(collected),
            vectors=tuple(tuple(vector) for vector in vectors),
        )
        return validate_embedding_batch(
            batch,
            expected_identity=self.identity,
            expected_dimensions=self.identity.dimensions,
            limits=self.limits,
        )

    def _headers(self) -> dict[str, str] | None:
        if not self.bearer_token:
            return None
        return {"Authorization": f"Bearer {self.bearer_token}"}


def _parse_ollama_embedding_response(
    payload: dict[str, Any],
    *,
    expected_count: int,
    expected_dimensions: int,
    limits: KnowledgeLimits,
) -> list[list[float]]:
    if type(payload) is not dict:
        raise InvalidRuntimeResponseError("Ollama /api/embed returned a non-object payload.")
    embeddings = payload.get("embeddings")
    if type(embeddings) is not list or len(embeddings) != expected_count:
        raise InvalidRuntimeResponseError("Ollama /api/embed returned a malformed embeddings list.")
    if len(embeddings) > limits.max_vector_count:
        raise InvalidRuntimeResponseError(
            "Ollama /api/embed returned more vectors than the configured limit."
        )
    vectors: list[list[float]] = []
    cells = 0
    for entry in embeddings:
        if (
            type(entry) is not list
            or len(entry) != expected_dimensions
            or len(entry) > HARD_MAX_VECTOR_DIMENSIONS
        ):
            raise InvalidRuntimeResponseError(
                "Ollama /api/embed returned malformed vector entries."
            )
        for value in entry:
            try:
                require_finite_number(value, label="Vector cell")
            except ResourceLimitError:
                raise InvalidRuntimeResponseError(
                    "Ollama /api/embed returned non-finite or non-numeric vector cells."
                ) from None
        cells += len(entry)
        if cells > limits.max_vector_cells:
            raise InvalidRuntimeResponseError(
                "Ollama /api/embed exceeded the aggregate vector cell limit."
            )
        if cells * 8 > limits.max_vector_cell_bytes:
            raise InvalidRuntimeResponseError(
                "Ollama /api/embed exceeded the aggregate vector byte limit."
            )
        vectors.append([float(value) for value in entry])
    return vectors
