"""Start/stop/session state machine with an injected runtime adapter."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from zana_core.domain.enums import InstanceStatus
from zana_core.instances.errors import InstanceError, InstanceErrorRecord
from zana_core.instances.models import (
    InstanceRecord,
    SessionBinding,
    SessionStatus,
    StartPlan,
)


class RevisionConflictError(InstanceError):
    """The caller's expected revision does not match current state."""


class SessionConflictError(InstanceError):
    """The instance is already transitioning or has an active session."""


class RuntimeBindingMismatchError(InstanceError):
    """The adapter returned a session bound to different identities."""


class SessionAdapterError(InstanceError):
    """The injected runtime session adapter failed."""


class RuntimeSessionAdapter(Protocol):
    """Injected protocol; this task never contacts a real runtime."""

    def start(self, plan: StartPlan) -> SessionBinding: ...

    def stop(self, binding: SessionBinding) -> None: ...

    def status(self, binding: SessionBinding) -> SessionStatus: ...


class StartResult(BaseModel):
    """Outcome of an idempotent, revision-checked start."""

    model_config = ConfigDict(extra="forbid")

    instance_id: str
    status: InstanceStatus
    binding: SessionBinding | None = None
    changed: bool
    error: InstanceErrorRecord | None = None


class LifecycleService:
    """Owns instance lifecycle transitions against an injected adapter."""

    def __init__(
        self,
        adapter: RuntimeSessionAdapter,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.adapter = adapter
        self._clock = clock or (lambda: datetime.now(UTC))

    def start(
        self,
        instance: InstanceRecord,
        *,
        expected_revision: int,
        plan: StartPlan,
    ) -> StartResult:
        """Start with optimistic revision check and exact binding verification."""
        if instance.state.state_revision != expected_revision:
            raise RevisionConflictError(
                "expected state revision does not match the current instance state"
            )
        if instance.status is InstanceStatus.STARTING:
            raise SessionConflictError("instance is already starting")

        if instance.status is InstanceStatus.RUNNING and instance.binding is not None:
            if self._binding_matches(instance.binding, plan):
                return StartResult(
                    instance_id=instance.instance_id,
                    status=InstanceStatus.RUNNING,
                    binding=instance.binding,
                    changed=False,
                )
            raise RuntimeBindingMismatchError(
                "instance is running against a different exact runtime/model identity"
            )

        try:
            binding = self.adapter.start(plan)
        except Exception as error:  # noqa: BLE001 - adapter boundary maps to typed state
            record = InstanceErrorRecord(
                code="SESSION_START_FAILED",
                message=f"Runtime session could not start: {error}",
                recovery_action="Retry after refreshing runtime discovery.",
                recoverable=True,
            )
            instance.status = InstanceStatus.ERROR
            instance.last_error = record
            instance.updated_at = self._clock()
            return StartResult(
                instance_id=instance.instance_id,
                status=InstanceStatus.ERROR,
                binding=None,
                changed=False,
                error=record,
            )

        if not self._binding_matches(binding, plan):
            raise RuntimeBindingMismatchError(
                "adapter session binding does not match the exact start plan"
            )

        instance.binding = binding
        instance.status = InstanceStatus.RUNNING
        instance.state.state_revision += 1
        instance.state.updated_at = self._clock()
        instance.updated_at = instance.state.updated_at
        return StartResult(
            instance_id=instance.instance_id,
            status=InstanceStatus.RUNNING,
            binding=binding,
            changed=True,
        )

    def stop(
        self,
        instance: InstanceRecord,
        *,
        expected_revision: int,
    ) -> StartResult:
        """Stop idempotently; a stopped instance is a successful no-op."""
        if instance.state.state_revision != expected_revision:
            raise RevisionConflictError(
                "expected state revision does not match the current instance state"
            )
        if instance.status is InstanceStatus.STOPPED:
            return StartResult(
                instance_id=instance.instance_id,
                status=InstanceStatus.STOPPED,
                binding=None,
                changed=False,
            )
        if instance.binding is None or instance.status is not InstanceStatus.RUNNING:
            raise SessionConflictError("instance has no active runtime session")

        binding = instance.binding
        try:
            self.adapter.stop(binding)
        except Exception as error:  # noqa: BLE001 - adapter boundary maps to typed state
            record = InstanceErrorRecord(
                code="SESSION_STOP_FAILED",
                message=f"Runtime session could not stop: {error}",
                recovery_action="Retry stopping the instance; the session may need cleanup.",
                recoverable=True,
            )
            instance.status = InstanceStatus.ERROR
            instance.last_error = record
            instance.updated_at = self._clock()
            return StartResult(
                instance_id=instance.instance_id,
                status=InstanceStatus.ERROR,
                binding=binding,
                changed=False,
                error=record,
            )

        instance.binding = None
        instance.status = InstanceStatus.STOPPED
        instance.state.state_revision += 1
        instance.state.updated_at = self._clock()
        instance.updated_at = instance.state.updated_at
        return StartResult(
            instance_id=instance.instance_id,
            status=InstanceStatus.STOPPED,
            binding=None,
            changed=True,
        )

    @staticmethod
    def _binding_matches(binding: SessionBinding, plan: StartPlan) -> bool:
        return (
            binding.instance_id == plan.instance_id
            and binding.image_digest == plan.image_digest
            and binding.base_model_digest == plan.base_model_digest
            and binding.runtime_id == plan.runtime_id
            and binding.model_key == plan.model_key
            and binding.model_digest == plan.model_digest
        )
