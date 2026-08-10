"""Real, injected MLX-LM training executor boundary.

This module owns the only code path that can spawn a training process. Tests
inject a fake ``ProcessBoundary`` and never spawn MLX.
"""

from __future__ import annotations

import contextlib
import os
import re
import signal
import stat
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from zana_core.training.adapters import validate_adapter
from zana_core.training.contracts import (
    MAX_ADAPTER_CAP,
    MAX_DEADLINE_SECONDS,
    MAX_LOG_CAP,
    MAX_SOURCE_FILE_CAP,
    MAX_TERMINATE_GRACE_SECONDS,
    ExecutionResult,
    ExecutionStatus,
    ProviderProbeStatus,
    ResourceGuard,
    ResourceGuardDecision,
    TrainingRequestConfig,
    validate_finite_positive,
)
from zana_core.training.invocations import build_mlx_lm_invocation
from zana_core.training.providers import MLXLMProviderProbe
from zana_core.training.workspaces import (
    WorkspaceAttestation,
    WorkspacePreparationError,
    cleanup_training_workspace,
    prepare_training_workspace,
    stage_training_data,
    validate_run_id,
    validate_workspace_root,
)

_POSIX = os.name == "posix"

ExecutableResolver = Callable[[str], str | None]
CancelCheck = Callable[[], bool]

_MANDATORY_RESOURCES = ("ram", "vram", "disk", "dry_run")
_TOKEN_RE = re.compile(
    r"(?i)(token|api[_-]?key|secret|password|authorization)\s*[:=]\s*[^\s,;]+"
    r"|\b(sk-[A-Za-z0-9_\-]+|hf_[A-Za-z0-9_\-]+)\b"
    r"|bearer\s+[A-Za-z0-9._\-]+"
)
_POSIX_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_/-])"
    r"(?:/[^\s/:]+(?: (?:[^\s/:]+ )*[^\s/:]+(?=[/\\]|$))?)+"
)
_WINDOWS_ABSOLUTE_PATH_RE = re.compile(
    r"(?<![A-Za-z0-9_])(?:[A-Za-z]:\\(?:[^\\\s:]+(?: [^\\\s:]+)*\\)*[^\\\s:]+"
    r"(?: [^\\\s:]+)*)"
)
_GENERIC_ABSOLUTE_PATH_RE = re.compile(
    f"(?:{_POSIX_ABSOLUTE_PATH_RE.pattern}|{_WINDOWS_ABSOLUTE_PATH_RE.pattern})"
)


