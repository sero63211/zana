"""Deterministic heading-aware chunking tests."""

from __future__ import annotations

from zana_core.knowledge.chunker import HeadingAwareChunker, TextEstimator
from zana_core.knowledge.models import ChunkConfiguration, NormalizedDocument, NormalizedSection


def section_document() -> NormalizedDocument:
    section = NormalizedSection(
        section_id="sec1",
        heading_path=["Chapter 1", "Section 2"],
        page_start=4,
        page_end=5,
        text="word " * 300,
        start_offset=100,
        end_offset=700,
    )
    return NormalizedDocument(
        document_id="sha256:" + "0" * 64,
        title="T",
        sections=[section],
    )


class TestHeadingAwareChunker:
    def test_chunk_boundaries_and_overlap_stay_in_section(self) -> None:
        config = ChunkConfiguration(
            target_tokens=40,
            max_tokens=60,
            overlap_tokens=6,
        )
        chunker = HeadingAwareChunker(config)
        document = section_document()
        chunks = chunker.chunk_document(document)

        assert len(chunks) > 1
        assert all(chunk.section_id == "sec1" for chunk in chunks)
        assert all(list(chunk.heading_path) == ["Chapter 1", "Section 2"] for chunk in chunks)
        assert all(chunk.page_start == 4 and chunk.page_end == 5 for chunk in chunks)
        assert all(chunk.token_estimate <= config.max_tokens for chunk in chunks)
        for chunk in chunks[1:]:
            assert chunk.overlap_prefix is not None

    def test_deterministic_ids_and_ordering(self) -> None:
        chunker = HeadingAwareChunker(ChunkConfiguration(target_tokens=40))
        first = chunker.chunk_document(section_document())
        second = chunker.chunk_document(section_document())

        assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
        assert [chunk.start_offset for chunk in first] == [chunk.start_offset for chunk in second]

    def test_overlap_never_merges_sections(self) -> None:
        chunker = HeadingAwareChunker(ChunkConfiguration(target_tokens=30, overlap_tokens=10))
        document = NormalizedDocument(
            document_id="sha256:" + "0" * 64,
            title="T",
            sections=[
                NormalizedSection(
                    section_id="a",
                    heading_path=["A"],
                    text="alpha " * 60,
                    start_offset=0,
                    end_offset=360,
                ),
                NormalizedSection(
                    section_id="b",
                    heading_path=["B"],
                    text="beta " * 60,
                    start_offset=400,
                    end_offset=760,
                ),
            ],
        )
        chunks = chunker.chunk_document(document)
        assert {chunk.section_id for chunk in chunks} == {"a", "b"}
        assert all("beta" not in chunk.text for chunk in chunks if chunk.section_id == "a")
        assert all("alpha" not in chunk.text for chunk in chunks if chunk.section_id == "b")

    def test_estimator_identity_is_recorded(self) -> None:
        chunker = HeadingAwareChunker(ChunkConfiguration(target_tokens=40))
        chunk = chunker.chunk_document(section_document())[0]
        assert chunk.tokenizer_identity == "zana.text-estimator.v1"
        assert TextEstimator().estimate("a b c") == 3
