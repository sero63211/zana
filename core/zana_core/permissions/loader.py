"""Safe YAML/dict loading for permission policies.

Loading is fail-closed: unknown fields, unknown schema versions, duplicate
mapping keys, forbidden tool ids, and malformed documents produce structured
errors with recovery guidance instead of partial policies.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from zana_core.permissions.models import (
    BUILTIN_TOOL_IDS,
    FORBIDDEN_TOOL_IDS,
    SCHEMA_VERSION,
    PermissionPolicy,
)


class PolicyError(Exception):
    """Base error for permission policy loading and validation."""

    code: str = "PERMISSION_POLICY_ERROR"

    def __init__(self, message: str, *, recoverable: bool = True) -> None:
        super().__init__(message)
        self.message = message
        self.recoverable = recoverable


class PolicyLoadError(PolicyError):
    """Raised when a policy document cannot be interpreted safely."""

    code = "PERMISSION_POLICY_INVALID"


class UnsupportedSchemaVersionError(PolicyError):
    """Raised when a policy uses a schema version this Core cannot handle."""

    code = "UNSUPPORTED_PERMISSION_SCHEMA_VERSION"

    def __init__(self, version: Any) -> None:
        self.version = version
        super().__init__(
            f"Unsupported permission policy schemaVersion {version!r}; "
            f"only version {SCHEMA_VERSION} is supported.",
            recoverable=True,
        )


class _StrictYamlLoader(yaml.SafeLoader):
    """SafeLoader variant that rejects duplicate mapping keys."""


def _construct_mapping(
    loader: _StrictYamlLoader, node: yaml.MappingNode, deep: bool = False
) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "permission policy keys must be strings",
                key_node.start_mark,
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictYamlLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_mapping,
)


def _validate_policy_data(data: Any) -> PermissionPolicy:
    if not isinstance(data, dict):
        raise PolicyLoadError(
            "A permission policy must be a mapping, not a scalar or list.",
            recoverable=True,
        )

    schema_version = data.get("schemaVersion", SCHEMA_VERSION)
    if schema_version != SCHEMA_VERSION:
        raise UnsupportedSchemaVersionError(schema_version)

    try:
        policy = PermissionPolicy.model_validate(data)
    except ValidationError as error:
        details = ", ".join(
            f"{'.'.join(str(part) for part in item['loc'])}: {item['msg']}"
            for item in error.errors()
        )
        raise PolicyLoadError(
            f"Permission policy failed validation: {details}",
            recoverable=True,
        ) from error

    forbidden = sorted(set(policy.tools.allow) & FORBIDDEN_TOOL_IDS)
    if forbidden:
        raise PolicyLoadError(
            "Capability sources cannot enable shell, Python execution, "
            "install scripts, post-install hooks, or arbitrary code. "
            f"Forbidden tool id(s): {', '.join(forbidden)}.",
            recoverable=True,
        )

    unknown_tools = sorted(set(policy.tools.allow) - BUILTIN_TOOL_IDS)
    if unknown_tools:
        raise PolicyLoadError(
            "Capability tools may only reference trusted built-in ZANA tools. "
            f"Unknown tool id(s): {', '.join(unknown_tools)}.",
            recoverable=True,
        )

    return policy


def load_policy(source: str | bytes | dict[str, Any]) -> PermissionPolicy:
    """Load and validate a permission policy from YAML text or a dict.

    Omitted sections resolve to deny-by-default values. Unknown fields and
    unknown schema versions are rejected.
    """
    if isinstance(source, dict):
        return _validate_policy_data(source)
    if isinstance(source, bytes):
        try:
            text = source.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PolicyLoadError(
                "Permission policy bytes are not valid UTF-8.",
                recoverable=True,
            ) from error
        return load_policy(text)
    if not isinstance(source, str):
        raise PolicyLoadError(
            "Permission policy source must be YAML text, bytes, or a dict.",
            recoverable=True,
        )

    try:
        data = yaml.load(source, Loader=_StrictYamlLoader)
    except yaml.YAMLError as error:
        raise PolicyLoadError(
            f"Permission policy is not valid YAML: {error}",
            recoverable=True,
        ) from error
    if data is None:
        data = {}
    return _validate_policy_data(data)


def load_policy_file(path: str | Path) -> PermissionPolicy:
    """Load a permission policy from a local file path."""
    file_path = Path(path)
    try:
        return load_policy(file_path.read_text(encoding="utf-8"))
    except OSError as error:
        raise PolicyLoadError(
            f"Permission policy file could not be read: {error}",
            recoverable=True,
        ) from error
