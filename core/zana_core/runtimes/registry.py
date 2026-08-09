"""Safe concurrent localhost probe registry with strict hard limits."""

from __future__ import annotations

import ipaddress
import math
import re
import time
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlsplit

from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeSource,
    RuntimeStatus,
)
from zana_core.runtimes.base import (
    AdapterType,
    HttpTransport,
    ModelDescriptor,
    ProbeTarget,
    RuntimeAdapter,
    RuntimeDescriptor,
    RuntimeProbeError,
)
from zana_core.runtimes.executables import ExecutableDiscovery
from zana_core.runtimes.limits import (
    MAX_EVIDENCE_ITEMS,
    RuntimeProbeLimits,
    _fresh_default_limits,
    _validated_limits,
)
from zana_core.runtimes.llamacpp import LlamaCppAdapter
from zana_core.runtimes.lmstudio import LMStudioAdapter
from zana_core.runtimes.mlx_server import MlxServerAdapter
from zana_core.runtimes.ollama import OLLAMA_DEFAULT_ENDPOINT, OllamaAdapter
from zana_core.runtimes.openai_compat import OpenAICompatAdapter
from zana_core.runtimes.transport import UrllibTransport

LM_STUDIO_DEFAULT_ENDPOINT = "http://127.0.0.1:1234"
LLAMA_CPP_DEFAULT_ENDPOINT = "http://127.0.0.1:8080"
MLX_LM_DEFAULT_ENDPOINT = "http://127.0.0.1:8080"

_REDACTED = "[REDACTED]"
_URL_CREDENTIAL_TOKEN = re.compile(r"[^\s<>]*//[^/@\s]+@[^\s<>]*")
_REDACT_BEARER = re.compile(r"(?i)(bearer\s*=\s*)[^\s<>]+")
_REDACT_RAW_TOKEN = re.compile(
    r"(?i)\b(?:token|secret|pass(?:word)?|api[-_]?key|authorization)\b"
    r"[=:\s]+[^\s<>]+"
)


