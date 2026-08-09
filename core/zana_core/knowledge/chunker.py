"""Deterministic zana.heading-aware.v1 structural chunking with hard caps.

Window boundaries are found with bounded linear word scans instead of a
repeated-slicing binary search.  A window ends before the first word that would
exceed ``target`` tokens, extended to the next newline only when that extended
window stays within ``max_tokens``.  Overlap is expressed in tokens through
the same estimator and never splits words.  Chunk ids, ordering, offsets, and
section-local overlap remain deterministic.
"""

from __future__ import annotations

import hashlib
import math
from collections.abc import Iterator
from dataclasses import dataclass

from zana_core.knowledge.limits import (
    HARD_MAX_STRING_BYTES,
    HARD_MAX_TEXT_BYTES,
    HARD_MAX_WORDS_PER_TOKEN,
    KnowledgeLimits,
    ResourceLimitError,
    check_deadline,
    check_utf8_bytes,
    make_deadline,
    require_finite_number,
    require_strict_int,
)
from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    NormalizedDocument,
    NormalizedSection,
    validate_canonical_sha256,
)


class ChunkLimitError(ResourceLimitError):
    """Raised when chunking would exceed configured document/chunk budgets."""


@dataclass(frozen=True)
class TextEstimator:
    """Deterministic bounded text-length token estimator."""

    identity: str = "zana.text-estimator.v1"
    words_per_token: float = 1.0

    def __post_init__(self) -> None:
        if type(self.identity) is not str:
            raise ValueError("TextEstimator identity must be an exact string.")
        try:
            check_utf8_bytes(
                self.identity,
                max_bytes=HARD_MAX_STRING_BYTES,
                label="TextEstimator identity",
            )
        except ResourceLimitError:
            raise ValueError("TextEstimator identity exceeds the bounded UTF-8 limit.") from None
        try:
            validated = require_finite_number(self.words_per_token, label="words_per_token")
        except ResourceLimitError:
            raise ValueError("words_per_token must be a finite number.") from None
        if validated <= 0 or validated > HARD_MAX_WORDS_PER_TOKEN:
            raise ValueError(f"words_per_token must be between 0 and {HARD_MAX_WORDS_PER_TOKEN}.")
        object.__setattr__(self, "words_per_token", validated)

    def estimate(self, text: str) -> int:
        if type(text) is not str:
            raise ValueError("Token estimation requires an exact string.")
        try:
            check_utf8_bytes(
                text,
                max_bytes=HARD_MAX_TEXT_BYTES,
                label="Token estimation text",
            )
        except ResourceLimitError:
            raise ValueError("Token estimation text exceeds the bounded UTF-8 limit.") from None
        if not text:
            return 0
        return max(1, math.ceil(self._word_count(text) / self.words_per_token))

    @staticmethod
    def _word_count(text: str) -> int:
        count = 0
        in_word = False
        for char in text:
            if char.isspace():
                in_word = False
            elif not in_word:
                count += 1
                in_word = True
        return count

    def estimate_word_count(self, word_count: int) -> int:
        try:
            validated = require_strict_int(word_count, label="word_count")
        except ResourceLimitError:
            raise ValueError("word_count must be a non-negative integer.") from None
        if validated < 0:
            raise ValueError("word_count must be non-negative.")
        return max(1, math.ceil(validated / self.words_per_token))


DEFAULT_ESTIMATOR = TextEstimator()


def estimate_tokens(text: str, estimator: TextEstimator = DEFAULT_ESTIMATOR) -> int:
    if type(text) is not str:
        raise ChunkLimitError("Token estimation requires an exact string.")
    if type(estimator) is not TextEstimator:
        raise ChunkLimitError("Token estimation requires an exact TextEstimator.")
    return estimator.estimate(text)


