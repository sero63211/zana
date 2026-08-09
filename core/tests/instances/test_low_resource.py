"""Low-resource bounded limits and honest failure tests."""

from __future__ import annotations

from datetime import UTC, datetime

from tests.instances.helpers import (
    FakeInferenceAdapter,
    FakeRetrievalAdapter,
    FakeToolExecutor,
    running_instance,
)
from zana_core.instances.chat import ChatOrchestrator, InferenceResult
from zana_core.instances.models import (
    ChatInput,
    ChatStatus,
    LowResourceLimits,
    MemorySuggestion,
    RetrievedChunk,
    ToolRequest,
)
from zana_core.memory.models import ConversationTurn
from zana_core.permissions.decisions import PermissionDecisionEngine
from zana_core.permissions.models import PermissionPolicy


def _orchestrator(instance, inference, retrieval=None, tool_executor=None, memory=None):
    return ChatOrchestrator(
        inference,
        PermissionDecisionEngine(PermissionPolicy()),
        retrieval=retrieval,
        tool_executor=tool_executor,
        memory_approval=memory,
    )


class TestBoundedInputs:
    def test_message_over_limit_returns_typed_failure(self) -> None:
        instance, _, _ = running_instance(
            context_token_budget=4096,
            low_resource_limits=LowResourceLimits(max_message_chars=10),
        )
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="x" * 11),
        )
        assert output.status is ChatStatus.FAILED
        assert output.error is not None
        assert output.error.code == "MESSAGE_TOO_LARGE"
        assert inference.calls == 0

    def test_instructions_over_limit_returns_typed_failure(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_user_instructions_chars=5)
        )
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(
                instance_id=instance.instance_id,
                message="hi",
                user_instructions="long instructions",
            ),
        )
        assert output.error is not None
        assert output.error.code == "INSTRUCTIONS_TOO_LARGE"
        assert inference.calls == 0

    def test_timeout_over_limit_returns_typed_failure(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_generation_timeout_seconds=10.0)
        )
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(
                instance_id=instance.instance_id,
                message="hi",
                timeout_seconds=30.0,
            ),
        )
        assert output.error is not None
        assert output.error.code == "TIMEOUT_TOO_LARGE"
        assert inference.calls == 0


class TestBoundedRetrievalAndContext:
    def test_retrieval_top_k_and_chars_are_bounded(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(
                max_retrieved_chunks=2,
                max_retrieved_text_chars=30,
            )
        )
        retrieval = FakeRetrievalAdapter(
            chunks=[
                RetrievedChunk(
                    chunk_id=f"c{i}",
                    document_digest="sha256:" + "1" * 64,
                    source_id=f"s{i}",
                    source_locator="doc.md",
                    score=0.9,
                    text="x" * 20,
                )
                for i in range(4)
            ]
        )
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference, retrieval=retrieval).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
            top_k=99,
        )
        assert output.status is ChatStatus.COMPLETED
        assert output.provenance is not None
        assert len(output.provenance.retrieved_chunks) <= 2

    def test_retrieval_unavailable_is_honest_failure(self) -> None:
        instance, _, _ = running_instance()
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert output.status is ChatStatus.FAILED
        assert output.error is not None
        assert output.error.code == "RETRIEVAL_UNAVAILABLE"
        assert inference.calls == 0

    def test_conversation_turns_are_bounded(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_conversation_turns=3)
        )
        fixed = datetime.now(UTC)
        for index in range(5):
            instance.state.conversation.append(
                ConversationTurn(
                    id=f"t{index}",
                    role="user",
                    content="x",
                    created_at=fixed,
                )
            )
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert output.status in {ChatStatus.COMPLETED, ChatStatus.PARTIAL, ChatStatus.FAILED}
        assert len(instance.state.conversation) >= 5


class TestBoundedToolsAndMemory:
    def test_tool_request_count_is_bounded(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_tool_requests=2)
        )
        inference = FakeInferenceAdapter(
            result=InferenceResult(
                status="completed",
                content="ok",
                raw_text="ok",
                tool_requests=tuple(ToolRequest(tool_id="zana.calculator") for _ in range(5)),
            )
        )
        executor = FakeToolExecutor()
        _orchestrator(instance, inference, tool_executor=executor).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert len(executor.calls) <= 2

    def test_tool_argument_limit_denies_without_execution(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_tool_arguments_chars=5)
        )
        inference = FakeInferenceAdapter(
            result=InferenceResult(
                status="completed",
                content="ok",
                raw_text="ok",
                tool_requests=(ToolRequest(tool_id="zana.calculator", arguments={"x": "long"}),),
            )
        )
        executor = FakeToolExecutor()
        output = _orchestrator(instance, inference, tool_executor=executor).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert executor.calls == []
        if output.provenance is not None and output.provenance.tool_decisions:
            assert output.provenance.tool_decisions[0].allowed is False

    def test_memory_suggestion_count_is_bounded(self) -> None:
        instance, _, _ = running_instance(
            low_resource_limits=LowResourceLimits(max_memory_suggestions=1)
        )
        inference = FakeInferenceAdapter(
            result=InferenceResult(
                status="completed",
                content="ok",
                raw_text="ok",
                memory_suggestions=tuple(
                    MemorySuggestion(
                        category="preference",
                        memory_type="preference",
                        content=f"fact {i}",
                    )
                    for i in range(4)
                ),
            )
        )
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert len(output.memory_proposals) <= 1


class TestCleanFailureBehavior:
    def test_partial_inference_never_verified(self) -> None:
        instance, _, _ = running_instance()
        inference = FakeInferenceAdapter(
            result=InferenceResult(
                status="partial",
                content="half answer",
                raw_text="half answer",
            )
        )
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert output.status in {ChatStatus.PARTIAL, ChatStatus.FAILED}
        assert output.content is None
        assert output.error is not None

    def test_inference_failure_returns_typed_recovery(self) -> None:
        instance, _, _ = running_instance()
        inference = FakeInferenceAdapter(
            result=InferenceResult(
                status="failed",
                raw_text="",
                error_code="MODEL_BUSY",
                error_message="runtime busy",
            )
        )
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert output.status is ChatStatus.FAILED
        assert output.error.code in {"MODEL_BUSY", "RETRIEVAL_UNAVAILABLE"}

    def test_context_budget_overflow_is_honest(self) -> None:
        instance, _, _ = running_instance(context_token_budget=1)
        inference = FakeInferenceAdapter()
        output = _orchestrator(instance, inference).run(
            instance,
            ChatInput(instance_id=instance.instance_id, message="hi"),
        )
        assert output.status is ChatStatus.FAILED
        assert output.error is not None
        assert output.error.code in {"CONTEXT_BUDGET_FAILED", "RETRIEVAL_UNAVAILABLE"}
        assert inference.calls == 0