class _TrainingGate:
    """Process-wide single-active-training coordinator."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active: str | None = None

    def acquire(self, run_id: str) -> bool:
        with self._lock:
            if self._active is not None:
                return False
            self._active = run_id
            return True

    def release(self, run_id: str) -> None:
        with self._lock:
            if self._active == run_id:
                self._active = None


_TRAINING_GATE = _TrainingGate()


@dataclass(frozen=True, slots=True)
class ProcessResult:
    """Bounded result from a process boundary; never includes raw secrets."""

    exit_code: int | None = None
    cancelled: bool = False
    timed_out: bool = False
    terminated: bool = False
    killed: bool = False
    error: str | None = None


class ProcessBoundary(Protocol):
    """Injected process runner so tests never spawn real providers."""

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        stdout_path: Path,
        stderr_path: Path,
        deadline_seconds: float,
        terminate_grace_seconds: float,
        cancel: CancelCheck,
    ) -> ProcessResult: ...


def _drain(pipe: Any, sink_fd: int, cap: int) -> None:
    """Read a pipe until EOF, writing at most ``cap`` bytes to an owned fd.

    The boundary owns the pipe and sink fd and closes them exactly once after
    all started drains have been joined; this function only consumes bytes.
    """
    if pipe is None:
        return
    total = 0
    try:
        while True:
            chunk = pipe.read(65536)
            if not chunk:
                return
            if total < cap:
                room = cap - total
                written = 0
                while written < len(chunk[:room]):
                    n = os.write(sink_fd, chunk[written:room])
                    if n == 0:
                        raise OSError("log sink zero write")
                    written += n
                total += written
    except Exception:
        return


class _OwnedResources:
    """Tracks boundary-owned fds, pipes, and request-created sink paths."""

    __slots__ = ("_fds", "_pipes", "_paths")

    def __init__(self) -> None:
        self._fds: set[int] = set()
        self._pipes: dict[int, Any] = {}
        self._paths: dict[Path, tuple[int, int]] = {}

    def add_sink(self, fd: int, path: Path) -> None:
        """Own a sink fd and bind its path to the fd's fstat identity."""
        self._fds.add(fd)
        try:
            st = os.fstat(fd)
        except OSError:
            return
        self._paths[path] = (st.st_dev, st.st_ino)

    def add_pipe(self, pipe: Any) -> None:
        if pipe is not None:
            self._pipes[id(pipe)] = pipe

    def close(self) -> None:
        for fd in list(self._fds):
            with contextlib.suppress(OSError):
                os.close(fd)
        self._fds.clear()
        for pipe in list(self._pipes.values()):
            with contextlib.suppress(OSError, ValueError, AttributeError):
                pipe.close()
        self._pipes.clear()

    def unlink_paths(self) -> None:
        """Remove only log sinks created by this request."""
        for path, (dev, ino) in list(self._paths.items()):
            try:
                st = path.lstat()
            except OSError:
                continue
            if (st.st_dev, st.st_ino) != (dev, ino):
                continue
            with contextlib.suppress(OSError):
                path.unlink()
        self._paths.clear()


def _shutdown_drains(
    owned: _OwnedResources,
    threads: Sequence[threading.Thread],
    started: Sequence[bool],
    join_timeout: float,
) -> bool:
    """Close owned endpoints, boundedly join started drains, and prove none survive."""
    owned.close()
    alive = False
    for thread, was_started in zip(threads, started, strict=True):
        if not was_started:
            continue
        try:
            thread.join(timeout=join_timeout)
        except Exception:
            alive = True
            continue
        try:
            still_alive = thread.is_alive()
        except Exception:
            alive = True
            continue
        alive = alive or still_alive
    return not alive


def _read_bounded(path: Path, cap: int) -> str:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    except OSError:
        return ""
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            return ""
        data = bytearray()
        while len(data) < cap:
            chunk = os.read(fd, min(65536, cap - len(data)))
            if not chunk:
                break
            data.extend(chunk)
        return bytes(data).decode("utf-8", errors="replace")
    except OSError:
        return ""
    finally:
        with contextlib.suppress(OSError):
            os.close(fd)


def _precreate_log_sink(path: Path) -> int:
    """Create an exclusive no-follow private regular-file log sink descriptor."""
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC
    fd = os.open(path, flags, 0o600)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise ValueError("log sink must be a regular file")
    except Exception:
        with contextlib.suppress(OSError):
            os.close(fd)
        raise
    return fd


def _sanitize_text(value: str, limit: int = 4096) -> str:
    cleaned = "".join(char for char in value if char.isprintable() or char in "\n\t")
    encoded = cleaned.encode("utf-8", errors="replace")
    truncated = encoded[:limit]
    while truncated:
        try:
            return truncated.decode("utf-8")
        except UnicodeDecodeError:
            truncated = truncated[:-1]
    return ""


def _redact_secrets(value: str) -> str:
    return _TOKEN_RE.sub("<redacted>", value)


def _sanitize_diagnostic(value: str, limit: int = 4096) -> str:
    """One UTF-8-byte-safe sanitizer for every public boundary/executor error."""
    if not isinstance(value, str):
        value = repr(value)
    redacted = _GENERIC_ABSOLUTE_PATH_RE.sub("<path>", value)
    redacted = _TOKEN_RE.sub("<redacted>", redacted)
    return _sanitize_text(redacted, limit)


