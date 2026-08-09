"""Immutable typed models for resource admission and lease accounting."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

MAX_SAFE_BYTES = 1 << 62


class PlatformLabel(str, Enum):
    """Platform families ZANA runs on; unknown is valid and never guessed."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class OperationCategory(str, Enum):
    """Operation categories with distinct resource limits."""

    TINY = "tiny"
    METADATA = "metadata"
    READ_ONLY = "read_only"
    BUILD = "build"
    EMBEDDING_INDEX = "embedding_index"
    INFERENCE = "inference"
    TRAINING = "training"
    EXPORT = "export"
    PORTABILITY = "portability"


HEAVY_CATEGORIES: frozenset[OperationCategory] = frozenset(
    {
        OperationCategory.BUILD,
        OperationCategory.EMBEDDING_INDEX,
        OperationCategory.INFERENCE,
        OperationCategory.TRAINING,
        OperationCategory.EXPORT,
        OperationCategory.PORTABILITY,
    }
)


class AdmissionOutcome(str, Enum):
    """Result of an admission decision."""

    ALLOW = "allow"
    ASK = "ask"
    BLOCK = "block"


class DenialReason(str, Enum):
    """Stable machine-readable denial reasons for UI/API later."""

    NONE = "none"
    INVALID_REQUEST = "invalid_request"
    OVERFLOW = "overflow"
    MEMORY_INSUFFICIENT = "memory_insufficient"
    DISK_INSUFFICIENT = "disk_insufficient"
    CONCURRENCY_LIMIT = "concurrency_limit"
    WORKER_LIMIT = "worker_limit"
    ITEM_LIMIT = "item_limit"
    BYTE_LIMIT = "byte_limit"
    FILE_LIMIT = "file_limit"
    RECURSION_LIMIT = "recursion_limit"
    UNKNOWN_SIZE = "unknown_size"
    UNKNOWN_HEADROOM = "unknown_headroom"
    CATEGORY_LIMIT = "category_limit"
    STALE_SNAPSHOT = "stale_snapshot"


class RecoveryAction(str, Enum):
    """Stable recovery guidance returned with every decision."""

    NONE = "none"
    PROVIDE_ESTIMATE = "provide_estimate"
    WAIT_FOR_HEADROOM = "wait_for_headroom"
    RETRY_AFTER_RELEASE = "retry_after_release"
    REDUCE_WORKERS = "reduce_workers"
    REDUCE_BATCH = "reduce_batch"
    FREE_DISK = "free_disk"
    INCREASE_POLICY_LIMIT = "increase_policy_limit"
    CHECK_SNAPSHOT = "check_snapshot"
    APPROVE = "approve"


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _non_negative(v: int) -> int:
    if v < 0:
        raise ValueError("byte and count values must be non-negative")
    if v > MAX_SAFE_BYTES:
        raise ValueError(f"value exceeds the safe bound {MAX_SAFE_BYTES}")
    return v


class ResourceSnapshot(_Frozen):
    """Cheap cross-platform snapshot; unknown fields stay None, never fake zero."""

    revision: int = Field(ge=0)
    platform: PlatformLabel
    os_name: str = Field(default="", max_length=100)
    arch: str = Field(default="", max_length=100)
    logical_cores: int | None = Field(default=None, ge=0, le=1 << 20)
    memory_total_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    memory_available_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    disk_path: str = Field(default="", max_length=4096)
    disk_free_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    probe_error: str | None = Field(default=None, max_length=2000)
    notes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _reject_unknown_with_guessed_values(self) -> ResourceSnapshot:
        if (
            self.memory_available_bytes is not None
            and self.memory_total_bytes is not None
            and self.memory_available_bytes > self.memory_total_bytes
        ):
            raise ValueError("available memory cannot exceed total memory")
        return self


class CategoryLimit(_Frozen):
    """Per-category caps; unknown sizes fail closed for heavy categories."""

    category: OperationCategory
    max_concurrency: int = Field(default=1, ge=1, le=64)
    max_workers: int = Field(default=1, ge=1, le=64)
    max_memory_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    max_disk_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    max_items: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    max_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    max_open_files: int | None = Field(default=None, ge=0, le=1 << 20)
    max_recursion_depth: int | None = Field(default=None, ge=0, le=1 << 20)
    tiny: bool = False
    allow_unknown_size: bool = False


