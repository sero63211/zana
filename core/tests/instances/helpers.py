"""Shared tiny injected adapters and builders for instance tests."""

from __future__ import annotations

from datetime import UTC, datetime

from zana_core.domain.enums import RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.images.models import (
    Adapter,
    BaseModelReference,
    Behavior,
    KnowledgeSnapshot,
    ZanaImageConfig,
)
from zana_core.images.models import (
    Permissions as ImagePermissions,
)
from zana_core.instances.chat import (
    InferenceAdapter,
    InferenceResult,
    RetrievalAdapter,
    ToolExecutor,
)
from zana_core.instances.creation import InstanceCreationService
from zana_core.instances.lifecycle import LifecycleService, RuntimeSessionAdapter
from zana_core.instances.models import (
    GenerationSettings,
    InstanceRecord,
    RetrievedChunk,
    SessionBinding,
    SessionStatus,
    StartPlan,
    ToolRequest,
    ToolResult,
)
from zana_core.instances.runtime_selection import RuntimeSelectionService
from zana_core.runtimes.base import (
    ModelDescriptor,
    RuntimeDescriptor,
)

IDENTITY_DIGEST = "sha256:" + "a" * 64
BEHAVIOR_DIGEST = "sha256:" + "b" * 64
KNOWLEDGE_DIGEST = "sha256:" + "c" * 64
ADAPTER_DIGEST = "sha256:" + "d" * 64
PERMISSIONS_DIGEST = "sha256:" + "e" * 64
IMAGE_DIGEST = "sha256:" + "f" * 64


def make_image_config(
    *,
    identity_digest: str | None = IDENTITY_DIGEST,
    with_knowledge: bool = True,
    tool_ids: tuple[str, ...] = (),
    tools_allow: tuple[str, ...] = (),
    secrets_allow: tuple[str, ...] = (),
) -> ZanaImageConfig:
    return ZanaImageConfig(
        name="math-tutor",
        version="1.0.0",
        base_model=BaseModelReference(
            display_name="example-model",
            identity_digest=identity_digest,
            runtime_compatibility=["ollama"],
            required_capabilities=["completion"],
        ),
        behavior=Behavior(
            system_policy_digest=BEHAVIOR_DIGEST,
            behavior_digest=BEHAVIOR_DIGEST,
        ),
        knowledge=(
            KnowledgeSnapshot(
                snapshot_digest=KNOWLEDGE_DIGEST,
                embedding_model_digest=IDENTITY_DIGEST,
            )
            if with_knowledge
            else None
        ),
        adapter=Adapter(
            type="lora",
            digest=ADAPTER_DIGEST,
            base_model_digest=identity_digest,
        ),
        tools=[{"id": tool_id} for tool_id in tool_ids],
        permissions=ImagePermissions(
            digest=PERMISSIONS_DIGEST,
            network_outbound=False,
            filesystem_read=[],
            filesystem_write=[],
            tools_allow=list(tools_allow),
            secrets_allow=list(secrets_allow),
        ),
    )


def make_model(
    identity_digest: str = IDENTITY_DIGEST, *, runtime_id: str = "ollama-local"
) -> ModelDescriptor:
    return ModelDescriptor(
        runtime_id=runtime_id,
        model_id="example-model:tag",
        display_name="example-model:tag",
        digest=identity_digest,
        capabilities=["completion"],
        identity_strength="exact_digest",
        last_seen_at=datetime.now(UTC),
    )


def make_runtime(*, online: bool = True) -> RuntimeDescriptor:
    return RuntimeDescriptor(
        runtime_id="ollama-local",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE if online else RuntimeStatus.OFFLINE,
        registered=True,
        server_running=online,
        installed=True,
        installed_not_running=not online,
        models=[],
        last_seen_at=datetime.now(UTC),
    )


def declared_artifact_set() -> set[str]:
    return {
        IDENTITY_DIGEST,
        BEHAVIOR_DIGEST,
        KNOWLEDGE_DIGEST,
        ADAPTER_DIGEST,
        PERMISSIONS_DIGEST,
    }


def create_instance(
    *,
    config: ZanaImageConfig | None = None,
    artifacts: set[str] | None = None,
    secrets: set[str] | None = None,
    runtimes: list[RuntimeDescriptor] | None = None,
    models: list[ModelDescriptor] | None = None,
    context_token_budget: int = 4096,
    low_resource_limits: object | None = None,
) -> InstanceRecord:
    config = config or make_image_config()
    creation = InstanceCreationService()
    return creation.create(
        config=config,
        image_digest=IMAGE_DIGEST,
        runtimes=runtimes or [make_runtime()],
        models=models or [make_model()],
        available_artifacts=artifacts or declared_artifact_set(),
        resolved_secrets=secrets or set(),
        context_token_budget=context_token_budget,
        low_resource_limits=low_resource_limits,
    )


