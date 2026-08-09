"""Deterministic retrieval service and compatibility decisions."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator

from zana_core.knowledge.embeddings import (
    BackendUnavailableError,
    EmbeddingBatch,
    EmbeddingIdentity,
    EmbeddingProvider,
    IndexIdentity,
    VectorIndex,
    VectorRecord,
)
from zana_core.knowledge.limits import (
    HARD_MAX_CHUNK_TEXT_BYTES,
    HARD_MAX_HEADING_DEPTH,
    HARD_MAX_PAGE_NUMBER,
    HARD_MAX_RESULT_RETAINED_BYTES,
    HARD_MAX_SMOKE_EXPECTATIONS,
    HARD_MAX_SMOKE_FAILURES,
    HARD_MAX_STRING_BYTES,
    HARD_MAX_TOP_K,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    StrictBool,
    StrictFiniteNumber,
    StrictInt,
    StrictUtcDatetime,
    check_utf8_bytes,
    require_finite_number,
    require_strict_int,
    resolve_limits,
)
from zana_core.knowledge.models import (
    CanonicalSha256,
    FrozenMetadata,
    Utf8ChunkText,
    Utf8Query,
    Utf8String,
)

DEFAULT_MAX_TOP_K = 100


class RetrievalError(Exception):
    """Base retrieval failure."""


class HostileIndexError(RetrievalError):
    """Raised when an index returns more or malformed results than requested."""


class IndexCorruptionError(RetrievalError):
    """Raised when an index returns non-contract candidate entries."""


class RetrievalQuery(BaseModel):
    """Immutable retrieval query."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: Utf8Query
    top_k: StrictInt = Field(default=5, ge=1, le=HARD_MAX_TOP_K)
    min_score: StrictFiniteNumber = Field(default=0.0, ge=-1, le=1)
    dedup_by: str | None = Field(default=None, pattern="^(document|section)$")


class RetrievalHit(BaseModel):
    """One deterministic retrieval hit with stable provenance."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chunk_id: Utf8String
    document_digest: CanonicalSha256
    source_title: Utf8String
    page_start: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    page_end: StrictInt | None = Field(default=None, ge=1, le=HARD_MAX_PAGE_NUMBER)
    heading_path: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_HEADING_DEPTH
    )
    section_id: Utf8String = ""
    text: Utf8ChunkText = ""
    score: StrictFiniteNumber = Field(ge=-1, le=1)
    rank: StrictInt = Field(gt=0, le=HARD_MAX_TOP_K)


class RetrievalResult(BaseModel):
    """Structured retrieval result for the evidence/context composer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: RetrievalQuery
    hits: tuple[RetrievalHit, ...] = Field(default_factory=tuple, max_length=HARD_MAX_TOP_K)
    index_identity: IndexIdentity
    queried_at: StrictUtcDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _bounded_retained_bytes(self) -> RetrievalResult:
        budget = RetainedByteBudget(HARD_MAX_RESULT_RETAINED_BYTES, label="Retrieval result")
        budget.add(self.query.text, max_bytes=HARD_MAX_STRING_BYTES, label="query text")
        identity = self.index_identity
        budget.add(
            identity.snapshot_digest,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="snapshot digest",
        )
        budget.add(
            identity.parser_version,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="parser version",
        )
        budget.add(
            identity.chunker_identity,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="chunker identity",
        )
        budget.add(
            identity.chunk_config_digest,
            max_bytes=HARD_MAX_STRING_BYTES,
            label="chunk config digest",
        )
        for hit in self.hits:
            budget.add(hit.chunk_id, max_bytes=HARD_MAX_STRING_BYTES, label="chunk_id")
            budget.add(
                hit.document_digest,
                max_bytes=HARD_MAX_STRING_BYTES,
                label="document digest",
            )
            budget.add(
                hit.source_title,
                max_bytes=HARD_MAX_STRING_BYTES,
                label="source title",
            )
            budget.add(hit.section_id, max_bytes=HARD_MAX_STRING_BYTES, label="section_id")
            budget.add(hit.text, max_bytes=HARD_MAX_CHUNK_TEXT_BYTES, label="hit text")
            for heading in hit.heading_path:
                budget.add(heading, max_bytes=HARD_MAX_STRING_BYTES, label="heading")
        return self