class RuntimeProbeRegistry:
    """Probes explicit localhost targets concurrently with bounded timeouts.

    No LAN scanning or automatic remote discovery is performed.  Every target
    is exact-type validated into a trusted snapshot before the batch is
    scheduled, and the whole batch shares one monotonic deadline.
    """

    def __init__(
        self,
        transport: HttpTransport | None = None,
        *,
        timeout: float = 1.5,
        max_workers: int = 4,
        executables: ExecutableDiscovery | None = None,
        limits: RuntimeProbeLimits | None = None,
    ) -> None:
        self.limits = _validated_limits(_fresh_default_limits() if limits is None else limits)
        if type(timeout) not in (int, float):
            raise ValueError("timeout must be an exact number")
        timeout_value = float(timeout)
        if math.isnan(timeout_value) or math.isinf(timeout_value):
            raise ValueError("timeout must be finite")
        if timeout_value <= 0 or timeout_value > self.limits.max_timeout_seconds:
            raise ValueError(f"timeout must be in (0, {self.limits.max_timeout_seconds:g}] seconds")
        if type(max_workers) is not int or max_workers < 1 or max_workers > self.limits.max_workers:
            raise ValueError(f"max_workers must be in [1, {self.limits.max_workers}]")
        self.transport = UrllibTransport() if transport is None else transport
        self.timeout = float(timeout)
        self.max_workers = max_workers
        self.executables = ExecutableDiscovery() if executables is None else executables

    def default_targets(self) -> list[ProbeTarget]:
        """Default candidate set: known localhost ports, never scanned."""
        return [
            ProbeTarget(
                runtime_id=OllamaAdapter.runtime_id,
                kind=RuntimeKind.OLLAMA,
                endpoint=OLLAMA_DEFAULT_ENDPOINT,
                source=RuntimeSource.AUTO,
                adapter_type=AdapterType.OLLAMA,
            ),
            ProbeTarget(
                runtime_id=LMStudioAdapter.runtime_id,
                kind=RuntimeKind.LM_STUDIO,
                endpoint=LM_STUDIO_DEFAULT_ENDPOINT,
                source=RuntimeSource.AUTO,
                adapter_type=AdapterType.LM_STUDIO,
            ),
            ProbeTarget(
                runtime_id=LlamaCppAdapter.runtime_id,
                kind=RuntimeKind.LLAMA_CPP,
                endpoint=LLAMA_CPP_DEFAULT_ENDPOINT,
                source=RuntimeSource.AUTO,
                adapter_type=AdapterType.LLAMA_CPP,
            ),
            ProbeTarget(
                runtime_id=MlxServerAdapter.runtime_id,
                kind=RuntimeKind.MLX_LM,
                endpoint=MLX_LM_DEFAULT_ENDPOINT,
                source=RuntimeSource.AUTO,
                adapter_type=AdapterType.MLX_LM,
            ),
        ]

    def probe(
        self,
        targets: Sequence[ProbeTarget] | Iterable[ProbeTarget],
    ) -> list[RuntimeDescriptor]:
        """Probe at most ``max_targets`` targets and return one descriptor each.

        Every input (list, Sequence, or arbitrary Iterable) goes through one
        cap+1 bounded iteration path: at most ``max_targets + 1`` items are
        consumed, ``Sequence.__len__`` is never trusted for safety, and no
        remainder is materialized.  Empty input returns immediately without
        creating an executor; a single target or ``max_workers == 1`` probes
        synchronously with zero threads.
        """
        limits, timeout, max_workers = self._trusted_runtime_config()
        collected = self._bounded_collect(targets, limits=limits)
        if not collected:
            return []
        snapshots = self._validated_snapshots(collected, limits=limits)
        deadline = time.monotonic() + timeout
        if len(snapshots) == 1 or max_workers == 1:
            return [
                self._probe_one_sanitized(
                    snapshot, deadline=deadline, limits=limits, timeout=timeout
                )
                for snapshot in snapshots
            ]
        results: list[RuntimeDescriptor] = []
        worker_count = min(max_workers, len(snapshots))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    self._probe_one_sanitized,
                    snapshot,
                    deadline=deadline,
                    limits=limits,
                    timeout=timeout,
                ): snapshot
                for snapshot in snapshots
            }
            for future in as_completed(futures):
                results.append(future.result())
        return sorted(results, key=lambda descriptor: _descriptor_runtime_id(descriptor))

    def _trusted_runtime_config(self) -> tuple[RuntimeProbeLimits, float, int]:
        """Capture one validated runtime configuration from raw registry state.

        Registry fields can be deleted or replaced through
        ``object.__delattr__``/``object.__setattr__``.  This reads the raw
        namespace once and fails closed with a typed ValueError instead of
        leaking AttributeError or invoking hostile attribute hooks.
        """
        namespace = object.__getattribute__(self, "__dict__")
        if type(namespace) is not dict:
            raise ValueError("registry state is corrupted")
        for name in ("limits", "timeout", "max_workers"):
            if name not in namespace:
                raise ValueError("registry state is missing a required field")
        limits_value = namespace["limits"]
        timeout_value = namespace["timeout"]
        workers_value = namespace["max_workers"]
        limits = _validated_limits(limits_value)
        timeout = _validated_timeout(timeout_value, limits)
        if type(workers_value) is not int or workers_value < 1:
            raise ValueError("max_workers is corrupted")
        if workers_value > limits.max_workers:
            raise ValueError("max_workers exceeds the configured limit")
        return limits, timeout, workers_value

    def _bounded_collect(
        self,
        targets: Sequence[ProbeTarget] | Iterable[ProbeTarget],
        *,
        limits: RuntimeProbeLimits,
    ) -> list[ProbeTarget]:
        # One cap+1 bounded path for every input; never trust __len__ and
        # never materialize beyond max_targets + 1, even for hostile Sequences.
        collected: list[ProbeTarget] = []
        for index, target in enumerate(targets):
            if index >= limits.max_targets:
                raise ValueError(
                    f"target count exceeds limit {limits.max_targets}; stopped at the bounded cap"
                )
            collected.append(target)
        return collected

    def _validated_snapshots(
        self,
        targets: list[ProbeTarget],
        *,
        limits: RuntimeProbeLimits,
    ) -> list[_TargetSnapshot]:
        seen: set[str] = set()
        snapshots: list[_TargetSnapshot] = []
        for index, target in enumerate(targets):
            if type(target) is not ProbeTarget:
                raise ValueError(f"target {index} is not a ProbeTarget")
            snapshot = _validated_target(target, index=index, limits=limits)
            runtime_id = snapshot.runtime_id
            if runtime_id in seen:
                raise ValueError("duplicate runtime_id in probe targets")
            seen.add(runtime_id)
            snapshots.append(snapshot)
        return snapshots

    def _probe_one_sanitized(
        self,
        snapshot: _TargetSnapshot,
        *,
        deadline: float,
        limits: RuntimeProbeLimits,
        timeout: float,
    ) -> RuntimeDescriptor:
        try:
            return self._probe_one(snapshot, deadline=deadline, limits=limits, timeout=timeout)
        except RuntimeProbeError as error:
            try:
                return self._error_descriptor(
                    snapshot, error=self._sanitize_error(error, limits=limits), limits=limits
                )
            except Exception:  # noqa: BLE001 - fallback must never raise
                return _bare_error_descriptor(
                    snapshot, "Unexpected probe failure; details are not exposed."
                )
        except Exception:  # noqa: BLE001 - unexpected failures are isolated and sanitized
            try:
                return self._error_descriptor(
                    snapshot,
                    error="Unexpected probe failure; details are not exposed.",
                    limits=limits,
                )
            except Exception:  # noqa: BLE001 - fallback must never raise
                return _bare_error_descriptor(
                    snapshot, "Unexpected probe failure; details are not exposed."
                )

    def _error_descriptor(
        self,
        snapshot: _TargetSnapshot,
        *,
        error: str,
        limits: RuntimeProbeLimits,
    ) -> RuntimeDescriptor:
        return _bare_error_descriptor(snapshot, error)

    def _probe_one(
        self,
        snapshot: _TargetSnapshot,
        *,
        deadline: float,
        limits: RuntimeProbeLimits,
        timeout: float,
    ) -> RuntimeDescriptor:
        remaining = _remaining(deadline)
        if remaining <= 0:
            return _bare_error_descriptor(
                snapshot, "probe deadline expired before the target was probed"
            )
        adapter_timeout = min(timeout if snapshot.timeout is None else snapshot.timeout, remaining)
        adapter = _make_adapter(self, snapshot, timeout=adapter_timeout, limits=limits)
        descriptor = self._bound_descriptor(adapter.probe(), snapshot, limits=limits)
        return descriptor

    def _bound_descriptor(
        self,
        descriptor: RuntimeDescriptor,
        snapshot: _TargetSnapshot,
        *,
        limits: RuntimeProbeLimits,
    ) -> RuntimeDescriptor:
        fields = _extract_descriptor_fields(descriptor)
        evidence = self._bounded_strings(
            fields["evidence"], item_limit=limits.max_evidence_items, limits=limits
        )
        warnings = self._bounded_strings(fields["warnings"], limits=limits)
        error = None if fields["error"] is None else self._sanitize_text(fields["error"], limits)
        models = self._bound_models(fields["models"], limits=limits, snapshot=snapshot)
        identified_vendor = (
            None
            if fields["identified_vendor"] is None
            else _validated_text(
                fields["identified_vendor"],
                label="identified_vendor",
                byte_limit=limits.max_model_field_bytes,
            )
        )
        return _rebuild_descriptor(
            source=descriptor,
            runtime_id=snapshot.runtime_id,
            endpoint=snapshot.endpoint,
            kind=snapshot.kind,
            source_value=snapshot.source,
            status=fields["status"],
            registered=fields["registered"],
            server_running=fields["server_running"],
            installed=fields["installed"],
            installed_not_running=fields["installed_not_running"],
            identified_vendor=identified_vendor,
            evidence=evidence,
            warnings=warnings,
            error=error,
            models=models,
            last_seen_at=fields["last_seen_at"],
        )

    def _bound_models(
        self,
        models: list[ModelDescriptor],
        *,
        limits: RuntimeProbeLimits,
        snapshot: _TargetSnapshot,
    ) -> list[ModelDescriptor]:
        if type(models) is not list:
            raise RuntimeProbeError("adapter returned an invalid models collection")
        if len(models) > limits.max_models:
            raise RuntimeProbeError(f"model count exceeds policy limit {limits.max_models}")
        projected: list[ModelDescriptor] = []
        total_bytes = 0
        for model in models:
            bounded = self._project_model(model, limits=limits, snapshot=snapshot)
            bytes_used = _model_byte_count(bounded, limits=limits)
            total_bytes += bytes_used
            if total_bytes > limits.max_models_total_bytes:
                raise RuntimeProbeError("projected model output exceeds the total byte limit")
            projected.append(bounded)
        return projected

    def _project_model(
        self,
        model: ModelDescriptor,
        *,
        limits: RuntimeProbeLimits,
        snapshot: _TargetSnapshot,
    ) -> ModelDescriptor:
        fields = _extract_model_fields(model, limits=limits)
        limit = limits.max_model_field_bytes
        # A model from another runtime must never be surfaced under a foreign
        # id.  The raw runtime_id is exact-validated first, then every
        # projected model is bound to the validated target snapshot.
        _validated_text(fields["runtime_id"], label="model runtime_id", byte_limit=limit)
        return ModelDescriptor(
            runtime_id=snapshot.runtime_id,
            model_id=_validated_text(fields["model_id"], label="model_id", byte_limit=limit),
            display_name=_validated_text(
                fields["display_name"], label="display_name", byte_limit=limit
            ),
            digest=None
            if fields["digest"] is None
            else _validated_text(fields["digest"], label="digest", byte_limit=limit),
            family=None
            if fields["family"] is None
            else _validated_text(fields["family"], label="family", byte_limit=limit),
            parameter_count=fields["parameter_count"],
            parameter_label=(
                None
                if fields["parameter_label"] is None
                else _validated_text(
                    fields["parameter_label"], label="parameter_label", byte_limit=limit
                )
            ),
            format=None
            if fields["format"] is None
            else _validated_text(fields["format"], label="format", byte_limit=limit),
            quantization=(
                None
                if fields["quantization"] is None
                else _validated_text(fields["quantization"], label="quantization", byte_limit=limit)
            ),
            size_bytes=fields["size_bytes"],
            context_length=fields["context_length"],
            capabilities=[
                _validated_text(item, label="capability", byte_limit=limit)
                for item in fields["capabilities"][: limits.max_model_capabilities]
            ],
            trainability=(
                None
                if fields["trainability"] is None
                else _validated_text(fields["trainability"], label="trainability", byte_limit=limit)
            ),
            metadata_source=_validated_text(
                fields["metadata_source"], label="metadata_source", byte_limit=limit
            ),
            last_seen_at=fields["last_seen_at"],
            identity_strength=fields["identity_strength"],
        )

    def _bounded_strings(
        self,
        values: list[str],
        *,
        item_limit: int = MAX_EVIDENCE_ITEMS,
        limits: RuntimeProbeLimits,
    ) -> list[str]:
        if type(values) is not list:
            return []
        total = 0
        bounded: list[str] = []
        for value in values[:item_limit]:
            sanitized = _sanitize_display_text(
                value,
                char_limit=limits.max_evidence_chars,
                byte_limit=limits.max_evidence_chars,
            )
            if total + len(sanitized) > limits.max_evidence_chars:
                break
            bounded.append(sanitized)
            total += len(sanitized)
        return bounded

    def _sanitize_error(
        self,
        error: RuntimeProbeError,
        *,
        limits: RuntimeProbeLimits,
    ) -> str:
        # Only an already-existing exact str argument is inspected; hostile
        # __str__/__getattribute__ implementations are never invoked.
        args = _exception_args(error)
        if len(args) == 1 and type(args[0]) is str:
            return self._sanitize_text(args[0], limits)
        return "Unexpected probe failure; details are not exposed."

    def _sanitize_text(self, text: str | None, limits: RuntimeProbeLimits) -> str:
        if text is None:
            return ""
        return _sanitize_display_text(
            text,
            char_limit=limits.max_error_chars,
            byte_limit=limits.max_error_chars,
        )


