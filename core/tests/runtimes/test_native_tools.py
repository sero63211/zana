"""Focused native tool request/continuation tests using injected transports.

These tests never open a socket, runtime, model, thread, or background
worker. They verify byte-compatible behavior-only requests, exact provider
function schemas, canonical tool-result continuation roles, and fail-closed
definition/order/bounds handling.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator, Mapping
from datetime import UTC, datetime
from typing import Any

from zana_core.instances import GenerationSettings, SessionBinding, ToolRequest, ToolResult
from zana_core.runtimes.inference import InferenceLimits
from zana_core.runtimes.ollama import OllamaInferenceAdapter
from zana_core.runtimes.openai_compat import OpenAICompatInferenceAdapter
from zana_core.tools.models import ToolDefinition

OLLAMA_END = "http://127.0.0.1:11434"
OPENAI_END = "http://127.0.0.1:1234"
NATIVE_MODEL_ID = "qwen-example:tag"
OLLAMA_MODEL_KEY = "ollama-local:qwen-example:tag"
OPENAI_MODEL_KEY = "openai-compatible:qwen-example:tag"
MODEL_DIGEST = "sha256:abc123"

CALCULATOR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {"expression": {"type": "string"}},
    "required": ["expression"],
}
CALCULATOR_DEFINITION = ToolDefinition(
    id="zana.calculator",
    version="1.0.0",
    description="Evaluate a bounded arithmetic expression.",
    input_schema=CALCULATOR_SCHEMA,
)
NATIVE_CALCULATOR_SCHEMA = {
    "type": "function",
    "function": {
        "name": "zana.calculator",
        "description": "Evaluate a bounded arithmetic expression.",
        "parameters": CALCULATOR_SCHEMA,
    },
}


def make_binding(
    *,
    model_key: str = OLLAMA_MODEL_KEY,
    runtime_model_id: str = NATIVE_MODEL_ID,
    runtime_id: str = "ollama-local",
    runtime_endpoint: str = OLLAMA_END,
) -> SessionBinding:
    return SessionBinding(
        session_id="sess-1",
        instance_id="inst-1",
        image_digest="sha256:image",
        base_model_digest="sha256:base",
        runtime_id=runtime_id,
        runtime_endpoint=runtime_endpoint,
        model_key=model_key,
        runtime_model_id=runtime_model_id,
        model_digest=MODEL_DIGEST,
        bound_at=datetime.now(UTC),
    )


def make_settings() -> GenerationSettings:
    return GenerationSettings(temperature=0.2, max_tokens=64, top_p=1.0)


def ollama_done() -> str:
    return json.dumps({"model": NATIVE_MODEL_ID, "message": {"content": "ok"}, "done": True}) + "\n"


class FakeStreamTransport:
    """Injected streaming transport; never touches the network."""

    def __init__(self, body: str = "{}") -> None:
        self._body = body.encode("utf-8")
        self.calls: list[tuple[str, str, Mapping[str, str] | None, bytes | None]] = []
        self.closed = False

    def open_stream(
        self,
        method: str,
        url: str,
        *,
        headers: Mapping[str, str] | None = None,
        body: bytes | None = None,
        timeout: float,
    ) -> Iterable[bytes]:
        self.calls.append((method, url, headers, body))
        return self._chunks()

    def _chunks(self) -> Iterator[bytes]:
        for index in range(0, len(self._body), 8):
            yield self._body[index : index + 8]

    def close(self) -> None:
        self.closed = True


class CancelToken:
    def __init__(self, cancel: bool) -> None:
        self._cancel = cancel

    def is_cancelled(self) -> bool:
        return self._cancel


class TestByteCompatibleDefaults:
    def test_ollama_default_request_is_byte_compatible(self) -> None:
        transport = FakeStreamTransport(body=ollama_done())
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(),
        )
        assert result.status == "completed"
        assert transport.calls[0][3] == (
            b'{"model":"qwen-example:tag","messages":[{"role":"system","content":"sys"},'
            b'{"role":"user","content":"hi"}],"stream":true,'
            b'"options":{"temperature":0.2,"num_predict":64,"top_p":1.0,"stop":[]}}'
        )

    def test_openai_default_request_is_byte_compatible(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
            "data: [DONE]\n\n"
        )
        transport = FakeStreamTransport(body=body)
        adapter = OpenAICompatInferenceAdapter(endpoint=OPENAI_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="hi",
            settings=make_settings(),
            binding=make_binding(
                model_key=OPENAI_MODEL_KEY,
                runtime_id="openai-compatible",
                runtime_endpoint=OPENAI_END,
            ),
        )
        assert result.status == "completed"
        assert transport.calls[0][3] == (
            b'{"model":"qwen-example:tag","messages":[{"role":"system","content":"sys"},'
            b'{"role":"user","content":"hi"}],"stream":true,"temperature":0.2,'
            b'"max_tokens":64,"top_p":1.0,"stop":[]}'
        )


class TestNativeToolSchemas:
    def test_ollama_sends_exact_schema_only_when_supplied(self) -> None:
        transport = FakeStreamTransport(body=ollama_done())
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
        )
        assert result.status == "completed"
        body = json.loads(transport.calls[0][3] or b"{}")
        assert body["tools"] == [NATIVE_CALCULATOR_SCHEMA]
        assert "version" not in body["tools"][0]
        assert body["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "calc"},
        ]

    def test_openai_sends_exact_schema_only_when_supplied(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
            "data: [DONE]\n\n"
        )
        transport = FakeStreamTransport(body=body)
        adapter = OpenAICompatInferenceAdapter(endpoint=OPENAI_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(
                model_key=OPENAI_MODEL_KEY,
                runtime_id="openai-compatible",
                runtime_endpoint=OPENAI_END,
            ),
            tool_definitions=[CALCULATOR_DEFINITION],
        )
        assert result.status == "completed"
        request = json.loads(transport.calls[0][3] or b"{}")
        assert request["tools"] == [NATIVE_CALCULATOR_SCHEMA]
        assert "version" not in request["tools"][0]

    def test_duplicate_tool_definitions_fail_closed_before_open(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION, CALCULATOR_DEFINITION],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_non_tooldefinition_input_fails_closed(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[{"id": "zana.calculator"}],  # type: ignore[list-item]
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_non_object_tool_schema_fails_closed(self) -> None:
        definition = CALCULATOR_DEFINITION.model_copy(
            update={"input_schema": {"type": "array", "items": {"type": "string"}}}
        )
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[definition],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_callable_in_tool_schema_fails_closed(self) -> None:
        definition = ToolDefinition(
            id="zana.calculator",
            version="1.0.0",
            description="bad schema",
            input_schema={"type": "object", "code": lambda: None},  # type: ignore[dict-item]
        )
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[definition],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_oversize_tool_schema_fails_closed(self) -> None:
        schema = {
            "type": "object",
            "properties": {"expression": {"type": "string", "description": "x" * 400}},
        }
        definition = ToolDefinition(
            id="zana.calculator",
            version="1.0.0",
            description="large schema",
            input_schema=schema,
        )
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            limits=InferenceLimits(max_tool_definition_bytes=64),
        )
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[definition],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_too_many_tool_definitions_fail_closed(self) -> None:
        second = CALCULATOR_DEFINITION.model_copy(update={"id": "zana.second"})
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            limits=InferenceLimits(max_tool_definitions=1),
        )
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION, second],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []

    def test_multibyte_schema_character_boundary_fails_closed(self) -> None:
        schema = {
            "type": "object",
            "description": "\u00e9" * 10,
        }
        definition = ToolDefinition(
            id="zana.calculator",
            version="1.0.0",
            description="multibyte schema",
            input_schema=schema,
        )
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            limits=InferenceLimits(max_tool_definition_chars=8, max_tool_definition_bytes=200),
        )
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[definition],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_DEFINITIONS_INVALID"
        assert transport.calls == []


class TestToolResultContinuation:
    def _result(self) -> ToolResult:
        return ToolResult(
            tool_id="zana.calculator",
            ok=True,
            output="2",
            input_digest="in-1",
            output_digest="out-1",
        )

    def _request(self) -> ToolRequest:
        return ToolRequest(
            tool_id="zana.calculator",
            version=1,
            arguments={"expression": "1+1"},
        )

    def test_ollama_continuation_uses_canonical_roles(self) -> None:
        transport = FakeStreamTransport(body=ollama_done())
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[self._result()],
        )
        assert result.status == "completed"
        request = json.loads(transport.calls[0][3] or b"{}")
        assert request["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "continue"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "function": {
                            "name": "zana.calculator",
                            "arguments": {"expression": "1+1"},
                        }
                    }
                ],
            },
            {
                "role": "tool",
                "name": "zana.calculator",
                "content": (
                    '{"tool_id":"zana.calculator","ok":true,"output":"2",'
                    '"error":null,"input_digest":"in-1","output_digest":"out-1"}'
                ),
            },
        ]
        assert transport.calls[0][3] == (
            b'{"model":"qwen-example:tag","messages":[{"role":"system","content":"sys"},'
            b'{"role":"user","content":"continue"},'
            b'{"role":"assistant","content":"","tool_calls":'
            b'[{"function":{"name":"zana.calculator",'
            b'"arguments":{"expression":"1+1"}}}]},'
            b'{"role":"tool","name":"zana.calculator","content":'
            b'"{\\"tool_id\\":\\"zana.calculator\\",\\"ok\\":true,\\"output\\":\\"2\\",'
            b'\\"error\\":null,\\"input_digest\\":\\"in-1\\",'
            b'\\"output_digest\\":\\"out-1\\"}"}],"stream":true,'
            b'"options":{"temperature":0.2,"num_predict":64,"top_p":1.0,"stop":[]},'
            b'"tools":[{"type":"function","function":{"name":"zana.calculator",'
            b'"description":"Evaluate a bounded arithmetic expression.",'
            b'"parameters":{"type":"object","properties":{"expression":{"type":"string"}},'
            b'"required":["expression"]}}}]}'
        )

    def test_openai_continuation_uses_canonical_roles(self) -> None:
        body = (
            'data: {"model":"qwen-example:tag",'
            '"choices":[{"delta":{},"finish_reason":"stop","index":0}]}\n\n'
            "data: [DONE]\n\n"
        )
        transport = FakeStreamTransport(body=body)
        adapter = OpenAICompatInferenceAdapter(endpoint=OPENAI_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(
                model_key=OPENAI_MODEL_KEY,
                runtime_id="openai-compatible",
                runtime_endpoint=OPENAI_END,
            ),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[self._result()],
        )
        assert result.status == "completed"
        request = json.loads(transport.calls[0][3] or b"{}")
        assert request["messages"] == [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "continue"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "zana-0",
                        "type": "function",
                        "function": {
                            "name": "zana.calculator",
                            "arguments": '{"expression":"1+1"}',
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": "zana-0",
                "content": (
                    '{"tool_id":"zana.calculator","ok":true,"output":"2",'
                    '"error":null,"input_digest":"in-1","output_digest":"out-1"}'
                ),
            },
        ]

    def test_tool_results_without_requests_fail_closed(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_results=[self._result()],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_CONTINUATION_INVALID"
        assert transport.calls == []

    def test_result_count_mismatch_fails_closed(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[self._result(), self._result()],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_CONTINUATION_INVALID"
        assert transport.calls == []

    def test_result_tool_id_mismatch_fails_closed(self) -> None:
        mismatch = self._result().model_copy(update={"tool_id": "zana.other"})
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[mismatch],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_CONTINUATION_INVALID"
        assert transport.calls == []

    def test_continuation_undeclared_tool_fails_closed(self) -> None:
        request = self._request().model_copy(update={"tool_id": "zana.other"})
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[request],
            tool_results=[self._result()],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_CONTINUATION_INVALID"
        assert transport.calls == []

    def test_continuation_without_definitions_fails_closed(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_requests=[self._request()],
            tool_results=[self._result()],
        )
        assert result.status == "failed"
        assert result.error_code == "TOOL_CONTINUATION_INVALID"
        assert transport.calls == []

    def test_oversize_tool_result_fails_closed(self) -> None:
        result = self._result().model_copy(update={"output": "x" * 2000})
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            limits=InferenceLimits(max_tool_result_bytes=64),
        )
        response = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[result],
        )
        assert response.status == "failed"
        assert response.error_code == "TOOL_RESULT_LIMIT"
        assert transport.calls == []

    def test_too_many_tool_results_fail_closed(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(
            endpoint=OLLAMA_END,
            transport=transport,
            limits=InferenceLimits(max_tool_results=1),
        )
        response = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[self._result(), self._result()],
        )
        assert response.status == "failed"
        assert response.error_code == "TOOL_RESULT_LIMIT"
        assert transport.calls == []

    def test_cancellation_with_tools_closes_before_open(self) -> None:
        transport = FakeStreamTransport()
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="calc",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            cancellation=CancelToken(cancel=True),
        )
        assert result.status == "cancelled"
        assert transport.calls == []

    def test_continuation_response_still_parsed(self) -> None:
        transport = FakeStreamTransport(body=ollama_done())
        adapter = OllamaInferenceAdapter(endpoint=OLLAMA_END, transport=transport)
        result = adapter.generate(
            context="sys",
            message="continue",
            settings=make_settings(),
            binding=make_binding(),
            tool_definitions=[CALCULATOR_DEFINITION],
            tool_requests=[self._request()],
            tool_results=[self._result()],
        )
        assert result.status == "completed"
        assert result.content == "ok"
        assert transport.closed is True


def test_native_tool_schema_has_no_version_or_code_field() -> None:
    from zana_core.runtimes.inference import native_tool_schema

    schema = native_tool_schema(CALCULATOR_DEFINITION)
    assert schema == NATIVE_CALCULATOR_SCHEMA
    assert "version" not in schema["function"]
