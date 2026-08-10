"""Bounded adapter validation with canonical safetensors checks."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from zana_core.training.contracts import AdapterBaseIdentity, AdapterMetadata, normalize_sha256

MAX_SAFETENSORS_HEADER_BYTES = 16 * 1024 * 1024
MAX_SHAPE_PRODUCT = 1 << 40
MAX_TENSOR_DIM = 1 << 32

SAFETENSORS_DTYPES: dict[str, int] = {
    "F64": 8,
    "F32": 4,
    "F16": 2,
    "BF16": 2,
    "I64": 8,
    "I32": 4,
    "I16": 2,
    "I8": 1,
    "U8": 1,
    "BOOL": 1,
}

_ALLOWED_TENSOR_KEYS = frozenset({"dtype", "shape", "data_offsets"})


@dataclass(frozen=True, slots=True)
class AdapterValidation:
    """Outcome of adapter artifact validation."""

    ok: bool
    reason: str
    digest: str | None = None


def _fstat(fd: int) -> os.stat_result:
    return os.fstat(fd)


def _read_exact(fd: int, size: int) -> bytes | None:
    data = bytearray()
    while len(data) < size:
        chunk = os.read(fd, min(65536, size - len(data)))
        if not chunk:
            return None
        data.extend(chunk)
    return bytes(data)


def _load_json_no_duplicates(data: bytes) -> Any:
    """Parse JSON and reject duplicate object keys instead of last-key-wins."""

    def reject_duplicates(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate JSON object key")
            result[key] = value
        return result

    return json.loads(data.decode("utf-8"), object_pairs_hook=reject_duplicates)


def _parse_tensor_region(header: Any) -> tuple[list[tuple[int, int]], str | None]:
    """Return dense ordered tensor offsets or a structural failure reason."""
    if not isinstance(header, dict) or not header:
        return [], "safetensors header must be a non-empty JSON object"
    tensors: list[tuple[int, int]] = []
    for name, spec in header.items():
        if not isinstance(name, str) or not name:
            return [], "tensor names must be non-empty strings"
        if name == "__metadata__":
            if not isinstance(spec, dict) or any(
                not isinstance(key, str) or not isinstance(value, str)
                for key, value in spec.items()
            ):
                return [], "metadata must be string key/value pairs"
            continue
        if not isinstance(spec, dict):
            return [], "tensor spec must be a JSON object"
        unknown = set(spec) - _ALLOWED_TENSOR_KEYS
        if unknown:
            return [], "tensor spec contains unknown keys"
        dtype = spec.get("dtype")
        if not isinstance(dtype, str) or dtype not in SAFETENSORS_DTYPES:
            return [], "tensor uses a non-canonical dtype"
        width = SAFETENSORS_DTYPES[dtype]
        shape = spec.get("shape")
        if not isinstance(shape, list):
            return [], "tensor shape must be an array"
        product = 1
        for dim in shape:
            if isinstance(dim, bool) or not isinstance(dim, int) or dim < 0:
                return [], "tensor shape must contain non-negative integers"
            if dim > MAX_TENSOR_DIM:
                return [], "tensor dimension exceeds the safe bound"
            product *= dim
            if product > MAX_SHAPE_PRODUCT:
                return [], "tensor shape product exceeds the safe bound"
        if product <= 0:
            return [], "tensor must be non-empty"
        offsets = spec.get("data_offsets")
        if not isinstance(offsets, list) or len(offsets) != 2:
            return [], "tensor data_offsets must be a pair"
        start, end = offsets
        if isinstance(start, bool) or isinstance(end, bool):
            return [], "tensor data_offsets must be integers"
        if not isinstance(start, int) or not isinstance(end, int):
            return [], "tensor data_offsets must be integers"
        if start < 0 or end < start:
            return [], "tensor data_offsets are invalid"
        expected = product * width
        if end - start != expected:
            return [], "tensor byte length does not match shape and dtype"
        tensors.append((start, end))
    if not tensors:
        return [], "safetensors file contains no tensors"
    tensors.sort()
    expected_offset = 0
    for start, end in tensors:
        if start != expected_offset:
            return [], "tensor offsets must be dense, ordered, and non-overlapping"
        expected_offset = end
    return tensors, None


def validate_adapter(
    *,
    path: Path,
    base: AdapterBaseIdentity,
    provider: str,
    dataset_digest: str,
    config_digest: str,
    provider_version: str,
    seed: int,
    package_version: str | None = None,
    max_size_bytes: int | None = None,
) -> tuple[AdapterValidation, AdapterMetadata | None]:
    """Validate a canonical safetensors adapter with one stable bounded read."""
    if path.suffix != ".safetensors":
        return AdapterValidation(False, "adapter must be a .safetensors file"), None
    if max_size_bytes is None or isinstance(max_size_bytes, bool) or max_size_bytes <= 0:
        return AdapterValidation(False, "adapter size cap must be positive"), None
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return AdapterValidation(False, "adapter could not be opened"), None
    try:
        initial = _fstat(fd)
        if not stat.S_ISREG(initial.st_mode):
            return AdapterValidation(False, "adapter must be a regular file"), None
        if initial.st_size <= 0 or initial.st_size > max_size_bytes:
            return AdapterValidation(False, "adapter is empty or oversized"), None
        length_bytes = _read_exact(fd, 8)
        if length_bytes is None:
            return AdapterValidation(False, "adapter is truncated before its header"), None
        (header_len,) = struct.unpack("<Q", length_bytes)
        if header_len <= 0 or header_len > MAX_SAFETENSORS_HEADER_BYTES:
            return AdapterValidation(False, "adapter header length is out of bounds"), None
        if 8 + header_len > initial.st_size:
            return AdapterValidation(False, "adapter is truncated within its header"), None
        header_bytes = _read_exact(fd, header_len)
        if header_bytes is None:
            return AdapterValidation(False, "adapter is truncated within its header"), None
        try:
            header = _load_json_no_duplicates(header_bytes)
        except (UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return AdapterValidation(False, "adapter header is not valid JSON"), None
        tensors, header_error = _parse_tensor_region(header)
        if header_error is not None:
            return AdapterValidation(False, header_error), None
        data_region = tensors[-1][1]
        expected_total = 8 + header_len + data_region
        if expected_total != initial.st_size:
            return AdapterValidation(False, "adapter has trailing or missing bytes"), None

        digest = hashlib.sha256()
        digest.update(length_bytes)
        digest.update(header_bytes)
        remaining = data_region
        while remaining:
            chunk = os.read(fd, min(65536, remaining))
            if not chunk:
                return AdapterValidation(False, "adapter truncated in the data region"), None
            digest.update(chunk)
            remaining -= len(chunk)
        if os.read(fd, 1):
            return AdapterValidation(False, "adapter grew beyond the declared size"), None
        final = _fstat(fd)
        if (
            final.st_dev != initial.st_dev
            or final.st_ino != initial.st_ino
            or final.st_size != initial.st_size
            or final.st_mtime_ns != initial.st_mtime_ns
        ):
            return AdapterValidation(False, "adapter drifted while it was read"), None
        adapter_digest = digest.hexdigest()
    except OSError:
        return AdapterValidation(False, "adapter could not be stably read"), None
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)

    if base.base_model_digest != base.training_source_digest:
        return AdapterValidation(False, "adapter base digest is not proven"), None
    if provider != "mlx_lm":
        return AdapterValidation(False, "unsupported adapter provider"), None
    if not dataset_digest or not config_digest or not provider_version:
        return AdapterValidation(False, "dataset/config/provider provenance is incomplete"), None
    try:
        dataset_hex = normalize_sha256(dataset_digest)
    except ValueError:
        return AdapterValidation(False, "dataset provenance is not a SHA-256 digest"), None
    metadata = AdapterMetadata(
        type="lora",
        format="safetensors",
        base_model_digest=base.base_model_digest,
        training_provider=provider,
        training_provider_version=provider_version,
        dataset_digest=dataset_hex,
        config_digest=config_digest,
        adapter_digest=adapter_digest,
        seed=seed,
        package_version=package_version,
    )
    return AdapterValidation(True, "adapter validated", adapter_digest), metadata
