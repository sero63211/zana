"""Training executor tests with an injected process boundary; no MLX spawn."""

from __future__ import annotations

import hashlib
import io
import json
import os
import signal
import struct
import subprocess
import threading
from pathlib import Path

import pytest

from zana_core.training import adapters as adapter_module
from zana_core.training import execution as execution_module
from zana_core.training import workspaces as workspace_module
from zana_core.training.contracts import (
    AdapterBaseIdentity,
    DatasetSplitManifest,
    ExecutionResult,
    ExecutionStatus,
    LocalTrainingSource,
    ResourceGuard,
    ResourceGuardDecision,
    TrainingRequestConfig,
    validate_finite_positive,
)
from zana_core.training.execution import (
    ProcessResult,
    SubprocessBoundary,
    TrainingExecutor,
)
from zana_core.training.providers import MLXLMProviderProbe, ProviderEnvironment
from zana_core.training.workspaces import sha256_file


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def safetensors_bytes() -> bytes:
    header = {
        "weight": {
            "dtype": "F32",
            "shape": [1],
            "data_offsets": [0, 4],
        }
    }
    raw = json.dumps(header, separators=(",", ":")).encode()
    return struct.pack("<Q", len(raw)) + raw + b"\x00\x00\x80\x3f"


def _allow_guards() -> list[ResourceGuard]:
    return [
        ResourceGuard(resource="ram", decision=ResourceGuardDecision.ALLOW, reason="ok"),
        ResourceGuard(resource="vram", decision=ResourceGuardDecision.ALLOW, reason="ok"),
        ResourceGuard(resource="disk", decision=ResourceGuardDecision.ALLOW, reason="ok"),
        ResourceGuard(resource="dry_run", decision=ResourceGuardDecision.ALLOW, reason="ok"),
    ]


def _config(tmp_path: Path) -> TrainingRequestConfig:
    model_dir = tmp_path / "model"
    model_dir.mkdir(exist_ok=True)
    train = tmp_path / "train.jsonl"
    valid = tmp_path / "valid.jsonl"
    evalf = tmp_path / "eval.jsonl"
    train.write_bytes(b'{"id":"train-1"}\n')
    valid.write_bytes(b'{"id":"valid-1"}\n')
    evalf.write_bytes(b'{"id":"eval-1"}\n')

    def manifest(role: str, path: Path, record_id: str) -> DatasetSplitManifest:
        return DatasetSplitManifest(
            role=role,
            path=path,
            sha256=sha256_file(path),
            size_bytes=path.stat().st_size,
            record_ids=(record_id,),
        )

    return TrainingRequestConfig(
        provider="mlx_lm",
        source=LocalTrainingSource(
            source_id="source-1",
            path=model_dir,
            digest=digest("base"),
            provider="mlx_lm",
        ),
        base=AdapterBaseIdentity(
            base_model_digest=digest("base"),
            training_source_digest=digest("base"),
            training_source_provider="mlx_lm",
            provider_version="0.5.0",
        ),
        train_split=manifest("train", train, "train-1"),
        validation_split=manifest("validation", valid, "valid-1"),
        eval_split=manifest("evaluation", evalf, "eval-1"),
        seed=7,
        iters=3,
        batch_size=2,
    )


def _probe(
    *,
    version: str = "0.5.0",
    executable: str = "/private/bin/mlx_lm.lora",
) -> MLXLMProviderProbe:
    return MLXLMProviderProbe(
        ProviderEnvironment(
            module_available=lambda name: object() if name == "mlx_lm" else None,
            version=lambda name: version if name == "mlx-lm" else None,
            which=lambda name: executable if name == "mlx_lm.lora" else None,
            system="Darwin",
            machine="arm64",
        )
    )


def _unavailable_probe() -> MLXLMProviderProbe:
    return MLXLMProviderProbe(
        ProviderEnvironment(
            module_available=lambda name: None,
            version=lambda name: None,
            which=lambda name: None,
            system="Darwin",
            machine="arm64",
        )
    )


def _make_executable(tmp_path: Path) -> Path:
    binary = tmp_path / "bin" / "mlx_lm.lora"
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_text("#!/bin/sh\nexit 0\n")
    binary.chmod(0o755)
    return binary


class FakeBoundary:
    def __init__(
        self,
        result: ProcessResult,
        *,
        adapter_bytes: bytes | None = None,
        stdout_bytes: bytes = b"",
        stderr_bytes: bytes = b"",
    ) -> None:
        self.result = result
        self.adapter_bytes = adapter_bytes
        self.stdout_bytes = stdout_bytes
        self.stderr_bytes = stderr_bytes
        self.calls: list[dict[str, object]] = []
        self.started = 0
        self.stopped = 0

    @property
    def active(self) -> int:
        return self.started - self.stopped

    def run(
        self,
        *,
        argv,
        cwd,
        env,
        stdout_path,
        stderr_path,
        deadline_seconds,
        terminate_grace_seconds,
        cancel,
    ) -> ProcessResult:
        self.started += 1
        self.calls.append(
            {
                "argv": tuple(argv),
                "cwd": Path(cwd),
                "env": dict(env),
                "stdout_path": Path(stdout_path),
                "stderr_path": Path(stderr_path),
                "deadline_seconds": deadline_seconds,
                "terminate_grace_seconds": terminate_grace_seconds,
            }
        )
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stdout_path.write_bytes(self.stdout_bytes)
        stderr_path.write_bytes(self.stderr_bytes)
        if self.adapter_bytes is not None:
            adapter_dir = Path(cwd) / "out"
            adapter_dir.mkdir(parents=True, exist_ok=True)
            (adapter_dir / "adapters.safetensors").write_bytes(self.adapter_bytes)
        self.stopped += 1
        return self.result