class HeadingAwareChunker:
    """Chunks sections independently; overlap never crosses sections."""

    def __init__(
        self,
        config: ChunkConfiguration | None = None,
        estimator: TextEstimator = DEFAULT_ESTIMATOR,
        limits: KnowledgeLimits | None = None,
    ) -> None:
        if config is not None and type(config) is not ChunkConfiguration:
            raise ChunkLimitError(
                "Chunker configuration must be an exact ChunkConfiguration instance."
            )
        if type(estimator) is not TextEstimator:
            raise ChunkLimitError("Chunker estimator must be an exact TextEstimator instance.")
        if limits is not None and type(limits) is not KnowledgeLimits:
            raise ChunkLimitError("Chunker limits must be an exact KnowledgeLimits instance.")
        self.config = config if config is not None else ChunkConfiguration()
        self.estimator = estimator
        self.limits = limits if limits is not None else KnowledgeLimits()

    def chunk_document(
        self,
        document: NormalizedDocument,
        deadline_seconds: float | None = None,
    ) -> list[Chunk]:
        if type(document) is not NormalizedDocument:
            raise ChunkLimitError("Chunking requires an exact NormalizedDocument instance.")
        if type(document.document_id) is not str:
            raise ChunkLimitError("Document id must be an exact string.")
        try:
            validate_canonical_sha256(document.document_id)
        except ValueError:
            raise ChunkLimitError(
                "Document id must be a canonical sha256 content digest."
            ) from None
        deadline = make_deadline(deadline_seconds, hard_max=self.limits.max_timeout_seconds)
        if len(document.sections) > self.limits.max_section_count:
            raise ChunkLimitError(
                f"Document exceeds the {self.limits.max_section_count}-section limit."
            )
        chunks: list[Chunk] = []
        for section in document.sections:
            check_deadline(deadline, label="document chunking")
            remaining = self.limits.max_chunk_count - len(chunks)
            for chunk in self._iter_section_chunks(
                document.document_id,
                section,
                limit=remaining,
                deadline=deadline,
            ):
                chunks.append(chunk)
        return chunks

    def chunk_section(
        self,
        document_digest: str,
        section: NormalizedSection,
        deadline_seconds: float | None = None,
    ) -> list[Chunk]:
        if type(section) is not NormalizedSection:
            raise ChunkLimitError("Chunking requires an exact NormalizedSection instance.")
        if type(document_digest) is not str:
            raise ChunkLimitError("Document digest must be an exact string.")
        try:
            validate_canonical_sha256(document_digest)
        except ValueError:
            raise ChunkLimitError(
                "Document digest must be a canonical sha256 content digest."
            ) from None
        deadline = make_deadline(deadline_seconds, hard_max=self.limits.max_timeout_seconds)
        return list(
            self._iter_section_chunks(
                document_digest,
                section,
                limit=self.limits.max_chunk_count,
                deadline=deadline,
            )
        )

    def _iter_section_chunks(
        self,
        document_digest: str,
        section: NormalizedSection,
        *,
        limit: int,
        deadline: float,
    ) -> Iterator[Chunk]:
        if len(section.heading_path) > self.limits.max_heading_depth:
            raise ChunkLimitError(
                f"Heading path exceeds the {self.limits.max_heading_depth}-level limit."
            )
        _check_chunk_text(
            section.text,
            max_bytes=self.limits.max_text_bytes,
            label="Section text",
        )
        text = section.text
        target = self.config.target_tokens
        max_tokens = self.config.max_tokens
        overlap = min(self.config.overlap_tokens, target - 1)
        offset = section.start_offset
        cursor = 0
        index = 0

        while cursor < len(text):
            check_deadline(deadline, label="section chunking")
            if index >= limit:
                raise ChunkLimitError(
                    f"Chunking exceeds the {self.limits.max_chunk_count}-chunk limit."
                )
            window_end = self._window_end(text, cursor, target, max_tokens)
            window = text[cursor:window_end]
            if not window.strip():
                break
            token_estimate = self.estimator.estimate(window)
            if token_estimate < self.config.min_chunk_tokens:
                break
            _check_chunk_text(
                window,
                max_bytes=self.limits.max_chunk_text_bytes,
                label="Chunk text",
            )
            chunk_id = self._chunk_id(document_digest, section.section_id, index)
            overlap_prefix = None
            if index > 0 and overlap > 0:
                overlap_start = self._overlap_start(text, cursor, overlap)
                if overlap_start < cursor:
                    overlap_prefix = text[overlap_start:cursor].strip() or None
            yield Chunk(
                chunk_id=chunk_id,
                document_digest=document_digest,
                section_id=section.section_id,
                heading_path=tuple(section.heading_path),
                page_start=section.page_start,
                page_end=section.page_end,
                start_offset=offset + cursor,
                end_offset=offset + window_end,
                text=window,
                token_estimate=token_estimate,
                tokenizer_identity=self.estimator.identity,
                overlap_prefix=overlap_prefix,
                metadata_json={
                    "chunker": "zana.heading-aware.v1",
                    "chunk_index": index,
                },
            )
            next_cursor = self._next_cursor(text, window_end, overlap)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            index += 1

    def _window_end(
        self,
        text: str,
        start: int,
        target: int,
        max_tokens: int,
    ) -> int:
        """Find the chunk end with one bounded linear word scan."""
        end = len(text)
        if start >= end:
            return end
        boundary = end
        word_count = 0
        in_word = False
        for index in range(start, end):
            char = text[index]
            if char.isspace():
                in_word = False
            elif not in_word:
                word_count += 1
                in_word = True
                if self.estimator.estimate_word_count(word_count) > target:
                    boundary = index
                    break
        if boundary >= end:
            return end
        next_newline = text.find("\n", boundary + 1)
        if (
            next_newline != -1
            and next_newline < end
            and self.estimator.estimate_word_count(self._count_words(text, start, next_newline))
            <= max_tokens
        ):
            boundary = next_newline
        return boundary

    @staticmethod
    def _count_words(text: str, start: int, end: int) -> int:
        count = 0
        in_word = False
        for index in range(start, end):
            char = text[index]
            if char.isspace():
                in_word = False
            elif not in_word:
                count += 1
                in_word = True
        return count

    def _overlap_start(self, text: str, end: int, overlap_tokens: int) -> int:
        """Return the earliest token-aligned start for an overlap window."""
        if overlap_tokens <= 0 or end <= 0:
            return 0
        starts: list[int] = []
        index = end - 1
        while index >= 0:
            if not text[index].isspace() and (index == 0 or text[index - 1].isspace()):
                starts.append(index)
                if self.estimator.estimate_word_count(len(starts)) >= overlap_tokens:
                    break
            index -= 1
        return starts[-1] if starts else 0

    def _next_cursor(
        self,
        text: str,
        window_end: int,
        overlap: int,
    ) -> int:
        if window_end >= len(text):
            return len(text)
        candidate = self._overlap_start(text, window_end, overlap)
        if candidate > 0 and candidate < window_end:
            return candidate
        index = window_end
        while index < len(text) and not text[index].isspace():
            index += 1
        while index < len(text) and text[index].isspace():
            index += 1
        return index if index > window_end else len(text)

    @staticmethod
    def _chunk_id(document_digest: str, section_id: str, index: int) -> str:
        raw = f"{document_digest}:{section_id}:{index}"
        return f"{document_digest}:c{index}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"


def _check_chunk_text(value: str, *, max_bytes: int, label: str) -> None:
    try:
        check_utf8_bytes(value, max_bytes=max_bytes, label=label)
    except ResourceLimitError:
        raise ChunkLimitError(f"{label} exceeds the configured UTF-8 byte limit.") from None