class RetrievalSmokeRecord(BaseModel):
    """Honest declared-query smoke-test record; no model judge."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    query: Utf8Query
    expected_chunk_ids: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_SMOKE_EXPECTATIONS
    )
    expected_source_ids: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_SMOKE_EXPECTATIONS
    )
    observed_chunk_ids: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_SMOKE_EXPECTATIONS
    )
    observed_source_ids: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_SMOKE_EXPECTATIONS
    )
    passed: StrictBool
    failures: tuple[Utf8String, ...] = Field(
        default_factory=tuple, max_length=HARD_MAX_SMOKE_FAILURES
    )
    ran_at: StrictUtcDatetime = Field(default_factory=lambda: datetime.now(UTC))

    @model_validator(mode="after")
    def _bounded_retained_bytes(self) -> RetrievalSmokeRecord:
        budget = RetainedByteBudget(HARD_MAX_RESULT_RETAINED_BYTES, label="Retrieval smoke record")
        for value in (
            self.query,
            *self.expected_chunk_ids,
            *self.expected_source_ids,
            *self.observed_chunk_ids,
            *self.observed_source_ids,
            *self.failures,
        ):
            budget.add(value, max_bytes=HARD_MAX_STRING_BYTES, label="smoke string")
        return self


def index_compatible(
    identity: IndexIdentity,
    *,
    expected_snapshot_digest: str,
    parser_version: str,
    chunker_identity: str,
    chunk_config_digest: str,
    embedding: EmbeddingIdentity,
) -> bool:
    """Never silently reuse vectors whose inputs or identity changed."""
    if type(identity) is not IndexIdentity or type(embedding) is not EmbeddingIdentity:
        raise RetrievalError("Index compatibility requires exact identity instances.")
    for label, value in (
        ("snapshot digest", expected_snapshot_digest),
        ("parser version", parser_version),
        ("chunker identity", chunker_identity),
        ("chunk config digest", chunk_config_digest),
    ):
        if type(value) is not str:
            raise RetrievalError(f"{label} must be an exact string.")
    return (
        identity.snapshot_digest == expected_snapshot_digest
        and identity.parser_version == parser_version
        and identity.chunker_identity == chunker_identity
        and identity.chunk_config_digest == chunk_config_digest
        and identity.embedding.identity_key() == embedding.identity_key()
    )


class RetrievalService:
    """Pure retrieval orchestration over injected provider and index."""

    def __init__(
        self,
        *,
        provider: EmbeddingProvider,
        index: VectorIndex,
        max_top_k: int | None = None,
        limits: KnowledgeLimits | None = None,
    ) -> None:
        self.limits = resolve_limits(limits)
        if max_top_k is not None:
            effective_max = require_strict_int(max_top_k, label="max_top_k")
        else:
            effective_max = self.limits.max_top_k
        if effective_max < 1 or effective_max > self.limits.max_top_k:
            raise ResourceLimitError(
                f"max_top_k must be within the {self.limits.max_top_k}-result limit."
            )
        try:
            embed = provider.embed
            index_identity = index.identity
            search = index.search
            close = index.close
        except Exception:
            raise RetrievalError(
                "Retrieval provider/index could not be inspected safely."
            ) from None
        if (
            not callable(embed)
            or type(index_identity) is not IndexIdentity
            or not callable(search)
            or not callable(close)
        ):
            raise RetrievalError("Retrieval provider/index do not match the required contract.")
        self.provider_embed = embed
        self.index_identity = index_identity
        self.index_search = search
        self.index_close = close
        self.provider = provider
        self.index = index
        self.max_top_k = effective_max

    def search(self, query: RetrievalQuery) -> RetrievalResult:
        if type(query) is not RetrievalQuery:
            raise RetrievalError("Retrieval query must be a RetrievalQuery instance.")
        try:
            index_identity = self.index.identity
        except Exception:
            raise IndexCorruptionError("Vector index identity could not be read safely.") from None
        if type(index_identity) is not IndexIdentity:
            raise IndexCorruptionError("Vector index returned a non-IndexIdentity identity.")
        try:
            index_embedding_key = index_identity.embedding.identity_key()
        except Exception:
            raise IndexCorruptionError(
                "Vector index embedding identity could not be read safely."
            ) from None
        check_utf8_bytes(
            query.text,
            max_bytes=self.limits.max_query_bytes,
            label="Query text",
        )
        if query.top_k > self.max_top_k or query.top_k > self.limits.max_top_k:
            raise ResourceLimitError(
                f"top_k exceeds the configured retrieval limit of {self.max_top_k}."
            )
        try:
            batch = self.provider_embed([query.text])
        except Exception:
            raise RetrievalError(
                "The embedding provider failed to produce a query embedding."
            ) from None
        if type(batch) is not EmbeddingBatch:
            raise RetrievalError("The embedding provider returned an unexpected result type.")
        if len(batch.vectors) != 1:
            raise RetrievalError("Query embedding returned unexpected cardinality.")
        vector = batch.vectors[0]
        if batch.identity.identity_key() != index_embedding_key:
            raise RetrievalError("Query embedding identity is incompatible with the index.")
        candidate_limit = min(max(query.top_k * 4, 1), self.limits.max_candidate_count)
        try:
            candidates = self.index_search(vector, limit=candidate_limit)
            hits: list[RetrievalHit] = []
            for index, candidate in enumerate(candidates, start=1):
                if index > candidate_limit:
                    raise HostileIndexError("Vector index returned more candidates than requested.")
                if type(candidate) is not tuple or len(candidate) != 2:
                    raise IndexCorruptionError("Vector index returned a malformed candidate entry.")
                record, score = candidate
                self._validate_candidate(record, score)
                if score >= query.min_score:
                    hits.append(self._to_hit(record, score))
        except (RetrievalError, IndexCorruptionError, HostileIndexError):
            raise
        except Exception:
            raise RetrievalError("The vector index failed during search.") from None
        hits = _deduplicate(hits, query.dedup_by)
        hits = sorted(
            hits,
            key=lambda hit: (-hit.score, hit.chunk_id, hit.document_digest),
        )[: query.top_k]
        ranked: list[RetrievalHit] = []
        for index, hit in enumerate(hits):
            data = hit.model_dump()
            data["rank"] = index + 1
            ranked.append(RetrievalHit(**data))
        return RetrievalResult(
            query=query,
            hits=tuple(ranked),
            index_identity=index_identity,
        )

    def smoke_test(
        self,
        *,
        query_text: str,
        expected_chunk_ids: Iterable[str],
        expected_source_ids: Iterable[str],
        top_k: int = 10,
    ) -> RetrievalSmokeRecord:
        validated_top_k = require_strict_int(top_k, label="Smoke test top_k")
        if validated_top_k < 1 or validated_top_k > self.limits.max_top_k:
            raise ResourceLimitError("Smoke test top_k exceeds the retrieval limit.")
        expected_chunks = _bounded_items(
            expected_chunk_ids,
            max_items=self.limits.max_smoke_expectations,
            max_bytes=self.limits.max_string_bytes,
            label="Expected chunk ids",
        )
        expected_sources = _bounded_items(
            expected_source_ids,
            max_items=self.limits.max_smoke_expectations,
            max_bytes=self.limits.max_string_bytes,
            label="Expected source ids",
        )
        result = self.search(RetrievalQuery(text=query_text, top_k=validated_top_k, min_score=-1.0))
        observed_chunks = [hit.chunk_id for hit in result.hits]
        observed_sources = list(dict.fromkeys(hit.document_digest for hit in result.hits))
        failures: list[str] = []
        for expected in expected_chunks:
            if expected not in observed_chunks:
                _append_failure(failures, "missing_chunk", self.limits)
        for expected in expected_sources:
            if expected not in observed_sources:
                _append_failure(failures, "missing_source", self.limits)
        return RetrievalSmokeRecord(
            query=query_text,
            expected_chunk_ids=tuple(expected_chunks),
            expected_source_ids=tuple(expected_sources),
            observed_chunk_ids=tuple(observed_chunks),
            observed_source_ids=tuple(observed_sources),
            passed=not failures,
            failures=tuple(failures),
        )

    @staticmethod
    def require_backend(index: VectorIndex) -> None:
        if index is None:
            raise BackendUnavailableError("Vector backend is not available.")
        try:
            identity = index.identity
            search = index.search
            close = index.close
        except Exception:
            raise BackendUnavailableError(
                "Vector backend does not expose the required index contract."
            ) from None
        if type(identity) is not IndexIdentity or not callable(search) or not callable(close):
            raise BackendUnavailableError(
                "Vector backend does not match the required index contract."
            )

    @classmethod
    def open_persistent(
        cls,
        location: str,
        *,
        provider: EmbeddingProvider,
        expected_identity: IndexIdentity | None = None,
        store: Any | None = None,
        max_top_k: int | None = None,
        limits: KnowledgeLimits | None = None,
    ) -> RetrievalService:
        """Open a persisted local index and bind it to a retrieval provider.

        The index is opened lazily and honestly through the persistent provider
        adapter; an unavailable, corrupt, or incompatible backend surfaces as a
        structured error rather than a silent fallback.
        """
        from zana_core.knowledge.lancedb_index import LanceDBIndex

        index = LanceDBIndex.open(
            location,
            expected_identity=expected_identity,
            store=store,
            limits=limits,
        )
        return cls(
            provider=provider,
            index=index,
            max_top_k=max_top_k,
            limits=limits,
        )

    def _validate_candidate(self, record: VectorRecord, score: float) -> None:
        if type(record) is not VectorRecord:
            raise IndexCorruptionError("Vector index returned a non-VectorRecord candidate.")
        try:
            validated_score = require_finite_number(score, label="Candidate score")
        except ResourceLimitError:
            raise HostileIndexError(
                "Vector index returned a non-finite or non-numeric score."
            ) from None
        if not (-1.0 <= validated_score <= 1.0):
            raise HostileIndexError("Vector index returned an out-of-range score.")
        try:
            check_utf8_bytes(
                record.chunk_id,
                max_bytes=self.limits.max_key_bytes,
                label="Candidate chunk_id",
            )
            check_utf8_bytes(
                record.document_digest,
                max_bytes=self.limits.max_string_bytes,
                label="Candidate document_digest",
            )
            check_utf8_bytes(
                record.source_title,
                max_bytes=self.limits.max_string_bytes,
                label="Candidate source_title",
            )
            check_utf8_bytes(
                record.section_id,
                max_bytes=self.limits.max_string_bytes,
                label="Candidate section_id",
            )
            check_utf8_bytes(
                record.text,
                max_bytes=self.limits.max_chunk_text_bytes,
                label="Candidate text",
            )
            if len(record.heading_path) > self.limits.max_heading_depth:
                raise HostileIndexError(
                    "Vector index returned an over-deep candidate heading path."
                )
            if type(record.metadata_json) is not FrozenMetadata:
                raise HostileIndexError("Vector index returned invalid metadata.")
        except (ResourceLimitError, ValueError):
            raise HostileIndexError(
                "Vector index returned an over-limit or malformed candidate."
            ) from None

    def _to_hit(self, record: VectorRecord, score: float) -> RetrievalHit:
        return RetrievalHit(
            chunk_id=record.chunk_id,
            document_digest=record.document_digest,
            source_title=record.source_title,
            page_start=record.page_start,
            page_end=record.page_end,
            heading_path=tuple(record.heading_path),
            section_id=record.section_id,
            text=record.text,
            score=score,
            rank=1,
        )


def _deduplicate(
    hits: list[RetrievalHit],
    dedup_by: str | None,
) -> list[RetrievalHit]:
    if dedup_by is None:
        return hits
    seen: set[str] = set()
    selected: list[RetrievalHit] = []
    for hit in hits:
        key = hit.document_digest if dedup_by == "document" else hit.section_id
        if key in seen:
            continue
        seen.add(key)
        selected.append(hit)
    return selected


def _bounded_items(
    items: Iterable[str],
    *,
    max_items: int,
    max_bytes: int,
    label: str,
) -> list[str]:
    result: list[str] = []
    try:
        for item in items:
            if len(result) >= max_items:
                raise ResourceLimitError(f"{label} exceed the {max_items}-item limit.")
            if type(item) is not str:
                raise ValueError(f"{label} must contain only exact strings.")
            check_utf8_bytes(item, max_bytes=max_bytes, label=label)
            result.append(item)
    except (ResourceLimitError, ValueError):
        raise
    except Exception:
        raise ResourceLimitError(f"{label} could not be iterated safely.") from None
    return result


def _append_failure(
    failures: list[str],
    code: str,
    limits: KnowledgeLimits,
) -> None:
    if len(failures) >= limits.max_smoke_failures:
        raise ResourceLimitError(
            f"Smoke-test failures exceed the {limits.max_smoke_failures}-item limit."
        )
    failures.append(code)
