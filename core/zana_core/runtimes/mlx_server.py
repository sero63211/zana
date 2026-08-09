"""Evidence-based MLX-LM development server identification."""

from __future__ import annotations

from typing import Any

from zana_core.domain.enums import RuntimeKind
from zana_core.runtimes.base import InvalidRuntimeResponseError
from zana_core.runtimes.lmstudio import _parse_any_json
from zana_core.runtimes.openai_compat import OpenAICompatAdapter


class MlxServerAdapter(OpenAICompatAdapter):
    """Identifies MLX-LM only through /version evidence, never by port."""

    runtime_id = "mlx-lm-local"
    kind = RuntimeKind.MLX_LM

    def _identify_provider(
        self,
        evidence: list[str],
        warnings: list[str],
    ) -> tuple[str | None, list[str], list[str], RuntimeKind]:
        try:
            response = self.transport.request(
                "GET",
                f"{self.endpoint}/version",
                headers=self._headers(),
                timeout=self.timeout,
            )
            payload = _parse_any_json(response)
            if not isinstance(payload, dict) or not _contains_mlx_marker(payload):
                raise InvalidRuntimeResponseError("MLX /version did not contain MLX markers")
            warnings.append(
                "MLX-LM development server is not treated as a hardened production server."
            )
            return (
                "MLX-LM",
                ["MLX-LM /version metadata matched server identity"],
                warnings,
                RuntimeKind.MLX_LM,
            )
        except (InvalidRuntimeResponseError, ValueError, KeyError, TypeError):
            warnings.append(
                "Server answered /v1/models but no MLX metadata; "
                "identified as generic OpenAI-compatible, not by port."
            )
            return None, [], warnings, RuntimeKind.OPENAI_COMPATIBLE


def _contains_mlx_marker(payload: dict[str, Any]) -> bool:
    values = [str(value).lower() for value in payload.values() if isinstance(value, str)]
    return any("mlx" in value for value in values)
