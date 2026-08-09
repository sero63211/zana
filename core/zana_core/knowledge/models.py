"""Typed knowledge pipeline models."""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class DocumentKind(str, Enum):
    """Supported document source kinds."""

    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    UNSUPPORTED = "unsupported"


class ParserWarning(BaseModel):
    """Structured non-fatal warning from normalization."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    line: int | None = None
    column: int | None = None


class ParserError(BaseModel):
    """Structured fatal parser error that prevents use of a document."""

    model_config = ConfigDict(frozen=True)

    code: str
    message: str
    recoverable: bool = False
    actions: list[str] = Field(default_factory=list)


class SourceMetadata(BaseModel):
    """Metadata recorded from the approved intake step."""

    model_config = ConfigDict(frozen=True)

    original_path: str
    display_name: str
    kind: DocumentKind
    size_bytes: int
    sha256: str
    approved: bool = True
    extra: dict[str, Any] = Field(default_factory=dict)


class NormalizedSection(BaseModel):
    """One normalized section with its heading path and offsets."""

    model_config = ConfigDict(frozen=True)

    section_id: str
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    text: str
    start_offset: int
    end_offset: int


class NormalizedDocument(BaseModel):
    """Canonical normalized document."""

    model_config = ConfigDict(frozen=True)

    document_id: str
    title: str
    sections: list[NormalizedSection] = Field(default_factory=list)
    warnings: list[ParserWarning] = Field(default_factory=list)


class Chunk(BaseModel):
    """Deterministic structural chunk."""

    model_config = ConfigDict(frozen=True)

    chunk_id: str
    document_digest: str
    section_id: str
    heading_path: list[str] = Field(default_factory=list)
    page_start: int | None = None
    page_end: int | None = None
    start_offset: int
    end_offset: int
    text: str
    token_estimate: int
    tokenizer_identity: str
    overlap_prefix: str | None = None
    metadata_json: dict[str, Any] = Field(default_factory=dict)


class ChunkConfiguration(BaseModel):
    """Configuration used by zana.heading-aware.v1."""

    model_config = ConfigDict(frozen=True)

    target_tokens: int = 640
    max_tokens: int = 900
    overlap_tokens: int = 64
    tokenizer_identity: str = "zana.text-estimator.v1"
    min_chunk_tokens: int = 1


class SnapshotManifest(BaseModel):
    """Immutable knowledge snapshot manifest with invalidation inputs."""

    model_config = ConfigDict(frozen=True)

    snapshot_id: str
    parser_version: str
    chunk_config: ChunkConfiguration
    embedding_identity_required: str
    sources: list[SourceMetadata] = Field(default_factory=list)
    chunks: list[Chunk] = Field(default_factory=list)
    created_at: datetime


class EvidenceBlock(BaseModel):
    """Structured evidence block used for citations and context."""

    model_config = ConfigDict(frozen=True)

    source_id: str
    source_title: str
    page: int | None = None
    section: str | None = None
    heading_path: list[str] = Field(default_factory=list)
    text: str
    token_estimate: int
    similarity: float | None = None


class ContextPackage(BaseModel):
    """Context fitted to a deterministic token budget."""

    model_config = ConfigDict(frozen=True)

    evidence: list[EvidenceBlock] = Field(default_factory=list)
    total_tokens: int = 0
    fitted: bool = True
