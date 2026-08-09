"""Explicit parser-provider protocol. PDF/Docling is deferred, never faked."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from zana_core.knowledge.limits import (
    HARD_MAX_DOCUMENT_RETAINED_BYTES,
    DeadlineExceededError,
    KnowledgeLimits,
    ResourceLimitError,
    RetainedByteBudget,
    check_deadline,
    check_utf8_bytes,
    make_deadline,
    resolve_limits,
)
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

    def parse(
        self,
        source: SourceMetadata,
        *,
        deadline: float | None = None,
    ) -> NormalizedDocument: ...


def unsupported_pdf_parser_error() -> ParserError:
    return ParserError(
        code="PARSER_UNAVAILABLE",
        message=(
            "PDF parsing via Docling is not installed in this bounded lane. "
            "No PDF content was read or claimed."
        ),
        recoverable=True,
        actions=("install_docling_later", "use_markdown_or_text"),
    )


def parse_sources(
    sources: Iterable[SourceMetadata],
    parser: DocumentParser,
    *,
    limits: KnowledgeLimits | None = None,
    deadline_seconds: float | None = None,
) -> tuple[list[NormalizedDocument], list[ParserError]]:
    """Parse approved sources under one shared absolute deadline.

    Any source whose kind cannot be handled by the provider is recorded as a
    structured error; no fake success or partial claim is produced.  The
    injected parser receives the absolute deadline so it can cooperate
    without threads or background workers.
    """
    active = resolve_limits(limits)
    try:
        parser_version = parser.parser_version
    except Exception:
        raise ResourceLimitError("Parser provider version could not be read safely.") from None
    if type(parser_version) is not str:
        raise ResourceLimitError("Parser provider version must be an exact string.")
    check_utf8_bytes(
        parser_version,
        max_bytes=active.max_string_bytes,
        label="Parser version",
    )
    try:
        supported_kinds = parser.supported_kinds
    except Exception:
        raise ResourceLimitError("Parser supported_kinds could not be read safely.") from None
    if type(supported_kinds) is not frozenset:
        raise ResourceLimitError("Parser supported_kinds must be an exact frozenset.")
    if len(supported_kinds) > len(DocumentKind):
        raise ResourceLimitError("Parser supported_kinds exceeds the document kind limit.")
    for kind in supported_kinds:
        if type(kind) is not DocumentKind:
            raise ResourceLimitError(
                "Parser supported_kinds must contain exact DocumentKind values."
            )
    try:
        parse = parser.parse
    except Exception:
        raise ResourceLimitError("Parser provider parse method could not be read safely.") from None
    if not callable(parse):
        raise ResourceLimitError("Parser provider parse must be callable.")
    deadline = make_deadline(deadline_seconds, hard_max=active.max_timeout_seconds)
    documents: list[NormalizedDocument] = []
    errors: list[ParserError] = []
    retained = RetainedByteBudget(HARD_MAX_DOCUMENT_RETAINED_BYTES, label="Parser outputs")
    try:
        source_iter = iter(sources)
    except Exception:
        raise ResourceLimitError("Parser input could not be iterated safely.") from None
    count = 0
    while True:
        try:
            source = next(source_iter)
        except StopIteration:
            break
        except Exception:
            raise ResourceLimitError("Parser input could not be iterated safely.") from None
        count += 1
        if count > active.max_source_count:
            raise ResourceLimitError(
                f"Source parsing exceeds the {active.max_source_count}-source limit."
            )
        check_deadline(deadline, label="document parsing")
        if type(source) is not SourceMetadata:
            raise ResourceLimitError("Parser sources must be SourceMetadata instances.")
        if source.kind not in supported_kinds:
            error = ParserError(
                code="PARSER_UNAVAILABLE",
                message=(
                    "Parsing this source kind is not supported by the configured "
                    "parser; no content was read or claimed."
                ),
                recoverable=True,
                actions=("use_markdown_or_text", "install_supported_parser"),
            )
            errors.append(error)
            _account_error(retained, error)
            continue
        try:
            parsed = parse(source, deadline=deadline)
            if type(parsed) is not NormalizedDocument:
                raise ResourceLimitError("Parser provider returned an unexpected result type.")
            _account_document(retained, parsed)
            documents.append(parsed)
            check_deadline(deadline, label="document parsing")
        except DeadlineExceededError:
            raise
        except Exception as exc:
            errors.append(_parser_failure(exc))
            _account_error(retained, errors[-1])
        if len(documents) > active.max_source_count or len(errors) > active.max_source_count:
            raise ResourceLimitError("Parsed output exceeded the configured source count limit.")
    return documents, errors


def _parser_failure(exc: Exception) -> ParserError:
    """Map a parser exception to a structured error, preserving honest states."""
    if getattr(exc, "code", None) == "PARSER_UNAVAILABLE":
        return ParserError(
            code="PARSER_UNAVAILABLE",
            message=str(exc) or "The configured parser is not available for this source.",
            recoverable=bool(getattr(exc, "recoverable", True)),
            actions=tuple(getattr(exc, "actions", ())),
        )
    return ParserError(
        code="PARSE_FAILED",
        message="The parser provider failed to process this source.",
        recoverable=True,
        actions=("retry_source",),
    )


def _account_document(
    budget: RetainedByteBudget,
    document: NormalizedDocument,
) -> None:
    budget.add(document.document_id, label="document_id")
    budget.add(document.title, label="title")
    for section in document.sections:
        budget.add(section.section_id, label="section_id")
        budget.add(section.text, label="section text")
        for heading in section.heading_path:
            budget.add(heading, label="heading")
    for warning in document.warnings:
        budget.add(warning.code, label="warning code")
        budget.add(warning.message, label="warning message")


def _account_error(budget: RetainedByteBudget, error: ParserError) -> None:
    budget.add(error.code, label="error code")
    budget.add(error.message, label="error message")
    for action in error.actions:
        budget.add(action, label="error action")
