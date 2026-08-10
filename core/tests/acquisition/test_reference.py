"""Focused tests for the conservative model reference grammar."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.acquisition.models import (
    AcquisitionKind,
    NativeAcquisitionRequest,
)
from zana_core.acquisition.redact import sanitize_model_reference

SAFE = [
    "llama3.2:1b",
    "qwen2.5:7b-instruct-q4_K_M",
    "deepseek-r1:7b",
    "org/model",
    "org/namespace/model:tag-1.2",
]

UNSAFE = [
    "https://user:secret@example.com/model",
    "http://example.com/model",
    "../model",
    "model/../secret",
    "model with spaces",
    "model?token=secret",
    "model#frag",
    "model=value",
    "model\\path",
    "bearer topsecret",
    "",
    ".",
    "..",
    "model:tag:extra",
    "-model",
    "model-",
    "model@host",
]


@pytest.mark.parametrize("reference", SAFE)
def test_safe_references_accepted(reference: str) -> None:
    assert sanitize_model_reference(reference) == reference
    NativeAcquisitionRequest(
        kind=AcquisitionKind.OLLAMA_PULL,
        endpoint="http://127.0.0.1:11434",
        model_reference=reference,
    )


@pytest.mark.parametrize("reference", UNSAFE)
def test_unsafe_references_rejected_without_leaking(reference: str) -> None:
    with pytest.raises(ValueError) as sanitized:
        sanitize_model_reference(reference)
    assert "secret" not in str(sanitized.value)
    assert "topsecret" not in str(sanitized.value)
    with pytest.raises(ValidationError):
        NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference=reference,
        )


@pytest.mark.parametrize(
    "reference",
    [" llama3.2:1b", "llama3.2:1b ", "\tllama3.2:1b", "llama3.2:1b\n", "\rllama3.2:1b"],
)
def test_surrounding_whitespace_is_rejected_not_normalized(reference: str) -> None:
    with pytest.raises(ValueError, match="surrounding whitespace"):
        sanitize_model_reference(reference)
    with pytest.raises(ValidationError):
        NativeAcquisitionRequest(
            kind=AcquisitionKind.OLLAMA_PULL,
            endpoint="http://127.0.0.1:11434",
            model_reference=reference,
        )
