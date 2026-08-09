"""Generic OpenAI-compatible /v1/models runtime adapter."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

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
from zana_core.runtimes.inference import (
    BaseRuntimeInferenceAdapter,
    EngineResult,
    InferenceLimits,
    ToolCallAccumulator,
    ToolCallArgumentsError,
    ToolCallLimitError,
    ToolCallParseError,
    _tool_call_failure,
    parse_json_line,
    verify_identity,
)
from zana_core.runtimes.transport import StreamTransport, UrllibTransport

if TYPE_CHECKING:
    from zana_core.instances import GenerationSettings, SessionBinding


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


class OpenAICompatInferenceAdapter(BaseRuntimeInferenceAdapter):
    """Bounded ``/v1/chat/completions`` SSE inference adapter."""

    runtime_id = "openai-compatible"

    def __init__(
        self,
        *,
        endpoint: str,
        runtime_id: str | None = None,
        limits: InferenceLimits | None = None,
        transport: StreamTransport | None = None,
        clock: Callable[[], float] | None = None,
        timeout_seconds: float | None = None,
        bearer_token: str | None = None,
    ) -> None:
        super().__init__(
            endpoint=endpoint,
            runtime_id=runtime_id if runtime_id is not None else self.runtime_id,
            limits=limits,
            transport=transport,
            clock=clock,
            timeout_seconds=timeout_seconds,
            bearer_token=bearer_token,
        )
        self._tool_accumulator = ToolCallAccumulator(self.limits)

    def begin_generation(self) -> None:
        self._tool_accumulator = ToolCallAccumulator(self.limits)

    def build_request(
        self,
        *,
        context: str,
        message: str,
        settings: GenerationSettings,
        binding: SessionBinding,
    ) -> tuple[str, dict[str, str], bytes]:
        url = self.chat_url()
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
        }
        if self.bearer_token:
            headers["Authorization"] = f"Bearer {self.bearer_token}"
        body = {
            "model": binding.runtime_model_id,
            "messages": [
                {"role": "system", "content": context},
                {"role": "user", "content": message},
            ],
            "stream": True,
            "temperature": settings.temperature,
            "max_tokens": settings.max_tokens,
            "top_p": settings.top_p,
            "stop": list(settings.stop),
        }
        return url, headers, UrllibTransport.json_body(body)

    def chat_url(self) -> str:
        if self.endpoint.endswith("/chat/completions"):
            return self.endpoint
        if self.endpoint.endswith("/v1"):
            return f"{self.endpoint}/chat/completions"
        return f"{self.endpoint}/v1/chat/completions"

    def parse_event(
        self,
        line: str,
        *,
        settings: GenerationSettings,
        binding: SessionBinding,
    ) -> EngineResult | None:
        if not line.startswith("data:"):
            return None
        payload_text = line[len("data:") :].lstrip()
        if payload_text == "[DONE]":
            try:
                tool_requests = self._tool_accumulator.finish()
            except (ToolCallLimitError, ToolCallParseError, ToolCallArgumentsError) as error:
                return _tool_call_failure(error)
            return EngineResult(status="completed", content="", tool_requests=tool_requests)
        if not payload_text:
            return None
        payload = parse_json_line(payload_text, "OpenAI-compatible stream")
        verify_identity(payload_model=payload.get("model"), binding=binding)
        if isinstance(payload.get("error"), dict):
            return EngineResult(
                status="failed",
                content="",
                error_code="RUNTIME_ERROR",
                error_message="The runtime reported an error.",
            )
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return None
        choice = choices[0]
        if not isinstance(choice, dict):
            return None
        finish = choice.get("finish_reason")
        delta = choice.get("delta")
        delta_dict: dict[str, Any] = delta if isinstance(delta, dict) else {}
        content = delta_dict.get("content")
        content_str = content if isinstance(content, str) else ""
        tool_calls = delta_dict.get("tool_calls")
        if tool_calls is not None:
            try:
                self._tool_accumulator.add_openai_delta(tool_calls)
            except (ToolCallLimitError, ToolCallParseError, ToolCallArgumentsError) as error:
                return _tool_call_failure(error)
        if finish is not None:
            if finish == "length":
                return EngineResult(
                    status="partial",
                    content="",
                    error_code="OUTPUT_LENGTH_LIMIT",
                    error_message="Output reached the model generation limit.",
                )
            try:
                tool_requests = self._tool_accumulator.finish()
            except (ToolCallLimitError, ToolCallParseError, ToolCallArgumentsError) as error:
                return _tool_call_failure(error)
            return EngineResult(
                status="completed",
                content=content_str,
                tool_requests=tool_requests,
            )
        if content_str:
            return EngineResult(status="streaming", content=content_str)
        return None
