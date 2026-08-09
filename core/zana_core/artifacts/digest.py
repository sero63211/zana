"""Canonical ``sha256:<lowercase hex>`` content digests."""

from __future__ import annotations

import hashlib
import re
from typing import BinaryIO

DIGEST_ALGORITHM = "sha256"
DIGEST_PREFIX = "sha256:"
SHA256_HEX_LENGTH = 64
DEFAULT_CHUNK_SIZE = 1024 * 1024

_DIGEST_PATTERN = re.compile(r"^sha256:[0-9a-f]{64}$")
_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")


class ArtifactError(Exception):
    """Base class for artifact store failures."""


class InvalidDigestError(ArtifactError, ValueError):
    """Raised when a value is not a canonical content digest."""


class ArtifactNotFoundError(ArtifactError, FileNotFoundError):
    """Raised when a digest has no blob in the store."""


class DigestMismatchError(ArtifactError):
    """Raised when written content does not match the expected digest."""


class RootEscapeError(ArtifactError, ValueError):
    """Raised when a path or symlink would escape the store root."""


class ArtifactCorruptedError(ArtifactError):
    """Raised when stored content no longer matches its digest."""


def validate_digest(value: str) -> str:
    """Validate and return the canonical lowercase digest form."""
    if _DIGEST_PATTERN.fullmatch(value) is None:
        raise InvalidDigestError(
            "Digest must be 'sha256:' followed by 64 lowercase hex characters."
        )
    return value


def digest_from_hex(hex_value: str) -> str:
    """Build a canonical digest from a 64-character lowercase hex value."""
    normalized = hex_value.lower()
    if _HEX_PATTERN.fullmatch(normalized) is None:
        raise InvalidDigestError("A digest hex value must contain exactly 64 hex characters.")
    return f"{DIGEST_PREFIX}{normalized}"


def digest_bytes(data: bytes) -> str:
    """Return the canonical digest for an in-memory byte string."""
    return f"{DIGEST_PREFIX}{hashlib.sha256(data).hexdigest()}"


def digest_stream(stream: BinaryIO, chunk_size: int = DEFAULT_CHUNK_SIZE) -> str:
    """Hash a binary stream with bounded memory and return its canonical digest."""
    if chunk_size <= 0:
        raise ValueError("chunk_size must be positive")
    hasher = hashlib.sha256()
    while True:
        chunk = stream.read(chunk_size)
        if not chunk:
            break
        hasher.update(chunk)
    return f"{DIGEST_PREFIX}{hasher.hexdigest()}"
