"""Evidence-based vendor identification without port-only assumptions."""

from __future__ import annotations

from tests.runtimes.conftest import FakeTransport, json_response
from zana_core.domain.enums import RuntimeKind
from zana_core.runtimes.base import HttpResponse
from zana_core.runtimes.llamacpp import LlamaCppAdapter
from zana_core.runtimes.lmstudio import LMStudioAdapter
from zana_core.runtimes.mlx_server import MlxServerAdapter


class TestLMStudioIdentification:
    def test_v0_metadata_evidence_identifies_lm_studio(self) -> None:
        endpoint = "http://127.0.0.1:1234"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "lm-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/api/v0/models"): json_response(
                    [{"id": "lm-model", "name": "LM Model", "path": "/models/lm-model"}]
                ),
            }
        )
        descriptor = LMStudioAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.LM_STUDIO
        assert descriptor.identified_vendor == "LM Studio"

    def test_metadata_absent_keeps_generic_identity(self) -> None:
        endpoint = "http://127.0.0.1:1234"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "unknown-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/api/v0/models"): HttpResponse(
                    status=404,
                    text="not found",
                    content_type="text/plain",
                ),
            }
        )
        descriptor = LMStudioAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.OPENAI_COMPATIBLE
        assert descriptor.identified_vendor is None
        assert any("not by port" in warning for warning in descriptor.warnings)


class TestLlamaCppIdentification:
    def test_props_evidence_identifies_llama_cpp(self) -> None:
        endpoint = "http://127.0.0.1:8080"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "loaded-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/props"): json_response(
                    {"server_name": "llama.cpp server", "version": "b1234"}
                ),
            }
        )
        descriptor = LlamaCppAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.LLAMA_CPP
        assert descriptor.identified_vendor == "llama.cpp"

    def test_props_absent_keeps_generic_identity(self) -> None:
        endpoint = "http://127.0.0.1:8080"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "loaded-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/props"): HttpResponse(
                    status=404,
                    text="not found",
                    content_type="text/plain",
                ),
            }
        )
        descriptor = LlamaCppAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.OPENAI_COMPATIBLE
        assert descriptor.identified_vendor is None


class TestMlxServerIdentification:
    def test_version_evidence_identifies_mlx_and_warns(self) -> None:
        endpoint = "http://127.0.0.1:8080"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "mlx-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/version"): json_response({"version": "mlx-lm dev server"}),
            }
        )
        descriptor = MlxServerAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.MLX_LM
        assert descriptor.identified_vendor == "MLX-LM"
        assert any(
            "not treated as a hardened production server" in warning
            for warning in descriptor.warnings
        )

    def test_version_absent_keeps_generic_identity(self) -> None:
        endpoint = "http://127.0.0.1:8080"
        transport = FakeTransport(
            {
                ("GET", f"{endpoint}/v1/models"): json_response(
                    {"data": [{"id": "unknown-model", "object": "model"}]}
                ),
                ("GET", f"{endpoint}/version"): json_response({"version": "1.2.3"}),
            }
        )
        descriptor = MlxServerAdapter(endpoint=endpoint, transport=transport).probe()

        assert descriptor.kind == RuntimeKind.OPENAI_COMPATIBLE
        assert descriptor.identified_vendor is None
