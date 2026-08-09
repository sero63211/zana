"""Safe approved-path intake: validation, hashing, and immutable copy."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path
from typing import BinaryIO

from zana_core.knowledge.models import DocumentKind, ParserError, SourceMetadata

SUPPORTED_SUFFIXES = {".md", ".markdown", ".txt"}
DEFAULT_MAX_BYTES = 64 * 1024 * 1024
STREAM_CHUNK_SIZE = 1024 * 1024


class IntakeError(Exception):
    """Base class for intake validation failures."""


class UnsupportedTypeError(IntakeError):
    """Raised when a file kind is not supported."""


class UnreadableFileError(IntakeError):
    """Raised when an approved path cannot be read."""


class OversizeFileError(IntakeError):
    """Raised when a file exceeds the configured size limit."""


class PathEscapeError(IntakeError):
    """Raised when a path traverses or resolves outside approved roots."""


class ApprovedPathResolver:
    """Resolves and validates a path against explicit approved roots."""

    def __init__(self, roots: list[str | Path]) -> None:
        self.roots = [Path(root).resolve(strict=True) for root in roots]

    def resolve(self, path: str | Path) -> Path:
        raw = Path(path)
        if raw.is_symlink():
            raise PathEscapeError("Approved intake paths must not be symlinks.")
        candidate = raw
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        candidate = candidate.resolve(strict=False)
        for root in self.roots:
            try:
                candidate.relative_to(root)
            except ValueError:
                continue
            return candidate
        raise PathEscapeError("The path escapes the approved intake roots or contains traversal.")

    @property
    def root_paths(self) -> list[Path]:
        return list(self.roots)


def detect_kind(path: Path) -> DocumentKind:
    suffix = path.suffix.lower()
    if suffix in {".md", ".markdown"}:
        return DocumentKind.MARKDOWN
    if suffix == ".txt":
        return DocumentKind.TEXT
    if suffix == ".pdf":
        return DocumentKind.PDF
    return DocumentKind.UNSUPPORTED


def digest_stream(stream: BinaryIO, chunk_size: int = STREAM_CHUNK_SIZE) -> str:
    """SHA-256 a stream with bounded memory."""
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    return f"sha256:{hasher.hexdigest()}"


def check_readable(path: Path) -> None:
    if not path.exists():
        raise UnreadableFileError("The approved path does not exist.")
    if not path.is_file():
        raise UnreadableFileError("The approved path is not a regular file.")
    if not os.access(path, os.R_OK):
        raise UnreadableFileError("The approved path is not readable.")


def check_size(path: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
    try:
        size = path.stat().st_size
    except OSError as error:
        raise UnreadableFileError("The approved file size could not be read.") from error
    if size > max_bytes:
        raise OversizeFileError(f"File exceeds the {max_bytes}-byte configured intake limit.")


def prepare_source(
    path: str | Path,
    *,
    approved_root: str | Path,
    max_bytes: int = DEFAULT_MAX_BYTES,
) -> SourceMetadata:
    """Validate, hash, and copy one approved file into an immutable intake copy.

    The original file is never modified. The caller owns the returned copy
    path through ``metadata.extra["copy_path"]``.
    """
    resolver = ApprovedPathResolver([approved_root])
    candidate = resolver.resolve(path)
    kind = detect_kind(candidate)
    if kind == DocumentKind.UNSUPPORTED:
        raise UnsupportedTypeError(
            f"Unsupported source type {candidate.suffix!r}; supported: markdown, txt."
        )
    check_readable(candidate)
    check_size(candidate, max_bytes=max_bytes)

    workspace = Path(approved_root) / ".zana-intake-copy"
    workspace.mkdir(parents=True, exist_ok=True)
    copy_path = workspace / f"{candidate.name}.{os.getpid()}.{_nonce()}"
    try:
        with candidate.open("rb") as source, copy_path.open("xb") as destination:
            hasher = hashlib.sha256()
            while True:
                chunk = source.read(STREAM_CHUNK_SIZE)
                if not chunk:
                    break
                hasher.update(chunk)
                destination.write(chunk)
        digest = f"sha256:{hasher.hexdigest()}"
    except OSError as error:
        copy_path.unlink(missing_ok=True)
        raise UnreadableFileError("The approved file could not be hashed or copied.") from error

    return SourceMetadata(
        original_path=str(candidate),
        display_name=candidate.name,
        kind=kind,
        size_bytes=candidate.stat().st_size,
        sha256=digest,
        approved=True,
        extra={"copy_path": str(copy_path)},
    )


def to_parser_error(error: IntakeError) -> ParserError:
    """Convert an intake failure into the typed parser error shape."""
    if isinstance(error, UnsupportedTypeError):
        return ParserError(
            code="UNSUPPORTED_DOCUMENT_TYPE",
            message=str(error),
            recoverable=True,
            actions=["select_supported_file"],
        )
    if isinstance(error, OversizeFileError):
        return ParserError(
            code="DOCUMENT_TOO_LARGE",
            message=str(error),
            recoverable=True,
            actions=["reduce_file_size"],
        )
    if isinstance(error, PathEscapeError):
        return ParserError(
            code="PATH_ESCAPE_REJECTED",
            message="Approved intake paths must resolve inside the configured roots.",
            recoverable=False,
            actions=["select_approved_file"],
        )
    return ParserError(
        code="DOCUMENT_UNREADABLE",
        message=str(error),
        recoverable=True,
        actions=["select_readable_file"],
    )


def _nonce() -> str:
    import secrets

    return secrets.token_hex(6)
