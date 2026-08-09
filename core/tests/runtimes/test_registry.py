"""Concurrent probe registry safety and bounded-timeout tests."""

from __future__ import annotations

from tests.runtimes.conftest import FakeTransport, json_response
from zana_core.domain.enums import RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import (
    AdapterType,
    HttpResponse,
    ProbeTarget,
)
from zana_core.runtimes.executables import ExecutableDiscovery
from zana_core.runtimes.registry import RuntimeProbeRegistry


class TestRegistryProbe:
    def test_probes_explicit_targets_concurrently(self) -> None:
        ollama = ProbeTarget(
            runtime_id="ollama-local",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        )
        openai = ProbeTarget(
            runtime_id="manual-openai",
            kind=RuntimeKind.OPENAI_COMPATIBLE,
            endpoint="http://127.0.0.1:8080/v1",
            source=RuntimeSource.MANUAL,
            adapter_type=AdapterType.OPENAI_COMPATIBLE,
        )
        transport = FakeTransport(
            {
                ("GET", f"{ollama.endpoint}/api/tags"): json_response({"models": []}),
                ("GET", f"{openai.endpoint}/models"): json_response(
                    {"data": [{"id": "manual-model", "object": "model"}]}
                ),
            }
        )
        registry = RuntimeProbeRegistry(transport, max_workers=2)
        descriptors = registry.probe([ollama, openai])

        assert {descriptor.runtime_id for descriptor in descriptors} == {
            "ollama-local",
            "manual-openai",
        }
        assert all(descriptor.registered for descriptor in descriptors)

    def test_invalid_known_port_response_is_not_registered(self) -> None:
        transport = FakeTransport(
            {
                ("GET", "http://127.0.0.1:11434/api/tags"): HttpResponse(
                    status=200,
                    text="<html>proxy error</html>",
                    content_type="text/html",
                )
            }
        )
        registry = RuntimeProbeRegistry(transport, max_workers=4)
        descriptors = registry.probe(registry.default_targets())

        ollama = next(item for item in descriptors if item.runtime_id == "ollama-local")
        assert ollama.registered is False
        assert ollama.status == RuntimeStatus.ERROR
        assert ollama.models == []

    def test_bounded_timeout_marks_offline(self) -> None:
        registry = RuntimeProbeRegistry(
            FakeTransport(default_timeout=True),
            max_workers=4,
        )
        descriptors = registry.probe(registry.default_targets())

        assert len(descriptors) == 4
        assert all(item.registered is False for item in descriptors)
        assert all(item.status == RuntimeStatus.OFFLINE for item in descriptors)

    def test_executable_present_and_server_off(self) -> None:
        executables = ExecutableDiscovery(
            which=lambda name: "/usr/local/bin/ollama" if name == "ollama" else None
        )
        registry = RuntimeProbeRegistry(
            FakeTransport(default_timeout=True),
            executables=executables,
        )
        descriptors = registry.probe(
            [
                ProbeTarget(
                    runtime_id="ollama-local",
                    kind=RuntimeKind.OLLAMA,
                    endpoint="http://127.0.0.1:11434",
                    source=RuntimeSource.AUTO,
                    adapter_type=AdapterType.OLLAMA,
                )
            ]
        )

        descriptor = descriptors[0]
        assert descriptor.installed is True
        assert descriptor.installed_not_running is True
        assert descriptor.server_running is False
