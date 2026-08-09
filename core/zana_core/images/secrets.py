"""Canonical recursive exclusion of secrets and mutable instance state.

This module is the single secret/mutable-state scanner used by both the
images and portability layers. Path classification, archive member-name
classification, and JSON payload value scanning all live here with bounded
iteration, depth, item, key, path, hit, value, and byte budgets.
"""

from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from zana_core.images.archive import (
    MAX_MARKER_POLICY_CHARS,
    MAX_MARKER_POLICY_ENTRIES,
    ArchiveCodecError,
    walk_bounded_tree,
)

SECRET_NAME_MARKERS = frozenset(
    {
        "secret",
        "secrets",
        "token",
        "tokens",
        "key",
        "keys",
        "credential",
        "credentials",
        "password",
        "passwords",
        "authorization",
        "auth",
        "id_rsa",
        "id_ed25519",
    }
)
SECRET_SUFFIXES = frozenset({".pem", ".key", ".crt", ".p12", ".pfx", ".kdbx", ".env", ".gpg"})
MUTABLE_STATE_DIRS = frozenset(
    {
        "conversations",
        "messages",
        "memories",
        "state",
        "states",
        "snapshots",
        "cache",
        "caches",
        "tmp",
        "temp",
        "runtime-cache",
    }
)

SENSITIVE_JSON_KEY_MARKERS = frozenset(
    {
        "accesskey",
        "api_key",
        "apikey",
        "api_token",
        "access_token",
        "authorization",
        "credential",
        "password",
        "passwd",
        "private_key",
        "secret",
        "secrets",
        "token",
        "tokens",
    }
)

SECRET_SCAN_MAX_ENTRIES = 8192
SECRET_SCAN_MAX_DEPTH = 32
SECRET_SCAN_MAX_NAME_CHARS = 1024
SECRET_SCAN_MAX_MEMBER_NAMES = 8192
LAYOUT_JSON_MAX_BYTES = 1024 * 1024


def _require_os_support() -> None:
    """Fail closed unless all required path-open primitives exist."""
    for attribute in ("O_NOFOLLOW", "O_CLOEXEC", "O_DIRECTORY"):
        if not hasattr(os, attribute):
            raise ExclusionError("secure filesystem open is unsupported on this platform")


class ExclusionError(ValueError):
    """Raised when an exclusion scan cannot be performed safely."""


class SecretScanLimits(BaseModel):
    """Frozen budgets for recursive JSON secret scanning."""

    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)

    max_items: int = Field(default=8192, gt=0, le=8192)
    max_depth: int = Field(default=32, gt=0, le=32)
    max_hits: int = Field(default=64, gt=0, le=64)
    max_key_chars: int = Field(default=200, gt=0, le=200)
    max_path_chars: int = Field(default=1024, gt=0, le=1024)
    max_value_chars: int = Field(default=4096, gt=0, le=4096)


def _reject_symlink_components(path: Path) -> None:
    candidate = _exact_path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    try:
        resolved = os.path.realpath(candidate)
    except OSError as error:
        raise ExclusionError("path could not be resolved safely") from error
    if Path(resolved) != candidate:
        raise ExclusionError("path contains a symlink component")


def _exact_path(value: object) -> Path:
    if type(value) is not type(Path()):
        raise ExclusionError("path must be an exact pathlib.Path")
    return value


