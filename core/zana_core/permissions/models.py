"""Typed permission policy models.

The policy is versioned and fail-closed: every field omitted by the caller
resolves to a deny-by-default value.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field

SCHEMA_VERSION = 1

# Canonical trusted built-in tool ids. Capability sources may only reference
# these ids; shell, Python, install hooks, and arbitrary code are rejected by
# the loader rather than represented as tools.
BUILTIN_TOOL_IDS: frozenset[str] = frozenset({"zana.calculator"})

# Names that explicitly map to arbitrary code or installation behavior. Even
# if a future built-in registry grows, these must never be reachable from a
# capability source.
FORBIDDEN_TOOL_IDS: frozenset[str] = frozenset(
    {
        "bash",
        "exec",
        "eval",
        "hook",
        "install",
        "install_script",
        "post_install",
        "post_install_hook",
        "py_exec",
        "python",
        "python_exec",
        "sh",
        "shell",
        "subprocess",
        "zsh",
    }
)


class NetworkMode(str, Enum):
    """Network permission modes supported by the V1 decision engine."""

    OFFLINE = "offline"
    ASK = "ask"


class NetworkPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: NetworkMode = NetworkMode.OFFLINE
    outbound: bool = False


class FilesystemPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: list[str] = Field(default_factory=list)
    write: list[str] = Field(default_factory=list)


class ToolsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)


class SecretsPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    allow: list[str] = Field(default_factory=list)


class MCPEndpoint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=200)
    endpoint: str = Field(min_length=1, max_length=2000)
    scopes: list[str] = Field(default_factory=list)
    enabled: bool = False


class ExperimentalMCP(BaseModel):
    model_config = ConfigDict(extra="forbid")

    endpoints: list[MCPEndpoint] = Field(default_factory=list)


class ExperimentalSection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mcp: ExperimentalMCP = Field(default_factory=ExperimentalMCP)


class PermissionPolicy(BaseModel):
    """Canonical permission policy, always versioned and deny by default."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    schema_version: int = Field(default=SCHEMA_VERSION, alias="schemaVersion")
    network: NetworkPolicy = Field(default_factory=NetworkPolicy)
    filesystem: FilesystemPolicy = Field(default_factory=FilesystemPolicy)
    tools: ToolsPolicy = Field(default_factory=ToolsPolicy)
    secrets: SecretsPolicy = Field(default_factory=SecretsPolicy)
    experimental: ExperimentalSection = Field(default_factory=ExperimentalSection)


DEFAULT_POLICY = PermissionPolicy()
