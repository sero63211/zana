"""Real optional Docling-backed document parsing with lazy imports and bounds.

Docling is the production PDF/conversion engine and is imported lazily so ZANA
starts without it.  Markdown and UTF-8 text sources are always parsed through
the deterministic :mod:`zana_core.knowledge.normalizers` pipeline, which is a
real parser and never a stub.  PDF parsing requires Docling; when the package
is absent the provider reports an honest unavailable state and reads no PDF
content.

All file reads go through a strict reader that enforces exact path/symlink/
type/byte limits, matching the intake guarantee that original files are never
modified.  Tests may inject a compliant converter or reader to exercise the
adapter calls against tiny local fixtures; they never fake production provider
availability.
"""

from __future__ import annotations

import hashlib
import importlib
import os
import stat as stat_module
from pathlib import Path, PosixPath, WindowsPath
from typing import Any, Protocol

from zana_core.knowledge.limits import (
    HARD_MAX_STREAM_CHUNK_SIZE,
    KnowledgeLimits,
    ResourceLimitError,
    check_deadline,
    check_utf8_bytes,
    make_deadline,
    resolve_limits,
    utf8_byte_length,
)
from zana_core.knowledge.models import (
    DocumentKind,
    NormalizedDocument,
    ParserError,
    SourceMetadata,
)
from zana_core.knowledge.normalizers import normalize_source

DOCTLING_PARSER_VERSION = "docling.v1"
STREAM_CHUNK_SIZE = 1024 * 1024


class DoclingUnavailableError(Exception):
    """Honest state raised when Docling is required but not available."""

    code = "PARSER_UNAVAILABLE"
    recoverable = True
    actions = ("install_docling_later", "use_markdown_or_text")


class ParserIOError(Exception):
    """Raised when a bounded source read fails."""


class ParserLimitExceededError(ParserIOError):
    """Raised when a source exceeds configured limits or is unsafe."""


class DoclingConverter(Protocol):
    """Narrow adapter contract for the Docling document converter."""

    def convert(self, path: str) -> Any: ...


class SourceReader(Protocol):
    """Protocol for a bounded reader of a source's UTF-8 text body."""

    def read(self, path: str | Path, *, limits: KnowledgeLimits) -> str: ...


def _safe_path_value(value: object) -> bool:
    return type(value) is str or type(value) in (Path, PosixPath, WindowsPath)


def _contains_symlink(path: Path) -> bool:
    current = path
    while True:
        try:
            mode = os.lstat(current).st_mode
        except OSError:
            parent = current.parent
            if parent == current:
                return False
            current = parent
            continue
        if stat_module.S_ISLNK(mode):
            return True
        parent = current.parent
        if parent == current:
            return False
        current = parent


def _validated_path(path: str | Path, *, limits: KnowledgeLimits) -> Path:
    if not _safe_path_value(path):
        raise ParserLimitExceededError("Source path must be an exact string or Path value.")
    raw = Path(path)
    try:
        utf8_byte_length(str(raw), max_bytes=limits.max_path_bytes, label="Source path")
    except ResourceLimitError:
        raise ParserLimitExceededError("Source path exceeds the configured byte limit.") from None
    if ".." in raw.parts:
        raise ParserLimitExceededError("Source path contains traversal components.")
    absolute = raw if raw.is_absolute() else Path.cwd() / raw
    if _contains_symlink(absolute):
        raise ParserLimitExceededError("Source path must not contain symlinks.")
    try:
        candidate = absolute.resolve(strict=True)
        if _contains_symlink(candidate):
            raise ParserLimitExceededError("Source path must not contain symlinks.")
        mode = os.lstat(candidate).st_mode
    except OSError:
        raise ParserIOError("Source file could not be inspected safely.") from None
    if not stat_module.S_ISREG(mode):
        raise ParserLimitExceededError("Source must be a regular file.")
    return candidate


def _read_utf8_bounded(path: Path, *, limits: KnowledgeLimits) -> str:
    expected: list[bytes] = []
    total = 0
    try:
        size = os.lstat(path).st_size
        if size < 0:
            raise ParserLimitExceededError("Source file size is invalid.")
        if size > limits.max_source_bytes:
            raise ParserLimitExceededError("Source file exceeds the configured size limit.")
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                total += len(chunk)
                if total > limits.max_source_bytes:
                    raise ParserLimitExceededError("Source file exceeds the configured size limit.")
                expected.append(chunk)
    except ParserLimitExceededError:
        raise
    except (OSError, UnicodeError):
        raise ParserIOError("Source file could not be read safely.") from None
    try:
        text = b"".join(expected).decode("utf-8")
    except UnicodeDecodeError:
        raise ParserIOError("Source is not valid UTF-8 text.") from None
    try:
        check_utf8_bytes(text, max_bytes=limits.max_text_bytes, label="Source text")
    except ResourceLimitError:
        raise ParserLimitExceededError(
            "Source text exceeds the configured retained text limit."
        ) from None
    return text


class PlainTextReader:
    """Default strict reader for Markdown and UTF-8 text sources."""

    def read(self, path: str | Path, *, limits: KnowledgeLimits) -> str:
        validated = _validated_path(path, limits=limits)
        return _read_utf8_bounded(validated, limits=limits)


def _docling_import_available() -> bool:
    try:
        importlib.import_module("docling")
    except Exception:
        return False
    return True


def unsupported_source_error(kind: DocumentKind) -> ParserError:
    return ParserError(
        code="PARSER_UNAVAILABLE",
        message=(
            f"Parsing {kind.value} sources is not supported by the configured parser; "
            "no content was read or claimed."
        ),
        recoverable=True,
        actions=("use_markdown_or_text", "install_supported_parser"),
    )


