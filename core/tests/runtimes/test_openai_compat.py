"""Generic OpenAI-compatible adapter and manual endpoint validation tests."""

from __future__ import annotations

import pytest

from tests.runtimes.conftest import FakeTransport, json_response
from zana_core.domain.enums import (
    ModelIdentityStrength,
    RuntimeKind,
    RuntimeStatus,
)
from zana_core.runtimes.base import AdapterType, ManualEndpointError
from zana_core.runtimes.endpoints import validate_manual_endpoint
from zana_core.runtimes.openai_compat import OpenAICompatAdapter


class TestManualEndpointValidation:
    def test_rejects_embedded_credentials(self) -> None:
        with pytest.raises(ManualEndpointError) as error:
            validate_manual_endpoint(
                "https://user:pass@example.com/v1",
                AdapterType.OPENAI_COMPATIBLE,
            )
        assert error.value.code == "ENDPOINT_CREDENTIALS_NOT_ALLOWED"

    def test_rejects_auto_adapter_selection(self) -> None:
        with pytest.raises(ManualEndpointError) as error:
            validate_manual_endpoint(
                "http://127.0.0.1:8080",
                AdapterType.AUTO,
            )
        assert error.value.code == "ADAPTER_REQUIRED"

    def test_accepts_explicit_remote_endpoint_without_probing(self) -> None:
        normalized = validate_manual_endpoint(
            "https://example.com/v1/",
            AdapterType.OPENAI_COMPATIBLE,
        )
        assert normalized == "https://example.com/v1"


class TestOpenAICompatProbe:
    def test_manual_endpoint_lists_models(self) -> None:
        endpoint = "http://127.0.0.1:8080/v1"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/models"): json_response(
                    {"object": "list", "data": [{"id": "local-model", "object": "model"}]}
                )
            }
        )
        adapter = OpenAICompatAdapter(
            endpoint=endpoint,
            transport=transport,
            bearer_token="stored-secret",
        )
        descriptor = adapter.probe()

        assert descriptor.registered is True
        assert descriptor.server_running is True
        assert descriptor.kind == RuntimeKind.OPENAI_COMPATIBLE
        assert len(descriptor.models) == 1
        model = descriptor.models[0]
        assert model.model_id == "local-model"
        assert model.digest is None
        assert model.identity_strength == ModelIdentityStrength.RUNTIME_MODEL_ID
        assert transport.calls[0][2] == {"Authorization": "Bearer stored-secret"}

    def test_invalid_response_is_not_registered(self) -> None:
        endpoint = "http://127.0.0.1:8080"
        transport = FakeTransport(
            {("GET", f"{endpoint}/v1/models"): json_response({"unexpected": True})}
        )
        descriptor = OpenAICompatAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.registered is False
        assert descriptor.status == RuntimeStatus.ERROR
        assert descriptor.models == []

    def test_timeout_is_offline(self) -> None:
        descriptor = OpenAICompatAdapter(
            endpoint="http://127.0.0.1:9999",
            transport=FakeTransport(default_timeout=True),
        ).probe()

        assert descriptor.registered is False
        assert descriptor.status == RuntimeStatus.OFFLINE
