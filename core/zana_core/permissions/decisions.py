"""Default-deny permission decision engine.

This module decides whether a requested action is allowed, needs explicit
user approval, or is denied. Denials are structured and redacted.
"""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from zana_core.permissions.models import (
    FilesystemPolicy,
    MCPEndpoint,
    NetworkMode,
    PermissionPolicy,
)
from zana_core.permissions.redaction import redact_path, redact_reference, redact_value

REDACTED_VALUE = "***"


class Decision(str, Enum):
    """Outcome of a permission check."""

    ALLOW = "allow"
    ASK = "ask"
    DENY = "deny"


class PermissionKind(str, Enum):
    """Category of resource being protected."""

    NETWORK = "network"
    FILESYSTEM_READ = "filesystem_read"
    FILESYSTEM_WRITE = "filesystem_write"
    TOOL = "tool"
    SECRET = "secret"
    MCP = "mcp"


class Denial(BaseModel):
    """Structured, redacted denial suitable for logs and API errors."""

    model_config = ConfigDict(extra="forbid")

    kind: PermissionKind
    decision: Decision
    code: str
    message: str
    target: str = REDACTED_VALUE
    recovery: str
    redacted: bool = True


def _resolve(path: str | Path) -> Path:
    """Resolve symlinks and `..` segments so escape attempts are normalized."""
    return Path(path).expanduser().resolve(strict=False)


def _is_within(candidate: Path, root: Path) -> bool:
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def _containment_roots(policy: FilesystemPolicy, kind: PermissionKind) -> list[Path]:
    roots = policy.write if kind == PermissionKind.FILESYSTEM_WRITE else policy.read
    return [_resolve(root) for root in roots]


class PermissionDecisionEngine:
    """Evaluates permission requests against one immutable policy."""

    def __init__(self, policy: PermissionPolicy) -> None:
        self.policy = policy
        self._mcp_by_id = {endpoint.id: endpoint for endpoint in policy.experimental.mcp.endpoints}

    def network_allowed(self, *, approved: bool = False) -> Decision:
        if not self.policy.network.outbound or self.policy.network.mode == NetworkMode.OFFLINE:
            return Decision.DENY
        if self.policy.network.mode == NetworkMode.ASK:
            return Decision.ALLOW if approved else Decision.ASK
        return Decision.ALLOW

    def network_denial(self) -> Denial:
        if self.policy.network.mode == NetworkMode.ASK:
            return Denial(
                kind=PermissionKind.NETWORK,
                decision=Decision.ASK,
                code="NETWORK_ASK_REQUIRED",
                message="Outbound network access requires explicit user approval.",
                recovery="Approve the network request in the desktop UI.",
            )
        return Denial(
            kind=PermissionKind.NETWORK,
            decision=Decision.DENY,
            code="NETWORK_DENIED",
            message="Outbound network access is denied by this capability's policy.",
            recovery="Run in ask mode and approve the explicit request, or omit the action.",
        )

    def tool_allowed(self, tool_id: str) -> Decision:
        return Decision.ALLOW if tool_id in self.policy.tools.allow else Decision.DENY

    def tool_denial(self, tool_id: str) -> Denial:
        return Denial(
            kind=PermissionKind.TOOL,
            decision=Decision.DENY,
            code="TOOL_NOT_ALLOWED",
            message="The requested tool is not in this capability's allowlist.",
            target=redact_reference(tool_id),
            recovery="Add the tool to the policy allowlist before using it.",
        )

    def secret_allowed(self, reference: str) -> Decision:
        return Decision.ALLOW if reference in self.policy.secrets.allow else Decision.DENY

    def secret_denial(self, reference: str) -> Denial:
        return Denial(
            kind=PermissionKind.SECRET,
            decision=Decision.DENY,
            code="SECRET_NOT_ALLOWED",
            message="The requested secret reference is not allowed; no secret value was exposed.",
            target=redact_reference(reference),
            recovery="Add the exact secret reference to the policy allowlist.",
        )

    def filesystem_allowed(self, kind: PermissionKind, path: str | Path) -> Decision:
        if kind not in (PermissionKind.FILESYSTEM_READ, PermissionKind.FILESYSTEM_WRITE):
            raise ValueError("filesystem_allowed expects a filesystem permission kind.")
        candidate = _resolve(path)
        for root in _containment_roots(self.policy.filesystem, kind):
            if _is_within(candidate, root):
                return Decision.ALLOW
        return Decision.DENY

    def filesystem_denial(self, kind: PermissionKind, path: str | Path) -> Denial:
        code = (
            "FILESYSTEM_WRITE_DENIED"
            if kind == PermissionKind.FILESYSTEM_WRITE
            else "FILESYSTEM_READ_DENIED"
        )
        action = "write" if kind == PermissionKind.FILESYSTEM_WRITE else "read"
        return Denial(
            kind=kind,
            decision=Decision.DENY,
            code=code,
            message=(
                f"{action.title()} access is denied: the resolved path escapes "
                "the explicit mount roots or contains a symlink/traversal."
            ),
            target=redact_path(path),
            recovery=f"Add an explicit mount root containing this file, then retry the {action}.",
        )

    def mcp_endpoint_allowed(self, endpoint_id: str, scope: str) -> Decision:
        endpoint = self._mcp_by_id.get(endpoint_id)
        if endpoint is None or not endpoint.enabled or scope not in endpoint.scopes:
            return Decision.DENY
        return Decision.ALLOW

    def mcp_denial(self, endpoint_id: str, scope: str) -> Denial:
        endpoint: MCPEndpoint | None = self._mcp_by_id.get(endpoint_id)
        if endpoint is None:
            return Denial(
                kind=PermissionKind.MCP,
                decision=Decision.DENY,
                code="MCP_ENDPOINT_NOT_CONFIGURED",
                message="The external MCP endpoint is not configured for this capability.",
                target=redact_reference(endpoint_id),
                recovery="Add and explicitly enable the endpoint and its scopes in policy.",
            )
        if not endpoint.enabled:
            return Denial(
                kind=PermissionKind.MCP,
                decision=Decision.DENY,
                code="MCP_ENDPOINT_DISABLED",
                message="The external MCP endpoint is disabled by default and was not enabled.",
                target=redact_reference(endpoint_id),
                recovery="Explicitly enable the endpoint for this capability before use.",
            )
        return Denial(
            kind=PermissionKind.MCP,
            decision=Decision.DENY,
            code="MCP_SCOPE_DENIED",
            message="The requested scope is not allowed for this MCP endpoint.",
            target=f"{redact_reference(endpoint_id)}:{redact_reference(scope)}",
            recovery="Add the exact scope to the endpoint policy.",
        )


def sanitize_for_logs(value: object) -> str:
    """Return a guaranteed redacted representation for any caller value."""
    del value
    return redact_value(None)
