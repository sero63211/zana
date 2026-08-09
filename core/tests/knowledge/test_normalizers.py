"""Markdown and UTF-8 normalization tests."""

from __future__ import annotations

from zana_core.knowledge.models import DocumentKind, ParserWarning
from zana_core.knowledge.normalizers import normalize_markdown, normalize_source, normalize_text


class TestTextNormalization:
    def test_deterministic_and_crlf_safe(self) -> None:
        source = "\ufeff# Title\r\nbody\r\n\r\n"
        normalized = normalize_text(source)
        assert normalized == "# Title\nbody\n"
        assert normalize_text(source) == normalize_text("\n# Title\nbody")

    def test_utf8_content_preserved(self) -> None:
        normalized = normalize_text("Größe — Straße ✓")
        assert "Größe" in normalized
        assert "Straße" in normalized


class TestMarkdownNormalization:
    def test_heading_and_section_structure(self) -> None:
        source = "# Chapter 1\n\nIntro text.\n\n## Section 2\n\nDetail here.\n"
        document = normalize_source(
            source,
            kind=DocumentKind.MARKDOWN,
            title="Policy Manual",
            document_id="sha256:" + "0" * 64,
        )
        assert document.title == "Policy Manual"
        assert len(document.sections) == 2
        assert list(document.sections[0].heading_path) == ["Chapter 1"]
        assert list(document.sections[1].heading_path) == ["Chapter 1", "Section 2"]
        assert "Intro text." in document.sections[0].text
        assert "Detail here." in document.sections[1].text

    def test_offsets_are_preserved(self) -> None:
        source = "# A\n\nbody one\n\n# B\n\nbody two\n"
        document = normalize_source(
            source,
            kind=DocumentKind.MARKDOWN,
            title="T",
            document_id="sha256:" + "0" * 64,
        )
        sections = document.sections
        assert sections[0].start_offset == 5
        assert sections[0].end_offset == 13
        assert sections[1].start_offset == 20
        assert sections[1].end_offset == 28

    def test_external_links_warn_without_ingesting(self) -> None:
        source = "# A\n\nSee [docs](https://example.com/x).\n"
        normalized, warnings = normalize_markdown(source)
        assert normalized
        assert any(
            isinstance(warning, ParserWarning) and warning.code == "EXTERNAL_LINK_NOT_INGESTED"
            for warning in warnings
        )
