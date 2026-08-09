"""Safe concurrent localhost probe registry with strict hard limits."""

from __future__ import annotations

import math
import re
from collections.abc import Iterable, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from urllib.parse import urlsplit

from zana_core.domain.enums import (
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
    DEFAULT_PROBE_LIMITS,
    MAX_EVIDENCE_ITEMS,
    RuntimeProbeLimits,
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


class RuntimeProbeRegistry:
    """Probes explicit localhost targets concurrently with bounded timeouts.

    No LAN scanning or automatic remote discovery is performed.
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
        self.limits = limits or DEFAULT_PROBE_LIMITS
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, int | float)
            or not math.isfinite(timeout)
            or timeout <= 0
            or timeout > self.limits.max_timeout_seconds
        ):
            raise ValueError(f"timeout must be in (0, {self.limits.max_timeout_seconds:g}] seconds")
        if (
            isinstance(max_workers, bool)
            or not isinstance(max_workers, int)
            or max_workers < 1
            or max_workers > self.limits.max_workers
        ):
            raise ValueError(f"max_workers must be in [1, {self.limits.max_workers}]")
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.max_workers = max_workers
        self.executables = executables or ExecutableDiscovery()

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
        remainder is materialized. Empty input returns immediately without
        creating an executor; a single target or ``max_workers == 1`` probes
        synchronously with zero threads.
        """
        collected = self._bounded_collect(targets)
        if not collected:
            return []
        self._validate_targets(collected)
        if len(collected) == 1 or self.max_workers == 1:
            return [self._probe_one_sanitized(target) for target in collected]
        results: list[RuntimeDescriptor] = []
        worker_count = min(self.max_workers, len(collected))
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(self._probe_one_sanitized, target): target for target in collected
            }
            for future in as_completed(futures):
                results.append(future.result())
        return sorted(results, key=lambda descriptor: descriptor.runtime_id)

    def _bounded_collect(
        self, targets: Sequence[ProbeTarget] | Iterable[ProbeTarget]
    ) -> list[ProbeTarget]:
        # One cap+1 bounded path for every input; never trust __len__ and
        # never materialize beyond max_targets + 1, even for hostile Sequences.
        collected: list[ProbeTarget] = []
        for index, target in enumerate(targets):
            if index >= self.limits.max_targets:
                raise ValueError(
                    f"target count exceeds limit {self.limits.max_targets}; "
                    "stopped at the bounded cap"
                )
            collected.append(target)
        return collected

    def _validate_targets(self, targets: list[ProbeTarget]) -> None:
        seen: set[str] = set()
        for index, target in enumerate(targets):
            if not isinstance(target, ProbeTarget):
                raise ValueError(f"target {index} is not a ProbeTarget")
            self._validate_target_enums(target)
            if not isinstance(target.runtime_id, str) or not target.runtime_id:
                raise ValueError(f"target {index} runtime_id must be a non-empty string")
            if not isinstance(target.endpoint, str) or not target.endpoint:
                raise ValueError(f"target {index} endpoint must be a non-empty string")
            if target.runtime_id in seen:
                raise ValueError(f"duplicate runtime_id {target.runtime_id!r} in probe targets")
            seen.add(target.runtime_id)
            self._validate_bounded_string(
                target.runtime_id,
                label="runtime_id",
                char_limit=self.limits.max_reference_length,
                byte_limit=self.limits.max_reference_bytes,
            )
            self._validate_bounded_string(
                target.endpoint,
                label="endpoint",
                char_limit=self.limits.max_endpoint_length,
                byte_limit=self.limits.max_endpoint_bytes,
            )
            self._validate_endpoint_shape(target.endpoint)
            if target.bearer_token is not None:
                if not isinstance(target.bearer_token, str):
                    raise ValueError("bearer_token must be a string")
                if len(target.bearer_token) > self.limits.max_bearer_token_bytes:
                    raise ValueError("bearer_token exceeds the configured byte limit")
                if len(target.bearer_token.encode("utf-8")) > self.limits.max_bearer_token_bytes:
                    raise ValueError("bearer_token exceeds the configured byte limit")
                if _contains_control_characters(target.bearer_token):
                    raise ValueError("bearer_token contains control characters")
            effective_timeout = self.timeout if target.timeout is None else target.timeout
            if (
                isinstance(effective_timeout, bool)
                or not isinstance(effective_timeout, int | float)
                or not math.isfinite(effective_timeout)
                or effective_timeout <= 0
                or effective_timeout > self.limits.max_timeout_seconds
            ):
                raise ValueError(
                    f"target timeout must be in (0, {self.limits.max_timeout_seconds:g}] seconds"
                )

    @staticmethod
    def _validate_target_enums(target: ProbeTarget) -> None:
        if not isinstance(target.kind, RuntimeKind):
            raise ValueError("target kind must be a RuntimeKind")
        if not isinstance(target.source, RuntimeSource):
            raise ValueError("target source must be a RuntimeSource")
        if not isinstance(target.adapter_type, AdapterType):
            raise ValueError("target adapter_type must be an AdapterType")

    def _validate_bounded_string(
        self,
        value: str,
        *,
        label: str,
        char_limit: int,
        byte_limit: int,
    ) -> None:
        if not isinstance(value, str):
            raise ValueError(f"{label} must be a string")
        if len(value) > char_limit:
            raise ValueError(f"{label} exceeds the configured character limit")
        if len(value.encode("utf-8")) > byte_limit:
            raise ValueError(f"{label} exceeds the configured byte limit")
        if _contains_control_characters(value):
            raise ValueError(f"{label} contains control characters")

    @staticmethod
    def _validate_endpoint_shape(endpoint: str) -> None:
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
        if not parts.hostname:
            raise ValueError("endpoint must contain a host")
        port: int | None
        try:
            port = parts.port
        except ValueError:
            raise ValueError("endpoint port is invalid") from None
        if port is not None and (port < 1 or port > 65535):
            raise ValueError("endpoint port is invalid")

    def _probe_one_sanitized(self, target: ProbeTarget) -> RuntimeDescriptor:
        try:
            return self._probe_one(target)
        except RuntimeProbeError as error:
            try:
                return self._error_descriptor(target, error=self._sanitize_error(error))
            except Exception:  # noqa: BLE001 - fallback must never raise
                return _bare_error_descriptor(
                    target, "Unexpected probe failure; details are not exposed."
                )
        except Exception:  # noqa: BLE001 - unexpected failures are isolated and sanitized
            try:
                return self._error_descriptor(
                    target, error="Unexpected probe failure; details are not exposed."
                )
            except Exception:  # noqa: BLE001 - fallback must never raise
                return _bare_error_descriptor(
                    target, "Unexpected probe failure; details are not exposed."
                )

    def _error_descriptor(self, target: ProbeTarget, *, error: str) -> RuntimeDescriptor:
        return _bare_error_descriptor(target, error)

    def _probe_one(self, target: ProbeTarget) -> RuntimeDescriptor:
        adapter = _make_adapter(self, target)
        descriptor = self._bound_descriptor(adapter.probe())
        return _rebuild_descriptor(
            descriptor,
            runtime_id=target.runtime_id,
            endpoint=target.endpoint,
            kind=target.kind,
            source_value=target.source,
            evidence=descriptor.evidence,
            warnings=descriptor.warnings,
            error=descriptor.error,
            models=descriptor.models,
            identified_vendor=descriptor.identified_vendor,
            last_seen_at=descriptor.last_seen_at,
        )

    def _bound_descriptor(self, descriptor: RuntimeDescriptor) -> RuntimeDescriptor:
        evidence = self._bounded_evidence(descriptor.evidence)
        warnings = self._bounded_strings(descriptor.warnings)
        error = self._sanitize_text(descriptor.error)
        models = self._bound_models(descriptor.models)
        identified_vendor = (
            None
            if descriptor.identified_vendor is None
            else _truncate_text(descriptor.identified_vendor, self.limits.max_model_field_bytes)
        )
        return _rebuild_descriptor(
            descriptor,
            runtime_id=descriptor.runtime_id,
            endpoint=descriptor.endpoint,
            kind=descriptor.kind,
            source_value=descriptor.source,
            evidence=evidence,
            warnings=warnings,
            error=error,
            models=models,
            identified_vendor=identified_vendor,
            last_seen_at=_now_utc(),
        )

    def _bound_models(self, models: list[ModelDescriptor]) -> list[ModelDescriptor]:
        if len(models) > self.limits.max_models:
            raise RuntimeProbeError(
                f"model count {len(models)} exceeds policy limit {self.limits.max_models}"
            )
        projected: list[ModelDescriptor] = []
        total_bytes = 0
        for model in models:
            bounded = self._project_model(model)
            bytes_used = _model_byte_count(bounded)
            total_bytes += bytes_used
            if total_bytes > self.limits.max_models_total_bytes:
                raise RuntimeProbeError("projected model output exceeds the total byte limit")
            projected.append(bounded)
        return projected

    def _project_model(self, model: ModelDescriptor) -> ModelDescriptor:
        limit = self.limits.max_model_field_bytes
        runtime_id = _truncate_text(model.runtime_id, limit)
        bounded = _truncate_text(model.model_id, limit)
        display_name = _truncate_text(model.display_name, limit)
        digest = None if model.digest is None else _truncate_text(model.digest, limit)
        family = None if model.family is None else _truncate_text(model.family, limit)
        parameter_label = (
            None if model.parameter_label is None else _truncate_text(model.parameter_label, limit)
        )
        model_format = None if model.format is None else _truncate_text(model.format, limit)
        quantization = (
            None if model.quantization is None else _truncate_text(model.quantization, limit)
        )
        trainability = (
            None if model.trainability is None else _truncate_text(model.trainability, limit)
        )
        metadata_source = _truncate_text(model.metadata_source, limit)
        capabilities = [
            _truncate_text(item, limit)
            for item in model.capabilities[: self.limits.max_model_capabilities]
        ]
        # Build a fresh validated descriptor instead of bypassing the contract.
        return ModelDescriptor(
            runtime_id=runtime_id,
            model_id=bounded,
            display_name=display_name,
            digest=digest,
            family=family,
            parameter_count=model.parameter_count,
            parameter_label=parameter_label,
            format=model_format,
            quantization=quantization,
            size_bytes=model.size_bytes,
            context_length=model.context_length,
            capabilities=capabilities,
            trainability=trainability,
            metadata_source=metadata_source,
            last_seen_at=model.last_seen_at,
            identity_strength=model.identity_strength,
        )

    def _bounded_evidence(self, evidence: list[str]) -> list[str]:
        return self._bounded_strings(evidence, item_limit=self.limits.max_evidence_items)

    def _bounded_strings(
        self,
        values: list[str],
        *,
        item_limit: int = MAX_EVIDENCE_ITEMS,
    ) -> list[str]:
        total = 0
        bounded: list[str] = []
        for index, value in enumerate(values):
            if index >= item_limit:
                break
            if not isinstance(value, str):
                # Never invoke arbitrary __str__/repr on untrusted objects.
                value = "[non-string]"
            if len(value) > self.limits.max_evidence_chars:
                value = value[: self.limits.max_evidence_chars]
            if total + len(value) > self.limits.max_evidence_chars:
                break
            bounded.append(value)
            total += len(value)
        return bounded

    def _sanitize_error(self, error: RuntimeProbeError) -> str:
        # Only an already-existing exact str argument is inspected; hostile
        # __str__ implementations are never invoked.
        args = getattr(error, "args", ())
        if len(args) == 1 and isinstance(args[0], str):
            return self._sanitize_text(args[0])
        return "Unexpected probe failure; details are not exposed."

    def _sanitize_text(self, text: str | None) -> str:
        if text is None:
            return ""
        # Only the retained prefix is inspected, so regex work and copies are
        # bounded regardless of exception string size.
        prefix = text[: self.limits.max_error_chars]
        if len(text) > self.limits.max_error_chars and _boundary_may_cut_credential(prefix):
            prefix = prefix[: _credential_safe_prefix_len(prefix)]
        sanitized = _REDACT_URL_CREDENTIALS.sub(_REDACTED, prefix)
        sanitized = _REDACT_BEARER.sub(_REDACTED, sanitized)
        sanitized = _REDACT_RAW_TOKEN.sub(_REDACTED, sanitized)
        return sanitized[: self.limits.max_error_chars]