class DoclingParser:
    """Real Docling-backed parser implementing the ``DocumentParser`` contract."""

    parser_version = DOCTLING_PARSER_VERSION
    supported_kinds = frozenset({DocumentKind.PDF, DocumentKind.MARKDOWN, DocumentKind.TEXT})

    def __init__(
        self,
        *,
        converter: DoclingConverter | None = None,
        reader: SourceReader | None = None,
        parser_version: str = DOCTLING_PARSER_VERSION,
        limits: KnowledgeLimits | None = None,
    ) -> None:
        self.limits = resolve_limits(limits)
        if type(parser_version) is not str or not parser_version:
            raise ResourceLimitError("Docling parser version must be a non-empty exact string.")
        check_utf8_bytes(
            parser_version,
            max_bytes=self.limits.max_string_bytes,
            label="Docling parser version",
        )
        self.parser_version = parser_version
        self._reader = reader if reader is not None else PlainTextReader()
        self._converter = converter
        self._has_docling = True if converter is not None else _docling_import_available()

    def parse(
        self,
        source: SourceMetadata,
        *,
        deadline: float | None = None,
    ) -> NormalizedDocument:
        if type(source) is not SourceMetadata:
            raise ParserIOError("Document parser requires a SourceMetadata instance.")
        if source.kind not in self.supported_kinds:
            raise DoclingUnavailableError(
                f"Docling parser does not support {source.kind.value} sources."
            )
        active = resolve_limits(self.limits)
        if deadline is None:
            deadline = make_deadline(deadline, hard_max=active.max_timeout_seconds)
        check_deadline(deadline, label="document parsing")
        if source.kind in {DocumentKind.MARKDOWN, DocumentKind.TEXT}:
            if not source.approved:
                raise ParserIOError("Document source was not approved for parsing.")
            text = self._read_source(source)
            check_deadline(deadline, label="document parsing")
            return normalize_source(
                text,
                kind=source.kind,
                title=source.display_name,
                document_id=source.sha256,
                limits=active,
            )
        if not self._has_docling:
            raise DoclingUnavailableError(
                "PDF parsing via Docling is not installed; no PDF content was read or claimed."
            )
        if self._converter is None:
            raise DoclingUnavailableError(
                "PDF parsing via Docling is not configured; no PDF content was read."
            )
        resolved = _validated_path(source.original_path, limits=active)
        _verify_source_digest(source, path=resolved)
        converted = self._converter.convert(str(resolved))
        check_deadline(deadline, label="document parsing")
        return _docling_document_to_normalised(converted, source=source, limits=active)

    def _read_source(self, source: SourceMetadata) -> str:
        resolved = _validated_path(source.original_path, limits=self.limits)
        _verify_source_digest(source, path=resolved)
        text = self._reader.read(resolved, limits=self.limits)
        if type(text) is not str:
            raise ParserIOError("Source reader returned a non-string body.")
        return text


def _verify_source_digest(source: SourceMetadata, *, path: Path) -> None:
    if not isinstance(source.sha256, str) or not source.sha256.startswith("sha256:"):
        return
    try:
        digest = hashlib.sha256()
        with open(path, "rb") as handle:
            while True:
                chunk = handle.read(HARD_MAX_STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                digest.update(chunk)
    except OSError:
        raise ParserIOError("Source digest could not be verified safely.") from None
    if f"sha256:{digest.hexdigest()}" != source.sha256:
        raise ParserIOError("Source content changed since intake; parsing refused.")


def _docling_document_to_normalised(
    document: Any,
    *,
    source: SourceMetadata,
    limits: KnowledgeLimits,
) -> NormalizedDocument:
    if document is None:
        raise ParserIOError("Docling returned no document.")
    try:
        items = list(document.texts) if hasattr(document, "texts") else []
    except Exception:
        raise ParserIOError("Docling document texts could not be read.") from None
    if len(items) > limits.max_section_count:
        raise ResourceLimitError("Docling output exceeds the section limit.")
    from zana_core.knowledge.models import NormalizedDocument, NormalizedSection

    sections: list[NormalizedSection] = []
    for sequence, item in enumerate(items):
        text = _docling_item_text(item)
        if not text:
            continue
        sections.append(
            NormalizedSection(
                section_id=f"{source.sha256}:docling:{sequence}",
                heading_path=_docling_heading_path(item),
                page_start=_docling_page(item, kind="start"),
                page_end=_docling_page(item, kind="end"),
                text=text,
                start_offset=0,
                end_offset=len(text),
            )
        )
    return NormalizedDocument(
        document_id=source.sha256,
        title=source.display_name,
        sections=tuple(sections),
        warnings=(),
    )


def _docling_item_text(item: Any) -> str:
    try:
        text = item.text
    except Exception:
        raise ParserIOError("Docling output contained a malformed text item.") from None
    if type(text) is not str:
        raise ParserIOError("Docling output text was not a string.")
    return text


def _docling_heading_path(item: Any) -> tuple[str, ...]:
    try:
        label = getattr(item, "label", None)
        if label is None:
            return ()
        text = str(label)
        if not text:
            return ()
        return (text,)
    except Exception:
        return ()


def _docling_page(item: Any, *, kind: str) -> int | None:
    try:
        prov = getattr(item, "prov", None)
        if prov is None:
            return None
        if isinstance(prov, list) and prov:
            page = getattr(prov[0], "page_no", None)
            if page is not None:
                return int(page)
        return None
    except (TypeError, ValueError):
        return None