@dataclass(frozen=True)
class _TargetSnapshot:
    """Trusted, fully validated target identity used after scheduling."""

    runtime_id: str
    kind: RuntimeKind
    endpoint: str
    source: RuntimeSource
    adapter_type: AdapterType
    bearer_token: str | None
    timeout: float | None


def _validated_target(
    target: ProbeTarget,
    *,
    index: int,
    limits: RuntimeProbeLimits,
) -> _TargetSnapshot:
    if type(target) is not ProbeTarget:
        raise ValueError(f"target {index} is not a ProbeTarget")
    raw = object.__getattribute__(target, "__dict__")
    if type(raw) is not dict:
        raise ValueError(f"target {index} is corrupted")
    names = (
        "runtime_id",
        "kind",
        "endpoint",
        "source",
        "adapter_type",
        "bearer_token",
        "timeout",
    )
    if any(name not in raw for name in names):
        raise ValueError(f"target {index} is missing required fields")
    fields = {name: raw[name] for name in names}
    runtime_id = fields["runtime_id"]
    endpoint = fields["endpoint"]
    if type(runtime_id) is not str or not runtime_id:
        raise ValueError(f"target {index} runtime_id must be a non-empty string")
    _bounded_string(
        runtime_id,
        label=f"target {index} runtime_id",
        char_limit=limits.max_reference_length,
        byte_limit=limits.max_reference_bytes,
    )
    if type(endpoint) is not str or not endpoint:
        raise ValueError(f"target {index} endpoint must be a non-empty string")
    _bounded_string(
        endpoint,
        label=f"target {index} endpoint",
        char_limit=limits.max_endpoint_length,
        byte_limit=limits.max_endpoint_bytes,
    )
    _validate_loopback_endpoint(endpoint)
    kind = fields["kind"]
    source = fields["source"]
    adapter_type = fields["adapter_type"]
    if type(kind) is not RuntimeKind:
        raise ValueError(f"target {index} kind must be a RuntimeKind")
    if type(source) is not RuntimeSource:
        raise ValueError(f"target {index} source must be a RuntimeSource")
    if type(adapter_type) is not AdapterType:
        raise ValueError(f"target {index} adapter_type must be an AdapterType")
    bearer_token = fields["bearer_token"]
    if bearer_token is not None:
        if type(bearer_token) is not str:
            raise ValueError("bearer_token must be a string")
        if len(bearer_token) > limits.max_bearer_token_bytes:
            raise ValueError("bearer_token exceeds the configured byte limit")
        if len(bearer_token.encode("utf-8")) > limits.max_bearer_token_bytes:
            raise ValueError("bearer_token exceeds the configured byte limit")
        if _contains_control_characters(bearer_token):
            raise ValueError("bearer_token contains control characters")
    timeout = fields["timeout"]
    if timeout is not None:
        if type(timeout) not in (int, float):
            raise ValueError(f"target {index} timeout must be a number")
        if not math.isfinite(timeout) or timeout <= 0 or timeout > limits.max_timeout_seconds:
            raise ValueError(
                f"target timeout must be in (0, {limits.max_timeout_seconds:g}] seconds"
            )
        timeout = float(timeout)
    return _TargetSnapshot(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=source,
        adapter_type=adapter_type,
        bearer_token=bearer_token,
        timeout=timeout,
    )