_REDACTED = "[REDACTED]"
_REDACT_URL_CREDENTIALS = re.compile(r"//[^/@\s]+@")
_REDACT_BEARER = re.compile(r"(?i)(bearer\s*=\s*)[^\s<>]+")
_REDACT_RAW_TOKEN = re.compile(
    r"(?i)\b(?:token|secret|pass(?:word)?|api[-_]?key|authorization)\b"
    r"[=:\s]+[^\s<>]+"
)


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
    # UTF-8 bytes are >= code points, so slicing to limit code points bounds
    # the prefix before any full-string encoding.
    bounded_prefix = value[:limit]
    encoded = bounded_prefix.encode("utf-8")
    if len(encoded) <= limit:
        return bounded_prefix
    return encoded[:limit].decode("utf-8", errors="ignore")


def _model_byte_count(model: ModelDescriptor) -> int:
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
        value = getattr(model, field)
        if value is not None:
            total += len(value.encode("utf-8"))
    total += sum(len(item.encode("utf-8")) for item in model.capabilities)
    return total


def _rebuild_descriptor(
    source: RuntimeDescriptor,
    *,
    runtime_id: str,
    endpoint: str,
    kind: RuntimeKind,
    source_value: RuntimeSource,
    evidence: list[str],
    warnings: list[str],
    error: str | None,
    models: list[ModelDescriptor],
    identified_vendor: str | None,
    last_seen_at: datetime,
) -> RuntimeDescriptor:
    """Validate a fresh RuntimeDescriptor from bounded fields; no copy bypass."""
    return RuntimeDescriptor(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=source_value,
        status=source.status,
        registered=source.registered,
        server_running=source.server_running,
        installed=source.installed,
        installed_not_running=source.installed_not_running,
        identified_vendor=identified_vendor,
        evidence=evidence,
        warnings=warnings,
        error=error,
        models=models,
        last_seen_at=last_seen_at,
    )


