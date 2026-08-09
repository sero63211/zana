"""Real Docling-backed parser tests: real text parsing, honest unavailable, bounds."""

from __future__ import annotations

import hashlib
import os

import pytest

from zana_core.knowledge.docling import (
    DoclingParser,
    DoclingUnavailableError,
    ParserIOError,
    ParserLimitExceededError,
)
from zana_core.knowledge.limits import KnowledgeLimits, ResourceLimitError
from zana_core.knowledge.models import (
    DocumentKind,
    NormalizedDocument,
    SourceMetadata,
)
from zana_core.knowledge.parsers import parse_sources


def _sha256(path) -> str:  # noqa: ANN001
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _source(path, kind: DocumentKind) -> SourceMetadata:  # noqa: ANN001
    return SourceMetadata(
        original_path=str(path),
        display_name=path.name,
        kind=kind,
        size_bytes=os.path.getsize(path),
        sha256=_sha256(path),
    )


class TestDoclingTextParsing:
    def test_real_markdown_parsing_from_fixture(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "doc.md"
        path.write_text("# Chapter 1\nHello world.\n", encoding="utf-8")
        parser = DoclingParser()
        document = parser.parse(_source(path, DocumentKind.MARKDOWN))
        assert isinstance(document, NormalizedDocument)
        assert document.document_id == _sha256(path)
        assert document.title == "doc.md"
        assert len(document.sections) >= 1
        assert "Hello world." in document.sections[0].text

    def test_real_text_parsing_from_fixture(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "notes.txt"
        path.write_text("Line one\nLine two\n", encoding="utf-8")
        parser = DoclingParser()
        document = parser.parse(_source(path, DocumentKind.TEXT))
        assert document.document_id == _sha256(path)
        assert document.sections and "Line one" in document.sections[0].text

    def test_unapproved_source_refused(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "doc.md"
        path.write_text("hello", encoding="utf-8")
        source = _source(path, DocumentKind.MARKDOWN).model_copy(update={"approved": False})
        with pytest.raises(ParserIOError):
            DoclingParser().parse(source)

    def test_content_changed_since_intake_refused(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "doc.md"
        path.write_text("original", encoding="utf-8")
        source = _source(path, DocumentKind.MARKDOWN)
        path.write_text("tampered", encoding="utf-8")
        with pytest.raises(ParserIOError):
            DoclingParser().parse(source)


class TestDoclingBoundsAndPaths:
    def test_oversize_source_rejected(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "big.txt"
        path.write_text("x" * 1024, encoding="utf-8")
        source = _source(path, DocumentKind.TEXT)
        small = KnowledgeLimits(
            max_source_bytes=64,
            max_text_bytes=64,
            max_query_bytes=16,
            max_chunk_text_bytes=32,
        )
        with pytest.raises(ParserLimitExceededError):
            DoclingParser(limits=small).parse(source)

    def test_symlink_source_rejected(self, tmp_path) -> None:  # noqa: ANN001
        target = tmp_path / "real.txt"
        target.write_text("real", encoding="utf-8")
        link = tmp_path / "link.txt"
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            pytest.skip("symlinks not supported on this platform")
        source = _source(target, DocumentKind.TEXT).model_copy(update={"original_path": str(link)})
        with pytest.raises(ParserLimitExceededError):
            DoclingParser().parse(source)

    def test_non_regular_file_rejected(self, tmp_path) -> None:  # noqa: ANN001
        directory = tmp_path / "adir"
        directory.mkdir()
        source = SourceMetadata(
            original_path=str(directory),
            display_name="adir",
            kind=DocumentKind.TEXT,
            size_bytes=0,
            sha256="sha256:" + "0" * 64,
        )
        with pytest.raises(ParserLimitExceededError):
            DoclingParser().parse(source)

    def test_traversal_path_rejected(self, tmp_path) -> None:  # noqa: ANN001
        source = SourceMetadata(
            original_path=str(tmp_path / ".." / "escape.txt"),
            display_name="escape.txt",
            kind=DocumentKind.TEXT,
            size_bytes=1,
            sha256="sha256:" + "0" * 64,
        )
        with pytest.raises(ParserLimitExceededError):
            DoclingParser().parse(source)


class TestDoclingPdfUnavailable:
    def test_pdf_honestly_unavailable(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)
        parser = DoclingParser()
        assert parser._has_docling is False
        documents, errors = parse_sources([source], parser)
        assert documents == []
        assert len(errors) == 1
        assert errors[0].code == "PARSER_UNAVAILABLE"
        assert errors[0].message == (
            "The configured parser is not available for this source; "
            "no content was read or claimed."
        )
        assert errors[0].recoverable is True

    def test_pdf_parse_raises_honest_error(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        with pytest.raises(DoclingUnavailableError):
            DoclingParser().parse(_source(path, DocumentKind.PDF))

    def test_unsupported_kind_honest(self) -> None:
        source = SourceMetadata(
            original_path="/approved/x.bin",
            display_name="x.bin",
            kind=DocumentKind.UNSUPPORTED,
            size_bytes=1,
            sha256="sha256:" + "0" * 64,
        )
        with pytest.raises(DoclingUnavailableError):
            DoclingParser().parse(source)


class _FakeItem:
    def __init__(self, text: str, label: str | None = None, page: int | None = None) -> None:
        self.text = text
        self.label = label
        self.prov = [type("P", (), {"page_no": page})()] if page is not None else None


class _FakeDocument:
    def __init__(self, items: list[_FakeItem]) -> None:
        self.texts = items


class _FakeConversionResult:
    def __init__(self, document) -> None:  # noqa: ANN001
        self.document = document


class _FakeConverter:
    def __init__(self, conversion_result) -> None:  # noqa: ANN001
        self._conversion_result = conversion_result
        self.calls = 0

    def convert(self, path: str) -> _FakeConversionResult:  # noqa: ARG002
        self.calls += 1
        return self._conversion_result


class _InfiniteItems:
    def __init__(self) -> None:
        self.count = 0

    def __iter__(self):  # noqa: ANN201
        return self

    def __next__(self) -> _FakeItem:  # noqa: ANN201
        self.count += 1
        return _FakeItem(f"item {self.count}")


class TestDoclingAdapterCalls:
    def test_injected_converter_maps_docling_document(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)
        converter = _FakeConverter(
            _FakeConversionResult(
                _FakeDocument(
                    [
                        _FakeItem("Intro paragraph.", label="text", page=1),
                        _FakeItem("Second section body.", label="section-heading", page=2),
                    ]
                )
            )
        )
        parser = DoclingParser(converter=converter)
        document = parser.parse(source)
        assert isinstance(document, NormalizedDocument)
        assert len(document.sections) == 2
        assert document.sections[0].text == "Intro paragraph."
        assert document.sections[0].page_start == 1
        assert document.document_id == source.sha256
        assert document.title == "scan.pdf"

    def test_injected_converter_malformed_output_fails_closed(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)

        class BadDocument:
            @property
            def texts(self):  # noqa: ANN201
                raise RuntimeError("boom")

        with pytest.raises(ParserIOError):
            DoclingParser(converter=_FakeConverter(_FakeConversionResult(BadDocument()))).parse(
                source
            )

    def test_injected_converter_none_document_fails_closed(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)
        with pytest.raises(ParserIOError):
            DoclingParser(converter=_FakeConverter(_FakeConversionResult(None))).parse(source)

    def test_injected_converter_bare_document_rejected(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)
        converter = _FakeConverter(_FakeDocument([_FakeItem("x")]))
        with pytest.raises(ParserIOError):
            DoclingParser(converter=converter).parse(source)
        assert converter.calls == 1

    def test_injected_reader_used_for_text(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "doc.txt"
        path.write_text("fixture ignored", encoding="utf-8")

        class FixedReader:
            def read(self, path, *, limits):  # noqa: ANN001, ARG002
                return "injected body line"

        source = _source(path, DocumentKind.TEXT)
        document = DoclingParser(reader=FixedReader()).parse(source)
        assert document.sections and "injected body line" in document.sections[0].text


class TestDoclingReviewFixes:
    def test_unapproved_pdf_refused_before_converter(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF).model_copy(update={"approved": False})
        converter = _FakeConverter(_FakeConversionResult(_FakeDocument([_FakeItem("x")])))
        with pytest.raises(ParserIOError):
            DoclingParser(converter=converter).parse(source)
        assert converter.calls == 0

    def test_pdf_declared_size_drift_rejected_before_converter(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"A" * 10)
        source = _source(path, DocumentKind.PDF).model_copy(update={"size_bytes": 5})
        converter = _FakeConverter(_FakeConversionResult(_FakeDocument([_FakeItem("x")])))
        with pytest.raises(ParserLimitExceededError):
            DoclingParser(converter=converter).parse(source)
        assert converter.calls == 0

    def test_oversized_pdf_rejected_before_converter(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"B" * 128)
        source = _source(path, DocumentKind.PDF)
        small = KnowledgeLimits(
            max_source_bytes=64,
            max_text_bytes=64,
            max_query_bytes=16,
            max_chunk_text_bytes=32,
        )
        converter = _FakeConverter(_FakeConversionResult(_FakeDocument([_FakeItem("x")])))
        with pytest.raises(ParserLimitExceededError):
            DoclingParser(converter=converter, limits=small).parse(source)
        assert converter.calls == 0

    def test_hostile_infinite_texts_consumes_cap_plus_one(self, tmp_path) -> None:  # noqa: ANN001
        path = tmp_path / "scan.pdf"
        path.write_bytes(b"%PDF-1.4 fake")
        source = _source(path, DocumentKind.PDF)
        small = KnowledgeLimits(max_section_count=3)
        infinite = _InfiniteItems()
        converter = _FakeConverter(_FakeConversionResult(_FakeDocument(infinite)))
        with pytest.raises(ResourceLimitError):
            DoclingParser(converter=converter, limits=small).parse(source)
        assert infinite.count == small.max_section_count + 1

    def test_unavailable_error_does_not_echo_hostile_content(self) -> None:  # noqa: ANN001
        class HostileUnavailableError(Exception):
            code = "PARSER_UNAVAILABLE"

        hostile = HostileUnavailableError("SECRET=abc123 raw traceback boom")
        hostile.recoverable = False
        hostile.actions = ("leak_path", "/etc/passwd")
        from zana_core.knowledge.parsers import _parser_failure

        mapped = _parser_failure(hostile)
        assert mapped.code == "PARSER_UNAVAILABLE"
        assert "SECRET" not in mapped.message
        assert "traceback" not in mapped.message
        assert "/etc/passwd" not in mapped.actions
        assert mapped.actions == ("install_supported_parser", "use_markdown_or_text")
        assert mapped.recoverable is True