class ExclusionScanner:
    """Recursively classify export candidates as safe, secret, or mutable state."""

    def __init__(
        self,
        *,
        secret_name_markers: frozenset[str] = SECRET_NAME_MARKERS,
        secret_suffixes: frozenset[str] = SECRET_SUFFIXES,
        mutable_state_dirs: frozenset[str] = MUTABLE_STATE_DIRS,
        max_depth: int = SECRET_SCAN_MAX_DEPTH,
        max_entries: int = SECRET_SCAN_MAX_ENTRIES,
        max_name_chars: int = SECRET_SCAN_MAX_NAME_CHARS,
    ) -> None:
        for policy, label in (
            (secret_name_markers, "secret-name marker"),
            (secret_suffixes, "secret-suffix"),
            (mutable_state_dirs, "mutable-state"),
        ):
            if type(policy) is not frozenset:
                raise ExclusionError(f"{label} policy must be a frozenset")
            if len(policy) > MAX_MARKER_POLICY_ENTRIES:
                raise ExclusionError(f"{label} policy exceeds the entry hard limit")
            for item in policy:
                if type(item) is not str or not item or len(item) > MAX_MARKER_POLICY_CHARS:
                    raise ExclusionError(f"{label} policy contains an invalid item")
        if type(max_depth) is not int or not 1 <= max_depth <= SECRET_SCAN_MAX_DEPTH:
            raise ExclusionError(f"scan depth must be within 1..{SECRET_SCAN_MAX_DEPTH}")
        if type(max_entries) is not int or not 1 <= max_entries <= SECRET_SCAN_MAX_ENTRIES:
            raise ExclusionError(f"scan entry budget must be within 1..{SECRET_SCAN_MAX_ENTRIES}")
        if type(max_name_chars) is not int or not 1 <= max_name_chars <= SECRET_SCAN_MAX_NAME_CHARS:
            raise ExclusionError(f"scan name budget must be within 1..{SECRET_SCAN_MAX_NAME_CHARS}")
        self.secret_name_markers = secret_name_markers
        self.secret_suffixes = secret_suffixes
        self.mutable_state_dirs = mutable_state_dirs
        self.max_depth = max_depth
        self.max_entries = max_entries
        self.max_name_chars = max_name_chars

    def scan(self, root: Path) -> list[Path]:
        """Return regular files under ``root`` that are safe to export."""
        original = _exact_path(root)
        _reject_symlink_components(original)
        if original.is_symlink():
            raise ExclusionError("export root is a symlink")
        if not original.is_dir():
            raise ExclusionError("export root is not a directory")
        try:
            entries = walk_bounded_tree(
                original,
                remaining_budget=self.max_entries,
                max_depth=self.max_depth,
                max_path_chars=self.max_name_chars,
            )
        except ArchiveCodecError as error:
            raise ExclusionError(str(error)) from error
        safe: list[Path] = []
        for _name, path in entries:
            if self._is_secret(path) or self._is_mutable_state(path):
                continue
            safe.append(path)
        return safe

    def classify(self, path: Path) -> str:
        """Return ``safe``, ``secret``, or ``mutable-state`` for one path."""
        if self._is_secret(path):
            return "secret"
        if self._is_mutable_state(path):
            return "mutable-state"
        return "safe"

    def classify_member_name(self, name: str) -> str:
        """Classify one archive/layout member name for export exclusion."""
        if type(name) is not str or len(name) > self.max_name_chars:
            raise ExclusionError("archive member name exceeds the length limit")
        normalized = name.replace("\\", "/").lower()
        parts = [part for part in normalized.split("/") if part]
        stems = [part.split(".")[0] for part in parts]
        if any(part.endswith(suffix) for suffix in self.secret_suffixes for part in parts):
            return "secret"
        if any(marker in part for part in parts for marker in self.secret_name_markers):
            return "secret"
        if any(
            part in self.mutable_state_dirs or stem in self.mutable_state_dirs
            for part, stem in zip(parts, stems, strict=False)
        ):
            return "mutable-state"
        return "safe"

    def scan_member_names(
        self,
        names: list[str],
        *,
        max_names: int = SECRET_SCAN_MAX_MEMBER_NAMES,
    ) -> list[str]:
        """Return safe member names; secret/mutable names raise ExclusionError."""
        if type(max_names) is not int or max_names <= 0 or max_names > SECRET_SCAN_MAX_MEMBER_NAMES:
            raise ExclusionError("member-name scan budget exceeds the hard limit")
        if type(names) is not list or len(names) > max_names:
            raise ExclusionError("member-name scan count exceeds the limit")
        safe: list[str] = []
        for name in names:
            classification = self.classify_member_name(name)
            if classification == "safe":
                safe.append(name)
                continue
            raise ExclusionError(f"archive member is not export-safe ({classification})")
        return safe

    def scan_payload(
        self,
        payload: dict[str, Any],
        *,
        limits: SecretScanLimits | None = None,
        deadline: Any | None = None,
    ) -> list[str]:
        """Return paths of non-empty string values under sensitive JSON keys."""
        return scan_payload_for_secrets(payload, limits=limits, deadline=deadline)

    def _is_secret(self, path: Path) -> bool:
        name = path.name.lower()
        if path.suffix.lower() in self.secret_suffixes:
            return True
        if any(marker in name for marker in self.secret_name_markers):
            return True
        return any(
            marker in part.lower() for part in path.parts for marker in self.secret_name_markers
        )

    def _is_mutable_state(self, path: Path) -> bool:
        return any(part.lower() in self.mutable_state_dirs for part in path.parts)