def _bounded_string(
    value: str,
    *,
    label: str,
    char_limit: int,
    byte_limit: int,
) -> None:
    if type(value) is not str:
        raise ValueError(f"{label} must be a string")
    if len(value) > char_limit:
        raise ValueError(f"{label} exceeds the configured character limit")
    if len(value.encode("utf-8")) > byte_limit:
        raise ValueError(f"{label} exceeds the configured byte limit")
    if _contains_control_characters(value):
        raise ValueError(f"{label} contains control characters")


def _validate_loopback_endpoint(endpoint: str) -> None:
    if any(character in " \t\n\r" for character in endpoint) or "\\" in endpoint:
        raise ValueError("endpoint is malformed")
    if _has_dangling_port(endpoint):
        raise ValueError("endpoint port is invalid")
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        raise ValueError("endpoint is malformed") from None
    if parts.scheme not in ("http", "https"):
        raise ValueError("endpoint must use http or https")
    if parts.username is not None or parts.password is not None:
        raise ValueError("endpoint must not contain embedded credentials")
    if parts.fragment:
        raise ValueError("endpoint must not contain a fragment")
    if parts.query:
        raise ValueError("endpoint must not contain a query")
    if not parts.hostname:
        raise ValueError("endpoint must contain a host")
    host = parts.hostname.rstrip(".")
    if host.lower() == "localhost":
        pass
    else:
        ip_host = host[1:-1] if host.startswith("[") and host.endswith("]") else host
        try:
            if not ipaddress.ip_address(ip_host).is_loopback:
                raise ValueError("endpoint host is not loopback")
        except ValueError:
            raise ValueError("endpoint host is not loopback") from None
    port: int | None
    try:
        port = parts.port
    except ValueError:
        raise ValueError("endpoint port is invalid") from None
    if port is not None and (port < 1 or port > 65535):
        raise ValueError("endpoint port is invalid")