def _executor(
    tmp_path: Path,
    boundary: FakeBoundary,
    *,
    max_adapter_bytes: int = 1024 * 1024,
    probe: MLXLMProviderProbe | None = None,
    resolver=None,
) -> TrainingExecutor:
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    executable = _make_executable(tmp_path)
    return TrainingExecutor(
        workspace_root=root,
        boundary=boundary,
        probe=probe or _probe(executable=str(executable)),
        resolver=resolver or (lambda name: str(executable) if name == "mlx_lm.lora" else None),
        max_adapter_bytes=max_adapter_bytes,
        deadline_seconds=30,
    )


def _run_workspaces(root: Path) -> list[Path]:
    if not root.exists():
        return []
    return [path for path in root.iterdir() if path.name.startswith("zana-training-")]


class TestTrainingExecutor:
    def test_success_verifies_adapter_binds_base_and_seed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-1")
        assert result.status == ExecutionStatus.COMPLETED
        assert result.exit_code == 0
        assert result.adapter_ok is True
        assert result.adapter is not None
        assert result.adapter.seed == 7
        assert result.adapter.base_model_digest == digest("base")
        assert result.adapter.training_provider == "mlx_lm"
        assert result.adapter.training_provider_version == "0.5.0"
        assert result.adapter.package_version == "0.5.0"
        assert result.adapter.adapter_digest == hashlib.sha256(safetensors_bytes()).hexdigest()
        assert result.adapter_path == "out/adapters.safetensors"
        dump = result.model_dump()
        assert str(tmp_path) not in str(dump)
        assert boundary.active == 0
        executor.cleanup(result)
        assert _run_workspaces(tmp_path / "runs") == []

    def test_official_argv_uses_staged_data_and_offline_env(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-2")
        assert result.status == ExecutionStatus.COMPLETED
        argv = boundary.calls[0]["argv"]
        assert isinstance(argv, tuple)
        argv_list = list(argv)
        executable = _make_executable(tmp_path)
        assert argv_list[0] == str(executable)
        assert "--model" in argv_list
        assert str(tmp_path / "model") in argv_list
        assert "--train" in argv_list
        assert "--data" in argv_list
        data_dir = argv_list[argv_list.index("--data") + 1]
        assert Path(data_dir).name == "data"
        assert "--adapter-path" in argv_list
        assert argv_list[argv_list.index("--fine-tune-type") + 1] == "lora"
        assert argv_list[argv_list.index("--iters") + 1] == "3"
        assert argv_list[argv_list.index("--seed") + 1] == "7"
        forbidden = {
            "--val-file",
            "--output",
            "--max-steps",
            "--max-tokens",
            "--trust-remote-code",
        }
        assert forbidden.isdisjoint(argv_list)
        assert digest("base") not in argv_list
        assert str(tmp_path / "eval.jsonl") not in argv_list
        data_files = sorted(path.name for path in Path(data_dir).iterdir())
        assert data_files == ["train.jsonl", "valid.jsonl"]
        env = boundary.calls[0]["env"]
        assert isinstance(env, dict)
        workspace = Path(boundary.calls[0]["cwd"])
        assert env["HOME"] == str(workspace / "home")
        assert env["TMPDIR"] == str(workspace / "tmp")
        assert env["HF_HOME"] == str(workspace / "cache" / "hf")
        assert env["HF_HUB_OFFLINE"] == "1"
        assert env["TRANSFORMERS_OFFLINE"] == "1"
        assert env["DO_NOT_TRACK"] == "1"
        assert env["PATH"] == "/usr/bin:/bin"
        executor.cleanup(result)

    def test_dataset_digest_covers_train_plus_validation(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        result = executor.run(config, _allow_guards(), run_id="run-digest")
        expected = hashlib.sha256(
            f"train:{sha256_file(tmp_path / 'train.jsonl')};"
            f"valid:{sha256_file(tmp_path / 'valid.jsonl')}".encode()
        ).hexdigest()
        assert result.adapter is not None
        assert result.adapter.dataset_digest == expected
        executor.cleanup(result)

    def test_empty_guards_block_before_spawn(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), [], run_id="run-empty")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_missing_and_duplicate_guards_block(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        missing = _allow_guards()[:3]
        result = executor.run(_config(tmp_path), missing, run_id="run-missing")
        assert result.status == ExecutionStatus.NOT_STARTED
        duplicate = _allow_guards() + [_allow_guards()[0]]
        result = executor.run(_config(tmp_path), duplicate, run_id="run-duplicate")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_unknown_guard_resource_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        guards = _allow_guards()[:-1] + [
            ResourceGuard(resource="cpu", decision=ResourceGuardDecision.ALLOW, reason="ok")
        ]
        result = executor.run(_config(tmp_path), guards, run_id="run-unknown")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_non_allow_guard_decision_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        guards = _allow_guards()
        guards[0] = ResourceGuard(
            resource="ram",
            decision=ResourceGuardDecision.UNKNOWN,
            reason="unknown",
        )
        result = executor.run(_config(tmp_path), guards, run_id="run-blocked")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert result.blocked_resources
        assert boundary.started == 0

    def test_dry_run_required_never_spawns(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path).model_copy(update={"dry_run_required": True})
        result = executor.run(config, _allow_guards(), run_id="run-dry")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "dry-run" in (result.error or "")
        assert boundary.started == 0

    def test_precancelled_request_never_spawns(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        result = executor.run(
            _config(tmp_path),
            _allow_guards(),
            run_id="run-cancel",
            cancel=lambda: True,
        )
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "cancelled" in (result.error or "")
        assert boundary.started == 0

    def test_unavailable_provider_blocks_before_spawn(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary, probe=_unavailable_probe())
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-unavailable")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "unavailable" in (result.error or "")
        assert boundary.started == 0

    def test_probe_failure_is_sanitized_not_started(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))

        class RaisingProbe:
            def probe(self):
                raise RuntimeError(f"probe exploded at {tmp_path}")

            def resolve_executable(self):
                raise AssertionError("should not be reached")

        executor = _executor(tmp_path, boundary, probe=RaisingProbe())  # type: ignore[arg-type]
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-probe-error")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "probe" in (result.error or "")
        assert str(tmp_path) not in (result.error or "")
        assert boundary.started == 0

    def test_identity_digest_mismatch_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        config = config.model_copy(
            update={"source": config.source.model_copy(update={"digest": digest("other")})}
        )
        result = executor.run(config, _allow_guards(), run_id="run-digest-mismatch")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "digests" in (result.error or "")
        assert boundary.started == 0

    def test_provider_version_mismatch_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        config = config.model_copy(
            update={"base": config.base.model_copy(update={"provider_version": "9.9.9"})}
        )
        result = executor.run(config, _allow_guards(), run_id="run-version-mismatch")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "version" in (result.error or "")
        assert boundary.started == 0

    def test_missing_model_source_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        config = config.model_copy(
            update={"source": config.source.model_copy(update={"path": tmp_path / "gone"})}
        )
        result = executor.run(config, _allow_guards(), run_id="run-missing-model")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "source" in (result.error or "")
        assert boundary.started == 0

    def test_symlink_model_source_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        real = tmp_path / "real-model"
        real.mkdir()
        link = tmp_path / "model-link"
        link.symlink_to(real, target_is_directory=True)
        config = config.model_copy(
            update={"source": config.source.model_copy(update={"path": link})}
        )
        result = executor.run(config, _allow_guards(), run_id="run-symlink-model")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert "non-symlink" in (result.error or "")
        assert boundary.started == 0

    def test_relative_executable_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        probe = _probe(executable="relative/mlx_lm.lora")
        executor = _executor(
            tmp_path,
            boundary,
            probe=probe,
            resolver=lambda name: "relative/mlx_lm.lora" if name == "mlx_lm.lora" else None,
        )
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-relative-exec")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_symlink_executable_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        real = _make_executable(tmp_path)
        link = tmp_path / "bin-link" / "mlx_lm.lora"
        link.parent.mkdir(exist_ok=True)
        link.symlink_to(real)
        executor = _executor(
            tmp_path,
            boundary,
            probe=_probe(executable=str(link)),
            resolver=lambda name: str(link) if name == "mlx_lm.lora" else None,
        )
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-symlink-exec")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_executable_mismatch_with_probe_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(
            tmp_path,
            boundary,
            resolver=lambda name: "/other/bin/mlx_lm.lora" if name == "mlx_lm.lora" else None,
        )
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-exec-mismatch")
        assert result.status == ExecutionStatus.NOT_STARTED
        assert boundary.started == 0

    def test_negative_and_nonfinite_limits_rejected(self, tmp_path: Path) -> None:
        root = tmp_path / "runs"
        root.mkdir(exist_ok=True)
        for kwargs in (
            {"max_source_file_bytes": -1},
            {"max_adapter_bytes": 0},
            {"max_log_bytes": float("nan")},
            {"deadline_seconds": float("inf")},
            {"terminate_grace_seconds": -5},
        ):
            with pytest.raises(ValueError):
                TrainingExecutor(workspace_root=root, **kwargs)
        with pytest.raises(ValueError):
            SubprocessBoundary(max_log_bytes=-1)

    def test_eval_isolation_violation_blocks_before_spawn(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        config = config.model_copy(
            update={"eval_split": config.eval_split.model_copy(update={"record_ids": ("train-1",)})}
        )
        result = executor.run(config, _allow_guards(), run_id="run-eval-leak")
        assert result.status == ExecutionStatus.FAILED
        assert "disjoint" in (result.error or "")
        assert boundary.started == 0
        assert _run_workspaces(tmp_path / "runs") == []

    def test_same_size_replacement_blocks_before_spawn(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        (tmp_path / "train.jsonl").write_bytes(b'{"id":"train-2"}\n')
        result = executor.run(config, _allow_guards(), run_id="run-replacement")
        assert result.status == ExecutionStatus.FAILED
        assert "preparation" in (result.error or "")
        assert boundary.started == 0
        assert _run_workspaces(tmp_path / "runs") == []

    def test_cleanup_failure_is_reported_not_lost(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        (tmp_path / "train.jsonl").write_bytes(b'{"id":"train-2"}\n')

        def broken_cleanup(*args, **kwargs):
            raise OSError("cleanup exploded")

        monkeypatch.setattr(execution_module, "cleanup_training_workspace", broken_cleanup)
        result = executor.run(config, _allow_guards(), run_id="run-cleanup-error")
        assert result.status == ExecutionStatus.FAILED
        assert "preparation" in (result.error or "")
        assert "cleanup" in (result.error or "")
        assert str(tmp_path) not in (result.error or "")
        assert boundary.started == 0

    def test_growing_dataset_blocks_before_spawn(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        config = _config(tmp_path)
        (tmp_path / "train.jsonl").write_bytes(b'{"id":"train-1"}\n' + b"x" * 10)
        result = executor.run(config, _allow_guards(), run_id="run-growth")
        assert result.status == ExecutionStatus.FAILED
        assert boundary.started == 0

    def test_staged_digest_mismatch_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        original = workspace_module.sha256_file

        def wrong_digest(path: Path, max_bytes: int = 1 << 30) -> str:
            actual = original(path, max_bytes)
            return "0" * 64 if path.name == "train.jsonl" else actual

        monkeypatch.setattr(workspace_module, "sha256_file", wrong_digest)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-staged-drift")
        assert result.status == ExecutionStatus.FAILED
        assert "drifted" in (result.error or "")
        assert boundary.started == 0

    def test_boundary_error_with_exit_zero_never_promotes(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0, error="process cleanup could not be confirmed"),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-boundary-error")
        assert result.status == ExecutionStatus.FAILED
        assert result.adapter is None
        assert "boundary" in (result.error or "")
        assert _run_workspaces(tmp_path / "runs") == []

    def test_unconfirmed_kill_fails(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(
                exit_code=None,
                timed_out=True,
                error="cleanup could not be confirmed",
            )
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-unconfirmed")
        assert result.status == ExecutionStatus.FAILED
        assert result.adapter is None

    def test_nonzero_exit_redacts_secrets_and_paths(self, tmp_path: Path) -> None:
        secret_path = str(tmp_path / "train.jsonl")
        secret = "hf_abcd1234token"
        boundary = FakeBoundary(
            ProcessResult(exit_code=1),
            stderr_bytes=(f"{secret_path} token={secret} boom ").encode() * 2000,
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-redact")
        assert result.status == ExecutionStatus.FAILED
        assert result.adapter is None
        assert not hasattr(result, "workspace")
        assert (result.error or "").startswith("training failed")
        assert len(result.error or "") <= 4096
        assert secret_path not in (result.error or "")
        assert secret not in (result.error or "")
        assert secret not in result.log_stderr
        assert secret_path not in result.log_stderr
        assert len(result.log_stderr.encode("utf-8")) <= 64 * 1024
        assert "<path>" in (result.error or "")
        assert "<path>" in result.log_stderr
        dump = str(result.model_dump())
        assert str(tmp_path) not in dump
        assert _run_workspaces(tmp_path / "runs") == []

    def test_cancelled_partial_output_is_removed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=None, cancelled=True, terminated=True),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-cancel-partial")
        assert result.status == ExecutionStatus.CANCELLED
        assert result.cancelled is True
        assert result.adapter is None
        assert _run_workspaces(tmp_path / "runs") == []

    def test_timeout_partial_output_is_removed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=None, timed_out=True, terminated=True, killed=True),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-timeout-partial")
        assert result.status == ExecutionStatus.TIMED_OUT
        assert result.timed_out is True
        assert result.adapter is None
        assert _run_workspaces(tmp_path / "runs") == []

    def test_failed_run_partial_output_is_removed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=1),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-fail-partial")
        assert result.status == ExecutionStatus.FAILED
        assert result.adapter is None
        assert _run_workspaces(tmp_path / "runs") == []

    def test_empty_adapter_fails_closed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0), adapter_bytes=b"")
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-empty-adapter")
        assert result.status == ExecutionStatus.FAILED
        assert result.adapter_ok is False
        assert "empty" in (result.adapter_reason or "")

    def test_garbage_adapter_fails_closed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0), adapter_bytes=b"garbage-bytes")
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-garbage-adapter")
        assert result.status == ExecutionStatus.FAILED
        assert "missing" in (result.adapter_reason or "") or "bounds" in (
            result.adapter_reason or ""
        )

    def test_truncated_adapter_fails_closed(self, tmp_path: Path) -> None:
        raw = safetensors_bytes()
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=raw[: len(raw) - 2],
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-truncated-adapter")
        assert result.status == ExecutionStatus.FAILED
        assert "missing" in (result.adapter_reason or "")

    def test_oversize_adapter_fails_closed(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary, max_adapter_bytes=1)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-oversize-adapter")
        assert result.status == ExecutionStatus.FAILED
        assert "oversized" in (result.adapter_reason or "")

    def test_drifting_adapter_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        monkeypatch.setattr(
            adapter_module,
            "_fstat",
            lambda fd: (tmp_path / "adapter.safetensors").stat(),
        )
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-drift-adapter")
        assert result.status == ExecutionStatus.FAILED
        assert "stably read" in (result.adapter_reason or "")

    def test_cleanup_failure_retains_retry_handle(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-retry-cleanup")
        assert result.status == ExecutionStatus.COMPLETED
        workspace = None
        with executor._lock:
            attestation = executor._workspaces[result.run_id]
            workspace = attestation.workspace
        with pytest.raises(RuntimeError):
            monkeypatch = None
            import zana_core.training.execution as ex

            def broken(*args, **kwargs):
                raise OSError("retry later")

            monkeypatch = pytest.MonkeyPatch()
            monkeypatch.setattr(ex, "cleanup_training_workspace", broken)
            try:
                executor.cleanup(result)
            finally:
                monkeypatch.undo()
        assert workspace is not None and workspace.exists()
        executor.cleanup(result)
        assert workspace.exists() is False

    def test_resources_all_allow_fails_closed_on_empty_and_partial(self, tmp_path: Path) -> None:
        from zana_core.training.resources import ResourceGuards

        assert ResourceGuards.all_allow([]) is False
        assert ResourceGuards.all_allow(_allow_guards()[:3]) is False
        duplicate = _allow_guards() + [_allow_guards()[0]]
        assert ResourceGuards.all_allow(duplicate) is False

    def test_config_rejects_bool_and_infinite_limits(self, tmp_path: Path) -> None:
        config = _config(tmp_path)
        assert config.iters == 3
        with pytest.raises(ValueError):
            validate_finite_positive(True, "limit", 100)
        from zana_core.training.adapters import validate_adapter

        validation, _ = validate_adapter(
            path=tmp_path / "x.safetensors",
            base=config.base,
            provider="mlx_lm",
            dataset_digest=config.train_split.sha256,
            config_digest="0" * 64,
            provider_version="0.5.0",
            seed=7,
            max_size_bytes=True,
        )
        assert validation.ok is False
        assert "positive" in (validation.reason or "")
        bad_values = (
            ("iters", True),
            ("iters", 0),
            ("iters", 2**40),
            ("batch_size", 0),
            ("batch_size", True),
            ("learning_rate", float("inf")),
            ("learning_rate", True),
            ("learning_rate", 0),
            ("max_seq_length", 2**40),
            ("max_seq_length", True),
            ("num_layers", True),
            ("seed", 2**31),
        )
        for field, value in bad_values:
            with pytest.raises(ValueError):
                data = config.model_dump()
                data[field] = value
                TrainingRequestConfig.model_validate(data)
        with pytest.raises(ValueError):
            TrainingRequestConfig.model_validate({**config.model_dump(), "seed": True})
        with pytest.raises(ValueError):
            DatasetSplitManifest(
                role="train",
                path=tmp_path / "train.jsonl",
                sha256=config.train_split.sha256,
                size_bytes=True,
            )

    def test_resource_guards_reject_bool_and_nonfinite(self) -> None:
        from zana_core.training.resources import ResourceGuards

        cases = [
            {
                "available_ram_bytes": float("nan"),
                "available_vram_bytes": 1000,
                "disk_free_bytes": 1000,
                "max_memory_fraction": 0.5,
                "disk_reserve_bytes": 0,
            },
            {
                "available_ram_bytes": 1000,
                "available_vram_bytes": True,
                "disk_free_bytes": 1000,
                "max_memory_fraction": 0.5,
                "disk_reserve_bytes": 0,
            },
            {
                "available_ram_bytes": 1000,
                "available_vram_bytes": 1000,
                "disk_free_bytes": float("inf"),
                "max_memory_fraction": 0.5,
                "disk_reserve_bytes": 0,
            },
            {
                "available_ram_bytes": 1000,
                "available_vram_bytes": 1000,
                "disk_free_bytes": 1000,
                "max_memory_fraction": True,
                "disk_reserve_bytes": 0,
            },
            {
                "available_ram_bytes": 1000,
                "available_vram_bytes": 1000,
                "disk_free_bytes": 1000,
                "max_memory_fraction": 0.5,
                "disk_reserve_bytes": -1,
            },
        ]
        for kwargs in cases:
            with pytest.raises(ValueError):
                ResourceGuards(
                    dry_run_required=False,
                    **kwargs,
                )

    def test_cleanup_unknown_run_is_noop(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)
        result = ExecutionResult(run_id="run-noop", status=ExecutionStatus.NOT_STARTED)
        executor.cleanup(result)
        assert _run_workspaces(tmp_path / "runs") == []

    def test_zero_surviving_fake_processes(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        for index in range(3):
            result = executor.run(_config(tmp_path), _allow_guards(), run_id=f"run-{index}")
            assert result.status == ExecutionStatus.COMPLETED
            executor.cleanup(result)
        assert boundary.started == 3
        assert boundary.active == 0
        assert _run_workspaces(tmp_path / "runs") == []

    def test_single_active_run_blocks_second(self, tmp_path: Path) -> None:
        release = threading.Event()

        class BlockingBoundary:
            started = 0
            started_event = threading.Event()

            def run(self, **kwargs):
                self.started += 1
                self.started_event.set()
                release.wait(timeout=5)
                return ProcessResult(exit_code=0)

        boundary = BlockingBoundary()
        executor = _executor(tmp_path, boundary)  # type: ignore[arg-type]
        results: list[ExecutionResult] = []

        def first():
            results.append(executor.run(_config(tmp_path), _allow_guards(), run_id="run-a"))

        thread = threading.Thread(target=first)
        thread.start()
        boundary.started_event.wait(timeout=5)
        second = executor.run(_config(tmp_path), _allow_guards(), run_id="run-b")
        assert second.status == ExecutionStatus.NOT_STARTED
        assert "active" in (second.error or "")
        release.set()
        thread.join(timeout=5)
        assert results[0].status == ExecutionStatus.FAILED

    def test_two_executors_share_one_active_gate(self, tmp_path: Path) -> None:
        release = threading.Event()

        class BlockingBoundary:
            started = 0
            started_event = threading.Event()

            def run(self, **kwargs):
                self.started += 1
                self.started_event.set()
                release.wait(timeout=5)
                return ProcessResult(exit_code=0)

        first = _executor(tmp_path, BlockingBoundary())  # type: ignore[arg-type]
        second = _executor(tmp_path, BlockingBoundary())  # type: ignore[arg-type]
        results: list[ExecutionResult] = []

        def start():
            results.append(first.run(_config(tmp_path), _allow_guards(), run_id="gate-1"))

        thread = threading.Thread(target=start)
        thread.start()
        first.boundary.started_event.wait(timeout=5)
        blocked = second.run(_config(tmp_path), _allow_guards(), run_id="gate-2")
        assert blocked.status == ExecutionStatus.NOT_STARTED
        release.set()
        thread.join(timeout=5)
        # Gate releases after the first terminal path even though its fake
        # boundary returned exit 0 without an adapter.
        third = second.run(_config(tmp_path), _allow_guards(), run_id="gate-3")
        assert third.status in (ExecutionStatus.FAILED, ExecutionStatus.COMPLETED)

    def test_duplicate_retained_run_id_blocks(self, tmp_path: Path) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-dupe")
        assert result.status == ExecutionStatus.COMPLETED
        second = executor.run(_config(tmp_path), _allow_guards(), run_id="run-dupe")
        assert second.status == ExecutionStatus.NOT_STARTED
        assert "reserved" in (second.error or "")
        executor.cleanup(result)
        third = executor.run(_config(tmp_path), _allow_guards(), run_id="run-dupe")
        assert third.status == ExecutionStatus.COMPLETED
        executor.cleanup(third)

    def test_cancel_after_staging_never_spawns(self, tmp_path: Path) -> None:
        calls = {"count": 0}
        boundary = FakeBoundary(ProcessResult(exit_code=0))
        executor = _executor(tmp_path, boundary)

        def cancel():
            calls["count"] += 1
            return calls["count"] == 2

        result = executor.run(
            _config(tmp_path),
            _allow_guards(),
            run_id="run-stage-cancel",
            cancel=cancel,
        )
        assert result.status == ExecutionStatus.CANCELLED
        assert calls["count"] == 2
        assert boundary.started == 0
        assert "after staging" in (result.error or "")
        assert _run_workspaces(tmp_path / "runs") == []
        with executor._lock:
            assert result.run_id not in executor._workspaces

    def test_construction_marker_fault_cleans_fresh_workspace_and_releases(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        boundary = FakeBoundary(
            ProcessResult(exit_code=0),
            adapter_bytes=safetensors_bytes(),
        )
        executor = _executor(tmp_path, boundary)
        real_marker = workspace_module._ensure_owner_marker
        marker_calls = {"count": 0}

        def flaky_marker(workspace, run_id, token):
            marker_calls["count"] += 1
            if marker_calls["count"] == 1:
                raise OSError("marker exploded")
            real_marker(workspace, run_id, token)

        def broken_rmtree(*args, **kwargs):
            raise OSError("rmtree exploded")

        monkeypatch.setattr(workspace_module, "_ensure_owner_marker", flaky_marker)
        monkeypatch.setattr(workspace_module.shutil, "rmtree", broken_rmtree)
        result = executor.run(_config(tmp_path), _allow_guards(), run_id="run-prep-fail")
        assert result.status == ExecutionStatus.FAILED
        assert "preparation" in (result.error or "")
        # The bounded fallback removes the fresh empty workspace even when the
        # marker and rmtree are both faulted, so nothing is retained for retry.
        assert _run_workspaces(tmp_path / "runs") == []
        with executor._lock:
            assert result.run_id not in executor._workspaces
        # The run id is therefore free; the next attempt constructs normally.
        duplicate = executor.run(_config(tmp_path), _allow_guards(), run_id="run-prep-fail")
        assert duplicate.status == ExecutionStatus.COMPLETED

        monkeypatch.undo()
        executor.cleanup(duplicate)
        assert _run_workspaces(tmp_path / "runs") == []


class TestSubprocessBoundary:
    def test_drain_caps_bytes(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        from zana_core.training import execution as ex

        sink = tmp_path / "sink.log"
        sink.write_bytes(b"")
        sink_fd = os.open(sink, os.O_WRONLY)
        ex._drain(io.BytesIO(b"x" * 5000), sink_fd, 128)
        assert sink.stat().st_size == 128

    def test_drain_zero_write_terminates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        sink = tmp_path / "sink.log"
        sink.write_bytes(b"")
        sink_fd = os.open(sink, os.O_WRONLY)
        writes: list[bytes] = []

        def zero_write(fd, data):
            writes.append(bytes(data))
            return 0

        monkeypatch.setattr(ex.os, "write", zero_write)
        ex._drain(io.BytesIO(b"aaaa"), sink_fd, 1024)
        monkeypatch.undo()
        assert writes == [b"aaaa"]
        assert sink.stat().st_size == 0

    def test_sink_creation_failure_cleans_owned_sinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        created = []
        real_open = os.open

        def fake_open(path, flags, mode=0o600):
            if str(path).endswith("stderr.log"):
                raise OSError("stderr sink failed")
            fd = real_open(path, flags, mode)
            created.append(fd)
            return fd

        monkeypatch.setattr(os, "open", fake_open)
        monkeypatch.setattr(ex.os, "open", fake_open)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.1,
            cancel=lambda: False,
        )
        assert "log sink failed" in (result.error or "")
        assert len(created) == 1
        assert (tmp_path / "stdout.log").exists() is False

    def test_fake_process_group_terminate_then_kill(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False
                self.waited = 0

            def wait(self, timeout=None):
                self.waited += 1
                if self.terminated or self.killed:
                    return 0
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        signals = []
        group_states = {"alive": True}

        def fake_getpgid(pid):
            return 9999

        def fake_killpg(pgid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                group_states["alive"] = False

        def fake_group_exists(pgid):
            return group_states["alive"]

        fake = FakeProcess()
        monkeypatch.setattr(ex.os, "getpgid", fake_getpgid)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(ex.SubprocessBoundary, "_group_exists", staticmethod(fake_group_exists))
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=0.1,
            terminate_grace_seconds=0.05,
            cancel=lambda: True,
        )
        assert result.cancelled is True
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals
        assert result.error is None or "cleanup" in (result.error or "")

    def test_permanently_surviving_group_reports_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if self.terminated or self.killed:
                    return 0
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        signals = []
        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", lambda pgid, sig: signals.append(sig))
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: True),
        )
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: FakeProcess())
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=0.1,
            terminate_grace_seconds=0.05,
            cancel=lambda: True,
        )
        assert result.error is not None
        assert "group" in (result.error or "").lower() or "cleanup" in (result.error or "").lower()
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals

    def test_sanitizer_redacts_paths_with_spaces_and_secrets(self) -> None:
        from zana_core.training import execution as ex

        text = "/Users/My Folder/file.txt token=hf_abcdef C:\\Program Files\\zana\\bin.exe"
        out = ex._sanitize_diagnostic(text, 4096)
        assert "/Users" not in out
        assert "Program Files" not in out
        assert "hf_abcdef" not in out
        assert "<path>" in out
        assert "<redacted>" in out
        assert len(out.encode("utf-8")) <= 4096

    def test_both_pipes_over_cap_become_exact_sinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class RecordingBytesIO(io.BytesIO):
            def __init__(self, data: bytes) -> None:
                super().__init__(data)
                self.read_total = 0

            def read(self, size: int = -1) -> bytes:
                chunk = super().read(size)
                self.read_total += len(chunk)
                return chunk

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = RecordingBytesIO(b"x" * 10000)
                self.stderr = RecordingBytesIO(b"y" * 20000)
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: False),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert result.exit_code == 0
        assert result.error is None
        assert (tmp_path / "stdout.log").stat().st_size == 128
        assert (tmp_path / "stderr.log").stat().st_size == 128
        assert fake.stdout.read_total == 10000
        assert fake.stderr.read_total == 20000
        assert fake.stdout.closed is True
        assert fake.stderr.closed is True

    def test_popen_failure_cleans_both_sinks(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        def failing_popen(*args, **kwargs):
            raise OSError("spawn exploded")

        monkeypatch.setattr(ex.subprocess, "Popen", failing_popen)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert "spawn failed" in (result.error or "")
        assert (tmp_path / "stdout.log").exists() is False
        assert (tmp_path / "stderr.log").exists() is False

    def test_getpgid_failure_terminates_then_kills_and_cleans(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if self.killed:
                    return 1
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()

        def failing_getpgid(pid):
            raise OSError("pgid exploded")

        monkeypatch.setattr(ex.os, "getpgid", failing_getpgid)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert "process group capture failed" in (result.error or "")
        assert fake.terminated is True
        assert fake.killed is True
        assert fake.stdout.closed is True
        assert fake.stderr.closed is True
        assert (tmp_path / "stdout.log").exists() is False
        assert (tmp_path / "stderr.log").exists() is False

    def test_thread_start_failures_clean_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if self.killed:
                    return 1
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: False),
        )
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        for fail_on in (0, 1):
            started = {"count": 0}
            joined: list[str] = []

            class FakeThread:
                def __init__(self, *, target, args=(), daemon=False, name=""):
                    self.target = target
                    self.args = args
                    self.daemon = daemon
                    self.name = name
                    self.started = False
                    self.alive = False

                def start(self, started=started, fail_on=fail_on):
                    if started["count"] >= fail_on:
                        raise RuntimeError("thread start failed")
                    started["count"] += 1
                    self.started = True
                    self.alive = True

                def join(self, timeout=None, joined=joined):
                    joined.append(self.name)
                    self.alive = False

                def is_alive(self):
                    return self.alive

            monkeypatch.setattr(ex.threading, "Thread", FakeThread)
            result = ex.SubprocessBoundary(max_log_bytes=128).run(
                argv=("mlx_lm.lora", "--train"),
                cwd=tmp_path,
                env={},
                stdout_path=tmp_path / "stdout.log",
                stderr_path=tmp_path / "stderr.log",
                deadline_seconds=1,
                terminate_grace_seconds=0.05,
                cancel=lambda: False,
            )
            assert "log drain start failed" in (result.error or "")
            assert fake.terminated is True
            assert fake.killed is True
            assert fake.stdout.closed is True
            assert fake.stderr.closed is True
            assert (tmp_path / "stdout.log").exists() is False
            assert (tmp_path / "stderr.log").exists() is False
            if fail_on == 1:
                assert joined == ["zana-stdout-drain"]

    def test_normal_exit_with_descendant_terms_then_kills(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                return 0

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        signals: list[int] = []
        group_alive = {"value": True}

        def fake_killpg(pgid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                group_alive["value"] = False

        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: group_alive["value"]),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert result.exit_code == 0
        assert result.error is None
        assert result.terminated is True
        assert result.killed is True
        assert signals == [signal.SIGTERM, signal.SIGKILL]

    def test_normal_no_descendant_sends_no_signal(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")

            def wait(self, timeout=None):
                return 0

        fake = FakeProcess()
        signals: list[int] = []
        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", lambda pgid, sig: signals.append(sig))
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: False),
        )
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert result.exit_code == 0
        assert result.error is None
        assert result.terminated is False
        assert result.killed is False
        assert signals == []

    def test_getpgid_failure_signals_session_pgid_group(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if not group_alive["value"]:
                    return 1
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        def failing_getpgid(pid):
            raise OSError("pgid exploded")

        fake = FakeProcess()
        signals: list[tuple[int, int]] = []
        group_alive = {"value": True}

        def fake_killpg(pgid, sig):
            signals.append((pgid, sig))
            if sig == signal.SIGKILL:
                group_alive["value"] = False

        monkeypatch.setattr(ex.os, "getpgid", failing_getpgid)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: group_alive["value"]),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert "process group capture failed" in (result.error or "")
        assert "cleanup could not be confirmed" not in (result.error or "")
        assert signals == [(4242, signal.SIGTERM), (4242, signal.SIGKILL)]
        assert fake.stdout.closed is True
        assert fake.stderr.closed is True
        assert (tmp_path / "stdout.log").exists() is False
        assert (tmp_path / "stderr.log").exists() is False

    def test_wait_oserror_fails_closed(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                raise OSError("wait exploded")

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        signals: list[int] = []
        group_alive = {"value": True}

        def fake_killpg(pgid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                group_alive["value"] = False

        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: group_alive["value"]),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert "process wait failed" in (result.error or "")
        assert "cleanup could not be confirmed" in (result.error or "")
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals
        assert result.killed is True

    def test_setup_stuck_drain_reports_cleanup_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if self.killed:
                    return 1
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: False),
        )
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        started = {"count": 0}
        joined: list[str] = []

        class StuckFakeThread:
            def __init__(self, *, target, args=(), daemon=False, name=""):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.name = name
                self.started = False
                self.alive = False

            def start(self, started=started):
                if started["count"] >= 1:
                    raise RuntimeError("thread start failed")
                started["count"] += 1
                self.started = True
                self.alive = True

            def join(self, timeout=None, joined=joined):
                joined.append(self.name)

            def is_alive(self):
                return self.alive

        monkeypatch.setattr(ex.threading, "Thread", StuckFakeThread)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert "log drain start failed" in (result.error or "")
        assert "cleanup could not be confirmed" in (result.error or "")
        assert fake.terminated is True
        assert fake.killed is True
        assert fake.stdout.closed is True
        assert fake.stderr.closed is True
        assert joined == ["zana-stdout-drain"]

    def test_terminal_stuck_drain_reports_cleanup_failure(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")
                self.terminated = False
                self.killed = False

            def wait(self, timeout=None):
                if not group_alive["value"]:
                    return 1
                raise subprocess.TimeoutExpired(cmd="fake", timeout=timeout)

            def terminate(self):
                self.terminated = True

            def kill(self):
                self.killed = True

        fake = FakeProcess()
        signals: list[int] = []
        group_alive = {"value": True}

        def fake_killpg(pgid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                group_alive["value"] = False

        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: group_alive["value"]),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        started = {"count": 0}
        joined: list[str] = []

        class StuckFakeThread:
            def __init__(self, *, target, args=(), daemon=False, name=""):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.name = name
                self.started = False
                self.alive = False

            def start(self, started=started):
                if started["count"] >= 2:
                    raise RuntimeError("thread start failed")
                started["count"] += 1
                self.started = True
                self.alive = True

            def join(self, timeout=None, joined=joined):
                joined.append(self.name)

            def is_alive(self):
                return self.alive

        monkeypatch.setattr(ex.threading, "Thread", StuckFakeThread)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: True,
        )
        assert result.cancelled is True
        assert "cleanup could not be confirmed" in (result.error or "")
        assert result.terminated is True
        assert result.killed is True
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals
        assert joined == ["zana-stdout-drain", "zana-stderr-drain"]

    def test_normal_exit_surviving_group_plus_stuck_drain_is_red(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from zana_core.training import execution as ex

        class FakeProcess:
            def __init__(self):
                self.pid = 4242
                self.stdout = io.BytesIO(b"")
                self.stderr = io.BytesIO(b"")

            def wait(self, timeout=None):
                return 0

        fake = FakeProcess()
        signals: list[int] = []
        group_alive = {"value": True}

        def fake_killpg(pgid, sig):
            signals.append(sig)
            if sig == signal.SIGKILL:
                group_alive["value"] = False

        monkeypatch.setattr(ex.os, "getpgid", lambda pid: 9999)
        monkeypatch.setattr(ex.os, "killpg", fake_killpg)
        monkeypatch.setattr(
            ex.SubprocessBoundary,
            "_group_exists",
            staticmethod(lambda pgid: group_alive["value"]),
        )
        monkeypatch.setattr(ex.time, "sleep", lambda _: None)
        monkeypatch.setattr(ex.subprocess, "Popen", lambda *a, **k: fake)
        started = {"count": 0}
        joined: list[str] = []

        class StuckFakeThread:
            def __init__(self, *, target, args=(), daemon=False, name=""):
                self.target = target
                self.args = args
                self.daemon = daemon
                self.name = name
                self.started = False
                self.alive = False

            def start(self, started=started):
                if started["count"] >= 2:
                    raise RuntimeError("thread start failed")
                started["count"] += 1
                self.started = True
                self.alive = True

            def join(self, timeout=None, joined=joined):
                joined.append(self.name)

            def is_alive(self):
                return self.alive

        monkeypatch.setattr(ex.threading, "Thread", StuckFakeThread)
        result = ex.SubprocessBoundary(max_log_bytes=128).run(
            argv=("mlx_lm.lora", "--train"),
            cwd=tmp_path,
            env={},
            stdout_path=tmp_path / "stdout.log",
            stderr_path=tmp_path / "stderr.log",
            deadline_seconds=1,
            terminate_grace_seconds=0.05,
            cancel=lambda: False,
        )
        assert result.exit_code == 0
        assert "cleanup could not be confirmed" in (result.error or "")
        assert result.terminated is True
        assert result.killed is True
        assert signal.SIGTERM in signals
        assert signal.SIGKILL in signals
        assert joined == ["zana-stdout-drain", "zana-stderr-drain"]