def scan_payload_for_secrets(
    payload: dict[str, Any],
    *,
    limits: SecretScanLimits | None = None,
    deadline: Any | None = None,
) -> list[str]:
    """Iteratively return JSON paths whose sensitive keys carry string values."""
    if type(payload) is not dict:
        raise ExclusionError("secret scan payload must be an exact builtin mapping")
    if limits is not None and type(limits) is not SecretScanLimits:
        raise ExclusionError("secret scan limits must be exact SecretScanLimits or None")
    active = SecretScanLimits() if limits is None else limits
    hits: list[str] = []
    items = 0
    visited: set[int] = set()
    stack: list[tuple[Any, str, int]] = [(payload, "", 0)]
    while stack:
        if deadline is not None:
            deadline.check("secret_scan")
        node, path, depth = stack.pop()
        if type(node) in (dict, list):
            identity = id(node)
            if identity in visited:
                raise ExclusionError("secret scan encountered a cyclic or aliased JSON graph")
            visited.add(identity)
        items += 1
        if items > active.max_items:
            raise ExclusionError("secret scan exceeded the item budget")
        if depth > active.max_depth:
            raise ExclusionError("secret scan exceeded the depth budget")
        children: list[tuple[Any, str, int]] = []
        remaining_items = active.max_items - items - len(stack)
        if remaining_items <= 0:
            raise ExclusionError("secret scan exceeded the item budget")
        if type(node) is dict:
            for collected, (key, value) in enumerate(node.items()):
                if collected >= remaining_items:
                    raise ExclusionError("secret scan exceeded the item budget")
                if type(key) is not str or len(key) > active.max_key_chars:
                    raise ExclusionError("secret scan encountered an oversized key")
                child = f"{path}.{key}" if path else key
                if len(child) > active.max_path_chars:
                    raise ExclusionError("secret scan exceeded the path budget")
                child_depth = depth + 1
                lowered = key.lower()
                if any(marker in lowered for marker in SENSITIVE_JSON_KEY_MARKERS):
                    if type(value) is str and value == "":
                        continue
                    if type(value) is str:
                        if len(value) > active.max_value_chars:
                            raise ExclusionError(
                                "secret scan encountered an oversized sensitive value"
                            )
                        hits.append(child)
                        if len(hits) > active.max_hits:
                            raise ExclusionError("secret scan exceeded the hit budget")
                    elif type(value) in (dict, list) and _is_permission_reference_path(path, key):
                        # Validated image permissions carry allow-lists of
                        # references (permissions.secrets_allow and friends),
                        # never secret values; recurse so child references are
                        # scanned without treating the container itself as a
                        # secret value.
                        children.append((value, child, child_depth))
                        continue
                    elif type(value) in (dict, list):
                        raise ExclusionError("secret scan encountered a non-string sensitive value")
                    elif value is None:
                        continue
                    else:
                        raise ExclusionError("secret scan encountered a non-string sensitive value")
                children.append((value, child, child_depth))
        elif type(node) is list:
            for index, value in enumerate(node):
                if index >= remaining_items:
                    raise ExclusionError("secret scan exceeded the item budget")
                child = f"{path}[{index}]"
                if len(child) > active.max_path_chars:
                    raise ExclusionError("secret scan exceeded the path budget")
                children.append((value, child, depth + 1))
        elif type(node) not in (type(None), str, int, float, bool):
            raise ExclusionError("secret scan encountered an unsupported JSON value")
        stack.extend(children)
    return hits