def _bare_error_descriptor(target: ProbeTarget, error: str) -> RuntimeDescriptor:
    """Construct a small generic error descriptor from validated identity only."""
    return RuntimeDescriptor(
        runtime_id=target.runtime_id,
        kind=target.kind,
        endpoint=target.endpoint,
        source=target.source,
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


def _make_adapter(registry: RuntimeProbeRegistry, target: ProbeTarget) -> RuntimeAdapter:
    timeout = registry.timeout if target.timeout is None else target.timeout
    installed = registry.executables.installed(target.kind)
    common = {
        "endpoint": target.endpoint,
        "source": target.source,
        "transport": registry.transport,
        "timeout": timeout,
        "installed": installed,
        "bearer_token": target.bearer_token,
    }
    if target.adapter_type == AdapterType.OLLAMA:
        return OllamaAdapter(**common)
    if target.adapter_type == AdapterType.LM_STUDIO:
        return LMStudioAdapter(**common)
    if target.adapter_type == AdapterType.LLAMA_CPP:
        return LlamaCppAdapter(**common)
    if target.adapter_type == AdapterType.MLX_LM:
        return MlxServerAdapter(**common)
    if target.adapter_type == AdapterType.OPENAI_COMPATIBLE:
        return OpenAICompatAdapter(**common)
    raise RuntimeProbeError(
        f"Adapter type {target.adapter_type.value} is not supported for probing."
    )
