"""Evidence-based LM Studio identification on top of OpenAI compatibility."""

from __future__ import annotations

import json
from typing import Any

from zana_core.domain.enums import RuntimeKind
from zana_core.runtimes.base import HttpResponse, InvalidRuntimeResponseError
from zana_core.runtimes.openai_compat import OpenAICompatAdapter


class LMStudioAdapter(OpenAICompatAdapter):
    """Identifies LM Studio only when its v0 metadata API responds."""

    runtime_id = "lm-studio-local"
    kind = RuntimeKind.LM_STUDIO

    def _identify_provider(
        self,
        evidence: list[str],
        warnings: list[str],
    ) -> tuple[str | None, list[str], list[str], RuntimeKind]:
        try:
            response = self.transport.request(
                "GET",
                f"{self.endpoint}/api/v0/models",
                headers=self._headers(),
                timeout=self.timeout,
            )
            payload = _parse_any_json(response)
            if _is_lmstudio_model_payload(payload):
                return (
                    "LM Studio",
                    ["LM Studio /api/v0/models responded with expected shape"],
                    [],
                    RuntimeKind.LM_STUDIO,
                )
            raise InvalidRuntimeResponseError("LM Studio v0 payload did not match expected shape")
        except (InvalidRuntimeResponseError, ValueError, KeyError, TypeError):
            warnings.append(
                "Server answered /v1/models but no LM Studio metadata; "
                "identified as generic OpenAI-compatible, not by port."
            )
            return None, [], warnings, RuntimeKind.OPENAI_COMPATIBLE


def _parse_any_json(response: HttpResponse) -> Any:
    try:
        return json.loads(response.text)
    except ValueError as error:
        raise InvalidRuntimeResponseError("Provider metadata returned invalid JSON.") from error


def _is_lmstudio_model_payload(payload: Any) -> bool:
    if isinstance(payload, list):
        return all(isinstance(item, dict) and "id" in item for item in payload)
    if isinstance(payload, dict):
        for key in ("data", "models"):
            value = payload.get(key)
            if isinstance(value, list) and all(
                isinstance(item, dict) and "id" in item for item in value
            ):
                return True
    return False
