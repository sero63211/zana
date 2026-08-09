"""Canonical digest validation and streaming hash tests."""

from __future__ import annotations

from io import BytesIO

import pytest

from zana_core.artifacts.digest import (
    InvalidDigestError,
    digest_bytes,
    digest_from_hex,
    digest_stream,
    validate_digest,
)

ABC_DIGEST = "sha256:ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad"


class TestDigestValidation:
    def test_known_sha256_vector_is_deterministic(self) -> None:
        assert digest_bytes(b"abc") == ABC_DIGEST
        assert digest_bytes(b"abc") == digest_bytes(b"abc")

    def test_validate_digest_accepts_canonical_lowercase(self) -> None:
        assert validate_digest(ABC_DIGEST) == ABC_DIGEST

    @pytest.mark.parametrize(
        "invalid",
        [
            "",
            "md5:abc",
            "sha256:",
            "sha256:abc",
            "sha256:" + "0" * 63,
            "sha256:" + "0" * 65,
            "sha256:" + "A" * 64,
            "sha256:" + "0" * 63 + "g",
        ],
    )
    def test_invalid_digests_are_rejected(self, invalid: str) -> None:
        with pytest.raises(InvalidDigestError):
            validate_digest(invalid)

    def test_digest_from_hex_normalizes_case(self) -> None:
        assert digest_from_hex(ABC_DIGEST.removeprefix("sha256:").upper()) == ABC_DIGEST

    def test_digest_from_hex_rejects_short_values(self) -> None:
        with pytest.raises(InvalidDigestError):
            digest_from_hex("abc")


class TestStreamingDigest:
    def test_stream_matches_in_memory_digest(self) -> None:
        data = b"streaming content with multiple chunks"
        assert digest_stream(BytesIO(data)) == digest_bytes(data)

    def test_stream_uses_requested_chunk_size(self) -> None:
        data = b"a" * 4096
        assert digest_stream(BytesIO(data), chunk_size=512) == digest_bytes(data)

    def test_stream_rejects_non_positive_chunk_size(self) -> None:
        with pytest.raises(ValueError):
            digest_stream(BytesIO(b"x"), chunk_size=0)
