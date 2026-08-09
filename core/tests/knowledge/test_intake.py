"""Intake validation, hashing, traversal, and symlink-escape tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.knowledge.intake import (
    ApprovedPathResolver,
    OversizeFileError,
    PathEscapeError,
    UnreadableFileError,
    UnsupportedTypeError,
    prepare_source,
)
from zana_core.knowledge.models import DocumentKind


class TestApprovedPathResolver:
    def test_rejects_traversal(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        resolver = ApprovedPathResolver([root])
        with pytest.raises(PathEscapeError):
            resolver.resolve(root / ".." / "outside.md")

    def test_rejects_symlink_escape(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        outside = tmp_path / "outside.md"
        outside.write_text("secret", encoding="utf-8")
        link = root / "link.md"
        link.symlink_to(outside)
        resolver = ApprovedPathResolver([root])
        with pytest.raises(PathEscapeError):
            resolver.resolve(link)

    def test_accepts_path_inside_root(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        (root / "doc.md").write_text("ok", encoding="utf-8")
        resolver = ApprovedPathResolver([root])
        resolved = resolver.resolve(root / "doc.md")
        assert resolved.name == "doc.md"


class TestPrepareSource:
    def test_hashes_and_never_modifies_original(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        original = root / "doc.md"
        original.write_bytes(b"# Heading\n\nbody\n")
        before = original.read_bytes()

        metadata = prepare_source(original, approved_root=root)

        assert metadata.kind == DocumentKind.MARKDOWN
        assert metadata.sha256.startswith("sha256:")
        assert metadata.size_bytes == len(before)
        assert original.read_bytes() == before
        copy = Path(metadata.extra["copy_path"])
        assert copy.exists()
        assert copy.read_bytes() == before

    def test_rejects_unsupported_type(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        original = root / "doc.docx"
        original.write_text("x", encoding="utf-8")
        with pytest.raises(UnsupportedTypeError):
            prepare_source(original, approved_root=root)

    def test_rejects_oversize(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        original = root / "big.txt"
        original.write_bytes(b"x" * 100)
        with pytest.raises(OversizeFileError):
            prepare_source(original, approved_root=root, max_bytes=10)

    def test_rejects_unreadable(self, tmp_path: Path) -> None:
        root = tmp_path / "approved"
        root.mkdir()
        original = root / "missing.txt"
        with pytest.raises(UnreadableFileError):
            prepare_source(original, approved_root=root)
