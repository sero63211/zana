"""Evidence-based llama.cpp server identification."""

from __future__ import annotations

from typing import Any

from zana_core.domain.enums import RuntimeKind
from zana_core.runtimes.base import InvalidRuntimeResponseError
from zana_core.runtimes.lmstudio import _parse_any_json
from zana_core.runtimes.openai_compat import OpenAICompatAdapter

IDENTITY_MARKERS = ("llama.cpp", "llama_cpp", "llama-cpp")


class LlamaCppAdapter(OpenAICompatAdapter):
    """Identifies llama.cpp only through /props evidence, never by port."""

    runtime_id = "llamacpp-local"
    kind = RuntimeKind.LLAMA_CPP

    def _identify_provider(
        self,
        evidence: list[str],
        warnings: list[str],
    ) -> tuple[str | None, list[str], list[str], RuntimeKind]:
        try:
            response = self.transport.request(
                "GET",
                f"{self.endpoint}/props",
                headers=self._headers(),
                timeout=self.timeout,
            )
            payload = _parse_any_json(response)
            if not isinstance(payload, dict):
                raise InvalidRuntimeResponseError("llama.cpp /props did not return an object")
            if _contains_identity_marker(payload):
                return (
                    "llama.cpp",
                    ["llama.cpp /props metadata matched server identity"],
                    [],
                    RuntimeKind.LLAMA_CPP,
                )
            raise InvalidRuntimeResponseError("llama.cpp /props did not contain identity markers")
        except (InvalidRuntimeResponseError, ValueError, KeyError, TypeError):
            warnings.append(
                "Server answered /v1/models but no llama.cpp metadata; "
                "identified as generic OpenAI-compatible, not by port."
            )
            return None, [], warnings, RuntimeKind.OPENAI_COMPATIBLE


def _contains_identity_marker(payload: dict[str, Any]) -> bool:
    values = [payload.get("server_name"), payload.get("version")]
    values.extend(str(value) for value in payload.values() if isinstance(value, str))
    lowered = [str(value).lower() for value in values if value is not None]
    return any(marker in value for value in lowered for marker in IDENTITY_MARKERS)