def _contains_control_characters(value: str) -> bool:
    return any(ord(character) < 32 or ord(character) == 127 for character in value)


def _boundary_may_cut_credential(prefix: str) -> bool:
    """True when the bounded prefix could end inside a credential token.

    Fixed-work over the already bounded prefix: checks only the trailing
    region after the last whitespace, so no 128-char blind spot exists.
    """
    if prefix.endswith("@"):
        return True
    last_space = max(prefix.rfind(" "), prefix.rfind("\t"), prefix.rfind("\n"))
    tail = prefix[last_space + 1 :] if last_space >= 0 else prefix
    marker = tail.rfind("//")
    return marker >= 0 and "@" not in tail[marker + 2 :]


def _credential_safe_prefix_len(prefix: str) -> int:
    """Cut to the last safe boundary, never splitting a credential."""
    candidates = [len(prefix)]
    last_at = prefix.rfind("@")
    if last_at >= 0:
        candidates.append(last_at + 1)
    last_space = max(prefix.rfind(" "), prefix.rfind("\t"), prefix.rfind("\n"))
    if last_space >= 0:
        candidates.append(last_space + 1)
    # Cut before a partial //userinfo that has no '@' inside the prefix.
    tail_start = last_space + 1 if last_space >= 0 else 0
    tail = prefix[tail_start:]
    marker = tail.rfind("//")
    if marker >= 0 and "@" not in tail[marker + 2 :]:
        candidates.append(tail_start + marker)
    return max(1, min(candidates))


