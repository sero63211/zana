"""Explicit parser-provider protocol. PDF/Docling is deferred, never faked."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from zana_core.knowledge.models import (
    DocumentKind,
    NormalizedDocument,
    ParserError,
    SourceMetadata,
)


class DocumentParser(Protocol):
    """Provider contract for document normalization."""

    parser_version: str
    supported_kinds: frozenset[DocumentKind]

    def parse(self, source: SourceMetadata) -> NormalizedDocument: ...


def unsupported_pdf_parser_error() -> ParserError:
    return ParserError(
        code="PARSER_UNAVAILABLE",
        message=(
            "PDF parsing via Docling is not installed in this bounded lane. "
            "No PDF content was read or claimed."
        ),
        recoverable=True,
        actions=["install_docling_later", "use_markdown_or_text"],
    )


def parse_sources(
    sources: Sequence[SourceMetadata],
    parser: DocumentParser,
) -> tuple[list[NormalizedDocument], list[ParserError]]:
    """Parse approved sources with the injected provider.

    Any source whose kind cannot be handled by the provider is recorded as a
    structured error; no fake success or partial claim is produced.
    """
    documents: list[NormalizedDocument] = []
    errors: list[ParserError] = []
    for source in sources:
        if source.kind not in parser.supported_kinds:
            error = ParserError(
                code="PARSER_UNAVAILABLE",
                message=(
                    f"Parsing {source.kind.value} is not supported by "
                    f"{parser.parser_version}; no content was read or claimed."
                ),
                recoverable=True,
                actions=["use_markdown_or_text", "install_supported_parser"],
            )
            errors.append(error)
            continue
        try:
            documents.append(parser.parse(source))
        except Exception as error:  # noqa: BLE001 - provider boundary converts to typed errors
            errors.append(
                ParserError(
                    code="PARSE_FAILED",
                    message="The parser provider failed to process this source.",
                    recoverable=True,
                    actions=["retry_source"],
                )
            )
    return documents, errors
