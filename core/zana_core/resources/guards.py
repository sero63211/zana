"""Pure integration guard protocols for ZANA heavy services."""

from __future__ import annotations

from typing import Protocol

from zana_core.resources.models import (
    AdmissionDecision,
    OperationCategory,
    OperationRequest,
    UsageRecord,
)


class ResourceGuard(Protocol):
    """Guard contract implemented by service-specific resource guards."""

    @property
    def category(self) -> OperationCategory: ...

    def acquire(self, request: OperationRequest) -> AdmissionDecision: ...

    def release(self, token: str) -> UsageRecord: ...


class BuildResourceGuard(ResourceGuard, Protocol):
    """Guard for build lifecycle heavy phases."""

    @property
    def category(self) -> OperationCategory: ...

    def acquire(self, request: OperationRequest) -> AdmissionDecision: ...

    def release(self, token: str) -> UsageRecord: ...


class EmbeddingIndexResourceGuard(ResourceGuard, Protocol):
    """Guard for embedding/index heavy phases."""

    @property
    def category(self) -> OperationCategory: ...

    def acquire(self, request: OperationRequest) -> AdmissionDecision: ...

    def release(self, token: str) -> UsageRecord: ...


class InferenceTrainingResourceGuard(ResourceGuard, Protocol):
    """Guard for inference and training heavy phases."""

    @property
    def category(self) -> OperationCategory: ...

    def acquire(self, request: OperationRequest) -> AdmissionDecision: ...

    def release(self, token: str) -> UsageRecord: ...


class PortabilityResourceGuard(ResourceGuard, Protocol):
    """Guard for export/import/portability heavy phases."""

    @property
    def category(self) -> OperationCategory: ...

    def acquire(self, request: OperationRequest) -> AdmissionDecision: ...

    def release(self, token: str) -> UsageRecord: ...