def _is_permission_reference_path(path: str, key: str) -> bool:
    """Whether a sensitive key is a validated permission allow-list path."""
    return path == "permissions" and key in {
        "filesystem_read",
        "filesystem_write",
        "tools_allow",
        "secrets_allow",
    }


def reject_mutable_member_names(names: list[str]) -> None:
    """Reject archive members carrying mutable instance state."""
    scanner = ExclusionScanner()
    for name in names:
        if scanner.classify_member_name(name) == "mutable-state":
            raise ExclusionError("archive member carries mutable instance state")


def scan_layout_payloads(
    layout: Path,
    *,
    max_json_bytes: int | None = None,
    deadline: Any | None = None,
    limits: SecretScanLimits | None = None,
) -> None:
    """Scan canonical layout JSON files with bounded reads and budgets."""
    cap = max_json_bytes if max_json_bytes is not None else LAYOUT_JSON_MAX_BYTES
    if type(cap) is not int or cap <= 0 or cap > LAYOUT_JSON_MAX_BYTES:
        raise ExclusionError("JSON scan budget exceeds the hard limit")
    if limits is not None and type(limits) is not SecretScanLimits:
        raise ExclusionError("secret scan limits must be exact SecretScanLimits or None")
    if deadline is not None:
        deadline.check("secret_scan")
    _reject_symlink_components(Path(layout))
    _require_os_support()
    root_fd = _open_layout_root(layout)
    try:
        for name in ("oci-layout", "index.json", "manifest.json"):
            if deadline is not None:
                deadline.check("secret_scan")
            try:
                info = os.stat(name, dir_fd=root_fd, follow_symlinks=False)
                if not stat.S_ISREG(info.st_mode):
                    raise ExclusionError(f"layout file is not regular: {name}")
                fd = os.open(
                    name,
                    os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC,
                    dir_fd=root_fd,
                )
            except OSError as error:
                raise ExclusionError(
                    f"required OCI JSON metadata is missing or unsafe: {name}"
                ) from error
            try:
                with os.fdopen(fd, "rb") as handle:
                    info = os.fstat(handle.fileno())
                    if not stat.S_ISREG(info.st_mode):
                        raise ExclusionError(f"layout file is not regular: {name}")
                    data = handle.read(cap + 1)
            except OSError as error:
                raise ExclusionError(f"could not read JSON metadata {name}") from error
            if len(data) > cap:
                raise ExclusionError(f"{name} exceeds the JSON size limit")
            try:
                payload = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, ValueError):
                raise ExclusionError(f"could not parse JSON metadata {name}") from None
            if type(payload) is not dict:
                raise ExclusionError(f"{name} must contain a JSON object")
            hits = scan_payload_for_secrets(payload, limits=limits, deadline=deadline)
            if hits:
                raise ExclusionError(f"{name} would serialize secret values")
    finally:
        os.close(root_fd)


def _open_layout_root(layout: Path) -> int:
    """Open the layout root by dirfd and require exact identity after stat."""
    try:
        fd = os.open(
            layout,
            os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
        )
    except OSError as error:
        raise ExclusionError("layout root is missing or unsafe") from error
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode):
        os.close(fd)
        raise ExclusionError("layout root is not a directory")
    try:
        stat_path = Path(layout).stat(follow_symlinks=False)
    except OSError as error:
        os.close(fd)
        raise ExclusionError("layout root identity could not be verified") from error
    if (stat_path.st_dev, stat_path.st_ino) != (info.st_dev, info.st_ino):
        os.close(fd)
        raise ExclusionError("layout root changed identity during scan")
    return fd
