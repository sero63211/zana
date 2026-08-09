"""Typed hardware-profile structures for the ZANA system profile."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel


class OSType(str, Enum):
    """Operating-system families ZANA can classify."""

    MACOS = "macos"
    LINUX = "linux"
    WINDOWS = "windows"
    UNKNOWN = "unknown"


class AcceleratorKind(str, Enum):
    """Accelerator families ZANA can classify."""

    APPLE_METAL = "apple_metal"
    NVIDIA_CUDA = "nvidia_cuda"
    UNKNOWN = "unknown"


class BackendRole(str, Enum):
    """Whether a backend serves training or runtime execution."""

    TRAINING = "training"
    RUNTIME = "runtime"


class BackendKind(str, Enum):
    """Named training/runtime backends with stable identifiers.

    String values align with the T005 ``RuntimeKind`` values so a later
    integration lane can map them without renegotiating the contract.
    """

    OLLAMA = "ollama"
    LM_STUDIO = "lm-studio"
    LLAMA_CPP = "llama.cpp"
    MLX_LM = "mlx-lm"
    HF_PEFT = "hf_peft"


class CpuInfo(BaseModel):
    """CPU identity and core counts."""

    name: str | None = None
    logical_cores: int | None = None
    physical_cores: int | None = None


class MemoryInfo(BaseModel):
    """Total and currently available system memory."""

    total_bytes: int | None = None
    available_bytes: int | None = None


class DiskInfo(BaseModel):
    """Filesystem capacity at an explicit workspace path."""

    path: str
    total_bytes: int | None = None
    used_bytes: int | None = None
    free_bytes: int | None = None
    error: str | None = None


class AcceleratorInfo(BaseModel):
    """A detected accelerator and its memory semantics."""

    kind: AcceleratorKind
    name: str | None = None
    shared_memory: bool | None = None
    vram_total_bytes: int | None = None
    vram_free_bytes: int | None = None
    driver: str | None = None
    detected_via: str | None = None


class BackendAvailability(BaseModel):
    """Availability of one backend derived from installed executables/modules."""

    backend: BackendKind
    role: BackendRole
    installed: bool
    detected_via: str | None = None
    error: str | None = None


class HardwareProfile(BaseModel):
    """Snapshot of host resources relevant to ZANA build planning."""

    os: OSType
    arch: str
    cpu: CpuInfo
    memory: MemoryInfo
    disk: DiskInfo
    accelerators: list[AcceleratorInfo]
    training_backends: list[BackendAvailability]
    runtime_backends: list[BackendAvailability]
    collected_at: str
    notes: list[str] = []