def _has_dangling_port(endpoint: str) -> bool:
    """True when the authority ends with a bare ':' or ':<empty>' port."""
    try:
        parts = urlsplit(endpoint)
    except ValueError:
        return False
    netloc = parts.netloc
    if ":" not in netloc:
        return False
    if netloc.endswith(":"):
        return True
    authority_without_userinfo = netloc.rsplit("@", 1)[-1]
    if "]" in authority_without_userinfo and authority_without_userinfo.endswith("]"):
        return False
    host, separator, port = authority_without_userinfo.rpartition(":")
    if not separator:
        return False
    if "]" in host:
        # Bracketed IPv6: only a trailing empty port after ']' counts.
        return port == ""
    return port == ""


def _truncate_text(value: str, limit: int) -> str:
    """Truncate by UTF-8 bytes without splitting a code point."""
    if type(value) is not str:
        raise RuntimeProbeError("invalid string field from runtime probe")
    # UTF-8 bytes are >= code points, so slicing to limit code points bounds
    # the prefix before any full-string encoding.
    bounded_prefix = value[:limit]
    encoded = bounded_prefix.encode("utf-8")
    if len(encoded) <= limit:
        return bounded_prefix
    return encoded[:limit].decode("utf-8", errors="ignore")


def _sanitize_display_text(value: object, *, char_limit: int, byte_limit: int) -> str:
    """Bound, redact, and neutralize one untrusted display/error string.

    Only an exact str is retained; hostile ``__str__``/``__repr__`` hooks are
    never invoked.  The string is prefix-gated before any encode/regex work,
    URL credentials and token/secret/password/api-key fragments are redacted,
    and control characters are neutralized before the bounded result is kept.
    """
    if type(value) is not str:
        return "[non-string]"
    prefix = value[:char_limit]
    if len(value) > char_limit and _boundary_may_cut_credential(prefix):
        prefix = prefix[: _credential_safe_prefix_len(prefix)]
    sanitized = _URL_CREDENTIAL_TOKEN.sub(_REDACTED, prefix)
    sanitized = _REDACT_BEARER.sub(_REDACTED, sanitized)
    sanitized = _REDACT_RAW_TOKEN.sub(_REDACTED, sanitized)
    sanitized = _neutralize_control_characters(sanitized)
    encoded = sanitized.encode("utf-8")
    if len(encoded) > byte_limit:
        sanitized = encoded[:byte_limit].decode("utf-8", errors="ignore")
    return sanitized


def _neutralize_control_characters(value: str) -> str:
    return "".join(
        " " if ord(character) < 32 or ord(character) == 127 else character for character in value
    )


def _validated_text(value: object, *, label: str, byte_limit: int) -> str:
    """Return an exact, control-free, byte-bounded text field or fail closed."""
    if type(value) is not str:
        raise RuntimeProbeError(f"{label} is invalid")
    if _contains_control_characters(value[:byte_limit]):
        raise RuntimeProbeError(f"{label} contains control characters")
    return _truncate_text(value, byte_limit)


def _model_byte_count(model: ModelDescriptor, *, limits: RuntimeProbeLimits) -> int:
    total = 0
    for field in (
        "model_id",
        "runtime_id",
        "display_name",
        "digest",
        "family",
        "parameter_label",
        "format",
        "quantization",
        "trainability",
        "metadata_source",
    ):
        value = _pydantic_field_value(model, field)
        if value is not None:
            total += len(value.encode("utf-8"))
    for item in _pydantic_field_value(model, "capabilities")[: limits.max_model_capabilities]:
        total += len(item.encode("utf-8"))
    return total


def _descriptor_runtime_id(descriptor: RuntimeDescriptor) -> str:
    if type(descriptor) is not RuntimeDescriptor:
        raise RuntimeProbeError("invalid runtime descriptor")
    return _pydantic_field_value(descriptor, "runtime_id")


def _pydantic_field_value(instance: object, name: str) -> Any:
    """Read one raw Pydantic field without model_dump/model_copy hooks."""
    namespace = object.__getattribute__(instance, "__dict__")
    if type(namespace) is not dict:
        raise RuntimeProbeError("runtime descriptor namespace is corrupted")
    if name in namespace:
        return namespace[name]
    raise RuntimeProbeError("runtime descriptor field is missing")


