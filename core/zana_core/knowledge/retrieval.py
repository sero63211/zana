"""Deterministic retrieval service and compatibility decisions."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from zana_core.knowledge.embeddings import (
    BackendUnavailableError,
    EmbeddingIdentity,
    EmbeddingProvider,
    IndexIdentity,
    ResourceLimitError,
    VectorIndex,
)

DEFAULT_MAX_TOP_K = 100


class RetrievalQuery(BaseModel):
    """Immutable retrieval query."""

    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    top_k: int = Field(default=5, gt=0)
    min_score: float = Field(default=0.0, ge=-1, le=1)
    dedup_by: str | None = Field(default=None, pattern="^(document|section)$")


class RetrievalHit(BaseModel):
    """One deterministic retrieval hit with stable provenance."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_digest: str
    source_title: str
    page_start: int | None = None
    page_end: int | None = None
    heading_path: list[str] = Field(default_factory=list)
    section_id: str = ""
    text: str = ""
    score: float
    rank: int = Field(gt=0)


class RetrievalResult(BaseModel):
    """Structured retrieval result for the evidence/context composer."""

    model_config = ConfigDict(frozen=True)

    query: RetrievalQuery
    hits: list[RetrievalHit] = Field(default_factory=list)
    index_identity: IndexIdentity
    queried_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class RetrievalSmokeRecord(BaseModel):
    """Honest declared-query smoke-test record; no model judge."""

    model_config = ConfigDict(frozen=True)

    query: str
    expected_chunk_ids: list[str] = Field(default_factory=list)
    expected_source_ids: list[str] = Field(default_factory=list)
    observed_chunk_ids: list[str] = Field(default_factory=list)
    observed_source_ids: list[str] = Field(default_factory=list)
    passed: bool
    failures: list[str] = Field(default_factory=list)
    ran_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


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
        max_top_k: int = DEFAULT_MAX_TOP_K,
    ) -> None:
        self.provider = provider
        self.index = index
        self.max_top_k = max_top_k

    def search(self, query: RetrievalQuery) -> RetrievalResult:
        if query.top_k > self.max_top_k:
            raise ResourceLimitError(
                f"top_k exceeds the configured retrieval limit of {self.max_top_k}."
            )
        batch = self.provider.embed([query.text])
        if len(batch.vectors) != 1:
            raise ValueError("Query embedding returned unexpected cardinality.")
        vector = batch.vectors[0]
        if batch.identity.identity_key() != self.index.identity.embedding.identity_key():
            raise ValueError("Query embedding identity is incompatible with the index.")
        candidates = self.index.search(vector, limit=max(query.top_k * 4, 1))
        hits = [
            RetrievalHit(
                chunk_id=record.chunk_id,
                document_digest=record.document_digest,
                source_title=record.source_title,
                page_start=record.page_start,
                page_end=record.page_end,
                heading_path=list(record.heading_path),
                section_id=record.section_id,
                text=record.text,
                score=score,
                rank=1,
            )
            for record, score in candidates
            if score >= query.min_score
        ]
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
        hits = ranked
        return RetrievalResult(
            query=query,
            hits=hits,
            index_identity=self.index.identity,
        )

    def smoke_test(
        self,
        *,
        query_text: str,
        expected_chunk_ids: Sequence[str],
        expected_source_ids: Sequence[str],
        top_k: int = 10,
    ) -> RetrievalSmokeRecord:
        result = self.search(RetrievalQuery(text=query_text, top_k=top_k, min_score=-1.0))
        observed_chunks = [hit.chunk_id for hit in result.hits]
        observed_sources = list(dict.fromkeys(hit.document_digest for hit in result.hits))
        failures: list[str] = []
        for expected in expected_chunk_ids:
            if expected not in observed_chunks:
                failures.append(f"missing_chunk:{expected}")
        for expected in expected_source_ids:
            if expected not in observed_sources:
                failures.append(f"missing_source:{expected}")
        return RetrievalSmokeRecord(
            query=query_text,
            expected_chunk_ids=list(expected_chunk_ids),
            expected_source_ids=list(expected_source_ids),
            observed_chunk_ids=observed_chunks,
            observed_source_ids=observed_sources,
            passed=not failures,
            failures=failures,
        )

    @staticmethod
    def require_backend(index: VectorIndex) -> None:
        if index is None:
            raise BackendUnavailableError("Vector backend is not available.")


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
