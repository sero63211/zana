"""Safe concurrent localhost probe registry."""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed

from zana_core.domain.enums import RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import (
    AdapterType,
    HttpTransport,
    ProbeTarget,
    RuntimeAdapter,
    RuntimeDescriptor,
    RuntimeProbeError,
    build_runtime_descriptor,
)
from zana_core.runtimes.executables import ExecutableDiscovery
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
    ) -> None:
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

    def probe(self, targets: list[ProbeTarget]) -> list[RuntimeDescriptor]:
        """Probe targets concurrently and return one descriptor per target."""
        results: list[RuntimeDescriptor] = []
        with ThreadPoolExecutor(
            max_workers=min(self.max_workers, max(1, len(targets)))
        ) as executor:
            futures = {executor.submit(self._probe_one, target): target for target in targets}
            for future in as_completed(futures):
                target = futures[future]
                try:
                    results.append(future.result())
                except RuntimeProbeError as error:
                    results.append(
                        build_runtime_descriptor(
                            runtime_id=target.runtime_id,
                            kind=target.kind,
                            endpoint=target.endpoint,
                            source=target.source,
                            installed=self.executables.installed(target.kind),
                            server_running=False,
                            registered=False,
                            status=RuntimeStatus.ERROR,
                            evidence=[],
                            warnings=[],
                            error=str(error),
                        )
                    )
        return sorted(results, key=lambda descriptor: descriptor.runtime_id)

    def _probe_one(self, target: ProbeTarget) -> RuntimeDescriptor:
        adapter = self._make_adapter(target)
        descriptor = adapter.probe()
        return descriptor.model_copy(
            update={"runtime_id": target.runtime_id, "endpoint": target.endpoint}
        )

    def _make_adapter(self, target: ProbeTarget) -> RuntimeAdapter:
        timeout = target.timeout or self.timeout
        installed = self.executables.installed(target.kind)
        common = {
            "endpoint": target.endpoint,
            "source": target.source,
            "transport": self.transport,
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