def _exception_args(error: BaseException) -> tuple[object, ...]:
    """Read args through the exact BaseException descriptor, never a hostile override."""
    descriptor = BaseException.__dict__.get("args")
    if descriptor is None:
        return ()
    try:
        args = descriptor.__get__(error, type(error))
    except Exception:  # noqa: BLE001 - a hostile subclass may corrupt the descriptor
        return ()
    return args if type(args) is tuple else ()


def _extract_descriptor_fields(descriptor: RuntimeDescriptor) -> dict[str, Any]:
    if type(descriptor) is not RuntimeDescriptor:
        raise RuntimeProbeError("adapter returned an invalid runtime descriptor")
    values: dict[str, Any] = {
        name: _pydantic_field_value(descriptor, name)
        for name in (
            "runtime_id",
            "kind",
            "endpoint",
            "source",
            "status",
            "registered",
            "server_running",
            "installed",
            "installed_not_running",
            "identified_vendor",
            "evidence",
            "warnings",
            "error",
            "models",
            "last_seen_at",
        )
    }
    if type(values["runtime_id"]) is not str or not values["runtime_id"]:
        raise RuntimeProbeError("runtime descriptor has an invalid runtime_id")
    if type(values["endpoint"]) is not str or not values["endpoint"]:
        raise RuntimeProbeError("runtime descriptor has an invalid endpoint")
    if type(values["kind"]) is not RuntimeKind:
        raise RuntimeProbeError("runtime descriptor has an invalid kind")
    if type(values["source"]) is not RuntimeSource:
        raise RuntimeProbeError("runtime descriptor has an invalid source")
    if type(values["status"]) is not RuntimeStatus:
        raise RuntimeProbeError("runtime descriptor has an invalid status")
    for name in ("registered", "server_running", "installed", "installed_not_running"):
        if type(values[name]) is not bool:
            raise RuntimeProbeError(f"runtime descriptor has an invalid {name}")
    if values["identified_vendor"] is not None and type(values["identified_vendor"]) is not str:
        raise RuntimeProbeError("runtime descriptor has an invalid identified_vendor")
    if type(values["evidence"]) is not list or type(values["warnings"]) is not list:
        raise RuntimeProbeError("runtime descriptor has invalid lists")
    if values["error"] is not None and type(values["error"]) is not str:
        raise RuntimeProbeError("runtime descriptor has an invalid error")
    if type(values["models"]) is not list:
        raise RuntimeProbeError("runtime descriptor has an invalid models list")
    if type(values["last_seen_at"]) is not datetime:
        raise RuntimeProbeError("runtime descriptor has an invalid last_seen_at")
    _validate_utc_datetime(values["last_seen_at"])
    return values


def _extract_model_fields(
    model: ModelDescriptor,
    *,
    limits: RuntimeProbeLimits,
) -> dict[str, Any]:
    if type(model) is not ModelDescriptor:
        raise RuntimeProbeError("adapter returned an invalid model descriptor")
    names = (
        "runtime_id",
        "model_id",
        "display_name",
        "digest",
        "family",
        "parameter_count",
        "parameter_label",
        "format",
        "quantization",
        "size_bytes",
        "context_length",
        "capabilities",
        "trainability",
        "metadata_source",
        "last_seen_at",
        "identity_strength",
    )
    values: dict[str, Any] = {name: _pydantic_field_value(model, name) for name in names}
    for name in ("runtime_id", "model_id", "display_name", "metadata_source"):
        if type(values[name]) is not str or not values[name]:
            raise RuntimeProbeError(f"model descriptor has an invalid {name}")
    for name in (
        "digest",
        "family",
        "parameter_label",
        "format",
        "quantization",
        "trainability",
    ):
        if values[name] is not None and type(values[name]) is not str:
            raise RuntimeProbeError(f"model descriptor has an invalid {name}")
    for name in ("parameter_count", "size_bytes", "context_length"):
        if values[name] is not None and type(values[name]) is not int:
            raise RuntimeProbeError(f"model descriptor has an invalid {name}")
        if values[name] is not None and values[name] < 0:
            raise RuntimeProbeError(f"model descriptor has a negative {name}")
        if values[name] is not None and values[name] > 1_000_000_000_000:
            raise RuntimeProbeError(f"model descriptor has an oversized {name}")
    if type(values["capabilities"]) is not list:
        raise RuntimeProbeError("model descriptor has invalid capabilities")
    bounded_capabilities = values["capabilities"][: limits.max_model_capabilities]
    if any(type(item) is not str for item in bounded_capabilities):
        raise RuntimeProbeError("model descriptor has invalid capabilities")
    if type(values["last_seen_at"]) is not datetime:
        raise RuntimeProbeError("model descriptor has an invalid last_seen_at")
    _validate_utc_datetime(values["last_seen_at"])
    if type(values["identity_strength"]) is not ModelIdentityStrength:
        raise RuntimeProbeError("model descriptor has an invalid identity_strength")
    return values


