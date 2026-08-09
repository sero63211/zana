"""Lifecycle transitions, idempotency, revision, and binding tests."""

from __future__ import annotations

import pytest

from tests.instances.helpers import (
    FakeRuntimeSessionAdapter,
    running_instance,
)
from zana_core.domain.enums import InstanceStatus
from zana_core.instances.lifecycle import (
    LifecycleService,
    RevisionConflictError,
    RuntimeBindingMismatchError,
    SessionConflictError,
)


class TestStart:
    def test_start_transitions_and_increments_revision(self) -> None:
        instance, adapter, _ = running_instance()
        assert instance.status is InstanceStatus.RUNNING
        assert instance.binding is not None
        assert instance.state.state_revision == 1
        assert adapter.starts == 1

    def test_idempotent_start_does_not_call_adapter_again(self) -> None:
        instance, adapter, plan = running_instance()
        result = LifecycleService(adapter).start(
            instance,
            expected_revision=instance.state.state_revision,
            plan=plan,
        )
        assert result.changed is False
        assert adapter.starts == 1
        assert instance.status is InstanceStatus.RUNNING

    def test_revision_conflict_blocks_start(self) -> None:
        instance, adapter, plan = running_instance()
        with pytest.raises(RevisionConflictError):
            LifecycleService(adapter).start(
                instance,
                expected_revision=99,
                plan=plan,
            )

    def test_adapter_start_failure_enters_error_state(self) -> None:
        adapter = FakeRuntimeSessionAdapter()
        adapter.fail_start = True
        instance, _, plan = running_instance(adapter=adapter)
        result = LifecycleService(adapter).start(
            instance,
            expected_revision=instance.state.state_revision,
            plan=plan,
        )
        assert result.status is InstanceStatus.ERROR
        assert result.error is not None
        assert instance.status is InstanceStatus.ERROR

    def test_binding_mismatch_blocks_start(self) -> None:
        adapter = FakeRuntimeSessionAdapter()
        adapter.mismatch = True
        with pytest.raises(RuntimeBindingMismatchError):
            running_instance(adapter=adapter)

    def test_runtime_model_mismatch_blocks_start(self) -> None:
        adapter = FakeRuntimeSessionAdapter()
        adapter.runtime_model_mismatch = True
        with pytest.raises(RuntimeBindingMismatchError):
            running_instance(adapter=adapter)

    def test_already_starting_conflict(self) -> None:
        instance, _, plan = running_instance()
        instance.status = InstanceStatus.STARTING
        with pytest.raises(SessionConflictError):
            LifecycleService(FakeRuntimeSessionAdapter()).start(
                instance,
                expected_revision=instance.state.state_revision,
                plan=plan,
            )


class TestStop:
    def test_stop_clears_binding_and_increments_revision(self) -> None:
        instance, adapter, _ = running_instance()
        before = instance.state.state_revision
        result = LifecycleService(adapter).stop(
            instance,
            expected_revision=before,
        )
        assert result.changed is True
        assert instance.status is InstanceStatus.STOPPED
        assert instance.binding is None
        assert instance.state.state_revision == before + 1
        assert adapter.stops == 1

    def test_idempotent_stop_is_noop(self) -> None:
        instance, adapter, _ = running_instance()
        service = LifecycleService(adapter)
        service.stop(instance, expected_revision=instance.state.state_revision)
        result = service.stop(
            instance,
            expected_revision=instance.state.state_revision,
        )
        assert result.changed is False
        assert adapter.stops == 1

    def test_stop_adapter_failure_enters_error_state(self) -> None:
        adapter = FakeRuntimeSessionAdapter()
        adapter.fail_stop = True
        instance, adapter, _ = running_instance(adapter=adapter)
        result = LifecycleService(adapter).stop(
            instance,
            expected_revision=instance.state.state_revision,
        )
        assert result.status is InstanceStatus.ERROR
        assert instance.status is InstanceStatus.ERROR
        assert instance.last_error is not None
