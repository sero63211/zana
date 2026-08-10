"""Real on-disk Capability Source authoring for authenticated Core drafts.

This module owns the canonical ``capabilities/<id>`` workspace layout under
the app data root, deterministic atomic source publication, and the coherent
``zana.yaml`` manifest updates the CapabilitySourceValidator reads. Source
content is data only: it is never executed, no install hooks exist, and the
Core derives every destination path from a fixed kind contract.
"""

from __future__ import annotations

import contextlib
import copy
import errno
import hashlib
import os
import re
import shutil
import stat
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

import yaml

from zana_core.capabilities.errors import (
    CapabilityIssue,
    CapabilitySourceValidationError,
    relative_label,
)
from zana_core.capabilities.evaluation import EvalKind, load_evaluation_set
from zana_core.capabilities.manifest import DuplicateKeyError, parse_safe_yaml

MAX_BEHAVIOR_BYTES = 1 << 20
MAX_EVAL_BYTES = 4 << 20
MAX_DOCUMENT_BYTES = 32 << 20
MAX_MANIFEST_BYTES = 1 << 20
MAX_VALIDATION_FILE_COUNT = 512
MAX_VALIDATION_TREE_BYTES = 128 << 20
MAX_VALIDATION_DEPTH = 32
MAX_LOCAL_PATH_CHARS = 2000
MAX_FILENAME_CHARS = 255
MAX_MESSAGE_CHARS = 500

BEHAVIOR_RELATIVE_PATH = "behavior/system.md"
EVAL_RELATIVE_PATHS: dict[str, str] = {
    "domain": "evals/domain.jsonl",
    "regression": "evals/regression.jsonl",
}
KNOWLEDGE_DIR_RELATIVE = "knowledge/sources"

MANAGED_DIR_RELATIVES = ("behavior", "knowledge", "knowledge/sources", "evals")
_MANAGED_DIR_NAMES = frozenset({"behavior", "knowledge", "sources", "evals"})

DOCUMENT_MEDIA_TYPES: dict[str, str] = {
    ".pdf": "application/pdf",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
}

_FILENAME_CHARSET = re.compile(r"^[A-Za-z0-9._ -]+$")