class FakeRuntimeSessionAdapter(RuntimeSessionAdapter):
    """Injected adapter that records calls and returns exact bindings."""

    def __init__(self) -> None:
        self.starts = 0
        self.stops = 0
        self.status_checks = 0
        self.mismatch = False
        self.runtime_model_mismatch = False
        self.endpoint_mismatch = False
        self.fail_start = False
        self.fail_stop = False

    def start(self, plan: StartPlan) -> SessionBinding:
        self.starts += 1
        if self.fail_start:
            raise RuntimeError("simulated start failure")
        return SessionBinding(
            session_id="session-1",
            instance_id=plan.instance_id,
            image_digest=plan.image_digest,
            base_model_digest=("sha256:" + "0" * 64 if self.mismatch else plan.base_model_digest),
            runtime_id=plan.runtime_id,
            runtime_endpoint=(
                "http://127.0.0.1:9999" if self.endpoint_mismatch else plan.runtime_endpoint
            ),
            model_key=plan.model_key,
            runtime_model_id=(
                "wrong-native-model" if self.runtime_model_mismatch else plan.runtime_model_id
            ),
            model_digest=plan.model_digest,
        )

    def stop(self, binding: SessionBinding) -> None:
        self.stops += 1
        if self.fail_stop:
            raise RuntimeError("simulated stop failure")

    def status(self, binding: SessionBinding) -> SessionStatus:
        self.status_checks += 1
        return SessionStatus.RUNNING


class FakeRetrievalAdapter(RetrievalAdapter):
    """Injected retrieval provider returning fixed chunks."""

    def __init__(self, chunks: list[RetrievedChunk] | None = None) -> None:
        self.calls = 0
        self.chunks = chunks or [
            RetrievedChunk(
                chunk_id="chunk-1",
                document_digest="sha256:" + "1" * 64,
                source_id="source-1",
                source_locator="policy-manual.md:12",
                score=0.91,
                text="Evidence text is untrusted data.",
            )
        ]

    def retrieve(
        self,
        query: str,
        snapshot_digest: str,
        *,
        top_k: int,
        score_threshold: float,
    ) -> list[RetrievedChunk]:
        self.calls += 1
        return self.chunks


class FakeInferenceAdapter(InferenceAdapter):
    """Injected inference provider that records exact identities."""

    def __init__(self, result: InferenceResult | None = None) -> None:
        self.calls = 0
        self.contexts: list[str] = []
        self.messages: list[str] = []
        self.settings: list[GenerationSettings] = []
        self.bindings: list[SessionBinding] = []
        self.result = result or InferenceResult(
            status="completed",
            content="Verified answer.",
            raw_text="Verified answer.",
        )

    def generate(
        self,
        *,
        context: str,
        settings: GenerationSettings,
        binding: SessionBinding,
        message: str,
        cancellation: object | None = None,
    ) -> InferenceResult:
        self.calls += 1
        self.contexts.append(context)
        self.settings.append(settings)
        self.bindings.append(binding)
        self.messages.append(message)
        return self.result


class FakeToolExecutor(ToolExecutor):
    """Injected built-in tool executor recording every call."""

    def __init__(self) -> None:
        self.calls: list[ToolRequest] = []

    def execute(self, request: ToolRequest) -> ToolResult:
        self.calls.append(request)
        return ToolResult(
            tool_id=request.tool_id,
            ok=True,
            output="42",
            input_digest="sha256:" + "1" * 64,
            output_digest="sha256:" + "2" * 64,
        )


def running_instance(
    *,
    adapter: FakeRuntimeSessionAdapter | None = None,
    config: ZanaImageConfig | None = None,
    artifacts: set[str] | None = None,
    context_token_budget: int = 4096,
    low_resource_limits: object | None = None,
) -> tuple[InstanceRecord, FakeRuntimeSessionAdapter, StartPlan]:
    adapter = adapter or FakeRuntimeSessionAdapter()
    config = config or make_image_config()
    instance = create_instance(
        config=config,
        artifacts=artifacts,
        context_token_budget=context_token_budget,
        low_resource_limits=low_resource_limits,
    )
    plan = RuntimeSelectionService().select(
        config=config,
        image_digest=IMAGE_DIGEST,
        instance_id=instance.instance_id,
        expected_state_revision=instance.state.state_revision,
        runtimes=[make_runtime()],
        models=[make_model()],
    )
    LifecycleService(adapter).start(
        instance,
        expected_revision=instance.state.state_revision,
        plan=plan,
    )
    return instance, adapter, plan
