"""Strict instance, session, and chat contract model tests."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from zana_core.domain.enums import InstanceStatus
from zana_core.instances.models import (
    ChatInput,
    ChatStatus,
    GenerationSettings,
    InstanceConfig,
    InstanceRecord,
    SessionBinding,
    SessionStatus,
    StartPlan,
    ToolDecisionRecord,
    ToolRequest,
)
from zana_core.memory.models import (
    ImagePointer,
    InstancePointer,
    MutableInstanceState,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def make_config() -> InstanceConfig:
    return InstanceConfig(
        instance_name="tutor",
        image_digest="sha256:" + "f" * 64,
        image_name="math-tutor",
        image_version="1.0.0",
        base_model_digest="sha256:" + "a" * 64,
        required_artifact_digests=("sha256:" + "b" * 64,),
    )


class TestImmutableConfig:
    def test_instance_config_is_frozen_and_strict(self) -> None:
        config = make_config()
        with pytest.raises(ValidationError):
            config.image_digest = "sha256:" + "0" * 64
        with pytest.raises(ValidationError):
            InstanceConfig.model_validate({"instance_name": "x"})

    def test_generation_settings_validates_ranges(self) -> None:
        with pytest.raises(ValidationError):
            GenerationSettings(temperature=3.0)
        with pytest.raises(ValidationError):
            GenerationSettings(max_tokens=0)


class TestStartPlanAndBinding:
    def test_start_plan_is_strict(self) -> None:
        plan = StartPlan(
            instance_id="i1",
            image_digest="sha256:" + "f" * 64,
            base_model_digest="sha256:" + "a" * 64,
            runtime_id="ollama-local",
            runtime_endpoint="http://127.0.0.1:11434",
            model_key="ollama-local:model",
            runtime_model_id="model",
            model_digest="sha256:" + "a" * 64,
            expected_state_revision=0,
        )
        with pytest.raises(ValidationError):
            plan.model_digest = "sha256:" + "0" * 64

    def test_session_binding_requires_exact_identities(self) -> None:
        binding = SessionBinding(
            session_id="s1",
            instance_id="i1",
            image_digest="sha256:" + "f" * 64,
            base_model_digest="sha256:" + "a" * 64,
            runtime_id="ollama-local",
            runtime_endpoint="http://127.0.0.1:11434",
            model_key="ollama-local:model",
            runtime_model_id="model",
            model_digest="sha256:" + "a" * 64,
        )
        assert binding.session_id == "s1"
        with pytest.raises(ValidationError):
            SessionBinding.model_validate({"session_id": "s1"})


class TestRecordModels:
    def test_instance_record_separates_config_from_mutable_state(self) -> None:
        image = ImagePointer(digest="sha256:" + "f" * 64, schema_version=1)
        pointer = InstancePointer(
            instance_id="i1",
            image=image,
            snapshot_revision=0,
            state_schema_version=1,
            updated_at=FIXED,
        )
        state = MutableInstanceState(
            instance_id="i1",
            state_revision=0,
            updated_at=FIXED,
        )
        record = InstanceRecord(
            instance_id="i1",
            config=make_config(),
            pointer=pointer,
            state=state,
            status=InstanceStatus.STOPPED,
            updated_at=FIXED,
        )
        assert record.config.image_digest.startswith("sha256:")
        assert record.status is InstanceStatus.STOPPED
        record.updated_at = FIXED
        assert record.pointer.image.digest == image.digest

    def test_chat_status_and_input_contract(self) -> None:
        assert ChatStatus.COMPLETED.value == "completed"
        chat = ChatInput(instance_id="i1", message="hello")
        assert chat.message == "hello"
        with pytest.raises(ValidationError):
            ChatInput(instance_id="i1", message="")

    def test_tool_decision_and_request_are_typed(self) -> None:
        request = ToolRequest(tool_id="zana.calculator", arguments={"expr": "2+2"})
        assert request.arguments["expr"] == "2+2"
        decision = ToolDecisionRecord(tool_id="zana.calculator", allowed=True)
        assert decision.allowed is True


class TestSessionStatus:
    def test_session_status_values(self) -> None:
        assert SessionStatus.RUNNING.value == "running"
        assert SessionStatus.STOPPED.value == "stopped"