def _redact_paths(value: str, paths: Sequence[Path]) -> str:
    """Replace full local paths with basenames so errors stay sanitized."""
    for path in paths:
        for candidate in (path, path.parent):
            if candidate != Path("/"):
                value = value.replace(str(candidate), candidate.name)
    return value


def _sanitized_error(value: str, paths: Sequence[Path], limit: int = 4096) -> str:
    return _sanitize_diagnostic(_redact_paths(value, paths), limit)


def _sanitize_boundary(value: str, limit: int = 256) -> str:
    """Apply the same path/secret sanitizer to boundary diagnostics."""
    return _sanitize_diagnostic(value, limit)


def _bounded_diagnostics(value: str) -> str:
    """Return a bounded, printable, redacted diagnostic string."""
    return _sanitize_diagnostic(value, 4096)


def _validate_resolved_executable(value: str | None) -> str | None:
    """Return a safe resolved executable path or None."""
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute() or path.name != "mlx_lm.lora":
        return None
    try:
        st = path.lstat()
    except OSError:
        return None
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return None
    if not os.access(path, os.X_OK):
        return None
    return str(path)


def _provider_env(workspace: Path) -> dict[str, str]:
    """Minimal offline environment with all caches confined to the workspace."""
    home = workspace / "home"
    tmp = workspace / "tmp"
    cache = workspace / "cache"
    return {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "TMP": str(tmp),
        "TEMP": str(tmp),
        "HF_HOME": str(cache / "hf"),
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_DATASETS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        "HF_HUB_DISABLE_PROGRESS_BARS": "1",
        "DO_NOT_TRACK": "1",
        "PYTHONNOUSERSITE": "1",
    }


