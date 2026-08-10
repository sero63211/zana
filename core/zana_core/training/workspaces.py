"""Atomic private training workspace preparation, staging, and cleanup."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import secrets
import shutil
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

from zana_core.training.contracts import (
    DatasetSplitManifest,
    TrainingRequestConfig,
    normalize_sha256,
)
from zana_core.training.datasets import check_split_isolation

WORKSPACE_PREFIX = "zana-training-"
WORKSPACE_OWNER_FILE = ".zana-training-owner"
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


class WorkspacePreparationError(RuntimeError):
    """Typed workspace construction failure with an optional retry attestation."""

    def __init__(self, message: str, attestation: WorkspaceAttestation | None = None) -> None:
        super().__init__(message)
        self.attestation = attestation


@dataclass(frozen=True, slots=True)
class WorkspaceAttestation:
    """In-memory ownership proof captured at workspace creation."""

    root: Path
    workspace: Path
    run_id: str
    root_dev: int
    root_ino: int
    workspace_dev: int
    workspace_ino: int
    token: str


def validate_run_id(run_id: str) -> str:
    if _RUN_ID_RE.fullmatch(run_id) is None:
        raise ValueError("invalid training run id")
    return run_id


def validate_workspace_root(root: Path) -> Path:
    """Validate an absolute, existing, non-symlink directory workspace root."""
    if not root.is_absolute():
        raise ValueError("workspace root must be an absolute path")
    try:
        st = root.lstat()
    except OSError:
        raise ValueError("workspace root does not exist") from None
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode):
        raise ValueError("workspace root must be a non-symlink directory")
    return root


def sha256_file(path: Path, max_bytes: int = 1 << 30) -> str:
    """Return the SHA-256 digest of a regular non-symlink file, bounded by cap."""
    with _open_regular_no_follow(path, max_bytes=max_bytes) as (fd, st):
        return _read_hashed(fd, st.st_size, max_bytes=max_bytes)


def _open_regular_no_follow(path: Path, *, max_bytes: int):
    """Open a regular file without following symlinks and return fd plus fstat."""
    if not path.is_absolute():
        raise ValueError("dataset path must be an absolute approved local path")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        raise ValueError("dataset file could not be opened as a regular file") from None
    try:
        st = os.fstat(fd)
    except OSError:
        os.close(fd)
        raise ValueError("dataset file could not be inspected") from None
    if not stat.S_ISREG(st.st_mode):
        os.close(fd)
        raise ValueError("dataset file must be a regular file, not a symlink")
    if st.st_size > max_bytes:
        os.close(fd)
        raise ValueError("dataset file exceeds the size cap")
    return _ManagedFd(fd, st)


class _ManagedFd:
    """Small RAII wrapper so callers cannot forget the descriptor."""

    __slots__ = ("fd", "st")

    def __init__(self, fd: int, st: os.stat_result) -> None:
        self.fd = fd
        self.st = st

    def __enter__(self) -> tuple[int, os.stat_result]:
        return self.fd, self.st

    def __exit__(self, exc_type, exc, tb) -> None:
        with contextlib.suppress(OSError):
            os.close(self.fd)


def _read_hashed(fd: int, expected_size: int, *, max_bytes: int) -> str:
    """Read exactly expected_size bytes and hash them; fail on drift/growth."""
    if expected_size < 0 or expected_size > max_bytes:
        raise ValueError("dataset file size exceeds the size cap")
    digest = hashlib.sha256()
    remaining = expected_size
    while remaining:
        chunk = os.read(fd, min(65536, remaining))
        if not chunk:
            raise ValueError("dataset file shrank while it was read")
        digest.update(chunk)
        remaining -= len(chunk)
    extra = os.read(fd, 1)
    if extra:
        raise ValueError("dataset file grew while it was read")
    return digest.hexdigest()


def _verify_split(
    manifest: DatasetSplitManifest,
    *,
    max_file_bytes: int,
) -> None:
    role = manifest.role
    with _open_regular_no_follow(manifest.path, max_bytes=max_file_bytes) as (fd, st):
        if st.st_size != manifest.size_bytes:
            raise ValueError(f"{role} file size does not match the manifest")
        actual = _read_hashed(fd, st.st_size, max_bytes=max_file_bytes)
    if actual.lower() != normalize_sha256(manifest.sha256):
        raise ValueError(f"{role} file sha256 does not match the manifest")


def _stage_split(
    manifest: DatasetSplitManifest,
    destination: Path,
    *,
    max_file_bytes: int,
) -> str:
    """Copy one verified regular file to a private destination and re-verify it."""
    role = manifest.role
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        out_fd = os.open(destination, flags, 0o600)
    except OSError:
        raise ValueError(f"{role} staged file could not be created") from None
    try:
        digest = hashlib.sha256()
        with _open_regular_no_follow(manifest.path, max_bytes=max_file_bytes) as (fd, st):
            if st.st_size != manifest.size_bytes:
                raise ValueError(f"{role} file size does not match the manifest")
            remaining = st.st_size
            while remaining:
                chunk = os.read(fd, min(65536, remaining))
                if not chunk:
                    raise ValueError(f"{role} file shrank while it was read")
                written = 0
                while written < len(chunk):
                    written += os.write(out_fd, chunk[written:])
                digest.update(chunk)
                remaining -= len(chunk)
            extra = os.read(fd, 1)
            if extra:
                raise ValueError(f"{role} file grew while it was read")
        os.fsync(out_fd)
    except ValueError:
        raise
    except OSError:
        raise ValueError(f"{role} staged file could not be written") from None
    finally:
        with contextlib.suppress(OSError):
            os.close(out_fd)
    if digest.hexdigest() != normalize_sha256(manifest.sha256):
        raise ValueError(f"{role} file sha256 does not match the manifest")
    staged_digest = sha256_file(destination, max_bytes=max_file_bytes)
    staged_size = destination.stat().st_size
    if staged_digest != normalize_sha256(manifest.sha256) or staged_size != manifest.size_bytes:
        raise ValueError(f"{role} staged file drifted from the verified manifest")
    return staged_digest


def _ensure_owner_marker(workspace: Path, run_id: str, token: str) -> None:
    """Write and verify the run-id/token ownership marker inside the workspace."""
    marker = workspace / WORKSPACE_OWNER_FILE
    payload = f"{run_id}\n{token}\n".encode("ascii")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    marker_fd = os.open(marker, flags, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(marker_fd, view)
            if written == 0:
                raise OSError("ownership marker zero write")
            view = view[written:]
        os.fsync(marker_fd)
    finally:
        with contextlib.suppress(OSError):
            os.close(marker_fd)
    read_fd = os.open(marker, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        actual = bytearray()
        while True:
            chunk = os.read(read_fd, 4096)
            if not chunk:
                break
            actual.extend(chunk)
            if len(actual) > len(payload):
                raise OSError("ownership marker verification failed")
    finally:
        with contextlib.suppress(OSError):
            os.close(read_fd)
    if bytes(actual) != payload:
        raise OSError("ownership marker verification failed")


_KNOWN_WORKSPACE_DIRS = ("data", "logs", "out", "home", "tmp", "cache")


def _matches_captured_workspace(
    root: Path,
    root_st: os.stat_result,
    workspace: Path,
    workspace_st: os.stat_result,
    run_id: str,
) -> os.stat_result | None:
    """Return the workspace stat only when root/workspace still match capture."""
    try:
        current_root = root.lstat()
        current_ws = workspace.lstat()
    except OSError:
        return None
    if (
        current_root.st_dev != root_st.st_dev
        or current_root.st_ino != root_st.st_ino
        or not stat.S_ISDIR(current_root.st_mode)
        or stat.S_ISLNK(current_root.st_mode)
        or workspace_st.st_dev != current_ws.st_dev
        or workspace_st.st_ino != current_ws.st_ino
        or not stat.S_ISDIR(current_ws.st_mode)
        or stat.S_ISLNK(current_ws.st_mode)
    ):
        return None
    if not workspace.is_absolute() or workspace.parent != root:
        return None
    if not workspace.name.startswith(f"{WORKSPACE_PREFIX}{run_id}-"):
        return None
    return current_ws


def _safe_fresh_cleanup(
    root: Path,
    root_st: os.stat_result,
    workspace: Path,
    workspace_st: os.stat_result,
    run_id: str,
) -> bool:
    """Remove a freshly created, partially built workspace without recursion.

    Runs only on construction failure. The workspace is seconds old and contains
    at most the ownership marker plus the known empty construction directories.
    The root and workspace identities are the ones captured immediately after
    mkdtemp, so this proves the exact path belongs to this request before any
    entry is touched. Entries are enumerated: unexpected entries are rejected,
    missing expected directories are allowed, only existing real empty known
    directories and an absent/partial regular owner marker are removed, then the
    exact workspace is rmdir'd. Returns False only when removal cannot be proven
    safe, in which case the caller must retain a usable attestation.
    """
    if _matches_captured_workspace(root, root_st, workspace, workspace_st, run_id) is None:
        return False
    try:
        entries = os.listdir(workspace)
    except OSError:
        return False
    for name in entries:
        path = workspace / name
        if name in _KNOWN_WORKSPACE_DIRS:
            try:
                st = path.lstat()
            except OSError:
                return False
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
                return False
            try:
                child_entries = os.listdir(path)
            except OSError:
                return False
            if child_entries:
                return False
            try:
                path.rmdir()
            except OSError:
                return False
            continue
        if name == WORKSPACE_OWNER_FILE:
            try:
                st = path.lstat()
            except OSError:
                return False
            if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
                return False
            try:
                path.unlink()
            except OSError:
                return False
            continue
        return False
    try:
        workspace.rmdir()
    except OSError:
        return False
    return True


def _build_retry_attestation(
    root: Path,
    root_st: os.stat_result,
    workspace: Path,
    workspace_st: os.stat_result | None,
    run_id: str,
    token: str,
) -> WorkspaceAttestation | None:
    """Return an attestation only when the exact captured workspace is in place."""
    if workspace_st is None:
        return None
    if _matches_captured_workspace(root, root_st, workspace, workspace_st, run_id) is None:
        return None
    try:
        _ensure_owner_marker(workspace, run_id, token)
    except Exception:
        return None
    current_ws = _matches_captured_workspace(
        root,
        root_st,
        workspace,
        workspace_st,
        run_id,
    )
    if current_ws is None:
        return None
    return WorkspaceAttestation(
        root=root,
        workspace=workspace,
        run_id=run_id,
        root_dev=root_st.st_dev,
        root_ino=root_st.st_ino,
        workspace_dev=current_ws.st_dev,
        workspace_ino=current_ws.st_ino,
        token=token,
    )


def prepare_training_workspace(root: Path, run_id: str) -> WorkspaceAttestation:
    """Create one attested private workspace with data/log/out/home/tmp/cache.

    The ownership token and marker are established immediately after the
    private directory is created. If later construction fails, either the exact
    new workspace is proven removed (the original exception is re-raised with no
    attestation because nothing remains to clean) or ``WorkspacePreparationError``
    carries an attestation that passes ``cleanup_training_workspace`` verification.
    """
    root = validate_workspace_root(root)
    validate_run_id(run_id)
    root_st = root.lstat()
    token = secrets.token_hex(16)
    workspace: Path | None = None
    workspace_st: os.stat_result | None = None
    try:
        workspace = Path(tempfile.mkdtemp(prefix=f"{WORKSPACE_PREFIX}{run_id}-", dir=root))
        workspace_st = workspace.lstat()
        os.chmod(workspace, 0o700)
        _ensure_owner_marker(workspace, run_id, token)
        for name in ("data", "logs", "out", "home", "tmp", "cache"):
            (workspace / name).mkdir(mode=0o700)
        ws_st = workspace.lstat()
        return WorkspaceAttestation(
            root=root,
            workspace=workspace,
            run_id=run_id,
            root_dev=root_st.st_dev,
            root_ino=root_st.st_ino,
            workspace_dev=ws_st.st_dev,
            workspace_ino=ws_st.st_ino,
            token=token,
        )
    except Exception:
        if workspace is None:
            raise
        if workspace_st is not None and _safe_fresh_cleanup(
            root,
            root_st,
            workspace,
            workspace_st,
            run_id,
        ):
            raise
        raise WorkspacePreparationError(
            "workspace construction failed and cleanup could not be confirmed",
            _build_retry_attestation(
                root,
                root_st,
                workspace,
                workspace_st,
                run_id,
                token,
            ),
        ) from None


@dataclass(frozen=True, slots=True)
class StagedTrainingData:
    """Verified staged training inputs inside a private workspace."""

    workspace: Path
    data_dir: Path
    train_path: Path
    valid_path: Path | None
    train_sha256: str
    valid_sha256: str | None


def stage_training_data(
    workspace: Path,
    config: TrainingRequestConfig,
    *,
    max_file_bytes: int,
) -> StagedTrainingData:
    """Verify and stage exactly train.jsonl plus optional valid.jsonl.

    The held-out evaluation split is verified for integrity but is never copied
    into the private workspace and never appears in training argv/config.
    """
    isolation = check_split_isolation(
        config.train_split,
        config.validation_split,
        config.eval_split,
    )
    if not isolation.ok:
        raise ValueError("train/validation/evaluation splits must be disjoint")
    _verify_split(config.train_split, max_file_bytes=max_file_bytes)
    if config.validation_split is not None:
        _verify_split(config.validation_split, max_file_bytes=max_file_bytes)
    if config.eval_split is not None:
        _verify_split(config.eval_split, max_file_bytes=max_file_bytes)

    data_dir = workspace / "data"
    train_path = data_dir / "train.jsonl"
    train_sha256 = _stage_split(config.train_split, train_path, max_file_bytes=max_file_bytes)
    valid_path: Path | None = None
    valid_sha256: str | None = None
    if config.validation_split is not None:
        valid_path = data_dir / "valid.jsonl"
        valid_sha256 = _stage_split(
            config.validation_split,
            valid_path,
            max_file_bytes=max_file_bytes,
        )
    return StagedTrainingData(
        workspace=workspace,
        data_dir=data_dir,
        train_path=train_path,
        valid_path=valid_path,
        train_sha256=train_sha256,
        valid_sha256=valid_sha256,
    )


def cleanup_training_workspace(attestation: WorkspaceAttestation) -> None:
    """Remove only the exact workspace proven by the in-memory attestation."""
    root = validate_workspace_root(attestation.root)
    root_st = root.lstat()
    if root_st.st_dev != attestation.root_dev or root_st.st_ino != attestation.root_ino:
        raise ValueError("workspace root was replaced since it was attested")
    workspace = attestation.workspace
    if not workspace.is_absolute():
        raise ValueError("workspace cleanup requires an absolute workspace path")
    if workspace.parent != root:
        raise ValueError("refusing to clean a path outside the owned workspace root")
    if not workspace.name.startswith(f"{WORKSPACE_PREFIX}{attestation.run_id}-"):
        raise ValueError("refusing to clean a path with an unexpected workspace name")
    try:
        ws_st = workspace.lstat()
    except OSError:
        raise ValueError("workspace does not exist") from None
    if stat.S_ISLNK(ws_st.st_mode) or not stat.S_ISDIR(ws_st.st_mode):
        raise ValueError("refusing to clean a symlink or non-directory workspace")
    if ws_st.st_dev != attestation.workspace_dev or ws_st.st_ino != attestation.workspace_ino:
        raise ValueError("workspace was replaced since it was attested")
    marker = workspace / WORKSPACE_OWNER_FILE
    try:
        marker_flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC | os.O_NONBLOCK
        marker_fd = os.open(marker, marker_flags)
    except OSError:
        raise ValueError("workspace ownership marker is missing") from None
    try:
        marker_st = os.fstat(marker_fd)
        if not stat.S_ISREG(marker_st.st_mode):
            raise ValueError("workspace ownership marker must be a regular file")
        if marker_st.st_size <= 0 or marker_st.st_size > 4096:
            raise ValueError("workspace ownership marker is oversized")
        marker_bytes = bytearray()
        while True:
            chunk = os.read(marker_fd, 4096)
            if not chunk:
                break
            marker_bytes.extend(chunk)
            if len(marker_bytes) > 4096:
                raise ValueError("workspace ownership marker is oversized")
    finally:
        with contextlib.suppress(OSError):
            os.close(marker_fd)
    lines = bytes(marker_bytes).decode("ascii").splitlines()
    if len(lines) != 2 or lines[0] != attestation.run_id or lines[1] != attestation.token:
        raise ValueError("workspace ownership marker does not match the attestation")
    shutil.rmtree(workspace)