def _validate_utc_datetime(value: datetime) -> None:
    if value.tzinfo is not UTC:
        raise RuntimeProbeError("runtime timestamp must be UTC")


def _rebuild_descriptor(
    source: RuntimeDescriptor,
    *,
    runtime_id: str,
    endpoint: str,
    kind: RuntimeKind,
    source_value: RuntimeSource,
    status: RuntimeStatus,
    registered: bool,
    server_running: bool,
    installed: bool,
    installed_not_running: bool,
    identified_vendor: str | None,
    evidence: list[str],
    warnings: list[str],
    error: str | None,
    models: list[ModelDescriptor],
    last_seen_at: datetime,
) -> RuntimeDescriptor:
    """Validate a fresh RuntimeDescriptor from bounded fields; no copy bypass."""
    return RuntimeDescriptor(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=source_value,
        status=status,
        registered=registered,
        server_running=server_running,
        installed=installed,
        installed_not_running=installed_not_running,
        identified_vendor=identified_vendor,
        evidence=evidence,
        warnings=warnings,
        error=error,
        models=models,
        last_seen_at=last_seen_at,
    )


def _bare_error_descriptor(snapshot: _TargetSnapshot, error: str) -> RuntimeDescriptor:
    """Construct a small generic error descriptor from validated identity only."""
    return RuntimeDescriptor(
        runtime_id=snapshot.runtime_id,
        kind=snapshot.kind,
        endpoint=snapshot.endpoint,
        source=snapshot.source,
        status=RuntimeStatus.ERROR,
        registered=False,
        server_running=False,
        installed=False,
        installed_not_running=False,
        identified_vendor=None,
        evidence=[],
        warnings=[],
        error=error,
        models=[],
        last_seen_at=_now_utc(),
    )


def _now_utc() -> datetime:
    return datetime.now(UTC)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    return remaining if remaining > 0 else 0.0


def _validated_timeout(value: Any, limits: RuntimeProbeLimits) -> float:
    if type(value) not in (int, float):
        raise RuntimeProbeError("timeout is corrupted")
    numeric = float(value)
    if math.isnan(numeric) or math.isinf(numeric):
        raise RuntimeProbeError("timeout is not finite")
    if numeric <= 0 or numeric > limits.max_timeout_seconds:
        raise RuntimeProbeError("timeout is out of range")
    return numeric


def _make_adapter(
    registry: RuntimeProbeRegistry,
    snapshot: _TargetSnapshot,
    *,
    timeout: float,
    limits: RuntimeProbeLimits,
) -> RuntimeAdapter:
    namespace = object.__getattribute__(registry, "__dict__")
    if type(namespace) is not dict:
        raise RuntimeProbeError("registry state is invalid")
    if "transport" not in namespace or "executables" not in namespace:
        raise RuntimeProbeError("registry state is missing transport or executables")
    transport = namespace["transport"]
    executables = namespace["executables"]
    if transport is None:
        raise RuntimeProbeError("transport is invalid")
    if any("__bool__" in cls.__dict__ for cls in type(transport).__mro__[:-1]):
        raise RuntimeProbeError("transport must not define hostile truthiness")
    if type(executables) is not ExecutableDiscovery and not isinstance(
        executables, ExecutableDiscovery
    ):
        raise RuntimeProbeError("executable discovery is invalid")
    installed = executables.installed(snapshot.kind)
    if type(installed) is not bool:
        raise RuntimeProbeError("executable discovery returned a non-bool result")
    common = {
        "endpoint": snapshot.endpoint,
        "source": snapshot.source,
        "transport": transport,
        "timeout": timeout,
        "installed": installed,
        "bearer_token": snapshot.bearer_token,
    }
    if snapshot.adapter_type == AdapterType.OLLAMA:
        return OllamaAdapter(**common)
    if snapshot.adapter_type == AdapterType.LM_STUDIO:
        return LMStudioAdapter(**common)
    if snapshot.adapter_type == AdapterType.LLAMA_CPP:
        return LlamaCppAdapter(**common)
    if snapshot.adapter_type == AdapterType.MLX_LM:
        return MlxServerAdapter(**common)
    if snapshot.adapter_type == AdapterType.OPENAI_COMPATIBLE:
        return OpenAICompatAdapter(**common)
    raise RuntimeProbeError(
        f"Adapter type {snapshot.adapter_type.value} is not supported for probing."
    )
