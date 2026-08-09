"""Deterministic zana.heading-aware.v1 structural chunking."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

from zana_core.knowledge.models import (
    Chunk,
    ChunkConfiguration,
    NormalizedDocument,
    NormalizedSection,
)

_WORD_RE = re.compile(r"\S+")


@dataclass(frozen=True)
class TextEstimator:
    """Deterministic text-length token estimator recorded in chunk identity."""

    identity: str = "zana.text-estimator.v1"
    words_per_token: float = 1.0

    def estimate(self, text: str) -> int:
        if not text:
            return 0
        return max(1, len(_WORD_RE.findall(text)))


DEFAULT_ESTIMATOR = TextEstimator()


def estimate_tokens(text: str, estimator: TextEstimator = DEFAULT_ESTIMATOR) -> int:
    return estimator.estimate(text)


class HeadingAwareChunker:
    """Chunks sections independently; overlap never crosses sections."""

    def __init__(
        self,
        config: ChunkConfiguration | None = None,
        estimator: TextEstimator = DEFAULT_ESTIMATOR,
    ) -> None:
        self.config = config or ChunkConfiguration()
        self.estimator = estimator

    def chunk_document(self, document: NormalizedDocument) -> list[Chunk]:
        chunks: list[Chunk] = []
        for section in document.sections:
            chunks.extend(self.chunk_section(document.document_id, section))
        return chunks

    def chunk_section(
        self,
        document_digest: str,
        section: NormalizedSection,
    ) -> list[Chunk]:
        text = section.text
        target = self.config.target_tokens
        max_tokens = max(target, self.config.max_tokens)
        overlap = min(self.config.overlap_tokens, target - 1)
        chunks: list[Chunk] = []
        offset = section.start_offset
        cursor = 0
        index = 0

        while cursor < len(text):
            window_end = self._window_end(text, cursor, target, max_tokens)
            window = text[cursor:window_end]
            if not window.strip():
                break
            token_estimate = self.estimator.estimate(window)
            if token_estimate < self.config.min_chunk_tokens:
                break
            chunk_id = self._chunk_id(document_digest, section.section_id, index)
            overlap_prefix = None
            if index > 0 and overlap > 0:
                overlap_start = max(0, cursor - overlap)
                overlap_prefix = text[overlap_start:cursor].strip() or None
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    document_digest=document_digest,
                    section_id=section.section_id,
                    heading_path=list(section.heading_path),
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
            )
            next_cursor = self._next_cursor(text, window_end, target, max_tokens, overlap)
            if next_cursor <= cursor:
                break
            cursor = next_cursor
            index += 1
        return chunks

    def _window_end(
        self,
        text: str,
        start: int,
        target: int,
        max_tokens: int,
    ) -> int:
        low = start
        high = len(text)
        while low < high:
            mid = (low + high + 1) // 2
            if self.estimator.estimate(text[start:mid]) <= target:
                low = mid
            else:
                high = mid - 1
        candidate = low
        if candidate >= len(text):
            return len(text)
        next_newline = text.find("\n", candidate + 1)
        if next_newline != -1 and next_newline > candidate:
            candidate = next_newline
        if self.estimator.estimate(text[start:candidate]) > max_tokens:
            candidate = low
        return candidate

    def _next_cursor(
        self,
        text: str,
        window_end: int,
        target: int,
        max_tokens: int,
        overlap: int,
    ) -> int:
        if window_end >= len(text):
            return len(text)
        advance = window_end
        if advance <= 0:
            return len(text)
        candidate = max(advance - overlap, advance - target)
        if candidate <= 0:
            return window_end
        return candidate

    @staticmethod
    def _chunk_id(document_digest: str, section_id: str, index: int) -> str:
        raw = f"{document_digest}:{section_id}:{index}"
        return f"{document_digest}:c{index}:{hashlib.sha256(raw.encode('utf-8')).hexdigest()[:16]}"