class AuthoringError(Exception):
    """Typed authoring failure with a stable machine code."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(message)


class AuthoringValidationError(AuthoringError):
    """Source content failed the real Capability Source validators."""

    def __init__(self, issues: list[CapabilityIssue] | tuple[CapabilityIssue, ...]) -> None:
        self.issues = tuple(issues)
        messages = "\n".join(issue.render() for issue in self.issues)
        super().__init__("SOURCE_INVALID", messages)


@dataclass(frozen=True, slots=True)
class SourceRequest:
    """Typed source-ingestion request decoupled from the API layer."""

    kind: Literal["behavior", "document", "evaluation"]
    content: str | None = None
    local_path: str | None = None
    user_approved: bool = False
    eval_kind: Literal["domain", "regression"] | None = None


@dataclass(frozen=True, slots=True)
class StagedSource:
    """One staged source ready for atomic publication after DB persistence."""

    temp_path: Path
    target_path: Path
    relative_path: str
    original_name: str
    sha256: str
    size_bytes: int
    media_type: str
    metadata: dict[str, Any]
    manifest_kind: Literal["behavior", "document", "evaluation"]
    eval_kind: Literal["domain", "regression"] | None = None

    def cleanup(self) -> None:
        discard_temp(self.temp_path)


@dataclass(frozen=True, slots=True)
class ValidationPreflight:
    """Bounded lstat-only package snapshot for fail-closed validation."""

    file_count: int
    aggregate_bytes: int
    files: tuple[Path, ...]
    symlink_issue: CapabilityIssue | None = None
    type_issue: CapabilityIssue | None = None
    count_issue: CapabilityIssue | None = None
    bytes_issue: CapabilityIssue | None = None

    @property
    def ok(self) -> bool:
        return not (self.symlink_issue or self.type_issue or self.count_issue or self.bytes_issue)

    @property
    def issues(self) -> tuple[CapabilityIssue, ...]:
        return tuple(
            issue
            for issue in (
                self.symlink_issue,
                self.type_issue,
                self.count_issue,
                self.bytes_issue,
            )
            if issue is not None
        )


def _unlink(path: Path) -> None:
    with contextlib.suppress(OSError):
        path.unlink(missing_ok=True)


def discard_temp(path: Path) -> None:
    """Best-effort cleanup for a staged temp file."""
    _unlink(path)


def _is_within(root: Path, candidate: Path) -> bool:
    try:
        candidate.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_absolute_root(root: Path) -> Path:
    path = Path(root)
    if not path.is_absolute():
        raise AuthoringError("DATA_ROOT_INVALID", "The app data root must be an absolute path.")
    return Path(os.path.normpath(str(path)))


def _validate_chain(
    root: Path,
    relative: str | Path,
    *,
    require_existing: bool,
    label: str,
) -> None:
    """Validate every existing managed path component is a real directory."""
    candidate = root
    for part in Path(relative).parts:
        candidate = candidate / part
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            if require_existing:
                raise AuthoringError(
                    "WORKSPACE_MISSING", f"managed {label} path is missing"
                ) from None
            continue
        except OSError:
            raise AuthoringError(
                "WORKSPACE_READ", "cannot inspect a managed capability path"
            ) from None
        if stat.S_ISLNK(info.st_mode):
            raise AuthoringError("PATH_SYMLINK", f"managed {label} path must not be a symlink")
        if not stat.S_ISDIR(info.st_mode):
            raise AuthoringError("WORKSPACE_TYPE", f"managed {label} path must be a directory")


def validate_workspace_tree(
    workspace: Path,
    data_root: Path | None = None,
    *,
    require_existing: bool = False,
) -> None:
    """Reject symlinked/non-directory components in the complete managed path."""
    root = _normalize_absolute_root(data_root if data_root is not None else workspace.parent.parent)
    workspace = Path(os.path.normpath(str(workspace)))
    try:
        capability_id = int(workspace.name)
    except (TypeError, ValueError):
        raise AuthoringError(
            "WORKSPACE_INVALID", "capability workspace name must be the numeric capability id"
        ) from None
    canonical = capability_workspace_path(root, capability_id)
    if workspace != canonical:
        raise AuthoringError(
            "WORKSPACE_ESCAPE", "capability workspace is not the canonical managed path"
        )
    try:
        root_info = root.lstat()
    except FileNotFoundError:
        raise AuthoringError("WORKSPACE_MISSING", "the app data root does not exist") from None
    except OSError:
        raise AuthoringError("WORKSPACE_READ", "cannot inspect the app data root") from None
    if stat.S_ISLNK(root_info.st_mode):
        raise AuthoringError("PATH_SYMLINK", "the app data root must not be a symlink")
    if not stat.S_ISDIR(root_info.st_mode):
        raise AuthoringError("WORKSPACE_TYPE", "the app data root must be a directory")
    _validate_chain(root, "capabilities", require_existing=require_existing, label="capabilities")
    _validate_chain(
        root,
        f"capabilities/{capability_id}",
        require_existing=require_existing,
        label="capability",
    )
    for relative in MANAGED_DIR_RELATIVES:
        _validate_chain(
            root,
            f"capabilities/{capability_id}/{relative}",
            require_existing=require_existing,
            label="source directory",
        )
    if require_existing:
        try:
            resolved_root = root.resolve(strict=False)
            resolved_workspace = workspace.resolve(strict=False)
        except OSError:
            raise AuthoringError(
                "WORKSPACE_READ", "cannot resolve the managed capability workspace"
            ) from None
        if not _is_within(resolved_root, resolved_workspace) or resolved_workspace == resolved_root:
            raise AuthoringError(
                "WORKSPACE_ESCAPE", "capability workspace escapes the app data root"
            )


def workspace_for_target(target: Path) -> Path:
    """Recover the managed workspace containing one canonical source target."""
    current = target.parent
    while current.name in _MANAGED_DIR_NAMES:
        current = current.parent
    return Path(os.path.normpath(str(current)))


def validate_target_parent(target: Path, workspace: Path | None = None) -> None:
    """Validate the exact workspace and source target parent chain."""
    if workspace is None:
        workspace = workspace_for_target(target)
    validate_workspace_tree(workspace, require_existing=True)
    try:
        relative = target.relative_to(workspace).as_posix()
    except ValueError:
        raise AuthoringError(
            "WORKSPACE_ESCAPE", "source target is outside the managed workspace"
        ) from None
    if target.parent != workspace:
        _validate_chain(
            workspace,
            target.parent.relative_to(workspace),
            require_existing=True,
            label="source target parent",
        )
    if "/" in relative or "\\" in relative:
        for part in relative.split("/"):
            if part in ("", ".", ".."):
                raise AuthoringError(
                    "PATH_TRAVERSAL", "source target path is not a safe managed path"
                )


def capability_workspace_path(data_root: Path, capability_id: int) -> Path:
    """Return the canonical, contained workspace path for one capability."""
    if type(capability_id) is not int or capability_id <= 0:
        raise AuthoringError("CAPABILITY_ID_INVALID", "Capability id must be a positive integer.")
    root = _normalize_absolute_root(data_root)
    workspace = Path(os.path.normpath(str(root / "capabilities" / str(capability_id))))
    if not _is_within(root, workspace):
        raise AuthoringError("WORKSPACE_ESCAPE", "Capability workspace escapes the app data root.")
    return workspace


def workspace_is_under_data_root(workspace: Path, data_root: Path) -> bool:
    """True when the saved workspace is the exact canonical managed workspace."""
    try:
        root = _normalize_absolute_root(data_root)
        canonical = capability_workspace_path(root, int(workspace.name))
        _validate_chain(root, "capabilities", require_existing=False, label="capabilities")
        _validate_chain(
            root,
            f"capabilities/{workspace.name}",
            require_existing=False,
            label="capability",
        )
    except (AuthoringError, TypeError, ValueError):
        return False
    return Path(os.path.normpath(str(workspace))) == canonical


def relative_workspace_path(workspace: Path, data_root: Path) -> str:
    """Deterministic data-root-relative label; never a full host path."""
    try:
        return (
            workspace.resolve(strict=False).relative_to(data_root.resolve(strict=False)).as_posix()
        )
    except (OSError, ValueError):
        return workspace.name


def ensure_workspace(workspace: Path, data_root: Path | None = None) -> None:
    """Create the private canonical workspace layout, validating the whole tree."""
    validate_workspace_tree(workspace, data_root, require_existing=False)
    try:
        for relative in ("", *MANAGED_DIR_RELATIVES):
            target = workspace if not relative else workspace / relative
            target.mkdir(parents=True, exist_ok=True, mode=0o700)
    except OSError as exc:
        raise AuthoringError("WORKSPACE_CREATE", "cannot create the capability workspace") from exc
    _make_private(workspace)
    validate_workspace_tree(workspace, data_root, require_existing=True)


def _make_private(workspace: Path) -> None:
    if os.name != "posix":
        return
    try:
        for relative in ("", *MANAGED_DIR_RELATIVES):
            target = workspace if not relative else workspace / relative
            _chmod_dir_no_follow(target)
    except OSError as exc:
        raise AuthoringError(
            "WORKSPACE_PERMISSIONS", "cannot make the capability workspace private"
        ) from exc


def _chmod_dir_no_follow(path: Path) -> None:
    """chmod a managed directory without following a swapped symlink."""
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    directory_flag = getattr(os, "O_DIRECTORY", 0)
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | no_follow | directory_flag)
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise AuthoringError(
                "WORKSPACE_TYPE", "managed capability directory is not a real directory"
            )
        os.fchmod(fd, 0o700)
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError(
            "WORKSPACE_PERMISSIONS", "cannot make the capability workspace private"
        ) from exc
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def remove_workspace(workspace: Path, data_root: Path, *, created_by_request: bool) -> bool:
    """Remove only a workspace proven newly created by the current request."""
    if not created_by_request:
        return False
    try:
        canonical = capability_workspace_path(data_root, int(workspace.name))
        validate_workspace_tree(canonical, data_root, require_existing=False)
    except (AuthoringError, TypeError, ValueError):
        return False
    if workspace != canonical:
        return False
    try:
        info = workspace.lstat()
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if stat.S_ISLNK(info.st_mode):
        return False
    if not stat.S_ISDIR(info.st_mode):
        return False
    try:
        shutil.rmtree(workspace)
    except OSError:
        return False
    return not workspace.exists()


def validate_source_preflight(
    workspace: Path,
    data_root: Path,
    *,
    max_files: int | None = None,
    max_bytes: int | None = None,
) -> ValidationPreflight:
    """Deterministic lstat-only managed tree preflight; never reads content."""
    resolved_max_files = MAX_VALIDATION_FILE_COUNT if max_files is None else max_files
    resolved_max_bytes = MAX_VALIDATION_TREE_BYTES if max_bytes is None else max_bytes
    try:
        validate_workspace_tree(workspace, data_root, require_existing=True)
    except AuthoringError as exc:
        return ValidationPreflight(
            file_count=0,
            aggregate_bytes=0,
            files=(),
            symlink_issue=CapabilityIssue(exc.code, exc.message),
        )
    files: list[Path] = []
    aggregate = 0
    symlink_issue: CapabilityIssue | None = None
    type_issue: CapabilityIssue | None = None
    count_issue: CapabilityIssue | None = None
    bytes_issue: CapabilityIssue | None = None
    pending: list[tuple[Path, int]] = [(workspace, 0)]
    while pending:
        directory, depth = pending.pop()
        if depth > MAX_VALIDATION_DEPTH:
            count_issue = CapabilityIssue(
                "SOURCE_DEPTH_LIMIT",
                f"managed source tree exceeds the {MAX_VALIDATION_DEPTH}-level depth limit",
            )
            break
        try:
            iterator = directory.iterdir()
        except FileNotFoundError:
            continue
        except OSError:
            return ValidationPreflight(
                file_count=len(files),
                aggregate_bytes=aggregate,
                files=tuple(files),
                symlink_issue=CapabilityIssue(
                    "PREFLIGHT_READ", "cannot read a managed source directory"
                ),
            )
        while True:
            try:
                entry = next(iterator)
            except StopIteration:
                break
            except OSError:
                return ValidationPreflight(
                    file_count=len(files),
                    aggregate_bytes=aggregate,
                    files=tuple(files),
                    symlink_issue=CapabilityIssue(
                        "PREFLIGHT_READ", "cannot read a managed source directory"
                    ),
                )
            if len(files) >= resolved_max_files + 1:
                count_issue = CapabilityIssue(
                    "SOURCE_COUNT_LIMIT",
                    f"managed source tree exceeds the {resolved_max_files}-file limit",
                )
                break
            if len(files) >= resolved_max_files:
                count_issue = CapabilityIssue(
                    "SOURCE_COUNT_LIMIT",
                    f"managed source tree exceeds the {resolved_max_files}-file limit",
                )
            try:
                info = entry.lstat()
            except OSError:
                return ValidationPreflight(
                    file_count=len(files),
                    aggregate_bytes=aggregate,
                    files=tuple(files),
                    symlink_issue=CapabilityIssue(
                        "PREFLIGHT_READ", "cannot inspect a managed source file"
                    ),
                )
            if stat.S_ISLNK(info.st_mode):
                symlink_issue = CapabilityIssue(
                    "PATH_SYMLINK", "managed source files must not be symlinks"
                )
                continue
            if stat.S_ISDIR(info.st_mode):
                pending.append((entry, depth + 1))
                continue
            if not stat.S_ISREG(info.st_mode):
                type_issue = CapabilityIssue(
                    "PATH_TYPE", "managed source entries must be regular files"
                )
                continue
            per_file_limit = MAX_MANIFEST_BYTES if entry.name == "zana.yaml" else resolved_max_bytes
            if info.st_size < 0 or info.st_size > per_file_limit:
                bytes_issue = CapabilityIssue(
                    "SOURCE_TREE_LIMIT",
                    f"managed source file exceeds the {per_file_limit}-byte limit",
                )
                continue
            aggregate += info.st_size
            files.append(entry)
            if aggregate > resolved_max_bytes:
                bytes_issue = CapabilityIssue(
                    "SOURCE_TREE_LIMIT",
                    f"managed source tree exceeds the {resolved_max_bytes}-byte aggregate limit",
                )
                break
        if count_issue is not None or bytes_issue is not None:
            break
    return ValidationPreflight(
        file_count=len(files),
        aggregate_bytes=aggregate,
        files=tuple(files),
        symlink_issue=symlink_issue,
        type_issue=type_issue,
        count_issue=count_issue,
        bytes_issue=bytes_issue,
    )


def sanitize_source_filename(filename: str) -> str:
    """Return a deterministic safe basename, rejecting path-like input."""
    if type(filename) is not str or not filename:
        raise AuthoringError("FILENAME_INVALID", "Source filename must be a non-empty string.")
    if len(filename) > MAX_FILENAME_CHARS:
        raise AuthoringError("FILENAME_TOO_LONG", "Source filename exceeds the length limit.")
    if "\x00" in filename or any(ord(char) < 32 or ord(char) == 127 for char in filename):
        raise AuthoringError(
            "FILENAME_INVALID", "Source filename contains control or NUL characters."
        )
    if "/" in filename or "\\" in filename or filename in (".", ".."):
        raise AuthoringError(
            "FILENAME_TRAVERSAL", "Source filename must be a single safe file name."
        )
    if filename != filename.strip() or not _FILENAME_CHARSET.fullmatch(filename):
        raise AuthoringError("FILENAME_INVALID", "Source filename uses unsupported characters.")
    return filename


def document_media_type(filename: str) -> str:
    suffix = Path(filename).suffix.lower()
    if suffix not in DOCUMENT_MEDIA_TYPES:
        raise AuthoringError(
            "SOURCE_KIND_UNSUPPORTED",
            "Only approved PDF, Markdown, and TXT documents can be ingested.",
        )
    return DOCUMENT_MEDIA_TYPES[suffix]


def validate_local_source_path(local_path: str, *, max_bytes: int, workspace: Path) -> Path:
    """Validate one explicitly approved absolute source file path."""
    if type(local_path) is not str or not local_path:
        raise AuthoringError("SOURCE_PATH_INVALID", "local_path must be a non-empty string.")
    if len(local_path) > MAX_LOCAL_PATH_CHARS:
        raise AuthoringError("SOURCE_PATH_TOO_LONG", "local_path exceeds the path length limit.")
    if "\x00" in local_path:
        raise AuthoringError("SOURCE_PATH_INVALID", "local_path must not contain NUL.")
    if "\\" in local_path:
        raise AuthoringError("SOURCE_PATH_INVALID", "local_path must use forward-slash separators.")
    path = Path(local_path)
    if not path.is_absolute():
        raise AuthoringError("SOURCE_PATH_RELATIVE", "local_path must be an absolute path.")
    for part in path.parts:
        if part in (".", ".."):
            raise AuthoringError(
                "SOURCE_PATH_TRAVERSAL",
                "local_path must not contain dot or parent segments.",
            )
    normalized = Path(os.path.normpath(str(path)))
    if not normalized.is_absolute():
        raise AuthoringError("SOURCE_PATH_RELATIVE", "local_path must be an absolute path.")
    if _is_within(workspace, normalized):
        raise AuthoringError(
            "SOURCE_PATH_WORKSPACE",
            "Approved source file must not live inside the capability workspace.",
        )
    try:
        info = normalized.lstat()
    except OSError as exc:
        raise AuthoringError("SOURCE_PATH_READ", "cannot stat the approved source file") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AuthoringError("SOURCE_PATH_SYMLINK", "Approved source file must not be a symlink.")
    if not stat.S_ISREG(info.st_mode):
        raise AuthoringError("SOURCE_PATH_TYPE", "Approved source file must be a regular file.")
    if info.st_size < 0 or info.st_size > max_bytes:
        raise AuthoringError(
            "SOURCE_TOO_LARGE", f"Approved source file exceeds the {max_bytes}-byte limit."
        )
    return normalized


def new_temp_path(target: Path) -> Path:
    """Unique hidden temp sibling so os.replace stays on one filesystem."""
    validate_target_parent(target)
    return target.parent / f".{target.name}.zana-tmp-{uuid.uuid4().hex}"


def stage_text_content(content: str, temp_target: Path, *, max_bytes: int) -> tuple[str, int]:
    """Hash and stage bounded UTF-8 text; returns (sha256 hex, size bytes)."""
    if "\x00" in content:
        raise AuthoringError("CONTENT_NUL", "Source content must not contain NUL bytes.")
    try:
        encoded = content.encode("utf-8")
    except UnicodeEncodeError:
        raise AuthoringError("CONTENT_UTF8", "Source content is not valid UTF-8.") from None
    if len(encoded) > max_bytes:
        raise AuthoringError(
            "CONTENT_TOO_LARGE", f"Source content exceeds the {max_bytes}-byte limit."
        )
    try:
        with temp_target.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError as exc:
        raise AuthoringError("CONTENT_WRITE", "cannot write source content") from exc
    return hashlib.sha256(encoded).hexdigest(), len(encoded)


def stage_document_copy(source: Path, temp_target: Path, *, max_bytes: int) -> tuple[str, int]:
    """Stream an approved document into the workspace without modifying it."""
    try:
        before = source.lstat()
    except OSError as exc:
        raise AuthoringError("SOURCE_PATH_READ", "cannot stat the approved source file") from exc
    if stat.S_ISLNK(before.st_mode):
        raise AuthoringError("SOURCE_PATH_SYMLINK", "Approved source file must not be a symlink.")
    if not stat.S_ISREG(before.st_mode):
        raise AuthoringError("SOURCE_PATH_TYPE", "Approved source file must be a regular file.")
    expected_size = before.st_size
    expected_identity = (before.st_dev, before.st_ino)
    expected_mtime_ns = before.st_mtime_ns
    digest = hashlib.sha256()
    copied = 0
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = None
    try:
        source_fd = os.open(source, os.O_RDONLY | no_follow)
        with os.fdopen(source_fd, "rb") as src, temp_target.open("wb") as dst:
            source_fd = None
            opened = os.fstat(src.fileno())
            if (
                opened.st_dev,
                opened.st_ino,
            ) != expected_identity or opened.st_size != expected_size:
                raise AuthoringError(
                    "SOURCE_DRIFT", "Approved source file changed before it was copied."
                )
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise AuthoringError(
                        "SOURCE_TOO_LARGE",
                        f"Approved source file exceeds the {max_bytes}-byte limit.",
                    )
                digest.update(chunk)
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
            finished = os.fstat(src.fileno())
            if (
                finished.st_size != expected_size
                or (finished.st_dev, finished.st_ino) != expected_identity
                or finished.st_mtime_ns != expected_mtime_ns
            ):
                raise AuthoringError(
                    "SOURCE_DRIFT", "Approved source file changed while it was being copied."
                )
    except AuthoringError:
        raise
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AuthoringError(
                "SOURCE_DRIFT", "Approved source file changed before it was copied."
            ) from None
        raise AuthoringError("SOURCE_COPY", "cannot copy the approved source file") from exc
    finally:
        if source_fd is not None:
            with contextlib.suppress(OSError):
                os.close(source_fd)
    if copied != expected_size:
        raise AuthoringError("SOURCE_DRIFT", "Approved source file changed while being copied.")
    return digest.hexdigest(), copied


def publish_staged(temp_path: Path, target: Path) -> None:
    """Atomically publish a staged regular file, refusing symlink targets."""
    validate_target_parent(target)
    try:
        if target.is_symlink():
            raise AuthoringError("TARGET_SYMLINK", "Source target must not be a symlink.")
        if target.exists():
            info = target.lstat()
            if not stat.S_ISREG(info.st_mode):
                raise AuthoringError(
                    "TARGET_TYPE", "Source target exists and is not a regular file."
                )
        os.replace(temp_path, target)
        _chmod_regular_no_follow(target)
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError("SOURCE_PUBLISH", "cannot publish source file") from exc


def _chmod_regular_no_follow(path: Path) -> None:
    """chmod a regular target without following a swapped symlink."""
    if os.name != "posix":
        return
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    fd = None
    try:
        fd = os.open(path, os.O_RDONLY | no_follow)
        info = os.fstat(fd)
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
            raise AuthoringError("TARGET_TYPE", "Source target is not a regular file.")
        os.fchmod(fd, 0o600)
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError("SOURCE_CHMOD", "cannot secure a published source file") from exc
    finally:
        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)


def stage_backup(target: Path) -> Path | None:
    """Descriptor-bounded copy of an existing regular target to a temp sibling."""
    validate_target_parent(target)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return None
    except OSError as exc:
        raise AuthoringError("SOURCE_BACKUP", "cannot inspect existing source") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AuthoringError("TARGET_SYMLINK", "Source target must not be a symlink.")
    if not stat.S_ISREG(info.st_mode):
        raise AuthoringError("TARGET_TYPE", "Existing source target is not a regular file.")
    max_bytes = backup_max_for_target(target)
    backup = new_temp_path(target.parent / f"{target.name}.bak")
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    source_fd = None
    try:
        if info.st_size > max_bytes:
            raise AuthoringError(
                "SOURCE_TOO_LARGE",
                f"Existing source target exceeds the {max_bytes}-byte backup limit.",
            )
        source_fd = os.open(target, os.O_RDONLY | no_follow)
        with os.fdopen(source_fd, "rb") as src, backup.open("xb") as dst:
            source_fd = None
            opened = os.fstat(src.fileno())
            if (
                (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)
                or opened.st_size != info.st_size
                or opened.st_mtime_ns != info.st_mtime_ns
            ):
                raise AuthoringError(
                    "SOURCE_DRIFT", "Existing source target changed before backup."
                )
            copied = 0
            while True:
                chunk = src.read(65536)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > max_bytes:
                    raise AuthoringError(
                        "SOURCE_TOO_LARGE",
                        f"Existing source target exceeds the {max_bytes}-byte backup limit.",
                    )
                dst.write(chunk)
            dst.flush()
            os.fsync(dst.fileno())
            finished = os.fstat(src.fileno())
            if (
                finished.st_size != info.st_size
                or (finished.st_dev, finished.st_ino) != (info.st_dev, info.st_ino)
                or finished.st_mtime_ns != info.st_mtime_ns
            ):
                raise AuthoringError(
                    "SOURCE_DRIFT", "Existing source target changed during backup."
                )
        if copied != info.st_size:
            raise AuthoringError("SOURCE_DRIFT", "Existing source target changed during backup.")
        _chmod_regular_no_follow(backup)
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AuthoringError("TARGET_SYMLINK", "Source target must not be a symlink") from None
        _unlink(backup)
        raise AuthoringError("SOURCE_BACKUP", "cannot stage source rollback") from exc
    except AuthoringError:
        _unlink(backup)
        raise
    finally:
        if source_fd is not None:
            with contextlib.suppress(OSError):
                os.close(source_fd)
    return backup


def backup_max_for_target(target: Path) -> int:
    """Explicit correct byte cap for one managed target backup."""
    name = target.name
    if target.parent.name == "behavior" or name == "system.md":
        return MAX_BEHAVIOR_BYTES
    if name in ("domain.jsonl", "regression.jsonl") or target.parent.name == "evals":
        return MAX_EVAL_BYTES
    if target.parent.name == "sources":
        return MAX_DOCUMENT_BYTES
    if name == "zana.yaml":
        return MAX_MANIFEST_BYTES
    raise AuthoringError(
        "SOURCE_KIND_UNSUPPORTED", "cannot determine a backup limit for this source target"
    )


def restore_backup(backup: Path, target: Path) -> None:
    """Atomically restore a previously staged target, failing when unconfirmed."""
    validate_target_parent(target)
    try:
        if not backup.is_file() or backup.is_symlink():
            raise AuthoringError(
                "SOURCE_RESTORE", "staged rollback file is missing or not a regular file"
            )
        os.replace(backup, target)
        _chmod_regular_no_follow(target)
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError("SOURCE_RESTORE", "cannot restore the prior source file") from exc
    try:
        info = target.lstat()
    except OSError as exc:
        raise AuthoringError("SOURCE_RESTORE", "cannot confirm the restored source file") from exc
    if stat.S_ISLNK(info.st_mode) or not stat.S_ISREG(info.st_mode):
        raise AuthoringError("SOURCE_RESTORE", "restored source file is not a regular file")


def remove_file_target(target: Path) -> bool:
    """Remove a target newly created by this request; never follow a symlink."""
    validate_target_parent(target)
    try:
        info = target.lstat()
    except FileNotFoundError:
        return True
    except OSError as exc:
        raise AuthoringError("SOURCE_REMOVE", "cannot inspect a newly created source file") from exc
    if stat.S_ISLNK(info.st_mode):
        raise AuthoringError("TARGET_SYMLINK", "Source target must not be a symlink.")
    if not stat.S_ISREG(info.st_mode):
        raise AuthoringError("TARGET_TYPE", "Source target is not a regular file.")
    try:
        target.unlink()
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError("SOURCE_REMOVE", "cannot remove a newly created source file") from exc
    return not target.exists()


def _validate_eval_content(workspace: Path, temp_path: Path, target: Path, eval_kind: str) -> None:
    try:
        load_evaluation_set(workspace, temp_path, EvalKind(eval_kind))
    except CapabilitySourceValidationError as exc:
        canonical = relative_label(workspace, target)
        relabeled = [
            CapabilityIssue(
                issue.code,
                issue.message,
                safe_issue_file(
                    canonical
                    if issue.file is not None and issue.file.endswith(temp_path.name)
                    else issue.file,
                    workspace,
                ),
                issue.line,
            )
            for issue in exc.issues
        ]
        raise AuthoringValidationError(relabeled) from None


def default_manifest(name: str, version: str, capability_id: int) -> dict[str, Any]:
    """Deterministic minimal valid manifest when the caller starts a draft."""
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", name.lower()).strip("-.")
    if not slug:
        slug = f"capability-{capability_id}"
    return {
        "schemaVersion": 1,
        "kind": "ZanaCapability",
        "id": f"zana.local.{slug[:200]}",
        "name": name,
        "version": version,
    }


def update_manifest_for_source(
    manifest: dict[str, Any],
    *,
    manifest_kind: str,
    eval_kind: str | None = None,
) -> dict[str, Any]:
    """Declare one published source in the manifest with canonical paths."""
    updated = copy.deepcopy(manifest)
    if manifest_kind == "behavior":
        updated["behavior"] = {"system": BEHAVIOR_RELATIVE_PATH}
        return updated
    if manifest_kind == "document":
        knowledge = updated.get("knowledge")
        if knowledge is None:
            knowledge = {}
            updated["knowledge"] = knowledge
        if type(knowledge) is not dict:
            raise AuthoringError(
                "MANIFEST_INVALID", "manifest knowledge section must be an object."
            )
        sources = knowledge.get("sources")
        if sources is None:
            sources = []
            knowledge["sources"] = sources
        if type(sources) is not list:
            raise AuthoringError("MANIFEST_INVALID", "manifest knowledge.sources must be a list.")
        entry = {"path": KNOWLEDGE_DIR_RELATIVE}
        if not any(
            type(item) is dict and item.get("path") == KNOWLEDGE_DIR_RELATIVE for item in sources
        ):
            sources.append(entry)
        return updated
    if manifest_kind == "evaluation":
        if eval_kind not in EVAL_RELATIVE_PATHS:
            raise AuthoringError(
                "MANIFEST_INVALID", "evaluation kind must be domain or regression."
            )
        evaluation = updated.get("evaluation")
        if evaluation is None:
            evaluation = {}
            updated["evaluation"] = evaluation
        if type(evaluation) is not dict:
            raise AuthoringError(
                "MANIFEST_INVALID", "manifest evaluation section must be an object."
            )
        evaluation[eval_kind] = EVAL_RELATIVE_PATHS[eval_kind]
        return updated
    raise AuthoringError("MANIFEST_INVALID", f"unsupported manifest update kind {manifest_kind!r}")


def serialize_manifest(manifest: dict[str, Any]) -> bytes:
    """Deterministic safe YAML representation of the accepted manifest."""
    if type(manifest) is not dict:
        raise AuthoringError("MANIFEST_INVALID", "manifest must be an object.")
    try:
        text = yaml.safe_dump(
            manifest,
            sort_keys=True,
            allow_unicode=False,
            default_flow_style=False,
            width=1000,
        )
    except (yaml.YAMLError, TypeError, ValueError) as exc:
        raise AuthoringError("MANIFEST_INVALID", "manifest could not be serialized") from exc
    encoded = text.encode("utf-8")
    if len(encoded) > MAX_MANIFEST_BYTES:
        raise AuthoringError(
            "MANIFEST_TOO_LARGE", f"manifest exceeds the {MAX_MANIFEST_BYTES}-byte limit."
        )
    return encoded


def stage_manifest(workspace: Path, manifest: dict[str, Any]) -> Path:
    """Stage a manifest temp that provably round-trips to the same dict."""
    encoded = serialize_manifest(manifest)
    target = workspace / "zana.yaml"
    temp_path = new_temp_path(target)
    try:
        with temp_path.open("wb") as handle:
            handle.write(encoded)
            handle.flush()
            os.fsync(handle.fileno())
        documents = parse_safe_yaml(encoded.decode("utf-8"))
    except Exception as exc:  # noqa: BLE001 - sanitized below
        _unlink(temp_path)
        raise AuthoringError(
            "MANIFEST_INVALID", "manifest could not be serialized deterministically"
        ) from exc
    if len(documents) != 1 or documents[0] != manifest:
        _unlink(temp_path)
        raise AuthoringError(
            "MANIFEST_DIVERGED", "serialized manifest did not round-trip deterministically"
        )
    return temp_path


def write_manifest(workspace: Path, manifest: dict[str, Any]) -> None:
    """Atomically publish zana.yaml from the exact persisted dict."""
    temp_path = stage_manifest(workspace, manifest)
    try:
        publish_staged(temp_path, workspace / "zana.yaml")
    except Exception:
        _unlink(temp_path)
        raise


def remove_manifest(workspace: Path) -> None:
    """Remove zana.yaml so an emptied manifest_json stays coherent on disk."""
    validate_workspace_tree(workspace, require_existing=True)
    target = workspace / "zana.yaml"
    try:
        if target.is_symlink():
            raise AuthoringError("MANIFEST_SYMLINK", "zana.yaml must not be a symlink.")
        if target.exists():
            if not target.is_file():
                raise AuthoringError("MANIFEST_TYPE", "zana.yaml exists and is not a regular file.")
            target.unlink()
    except AuthoringError:
        raise
    except OSError as exc:
        raise AuthoringError("MANIFEST_REMOVE", "cannot remove zana.yaml") from exc


def load_manifest_dict(workspace: Path) -> dict[str, Any] | None:
    """Read the on-disk manifest dict, or None when absent or unparsable."""
    validate_workspace_tree(workspace, require_existing=True)
    path = workspace / "zana.yaml"
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise AuthoringError("MANIFEST_READ", "cannot inspect zana.yaml") from None
    if stat.S_ISLNK(info.st_mode):
        raise AuthoringError("MANIFEST_SYMLINK", "zana.yaml must not be a symlink.")
    if not stat.S_ISREG(info.st_mode):
        raise AuthoringError("MANIFEST_TYPE", "zana.yaml must be a regular file.")
    if info.st_size < 0 or info.st_size > MAX_MANIFEST_BYTES:
        raise AuthoringError(
            "MANIFEST_TOO_LARGE", f"zana.yaml exceeds the {MAX_MANIFEST_BYTES}-byte limit"
        )
    no_follow = getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, os.O_RDONLY | no_follow)
        with os.fdopen(fd, "rb") as handle:
            opened = os.fstat(handle.fileno())
            if stat.S_ISLNK(opened.st_mode) or not stat.S_ISREG(opened.st_mode):
                raise AuthoringError("MANIFEST_TYPE", "zana.yaml must be a regular file")
            raw = handle.read(MAX_MANIFEST_BYTES + 1)
        if len(raw) > MAX_MANIFEST_BYTES:
            raise AuthoringError(
                "MANIFEST_TOO_LARGE", f"zana.yaml exceeds the {MAX_MANIFEST_BYTES}-byte limit"
            )
        text = raw.decode("utf-8")
        documents = parse_safe_yaml(text)
    except UnicodeDecodeError:
        raise AuthoringError("MANIFEST_UTF8", "zana.yaml is not valid UTF-8") from None
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise AuthoringError("MANIFEST_SYMLINK", "zana.yaml must not be a symlink") from None
        return None
    except (yaml.YAMLError, DuplicateKeyError):
        return None
    if len(documents) != 1 or type(documents[0]) is not dict:
        return None
    return documents[0]


def stage_source(workspace: Path, request: SourceRequest) -> StagedSource:
    """Stage one source with bounds checks; nothing is published yet."""
    validate_workspace_tree(workspace, require_existing=True)
    if request.kind == "behavior":
        content = request.content
        if content is None:
            raise AuthoringError("SOURCE_INVALID", "behavior source requires content.")
        target = workspace / BEHAVIOR_RELATIVE_PATH
        temp_path = new_temp_path(target)
        try:
            digest, size = stage_text_content(content, temp_path, max_bytes=MAX_BEHAVIOR_BYTES)
        except Exception:
            _unlink(temp_path)
            raise
        metadata = {
            "kind": "behavior",
            "role": "behavior",
            "ingested_at": datetime.now(UTC).isoformat(),
        }
        return StagedSource(
            temp_path=temp_path,
            target_path=target,
            relative_path=BEHAVIOR_RELATIVE_PATH,
            original_name="system.md",
            sha256=digest,
            size_bytes=size,
            media_type="text/markdown",
            metadata=metadata,
            manifest_kind="behavior",
        )
    if request.kind == "document":
        if not request.user_approved:
            raise AuthoringError(
                "USER_APPROVAL_REQUIRED", "Local document copy requires explicit user approval."
            )
        local_path = request.local_path
        if local_path is None:
            raise AuthoringError("SOURCE_INVALID", "document source requires local_path.")
        source = validate_local_source_path(
            local_path, max_bytes=MAX_DOCUMENT_BYTES, workspace=workspace
        )
        filename = sanitize_source_filename(source.name)
        media_type = document_media_type(filename)
        target = workspace / KNOWLEDGE_DIR_RELATIVE / filename
        temp_path = new_temp_path(target)
        try:
            digest, size = stage_document_copy(source, temp_path, max_bytes=MAX_DOCUMENT_BYTES)
        except Exception:
            _unlink(temp_path)
            raise
        metadata = {
            "kind": "document",
            "role": "knowledge",
            "original_name": filename,
            "ingested_at": datetime.now(UTC).isoformat(),
        }
        return StagedSource(
            temp_path=temp_path,
            target_path=target,
            relative_path=relative_label(workspace, target),
            original_name=filename,
            sha256=digest,
            size_bytes=size,
            media_type=media_type,
            metadata=metadata,
            manifest_kind="document",
        )
    if request.kind == "evaluation":
        eval_kind = request.eval_kind
        content = request.content
        if eval_kind is None or content is None:
            raise AuthoringError(
                "SOURCE_INVALID", "evaluation source requires eval_kind and content."
            )
        target = workspace / EVAL_RELATIVE_PATHS[eval_kind]
        temp_path = new_temp_path(target)
        try:
            digest, size = stage_text_content(content, temp_path, max_bytes=MAX_EVAL_BYTES)
            _validate_eval_content(workspace, temp_path, target, eval_kind)
        except Exception:
            _unlink(temp_path)
            raise
        metadata = {
            "kind": "evaluation",
            "role": "evaluation",
            "eval_kind": eval_kind,
            "ingested_at": datetime.now(UTC).isoformat(),
        }
        return StagedSource(
            temp_path=temp_path,
            target_path=target,
            relative_path=EVAL_RELATIVE_PATHS[eval_kind],
            original_name=f"{eval_kind}.jsonl",
            sha256=digest,
            size_bytes=size,
            media_type="application/x-ndjson",
            metadata=metadata,
            manifest_kind="evaluation",
            eval_kind=eval_kind,
        )
    raise AuthoringError("SOURCE_KIND_UNSUPPORTED", f"unsupported source kind {request.kind!r}")


def safe_issue_file(value: str | None, workspace: Path) -> str | None:
    """Return a workspace-relative label or None; never an arbitrary host path."""
    if value is None:
        return None
    if not value or value.startswith("/") or "\\" in value or value.startswith("~"):
        return None
    normalized = value.replace("\\", "/")
    parts = normalized.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return None
    candidate = workspace.joinpath(*parts)
    try:
        resolved = candidate.resolve(strict=False)
        root = workspace.resolve(strict=False)
    except OSError:
        return None
    if not _is_within(root, resolved):
        return None
    return normalized


def sanitize_message(message: str, workspace: Path, data_root: Path) -> str:
    """Strip workspace/data-root and arbitrary absolute paths, then bound length."""
    result = message
    try:
        workspace_text = str(workspace)
        data_root_text = str(data_root)
        if workspace_text:
            result = result.replace(workspace_text, "<workspace>")
        if data_root_text:
            result = result.replace(data_root_text, "<data_root>")
    except (OSError, RuntimeError):
        pass
    try:
        resolved_workspace = workspace.resolve(strict=False)
        resolved_data_root = data_root.resolve(strict=False)
        workspace_resolved_text = str(resolved_workspace)
        data_root_resolved_text = str(resolved_data_root)
        if workspace_resolved_text:
            result = result.replace(workspace_resolved_text, "<workspace>")
        if data_root_resolved_text:
            result = result.replace(data_root_resolved_text, "<data_root>")
    except (OSError, RuntimeError):
        pass
    try:
        result = re.sub(r"(?:/[A-Za-z0-9_@.+-]+){2,}", "<path>", result)
        result = re.sub(r"[A-Za-z]:\\[^\\s]+", "<path>", result)
        if len(result) > MAX_MESSAGE_CHARS:
            result = result[:MAX_MESSAGE_CHARS] + "..."
        return result
    except (AttributeError, TypeError, ValueError, OSError, RuntimeError):
        return "Capability operation failed."