class ResourcePolicy(_Frozen):
    """Conservative adaptive policy with explicit reserves and caps."""

    memory_reserve_bytes: int = Field(default=1 << 30, ge=0, le=MAX_SAFE_BYTES)
    disk_reserve_bytes: int = Field(default=1 << 30, ge=0, le=MAX_SAFE_BYTES)
    safety_reserve_fraction: float = Field(default=0.15, ge=0, lt=1)
    disk_overhead_fraction: float = Field(default=0.5, ge=0, lt=10)
    max_open_files: int | None = Field(default=512, ge=1, le=1 << 20)
    max_recursion_depth: int | None = Field(default=64, ge=1, le=1 << 20)
    auto_heavy_concurrency: bool = True
    max_heavy_concurrency: int = Field(default=2, ge=1, le=8)
    large_host_min_memory_bytes: int = Field(default=32 << 30, ge=0, le=MAX_SAFE_BYTES)
    large_host_min_cores: int = Field(default=8, ge=1, le=1 << 20)
    categories: dict[OperationCategory, CategoryLimit] = Field(
        default_factory=lambda: _default_categories()
    )

    @field_validator("categories")
    @classmethod
    def _merge_categories(
        cls, value: dict[OperationCategory, CategoryLimit]
    ) -> dict[OperationCategory, CategoryLimit]:
        defaults = _default_categories()
        merged: dict[OperationCategory, CategoryLimit] = {}
        for category in defaults:
            merged[category] = value.get(category, defaults[category])
        for category, limit in value.items():
            if category not in defaults:
                merged[category] = limit
        for category, limit in merged.items():
            if limit.category != category:
                raise ValueError(
                    f"category limit for {category!r} declares category {limit.category!r}"
                )
        return merged

    def category_limit(self, category: OperationCategory) -> CategoryLimit:
        return self.categories.get(
            category,
            CategoryLimit(
                category=category, allow_unknown_size=category in {OperationCategory.TINY}
            ),
        )


def _default_categories() -> dict[OperationCategory, CategoryLimit]:
    defaults: dict[OperationCategory, CategoryLimit] = {
        OperationCategory.TINY: CategoryLimit(
            category=OperationCategory.TINY,
            max_concurrency=16,
            max_workers=2,
            max_items=1000,
            max_bytes=1 << 20,
            tiny=True,
            allow_unknown_size=True,
        ),
        OperationCategory.METADATA: CategoryLimit(
            category=OperationCategory.METADATA,
            max_concurrency=8,
            max_workers=2,
            max_items=10_000,
            max_bytes=8 << 20,
            tiny=True,
            allow_unknown_size=True,
        ),
        OperationCategory.READ_ONLY: CategoryLimit(
            category=OperationCategory.READ_ONLY,
            max_concurrency=4,
            max_workers=2,
            max_items=100_000,
            max_bytes=256 << 20,
            tiny=True,
            allow_unknown_size=True,
        ),
    }
    for category in HEAVY_CATEGORIES:
        defaults[category] = CategoryLimit(category=category)
    return defaults


class OperationRequest(_Frozen):
    """One bounded operation request; every field is strictly validated."""

    id: str = Field(min_length=1, max_length=100)
    category: OperationCategory
    name: str = Field(min_length=1, max_length=200)
    required_memory_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    required_disk_bytes: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    requested_workers: int | None = Field(default=None, ge=1, le=64)
    items_count: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    byte_count: int | None = Field(default=None, ge=0, le=MAX_SAFE_BYTES)
    open_files: int | None = Field(default=None, ge=0, le=1 << 20)
    recursion_depth: int | None = Field(default=None, ge=0, le=1 << 20)
    ttl_seconds: int | None = Field(default=None, ge=0, le=1 << 40)


class ResourceLease(_Frozen):
    """Lease token bound to the exact request, policy, and snapshot revision."""

    token: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=100)
    category: OperationCategory
    policy_revision: int = Field(ge=0)
    snapshot_revision: int = Field(ge=0)
    memory_bytes: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    disk_bytes: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    workers: int = Field(default=1, ge=1, le=64)
    items: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    bytes_accounted: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    open_files: int = Field(default=0, ge=0, le=1 << 20)
    active: bool = True


class AdmissionDecision(_Frozen):
    """Deterministic admission result with an optional granted lease."""

    request_id: str = Field(min_length=1, max_length=100)
    category: OperationCategory
    outcome: AdmissionOutcome
    reason: DenialReason = DenialReason.NONE
    recovery: RecoveryAction = RecoveryAction.NONE
    detail: str = Field(default="", max_length=2000)
    snapshot_revision: int = Field(ge=0)
    lease: ResourceLease | None = None


class UsageRecord(_Frozen):
    """Immutable accounting record for one granted or released lease."""

    token: str = Field(min_length=1, max_length=200)
    request_id: str = Field(min_length=1, max_length=100)
    category: OperationCategory
    policy_revision: int = Field(ge=0)
    snapshot_revision: int = Field(ge=0)
    memory_bytes: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    disk_bytes: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    workers: int = Field(default=1, ge=1, le=64)
    items: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    bytes_accounted: int = Field(default=0, ge=0, le=MAX_SAFE_BYTES)
    open_files: int = Field(default=0, ge=0, le=1 << 20)
    released: bool = False
    sequence: int = Field(ge=0)
