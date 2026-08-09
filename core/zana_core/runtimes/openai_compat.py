"""Generic OpenAI-compatible /v1/models runtime adapter."""

from __future__ import annotations

from typing import Any

from zana_core.domain.enums import ModelIdentityStrength, RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import (
    HttpTransport,
    ModelDescriptor,
    RuntimeDescriptor,
    RuntimeProbeError,
    RuntimeProbeTimeoutError,
    build_runtime_descriptor,
    now_utc,
    parse_json_object,
    require_http_ok,
)
from zana_core.runtimes.transport import UrllibTransport


class OpenAICompatAdapter:
    """Probes an explicit OpenAI-compatible /v1/models endpoint."""

    runtime_id = "openai-compatible"
    kind = RuntimeKind.OPENAI_COMPATIBLE

    def __init__(
        self,
        *,
        endpoint: str,
        source: RuntimeSource = RuntimeSource.MANUAL,
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
        models_url = self.models_url()
        try:
            response = self.transport.request(
                "GET",
                models_url,
                headers=self._headers(),
                timeout=self.timeout,
            )
            require_http_ok(response, "OpenAI-compatible /v1/models")
            payload = parse_json_object(response, "OpenAI-compatible /v1/models")
            entries = payload.get("data")
            if not isinstance(entries, list):
                raise ValueError("missing data list")
            models = self._parse_models(entries, evidence, warnings)
            evidence.append("OpenAI-compatible /v1/models matched expected shape")
            vendor, vendor_evidence, vendor_warnings, kind = self._identify_provider(
                evidence, warnings
            )
            evidence.extend(vendor_evidence)
            warnings.extend(vendor_warnings)
            descriptor = build_runtime_descriptor(
                runtime_id=self.runtime_id,
                kind=kind,
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
            return descriptor.model_copy(update={"identified_vendor": vendor})
        except RuntimeProbeTimeoutError as error:
            return self._offline(evidence, warnings, str(error))
        except RuntimeProbeError as error:
            return self._error(evidence, warnings, str(error))
        except (ValueError, KeyError, TypeError):
            return self._error(
                evidence,
                warnings,
                "OpenAI-compatible /v1/models did not match the expected shape.",
            )

    def models_url(self) -> str:
        if self.endpoint.endswith("/models"):
            return self.endpoint
        if self.endpoint.endswith("/v1"):
            return f"{self.endpoint}/models"
        return f"{self.endpoint}/v1/models"

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
                warnings.append("Skipped a non-object model entry in /v1/models.")
                continue
            model_id = entry.get("id")
            if not isinstance(model_id, str) or not model_id:
                warnings.append("Skipped a model entry without an id.")
                continue
            models.append(
                ModelDescriptor(
                    runtime_id=self.runtime_id,
                    model_id=model_id,
                    display_name=model_id,
                    metadata_source="runtime",
                    last_seen_at=now_utc(),
                    identity_strength=ModelIdentityStrength.RUNTIME_MODEL_ID,
                )
            )
        evidence.append(f"OpenAI-compatible list returned {len(models)} model(s)")
        return models

    def _identify_provider(
        self,
        evidence: list[str],
        warnings: list[str],
    ) -> tuple[str | None, list[str], list[str], RuntimeKind]:
        """Subclasses add evidence-based provider identification here."""
        return None, [], [], self.kind

    def _offline(
        self,
        evidence: list[str],
        warnings: list[str],
        error: str,
    ) -> RuntimeDescriptor:
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
            error=error,
        )

    def _error(
        self,
        evidence: list[str],
        warnings: list[str],
        error: str,
    ) -> RuntimeDescriptor:
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
            error=error,
        )
