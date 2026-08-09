"""Ollama runtime probe and explicit native pull planning."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from zana_core.domain.enums import ModelIdentityStrength, RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import (
    HttpTransport,
    ModelDescriptor,
    PullApproval,
    RuntimeDescriptor,
    RuntimeProbeError,
    RuntimeProbeTimeoutError,
    build_runtime_descriptor,
    now_utc,
    parse_json_object,
    parse_parameter_label,
    require_http_ok,
)
from zana_core.runtimes.transport import UrllibTransport

OLLAMA_DEFAULT_ENDPOINT = "http://127.0.0.1:11434"


class OllamaAdapter:
    """Probes /api/tags and enriches each model via /api/show."""

    runtime_id = "ollama-local"
    kind = RuntimeKind.OLLAMA

    def __init__(
        self,
        *,
        endpoint: str = OLLAMA_DEFAULT_ENDPOINT,
        source: RuntimeSource = RuntimeSource.AUTO,
        transport: HttpTransport | None = None,
        timeout: float = 1.5,
        installed: bool = False,
        bearer_token: str | None = None,
    ) -> None:
        self.endpoint = endpoint.rstrip("/")
        self.source = source
        self.transport = transport or UrllibTransport()
        self.timeout = timeout
        self.installed = installed
        self.bearer_token = bearer_token

    def probe(self) -> RuntimeDescriptor:
        evidence: list[str] = []
        warnings: list[str] = []
        if self.installed:
            evidence.append("ollama executable present on PATH")
        try:
            tags_response = self.transport.request(
                "GET",
                f"{self.endpoint}/api/tags",
                headers=self._headers(),
                timeout=self.timeout,
            )
            require_http_ok(tags_response, "Ollama /api/tags")
            payload = parse_json_object(tags_response, "Ollama /api/tags")
            if not isinstance(payload.get("models"), list):
                raise ValueError("missing models list")
            evidence.append("Ollama /api/tags matched expected shape")
            models = self._parse_models(payload["models"], evidence, warnings)
            return build_runtime_descriptor(
                runtime_id=self.runtime_id,
                kind=self.kind,
                endpoint=self.endpoint,
                source=self.source,
                installed=self.installed,
                server_running=True,
                registered=True,
                status=RuntimeStatus.ONLINE,
                evidence=evidence,
                warnings=warnings,
                models=models,
            )
        except RuntimeProbeTimeoutError as error:
            return build_runtime_descriptor(
                runtime_id=self.runtime_id,
                kind=self.kind,
                endpoint=self.endpoint,
                source=self.source,
                installed=self.installed,
                server_running=False,
                registered=False,
                status=RuntimeStatus.OFFLINE,
                evidence=evidence,
                warnings=warnings,
                error=str(error),
            )
        except RuntimeProbeError as error:
            return build_runtime_descriptor(
                runtime_id=self.runtime_id,
                kind=self.kind,
                endpoint=self.endpoint,
                source=self.source,
                installed=self.installed,
                server_running=False,
                registered=False,
                status=RuntimeStatus.ERROR,
                evidence=evidence,
                warnings=warnings,
                error=str(error),
            )
        except (ValueError, KeyError, TypeError):
            return build_runtime_descriptor(
                runtime_id=self.runtime_id,
                kind=self.kind,
                endpoint=self.endpoint,
                source=self.source,
                installed=self.installed,
                server_running=False,
                registered=False,
                status=RuntimeStatus.ERROR,
                evidence=evidence,
                warnings=warnings,
                error="Ollama /api/tags did not match the expected shape.",
            )

    def _headers(self) -> dict[str, str] | None:
        if not self.bearer_token:
            return None
        return {"Authorization": f"Bearer {self.bearer_token}"}

    def _parse_models(
        self,
        entries: list[Any],
        evidence: list[str],
        warnings: list[str],
    ) -> list[ModelDescriptor]:
        models: list[ModelDescriptor] = []
        for entry in entries:
            if not isinstance(entry, dict):
                warnings.append("Skipped a non-object model entry in /api/tags.")
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name:
                warnings.append("Skipped a model entry without a name.")
                continue
            descriptor = self._build_model(name, entry, evidence, warnings)
            if descriptor is not None:
                models.append(descriptor)
        return models

    def _build_model(
        self,
        name: str,
        tag_entry: dict[str, Any],
        evidence: list[str],
        warnings: list[str],
    ) -> ModelDescriptor | None:
        details_value = tag_entry.get("details")
        details: dict[str, Any] = details_value if isinstance(details_value, dict) else {}
        digest = self._text(tag_entry.get("digest"))
        identity = (
            ModelIdentityStrength.EXACT_DIGEST if digest else ModelIdentityStrength.RUNTIME_MODEL_ID
        )
        values: dict[str, Any] = {
            "runtime_id": self.runtime_id,
            "model_id": name,
            "display_name": name,
            "digest": digest,
            "family": self._text(details.get("family")),
            "parameter_label": self._text(details.get("parameter_size")),
            "format": self._text(details.get("format")),
            "quantization": self._text(details.get("quantization_level")),
            "size_bytes": _as_int(tag_entry.get("size")),
            "metadata_source": "runtime",
            "last_seen_at": now_utc(),
            "identity_strength": identity,
        }

        try:
            show_response = self.transport.request(
                "POST",
                f"{self.endpoint}/api/show",
                headers=self._headers(),
                body=UrllibTransport.json_body({"model": name}),
                timeout=self.timeout,
            )
            require_http_ok(show_response, "Ollama /api/show")
            show = parse_json_object(show_response, "Ollama /api/show")
            values = self._apply_show_metadata(values, show, evidence)
        except (RuntimeProbeError, ValueError, KeyError, TypeError):
            warnings.append(f"/api/show enrichment failed for {name}; tags metadata only.")
        return ModelDescriptor(**values)

    def _apply_show_metadata(
        self,
        values: dict[str, Any],
        show: dict[str, Any],
        evidence: list[str],
    ) -> dict[str, Any]:
        details_value = show.get("details")
        details: dict[str, Any] = details_value if isinstance(details_value, dict) else {}
        model_info_value = show.get("model_info")
        model_info: dict[str, Any] = model_info_value if isinstance(model_info_value, dict) else {}
        if not values.get("digest"):
            values["digest"] = self._text(show.get("digest"))
            if values["digest"]:
                values["identity_strength"] = ModelIdentityStrength.EXACT_DIGEST
        for field, source_key in (
            ("family", "family"),
            ("parameter_label", "parameter_size"),
            ("format", "format"),
            ("quantization", "quantization_level"),
        ):
            value = self._text(details.get(source_key))
            if value and values.get(field) is None:
                values[field] = value
        parameter_count = _as_int(model_info.get("general.parameter_count"))
        if parameter_count is None:
            parameter_count = parse_parameter_label(values.get("parameter_label"))
        if parameter_count is not None:
            values["parameter_count"] = parameter_count
        size_bytes = _as_int(model_info.get("general.size"))
        if size_bytes is not None:
            values["size_bytes"] = size_bytes
        context_length = _as_int(model_info.get("llama.context_length"))
        if context_length is not None:
            values["context_length"] = context_length
        capabilities = show.get("capabilities")
        if isinstance(capabilities, list):
            values["capabilities"] = [str(item) for item in capabilities if isinstance(item, str)]
        evidence.append(f"Ollama /api/show enriched {values['model_id']}")
        return values

    @staticmethod
    def _text(value: Any) -> str | None:
        return value if isinstance(value, str) and value else None


@dataclass(frozen=True)
class OllamaPullPlan:
    """A prepared native pull request requiring prior user approval."""

    endpoint: str
    model_reference: str
    method: str = "POST"
    path: str = "/api/pull"
    body: dict[str, Any] | None = None
    stream: bool = True
    approved_at: datetime | None = None


def plan_ollama_pull(
    endpoint: str,
    model_reference: str,
    approval: PullApproval | None,
) -> OllamaPullPlan:
    """Return a pull plan only when an explicit user approval object exists.

    This primitive never performs HTTP and never proxies model bytes; the
    caller decides whether and how to execute the returned plan.
    """
    if approval is None or approval.granted_by != "user":
        raise RuntimeProbeError("Ollama pull requires explicit user approval.")
    if not model_reference.strip():
        raise RuntimeProbeError("A model reference is required for Ollama pull.")
    normalized = endpoint.rstrip("/")
    return OllamaPullPlan(
        endpoint=normalized,
        model_reference=model_reference,
        body={"model": model_reference, "stream": True},
        stream=True,
        approved_at=approval.granted_at,
    )


@dataclass(frozen=True)
class OllamaPullEvent:
    """One parsed native pull progress line; no bytes are proxied."""

    status: str
    digest: str | None = None
    total: int | None = None
    completed: int | None = None
    error: str | None = None
    progress_0_1: float | None = None


def parse_ollama_pull_event(line: str) -> OllamaPullEvent | None:
    """Parse one JSON line from Ollama's native pull stream."""
    stripped = line.strip()
    if not stripped:
        return None
    try:
        payload = json.loads(stripped)
    except ValueError:
        return None
    if not isinstance(payload, dict):
        return None
    status = payload.get("status")
    total = _as_int(payload.get("total"))
    completed = _as_int(payload.get("completed"))
    progress = None
    if total is not None and total > 0 and completed is not None:
        progress = max(0.0, min(1.0, completed / total))
    error = payload.get("error")
    if not isinstance(status, str):
        if isinstance(error, str):
            return OllamaPullEvent(status="error", error=error)
        return None
    return OllamaPullEvent(
        status=status,
        digest=payload.get("digest") if isinstance(payload.get("digest"), str) else None,
        total=total,
        completed=completed,
        error=error if isinstance(error, str) else None,
        progress_0_1=progress,
    )


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value
