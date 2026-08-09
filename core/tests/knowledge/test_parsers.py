"""Parser-provider protocol and honest PDF-unavailable tests."""

from __future__ import annotations

from zana_core.knowledge.models import DocumentKind, NormalizedDocument, ParserError, SourceMetadata
from zana_core.knowledge.parsers import (
    parse_sources,
    unsupported_pdf_parser_error,
)


class MarkdownParser:
    parser_version = "markdown-text.v1"
    supported_kinds = frozenset({DocumentKind.MARKDOWN, DocumentKind.TEXT})

    def parse(self, source: SourceMetadata) -> NormalizedDocument:
        return NormalizedDocument(
            document_id=source.sha256,
            title=source.display_name,
            sections=[],
            warnings=[],
        )


def source(kind: DocumentKind = DocumentKind.MARKDOWN) -> SourceMetadata:
    return SourceMetadata(
        original_path="/approved/doc",
        display_name="doc",
        kind=kind,
        size_bytes=10,
        sha256="sha256:doc",
    )


class TestParserProtocol:
    def test_injected_parser_produces_documents(self) -> None:
        documents, errors = parse_sources([source()], MarkdownParser())
        assert len(documents) == 1
        assert errors == []

    def test_pdf_is_honestly_unavailable(self) -> None:
        error = unsupported_pdf_parser_error()
        assert isinstance(error, ParserError)
        assert error.code == "PARSER_UNAVAILABLE"
        assert "Docling" in error.message
        assert "No PDF content was read or claimed." in error.message

    def test_pdf_source_records_error_not_fake_document(self) -> None:
        documents, errors = parse_sources([source(DocumentKind.PDF)], MarkdownParser())
        assert documents == []
        assert len(errors) == 1
        assert errors[0].code == "PARSER_UNAVAILABLE"