class SubprocessBoundary:
    """Real deterministic subprocess boundary with confirmed cleanup."""

    def __init__(self, *, max_log_bytes: int = 1024 * 1024) -> None:
        self.max_log_bytes = int(
            validate_finite_positive(max_log_bytes, "max_log_bytes", MAX_LOG_CAP)
        )
        self._max_log_bytes = self.max_log_bytes

    @staticmethod
    def _group_exists(pgid: int) -> bool:
        if not _POSIX:
            return False
        try:
            os.killpg(pgid, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True

    @staticmethod
    def _wait_parent(proc: Any, timeout: float) -> bool:
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False
        except OSError:
            return False

    @classmethod
    def _wait_for_group_absent(cls, pgid: int | None, timeout: float) -> bool:
        if pgid is None:
            return True
        deadline = time.monotonic() + timeout
        while True:
            if not cls._group_exists(pgid):
                return True
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return not cls._group_exists(pgid)
            time.sleep(min(remaining, 0.02))

    @staticmethod
    def _deliver_signal(pgid: int | None, sig: int, proc: Any) -> bool:
        """Deliver a signal to the captured group, falling back to the parent."""
        if pgid is not None:
            try:
                os.killpg(pgid, sig)
                return True
            except (ProcessLookupError, OSError):
                pass
        try:
            if sig == signal.SIGTERM:
                proc.terminate()
            else:
                proc.kill()
            return True
        except OSError:
            return False

    @classmethod
    def _terminate_then_kill(
        cls,
        proc: Any,
        pgid: int | None,
        grace_seconds: float,
    ) -> tuple[bool, bool, bool]:
        """TERM the group, prove parent+group gone, then KILL if anything remains.

        Parent reaping and group disappearance are checked independently and
        both must be true for cleanup to be confirmed. KILL is attempted even
        when the parent is already reaped because descendants can survive.
        """
        terminated = cls._deliver_signal(pgid, signal.SIGTERM, proc)
        parent_reaped = cls._wait_parent(proc, grace_seconds)
        group_absent = True if pgid is None else not cls._group_exists(pgid)
        killed = False
        if not (parent_reaped and group_absent):
            killed = cls._deliver_signal(pgid, signal.SIGKILL, proc)
            if not parent_reaped:
                parent_reaped = cls._wait_parent(proc, grace_seconds)
            group_absent = cls._wait_for_group_absent(pgid, grace_seconds)
        return terminated, killed, parent_reaped and group_absent

    def run(
        self,
        *,
        argv: Sequence[str],
        cwd: Path,
        env: Mapping[str, str],
        stdout_path: Path,
        stderr_path: Path,
        deadline_seconds: float,
        terminate_grace_seconds: float,
        cancel: CancelCheck,
    ) -> ProcessResult:
        if not argv:
            return ProcessResult(error=_sanitize_boundary("empty argv", 256))
        validate_finite_positive(deadline_seconds, "deadline_seconds", MAX_DEADLINE_SECONDS)
        validate_finite_positive(
            terminate_grace_seconds,
            "terminate_grace_seconds",
            MAX_TERMINATE_GRACE_SECONDS,
        )
        owned = _OwnedResources()
        threads: list[threading.Thread] = []
        started: list[bool] = []
        proc: Any = None
        pgid: int | None = None

        def setup_failure(error: str) -> ProcessResult:
            confirmed = True
            if proc is not None:
                _, _, confirmed = self._terminate_then_kill(
                    proc,
                    pgid,
                    terminate_grace_seconds,
                )
            drains_gone = _shutdown_drains(
                owned,
                threads,
                started,
                terminate_grace_seconds,
            )
            confirmed = confirmed and drains_gone
            owned.unlink_paths()
            if not confirmed:
                return ProcessResult(
                    error=_sanitize_boundary(
                        f"{error}; cleanup could not be confirmed",
                        256,
                    )
                )
            return ProcessResult(error=_sanitize_boundary(error, 256))

        try:
            stdout_fd = _precreate_log_sink(stdout_path)
        except Exception as error:
            return ProcessResult(error=_sanitize_boundary(f"log sink failed: {error}", 256))
        owned.add_sink(stdout_fd, Path(stdout_path))
        try:
            stderr_fd = _precreate_log_sink(stderr_path)
        except Exception as error:
            owned.close()
            owned.unlink_paths()
            return ProcessResult(error=_sanitize_boundary(f"log sink failed: {error}", 256))
        owned.add_sink(stderr_fd, Path(stderr_path))

        try:
            try:
                proc = subprocess.Popen(
                    list(argv),
                    cwd=str(cwd),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    start_new_session=True,
                    env=dict(env),
                )
            except Exception as error:
                owned.close()
                owned.unlink_paths()
                return ProcessResult(error=_sanitize_boundary(f"spawn failed: {error}", 256))
            owned.add_pipe(proc.stdout)
            owned.add_pipe(proc.stderr)
            try:
                pgid = os.getpgid(proc.pid) if _POSIX else None
            except Exception as error:
                if _POSIX:
                    pgid = proc.pid
                return setup_failure(f"process group capture failed: {error}")
            threads = [
                threading.Thread(
                    target=_drain,
                    args=(proc.stdout, stdout_fd, self.max_log_bytes),
                    name="zana-stdout-drain",
                    daemon=True,
                ),
                threading.Thread(
                    target=_drain,
                    args=(proc.stderr, stderr_fd, self.max_log_bytes),
                    name="zana-stderr-drain",
                    daemon=True,
                ),
            ]
            started = [False, False]
            try:
                threads[0].start()
                started[0] = True
            except Exception as error:
                return setup_failure(f"log drain start failed: {error}")
            try:
                threads[1].start()
                started[1] = True
            except Exception as error:
                return setup_failure(f"log drain start failed: {error}")
        except Exception as error:
            return setup_failure(f"boundary setup failed: {error}")

        exit_code: int | None = None
        timed_out = False
        cancelled = False
        terminated = False
        killed = False
        boundary_error: str | None = None
        deadline = time.monotonic() + deadline_seconds
        try:
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    timed_out = True
                    break
                try:
                    if cancel():
                        cancelled = True
                        break
                except Exception as error:
                    boundary_error = _sanitize_boundary(
                        f"cancel callback failed: {error}",
                        256,
                    )
                    cancelled = True
                    break
                try:
                    exit_code = proc.wait(timeout=min(remaining, 0.25))
                    break
                except subprocess.TimeoutExpired:
                    continue
        except OSError as error:
            boundary_error = _sanitize_boundary(f"process wait failed: {error}", 256)

        terminal = timed_out or cancelled or boundary_error is not None
        confirmed = True
        if terminal:
            terminated, killed, confirmed = self._terminate_then_kill(
                proc,
                pgid,
                terminate_grace_seconds,
            )
            if confirmed and exit_code is None:
                try:
                    exit_code = proc.wait(timeout=5.0)
                except subprocess.TimeoutExpired:
                    confirmed = False
                except OSError:
                    confirmed = False
        else:
            terminated = False
            killed = False
        if not terminal and pgid is not None and self._group_exists(pgid):
            terminated, killed, group_confirmed = self._terminate_then_kill(
                proc,
                pgid,
                terminate_grace_seconds,
            )
            confirmed = confirmed and group_confirmed
        drains_gone = _shutdown_drains(owned, threads, started, 5.0)
        confirmed = confirmed and drains_gone
        group_alive = pgid is not None and self._group_exists(pgid)
        confirmed = confirmed and not group_alive
        if not confirmed:
            owned.unlink_paths()
        if not confirmed:
            boundary_error = _sanitize_boundary(
                (
                    f"{boundary_error}; cleanup could not be confirmed"
                    if boundary_error
                    else "process or log cleanup could not be confirmed"
                ),
                256,
            )
        return ProcessResult(
            exit_code=exit_code,
            cancelled=cancelled,
            timed_out=timed_out,
            terminated=terminated,
            killed=killed,
            error=boundary_error,
        )


class TrainingExecutor:
    """Bound the full MLX-LM run: guards, identity, staging, spawn, verification."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        boundary: ProcessBoundary | None = None,
        probe: MLXLMProviderProbe | None = None,
        resolver: ExecutableResolver | None = None,
        max_source_file_bytes: int = MAX_SOURCE_FILE_CAP,
        max_adapter_bytes: int = MAX_ADAPTER_CAP,
        max_log_bytes: int = 1024 * 1024,
        deadline_seconds: float = 3600.0,
        terminate_grace_seconds: float = 5.0,
    ) -> None:
        validate_workspace_root(workspace_root)
        self.workspace_root = workspace_root
        self.boundary = boundary or SubprocessBoundary(max_log_bytes=max_log_bytes)
        self.probe = probe or MLXLMProviderProbe()
        self.resolver = resolver or (
            lambda name: self.probe.resolve_executable() if name == "mlx_lm.lora" else None
        )
        self.max_source_file_bytes = int(
            validate_finite_positive(
                max_source_file_bytes,
                "max_source_file_bytes",
                MAX_SOURCE_FILE_CAP,
            )
        )
        self.max_adapter_bytes = int(
            validate_finite_positive(max_adapter_bytes, "max_adapter_bytes", MAX_ADAPTER_CAP)
        )
        self.max_log_bytes = int(
            validate_finite_positive(max_log_bytes, "max_log_bytes", MAX_LOG_CAP)
        )
        self.deadline_seconds = validate_finite_positive(
            deadline_seconds, "deadline_seconds", MAX_DEADLINE_SECONDS
        )
        self.terminate_grace_seconds = validate_finite_positive(
            terminate_grace_seconds,
            "terminate_grace_seconds",
            MAX_TERMINATE_GRACE_SECONDS,
        )
        self._workspaces: dict[str, WorkspaceAttestation] = {}
        self._lock = threading.Lock()

    def run(
        self,
        config: TrainingRequestConfig,
        guards: Sequence[ResourceGuard],
        *,
        run_id: str | None = None,
        cancel: CancelCheck | None = None,
    ) -> ExecutionResult:
        rid = validate_run_id(run_id or uuid.uuid4().hex[:12])
        if not _TRAINING_GATE.acquire(rid):
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="another training run is active or this run id is already reserved",
            )
        with self._lock:
            if rid in self._workspaces:
                _TRAINING_GATE.release(rid)
                return ExecutionResult(
                    run_id=rid,
                    status=ExecutionStatus.NOT_STARTED,
                    error="another training run is active or this run id is already reserved",
                )
        try:
            return self._run_locked(config, guards, run_id=rid, cancel=cancel)
        finally:
            _TRAINING_GATE.release(rid)

    def _run_locked(
        self,
        config: TrainingRequestConfig,
        guards: Sequence[ResourceGuard],
        *,
        run_id: str,
        cancel: CancelCheck | None,
    ) -> ExecutionResult:
        rid = run_id
        if not self._reserve_run(rid):
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="another training run is active or this run id is already reserved",
            )
        guard_error = self._validate_guards(config, guards)
        if guard_error is not None:
            blocked = tuple(
                guard for guard in guards if guard.decision != ResourceGuardDecision.ALLOW
            )
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                blocked_resources=blocked,
                error=guard_error,
            )
        if config.dry_run_required:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="dry-run training is not supported by this code path",
            )
        try:
            cancelled = cancel is not None and cancel()
        except Exception as error:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error=_sanitized_error(f"cancellation check failed: {error}", [], 512),
            )
        if cancelled:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="training was cancelled before it could start",
            )

        sensitive_paths = [
            config.source.path,
            config.train_split.path,
        ]
        if config.validation_split is not None:
            sensitive_paths.append(config.validation_split.path)
        if config.eval_split is not None:
            sensitive_paths.append(config.eval_split.path)
        identity_error = self._validate_identity(config)
        if identity_error is not None:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error=identity_error,
            )
        try:
            probe_result = self.probe.probe()
        except Exception as error:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error=_sanitized_error(
                    f"provider probe failed: {error}",
                    sensitive_paths,
                    512,
                ),
            )
        if probe_result.status != ProviderProbeStatus.AVAILABLE or not probe_result.version:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="MLX-LM provider is unavailable; training was not started",
            )
        if config.base.provider_version != probe_result.version:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="configured provider version does not match the installed package",
            )
        try:
            probe_executable = self.probe.resolve_executable()
            resolved = self.resolver("mlx_lm.lora")
        except Exception as error:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error=_sanitized_error(
                    f"provider executable probe failed: {error}",
                    sensitive_paths,
                    512,
                ),
            )
        if (
            probe_executable is None
            or resolved is None
            or probe_executable != resolved
            or _validate_resolved_executable(resolved) is None
        ):
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error="mlx_lm.lora is not a validated local executable",
            )

        source_error = self._validate_source_directory(config.source.path)
        if source_error is not None:
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.NOT_STARTED,
                error=source_error,
            )

        attestation: WorkspaceAttestation | None = None
        workspace: Path | None = None
        try:
            attestation = prepare_training_workspace(self.workspace_root, rid)
            workspace = attestation.workspace
            with self._lock:
                self._workspaces[rid] = attestation
            staged = stage_training_data(
                workspace,
                config,
                max_file_bytes=self.max_source_file_bytes,
            )
        except WorkspacePreparationError as error:
            if error.attestation is not None:
                with self._lock:
                    self._workspaces[rid] = error.attestation
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.FAILED,
                error=_sanitized_error(
                    f"training data preparation failed: {error}",
                    sensitive_paths,
                    512,
                ),
            )
        except (OSError, ValueError) as error:
            cleanup_error = None
            if attestation is not None:
                cleanup_error = self._cleanup_workspace(attestation)
            detail = f"training data preparation failed: {error}"
            if cleanup_error:
                detail = f"{detail}; {cleanup_error}"
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.FAILED,
                error=_sanitized_error(detail, sensitive_paths, 512),
            )
        assert workspace is not None
        sensitive_paths.append(workspace)

        adapter_dir = workspace / "out"
        try:
            spec = build_mlx_lm_invocation(
                config,
                data_dir=staged.data_dir,
                adapter_path=adapter_dir,
                provider_version=probe_result.version,
                package_version=probe_result.version,
            )
            stdout_path = workspace / "logs" / "stdout.log"
            stderr_path = workspace / "logs" / "stderr.log"
            try:
                cancelled = cancel is not None and cancel()
            except Exception as error:
                cancelled = True
                cleanup_error = self._cleanup_workspace(attestation)
                detail = f"cancellation check failed: {_sanitized_error(str(error), [], 256)}"
                if cleanup_error:
                    detail = f"{detail}; {cleanup_error}"
                return ExecutionResult(
                    run_id=rid,
                    status=ExecutionStatus.CANCELLED,
                    error=detail,
                )
            if cancelled:
                cleanup_error = self._cleanup_workspace(attestation)
                detail = "training was cancelled after staging; provider was not started"
                if cleanup_error:
                    detail = f"{detail}; {cleanup_error}"
                return ExecutionResult(
                    run_id=rid,
                    status=ExecutionStatus.CANCELLED,
                    error=_sanitized_error(detail, sensitive_paths, 512),
                )
            process = self.boundary.run(
                argv=(resolved,) + spec.args,
                cwd=workspace,
                env=_provider_env(workspace),
                stdout_path=stdout_path,
                stderr_path=stderr_path,
                deadline_seconds=self.deadline_seconds,
                terminate_grace_seconds=self.terminate_grace_seconds,
                cancel=cancel or (lambda: False),
            )
        except Exception as error:
            cleanup_error = self._cleanup_workspace(attestation)
            detail = f"training process could not start: {error}"
            if cleanup_error:
                detail = f"{detail}; {cleanup_error}"
            return ExecutionResult(
                run_id=rid,
                status=ExecutionStatus.FAILED,
                error=_sanitized_error(detail, sensitive_paths, 512),
            )

        result = ExecutionResult(
            run_id=rid,
            status=ExecutionStatus.FAILED,
            exit_code=process.exit_code,
            cancelled=process.cancelled,
            timed_out=process.timed_out,
            terminated=process.terminated,
            killed=process.killed,
        )
        stdout_diag = _bounded_diagnostics(
            _read_bounded(workspace / "logs" / "stdout.log", self.max_log_bytes)
        )
        stderr_diag = _bounded_diagnostics(
            _read_bounded(workspace / "logs" / "stderr.log", self.max_log_bytes)
        )
        result.log_stdout = stdout_diag
        result.log_stderr = stderr_diag
        if process.error is not None:
            result.error = _sanitized_error(
                f"training boundary failed: {process.error}",
                sensitive_paths,
                512,
            )
            cleanup_error = self._cleanup_workspace(attestation)
            if cleanup_error:
                result.error = f"{result.error}; {cleanup_error}"
            return result
        if process.cancelled:
            result.status = ExecutionStatus.CANCELLED
            result.error = "training cancelled; partial output was not promoted"
            cleanup_error = self._cleanup_workspace(attestation)
            if cleanup_error:
                result.error = f"{result.error}; {cleanup_error}"
            return result
        if process.timed_out:
            result.status = ExecutionStatus.TIMED_OUT
            result.error = "training exceeded the bounded deadline; partial output was not promoted"
            cleanup_error = self._cleanup_workspace(attestation)
            if cleanup_error:
                result.error = f"{result.error}; {cleanup_error}"
            return result
        if process.exit_code != 0:
            result.error = _sanitized_error(
                f"training failed: {stderr_diag[-2048:]}",
                sensitive_paths,
                4096,
            )
            cleanup_error = self._cleanup_workspace(attestation)
            if cleanup_error:
                result.error = f"{result.error}; {cleanup_error}"
            return result

        adapter_path = adapter_dir / "adapters.safetensors"
        validation, metadata = validate_adapter(
            path=adapter_path,
            base=config.base,
            provider="mlx_lm",
            dataset_digest=spec.dataset_digest,
            config_digest=spec.config_digest,
            provider_version=probe_result.version,
            seed=config.seed,
            package_version=probe_result.version,
            max_size_bytes=self.max_adapter_bytes,
        )
        if not validation.ok:
            result.adapter_ok = False
            result.adapter_reason = _sanitized_error(validation.reason, sensitive_paths, 512)
            result.error = f"adapter verification failed: {result.adapter_reason}"
            result.log_stdout = stdout_diag
            result.log_stderr = stderr_diag
            cleanup_error = self._cleanup_workspace(attestation)
            if cleanup_error:
                result.error = f"{result.error}; {cleanup_error}"
            return result
        result.status = ExecutionStatus.COMPLETED
        result.adapter_ok = True
        result.adapter_reason = "adapter validated"
        result.adapter_digest = validation.digest
        result.adapter = metadata
        result.adapter_path = "out/adapters.safetensors"
        return result

    def _reserve_run(self, run_id: str) -> bool:
        with self._lock:
            return run_id not in self._workspaces

    def cleanup(self, result: ExecutionResult) -> None:
        """Remove the exact root-owned run workspace for this executor instance."""
        with self._lock:
            attestation = self._workspaces.get(result.run_id)
        if attestation is None:
            return
        try:
            cleanup_training_workspace(attestation)
        except (OSError, ValueError) as error:
            with self._lock:
                self._workspaces[result.run_id] = attestation
            raise RuntimeError("workspace cleanup failed; ownership retained for retry") from error
        with self._lock:
            self._workspaces.pop(result.run_id, None)

    def _cleanup_workspace(self, attestation: WorkspaceAttestation) -> str | None:
        try:
            cleanup_training_workspace(attestation)
            with self._lock:
                self._workspaces.pop(attestation.run_id, None)
        except (OSError, ValueError) as error:
            return _sanitized_error(
                f"workspace cleanup failed: {error}",
                [attestation.workspace],
                256,
            )
        return None

    @staticmethod
    def _validate_guards(
        config: TrainingRequestConfig,
        guards: Sequence[ResourceGuard],
    ) -> str | None:
        guard_list = list(guards)
        if len(guard_list) != len(_MANDATORY_RESOURCES):
            return "exactly ram, vram, disk, and dry_run guards are required"
        seen: set[str] = set()
        for guard in guard_list:
            if guard.resource not in _MANDATORY_RESOURCES:
                return f"unknown resource guard {guard.resource!r}"
            if guard.resource in seen:
                return f"duplicate resource guard {guard.resource!r}"
            seen.add(guard.resource)
            if guard.decision != ResourceGuardDecision.ALLOW:
                return f"resource guard {guard.resource!r} is not ALLOW"
        dry_run = next(guard for guard in guard_list if guard.resource == "dry_run")
        if config.dry_run_required and dry_run.decision == ResourceGuardDecision.ALLOW:
            return "dry-run training is not supported by this code path"
        return None

    @staticmethod
    def _validate_identity(config: TrainingRequestConfig) -> str | None:
        if (
            config.provider != "mlx_lm"
            or config.source.provider != "mlx_lm"
            or config.base.training_source_provider != "mlx_lm"
        ):
            return "training identity requires the mlx_lm provider end to end"
        if (
            config.source.digest != config.base.training_source_digest
            or config.base.training_source_digest != config.base.base_model_digest
        ):
            return "training source and base digests must match exactly"
        if not config.base.provider_version:
            return "provider version is required"
        return None

    @staticmethod
    def _validate_source_directory(path: Path) -> str | None:
        try:
            st = path.lstat()
        except OSError:
            return "local training source directory is missing"
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            return "local training source must be a non-symlink directory"
        return None
