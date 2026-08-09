"""Ollama adapter discovery and native pull planning tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from tests.runtimes.conftest import FakeTransport, json_response
from zana_core.domain.enums import ModelIdentityStrength, RuntimeStatus
from zana_core.runtimes.base import HttpResponse, PullApproval, RuntimeProbeError
from zana_core.runtimes.ollama import (
    OllamaAdapter,
    parse_ollama_pull_event,
    plan_ollama_pull,
)

ENDPOINT = "http://127.0.0.1:11434"


def tags_url() -> str:
    return f"{ENDPOINT}/api/tags"


def show_url() -> str:
    return f"{ENDPOINT}/api/show"


class TestOllamaProbe:
    def test_unavailable_is_not_registered(self) -> None:
        transport = FakeTransport(default_timeout=True)
        descriptor = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()

        assert descriptor.registered is False
        assert descriptor.server_running is False
        assert descriptor.status == RuntimeStatus.OFFLINE
        assert descriptor.models == []

    def test_executable_present_server_off(self) -> None:
        transport = FakeTransport(default_timeout=True)
        descriptor = OllamaAdapter(
            endpoint=ENDPOINT,
            transport=transport,
            installed=True,
        ).probe()

        assert descriptor.installed is True
        assert descriptor.installed_not_running is True
        assert descriptor.server_running is False
        assert descriptor.registered is False

    def test_empty_model_list_is_registered(self) -> None:
        transport = FakeTransport({("GET", tags_url()): json_response({"models": []})})
        descriptor = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()

        assert descriptor.registered is True
        assert descriptor.server_running is True
        assert descriptor.status == RuntimeStatus.ONLINE
        assert descriptor.models == []

    def test_real_shaped_metadata_is_parsed(self) -> None:
        transport = FakeTransport(
            {
                ("GET", tags_url()): json_response(
                    {
                        "models": [
                            {
                                "name": "qwen-example:tag",
                                "size": 2500000000,
                                "digest": "sha256:model-digest",
                                "details": {
                                    "format": "gguf",
                                    "family": "qwen",
                                    "parameter_size": "4B",
                                    "quantization_level": "Q4_K_M",
                                },
                            }
                        ]
                    }
                ),
                ("POST", show_url()): json_response(
                    {
                        "details": {
                            "format": "gguf",
                            "family": "qwen",
                            "parameter_size": "4B",
                            "quantization_level": "Q4_K_M",
                        },
                        "model_info": {
                            "general.parameter_count": 4000000000,
                            "general.size": 2500000000,
                            "llama.context_length": 32768,
                        },
                        "capabilities": ["completion", "tools"],
                    }
                ),
            }
        )
        descriptor = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()

        assert descriptor.registered is True
        assert len(descriptor.models) == 1
        model = descriptor.models[0]
        assert model.model_id == "qwen-example:tag"
        assert model.digest == "sha256:model-digest"
        assert model.family == "qwen"
        assert model.parameter_count == 4000000000
        assert model.parameter_label == "4B"
        assert model.format == "gguf"
        assert model.quantization == "Q4_K_M"
        assert model.size_bytes == 2500000000
        assert model.context_length == 32768
        assert model.capabilities == ["completion", "tools"]
        assert model.identity_strength == ModelIdentityStrength.EXACT_DIGEST

    def test_missing_digest_is_honestly_weak(self) -> None:
        transport = FakeTransport(
            {
                ("GET", tags_url()): json_response(
                    {
                        "models": [
                            {
                                "name": "generic-model",
                                "size": 100,
                                "details": {},
                            }
                        ]
                    }
                ),
                ("POST", show_url()): json_response({"details": {}}),
            }
        )
        descriptor = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()

        model = descriptor.models[0]
        assert model.digest is None
        assert model.identity_strength == ModelIdentityStrength.RUNTIME_MODEL_ID

    def test_invalid_response_is_not_registered(self) -> None:
        transport = FakeTransport(
            {
                ("GET", tags_url()): HttpResponse(
                    status=200,
                    text="<html>not json</html>",
                    content_type="text/html",
                ),
            }
        )
        invalid_html = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()
        assert invalid_html.registered is False
        assert invalid_html.status == RuntimeStatus.ERROR
        assert invalid_html.error is not None

        transport.routes[("GET", tags_url())] = json_response({"unexpected": True})
        missing_models = OllamaAdapter(endpoint=ENDPOINT, transport=transport).probe()
        assert missing_models.registered is False
        assert missing_models.status == RuntimeStatus.ERROR


class TestOllamaPullPlanner:
    def test_pull_requires_explicit_user_approval(self) -> None:
        with pytest.raises(RuntimeProbeError):
            plan_ollama_pull(ENDPOINT, "qwen-example:tag", None)

    def test_approved_pull_returns_plan_without_calling(self) -> None:
        approval = PullApproval(
            model_reference="qwen-example:tag",
            granted_at=datetime(2026, 8, 9, tzinfo=UTC),
        )
        plan = plan_ollama_pull(ENDPOINT, "qwen-example:tag", approval)

        assert plan.method == "POST"
        assert plan.path == "/api/pull"
        assert plan.body == {"model": "qwen-example:tag", "stream": True}
        assert plan.stream is True
        assert plan.approved_at == approval.granted_at

    def test_pull_event_parser(self) -> None:
        event = parse_ollama_pull_event(
            '{"status":"downloading","digest":"sha256:abc","total":100,"completed":25}'
        )
        assert event is not None
        assert event.status == "downloading"
        assert event.progress_0_1 == 0.25
        assert parse_ollama_pull_event("not json") is None
        assert parse_ollama_pull_event('{"error":"denied"}') is not None
