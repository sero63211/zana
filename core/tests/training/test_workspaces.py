"""Private training workspace staging, isolation, and cleanup tests."""

from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from zana_core.training import workspaces as workspace_module
from zana_core.training.contracts import (
    AdapterBaseIdentity,
    DatasetSplitManifest,
    LocalTrainingSource,
    TrainingRequestConfig,
)
from zana_core.training.workspaces import (
    WorkspacePreparationError,
    cleanup_training_workspace,
    prepare_training_workspace,
    sha256_file,
    stage_training_data,
)


def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


def _config(tmp_path: Path, *, eval_record_id: str = "eval-1") -> TrainingRequestConfig:
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
        eval_split=manifest("evaluation", evalf, eval_record_id),
        seed=7,
        iters=3,
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "runs"
    root.mkdir(exist_ok=True)
    return root


class TestTrainingWorkspace:
    def test_prepare_creates_private_dirs_and_owner_marker(self, tmp_path: Path) -> None:
        attestation = prepare_training_workspace(_root(tmp_path), "run-1")
        workspace = attestation.workspace
        assert workspace.name.startswith("zana-training-")
        for name in ("data", "logs", "out", "home", "tmp", "cache"):
            assert (workspace / name).is_dir()
        marker = workspace / workspace_module.WORKSPACE_OWNER_FILE
        lines = marker.read_text(encoding="ascii").splitlines()
        assert lines[0] == "run-1"
        assert lines[1] == attestation.token
        assert attestation.root_dev > 0
        assert attestation.workspace_ino > 0

    def test_prepare_rejects_relative_root_and_bad_run_id(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            prepare_training_workspace(Path("relative"), "run-1")
        with pytest.raises(ValueError):
            prepare_training_workspace(_root(tmp_path), "../bad")

    def test_prepare_rejects_symlink_root(self, tmp_path: Path) -> None:
        real = tmp_path / "real-runs"
        real.mkdir()
        link = tmp_path / "runs-link"
        link.symlink_to(real, target_is_directory=True)
        with pytest.raises(ValueError):
            prepare_training_workspace(link, "run-1")

    def test_marker_and_rmtree_faults_still_clean_fresh_workspace_via_bounded_fallback(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)

        def broken_marker(*args, **kwargs):
            raise OSError("marker always fails")

        def broken_rmtree(*args, **kwargs):
            raise OSError("rmtree always fails")

        monkeypatch.setattr(workspace_module, "_ensure_owner_marker", broken_marker)
        monkeypatch.setattr(workspace_module.shutil, "rmtree", broken_rmtree)
        with pytest.raises(OSError):
            prepare_training_workspace(root, "run-1")
        # Even with both marker creation and rmtree faulted, the bounded
        # entry-enumerating fallback removes the fresh empty workspace.
        assert list(root.iterdir()) == []

        monkeypatch.undo()
        attestation = prepare_training_workspace(root, "run-1")
        cleanup_training_workspace(attestation)
        assert attestation.workspace.exists() is False

    def test_chmod_fault_with_rmtree_fault_never_orphans_fresh_workspace(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)

        def broken_chmod(*args, **kwargs):
            raise OSError("chmod always fails")

        def broken_rmtree(*args, **kwargs):
            raise OSError("rmtree always fails")

        monkeypatch.setattr(workspace_module.os, "chmod", broken_chmod)
        monkeypatch.setattr(workspace_module.shutil, "rmtree", broken_rmtree)
        with pytest.raises(OSError):
            prepare_training_workspace(root, "run-1")
        # The workspace identity is captured before chmod, so the bounded
        # fallback still proves and removes the fresh empty workspace.
        assert list(root.iterdir()) == []

    def test_partial_marker_fault_cleans_without_recursion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)

        def broken_marker(workspace: Path, run_id: str, token: str) -> None:
            marker = workspace / workspace_module.WORKSPACE_OWNER_FILE
            marker.write_text("run-1\n", encoding="ascii")
            raise OSError("marker verification failed")

        monkeypatch.setattr(workspace_module, "_ensure_owner_marker", broken_marker)
        with pytest.raises(OSError):
            prepare_training_workspace(root, "run-1")
        # The partial marker is removed and the fresh empty workspace is rmdir'd.
        assert list(root.iterdir()) == []

    def test_late_known_dir_fault_cleans_partial_workspace_without_recursion(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)
        original_mkdir = Path.mkdir

        def failing_logs_mkdir(self, *args, **kwargs):
            if self.name == "logs" and self.parent.name.startswith("zana-training-run-1-"):
                raise OSError("logs mkdir failed")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_logs_mkdir)
        with pytest.raises(OSError):
            prepare_training_workspace(root, "run-1")
        # The complete marker, the already-created known empty dirs, and the
        # workspace itself are all removed by the bounded fallback.
        assert list(root.iterdir()) == []

    def test_tampered_fresh_workspace_retained_with_usable_attestation(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)
        original_mkdir = Path.mkdir

        def failing_data_mkdir(self, *args, **kwargs):
            if self.name == "data" and self.parent.name.startswith("zana-training-run-1-"):
                intruder = self.parent / "intruder"
                original_mkdir(intruder)
                (intruder / "future-content").write_text("keep")
                raise OSError("data mkdir failed")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_data_mkdir)
        with pytest.raises(WorkspacePreparationError) as excinfo:
            prepare_training_workspace(root, "run-1")
        attestation = excinfo.value.attestation
        assert attestation is not None
        workspace = attestation.workspace
        assert (workspace / "intruder" / "future-content").read_text() == "keep"
        assert workspace.exists()
        cleanup_training_workspace(attestation)
        assert workspace.exists() is False
        assert root.exists()

    def test_replaced_workspace_gets_no_attestation_and_replacement_preserved(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        root = _root(tmp_path)
        original_mkdir = Path.mkdir

        def failing_data_mkdir(self, *args, **kwargs):
            if self.name == "data" and self.parent.name.startswith("zana-training-run-1-"):
                intruder = self.parent / "intruder"
                original_mkdir(intruder)
                (intruder / "future-content").write_text("keep")
                raise OSError("data mkdir failed")
            return original_mkdir(self, *args, **kwargs)

        monkeypatch.setattr(Path, "mkdir", failing_data_mkdir)
        original_cleanup = workspace_module._safe_fresh_cleanup
        captured: dict[str, Path] = {}

        def replaced_cleanup(root, root_st, workspace, workspace_st, run_id) -> bool:
            if original_cleanup(root, root_st, workspace, workspace_st, run_id):
                return True
            captured["workspace"] = workspace
            swapped = workspace.with_name(workspace.name + "-swapped")
            workspace.rename(swapped)
            replacement = workspace.parent / "zana-training-replacement"
            replacement.mkdir()
            (replacement / "replacement-content").write_text("keep")
            replacement.rename(workspace)
            return False

        monkeypatch.setattr(workspace_module, "_safe_fresh_cleanup", replaced_cleanup)
        with pytest.raises(WorkspacePreparationError) as excinfo:
            prepare_training_workspace(root, "run-1")
        assert excinfo.value.attestation is None
        workspace = captured["workspace"]
        # The replacement is never marked or attested, and its content survives.
        assert (workspace / "replacement-content").read_text() == "keep"
        assert (workspace / workspace_module.WORKSPACE_OWNER_FILE).exists() is False
        # The tampered original was only renamed aside, never deleted.
        assert (
            workspace.with_name(workspace.name + "-swapped") / "intruder" / "future-content"
        ).read_text() == "keep"

    def test_stage_copies_exactly_train_and_valid_never_eval(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        staged = stage_training_data(workspace, _config(tmp_path), max_file_bytes=1024)
        assert staged.train_path.read_bytes() == b'{"id":"train-1"}\n'
        assert staged.valid_path is not None
        assert staged.valid_path.read_bytes() == b'{"id":"valid-1"}\n'
        assert (workspace / "data" / "eval.jsonl").exists() is False
        assert staged.train_sha256 == sha256_file(tmp_path / "train.jsonl")

    def test_eval_leak_by_record_id_blocks_staging(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path, eval_record_id="train-1")
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_symlink_train_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        real = tmp_path / "real-train.jsonl"
        real.write_bytes(b'{"id":"train-1"}\n')
        link = tmp_path / "train-link.jsonl"
        link.symlink_to(real)
        config = _config(tmp_path)
        config = config.model_copy(
            update={
                "train_split": config.train_split.model_copy(
                    update={"path": link, "sha256": sha256_file(real)}
                )
            }
        )
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_size_mismatch_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path)
        config = config.model_copy(
            update={
                "train_split": config.train_split.model_copy(
                    update={"size_bytes": config.train_split.size_bytes + 1}
                )
            }
        )
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_hash_mismatch_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path)
        config = config.model_copy(
            update={"train_split": config.train_split.model_copy(update={"sha256": "0" * 64})}
        )
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_relative_split_path_rejected_by_contract(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            DatasetSplitManifest(
                role="train",
                path=Path("relative/train.jsonl"),
                sha256="0" * 64,
                size_bytes=1,
            )

    def test_size_cap_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        with pytest.raises(ValueError):
            stage_training_data(workspace, _config(tmp_path), max_file_bytes=1)

    def test_same_size_replacement_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path)
        replacement = tmp_path / "train.jsonl"
        replacement.write_bytes(b'{"id":"train-1"}\n')
        replacement.write_bytes(b'{"id":"train-2"}\n')
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_growing_file_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path)
        (tmp_path / "train.jsonl").write_bytes(b'{"id":"train-1"}\n' + b"x" * 10)
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_swapped_symlink_rejected(self, tmp_path: Path) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        config = _config(tmp_path)
        real = tmp_path / "swapped.jsonl"
        real.write_bytes(b'{"id":"train-1"}\n')
        train = tmp_path / "train.jsonl"
        train.unlink()
        train.symlink_to(real)
        with pytest.raises(ValueError):
            stage_training_data(workspace, config, max_file_bytes=1024)

    def test_staged_digest_mismatch_rejected(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        workspace = prepare_training_workspace(_root(tmp_path), "run-1").workspace
        original = workspace_module.sha256_file

        def wrong_digest(path: Path, max_bytes: int = 1 << 30) -> str:
            actual = original(path, max_bytes)
            return "0" * 64 if path.name == "train.jsonl" else actual

        monkeypatch.setattr(workspace_module, "sha256_file", wrong_digest)
        with pytest.raises(ValueError):
            stage_training_data(workspace, _config(tmp_path), max_file_bytes=1024)

    def test_cleanup_removes_only_owned_workspace(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        attestation = prepare_training_workspace(root, "run-1")
        workspace = attestation.workspace
        marker = tmp_path / "outside.txt"
        marker.write_text("keep")
        cleanup_training_workspace(attestation)
        assert workspace.exists() is False
        assert marker.read_text() == "keep"
        with pytest.raises(ValueError):
            cleanup_training_workspace(
                workspace_module.WorkspaceAttestation(
                    root=root,
                    workspace=marker,
                    run_id="run-1",
                    root_dev=attestation.root_dev,
                    root_ino=attestation.root_ino,
                    workspace_dev=attestation.workspace_dev,
                    workspace_ino=attestation.workspace_ino,
                    token=attestation.token,
                )
            )
        with pytest.raises(ValueError):
            cleanup_training_workspace(
                workspace_module.WorkspaceAttestation(
                    root=root,
                    workspace=workspace,
                    run_id="run-2",
                    root_dev=attestation.root_dev,
                    root_ino=attestation.root_ino,
                    workspace_dev=attestation.workspace_dev,
                    workspace_ino=attestation.workspace_ino,
                    token=attestation.token,
                )
            )

    def test_cleanup_refuses_forged_same_prefix(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        real = prepare_training_workspace(root, "run-1")
        forged = root / "zana-training-run-1-forged"
        forged.mkdir()
        with pytest.raises(ValueError):
            cleanup_training_workspace(
                workspace_module.WorkspaceAttestation(
                    root=root,
                    workspace=forged,
                    run_id="run-1",
                    root_dev=real.root_dev,
                    root_ino=real.root_ino,
                    workspace_dev=real.workspace_dev,
                    workspace_ino=real.workspace_ino,
                    token=real.token,
                )
            )
        cleanup_training_workspace(real)

    def test_cleanup_refuses_symlink_workspace(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        real = prepare_training_workspace(root, "run-1")
        target = tmp_path / "outside-dir"
        target.mkdir()
        link = root / "zana-training-run-1-link"
        link.symlink_to(target, target_is_directory=True)
        with pytest.raises(ValueError):
            cleanup_training_workspace(
                workspace_module.WorkspaceAttestation(
                    root=root,
                    workspace=link,
                    run_id="run-1",
                    root_dev=real.root_dev,
                    root_ino=real.root_ino,
                    workspace_dev=real.workspace_dev,
                    workspace_ino=real.workspace_ino,
                    token=real.token,
                )
            )
        assert target.exists()
        cleanup_training_workspace(real)

    def test_cleanup_refuses_replaced_workspace(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        attestation = prepare_training_workspace(root, "run-1")
        import shutil

        shutil.rmtree(attestation.workspace)
        attestation.workspace.mkdir()
        with pytest.raises(ValueError):
            cleanup_training_workspace(attestation)

    def test_cleanup_retry_after_failure(self, tmp_path: Path) -> None:
        root = _root(tmp_path)
        attestation = prepare_training_workspace(root, "run-1")
        marker = attestation.workspace / workspace_module.WORKSPACE_OWNER_FILE
        marker.chmod(0o000)
        removed = False
        try:
            with pytest.raises(ValueError):
                cleanup_training_workspace(attestation)
            marker.chmod(0o600)
            cleanup_training_workspace(attestation)
            removed = True
        finally:
            if not removed and marker.exists():
                marker.chmod(0o600)

    def test_record_ids_are_bounded_by_contract(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            DatasetSplitManifest(
                role="train",
                path=Path("/data/train.jsonl"),
                sha256="0" * 64,
                size_bytes=1,
                record_ids=("a",) * 100_001,
            )
