"""Focused unit tests for on-disk Capability Source authoring primitives."""

from __future__ import annotations

import hashlib
import os
import shutil
from pathlib import Path

import pytest

from tests.capabilities.helpers import MATH_EVAL_JSONL
from zana_core.capabilities import authoring
from zana_core.capabilities.authoring import (
    AuthoringError,
    AuthoringValidationError,
    SourceRequest,
)


def sha256_bytes(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


class TestWorkspacePaths:
    def test_workspace_path_is_canonical_and_contained(self, tmp_path: Path) -> None:
        workspace = authoring.capability_workspace_path(tmp_path, 7)
        assert workspace == tmp_path / "capabilities" / "7"
        assert authoring.workspace_is_under_data_root(workspace, tmp_path)
        assert authoring.relative_workspace_path(workspace, tmp_path) == "capabilities/7"

    def test_workspace_path_rejects_invalid_id_and_relative_root(self, tmp_path: Path) -> None:
        with pytest.raises(AuthoringError) as exc_info:
            authoring.capability_workspace_path(tmp_path, 0)
        assert exc_info.value.code == "CAPABILITY_ID_INVALID"
        with pytest.raises(AuthoringError) as exc_info:
            authoring.capability_workspace_path(Path("relative/root"), 1)
        assert exc_info.value.code == "DATA_ROOT_INVALID"

    def test_ensure_workspace_creates_private_layout_and_rejects_symlink(
        self, tmp_path: Path
    ) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        for relative in ("", "behavior", "knowledge/sources", "evals"):
            assert (workspace / relative).is_dir()
        outside = tmp_path / "outside"
        outside.mkdir()
        link = tmp_path / "capabilities" / "1"
        shutil.rmtree(link)
        link.symlink_to(outside, target_is_directory=True)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.ensure_workspace(link)
        assert exc_info.value.code == "PATH_SYMLINK"
        assert outside.exists()

    def test_remove_workspace_never_touches_siblings(self, tmp_path: Path) -> None:
        authoring.ensure_workspace(tmp_path / "capabilities" / "1")
        (tmp_path / "capabilities" / "1" / "evals" / "domain.jsonl").write_text("x\n")
        assert authoring.remove_workspace(
            tmp_path / "capabilities" / "1", tmp_path, created_by_request=True
        )
        assert not (tmp_path / "capabilities" / "1").exists()
        assert (tmp_path / "capabilities").exists()
        outside = tmp_path.parent / "untouched"
        outside.mkdir(exist_ok=True)
        assert not authoring.remove_workspace(outside, tmp_path, created_by_request=True)
        assert outside.exists()

    def test_rollback_never_deletes_symlink_target_or_preexisting_workspace(
        self, tmp_path: Path
    ) -> None:
        outside = tmp_path / "outside"
        outside.mkdir()
        outside.joinpath("keep.txt").write_text("keep\n")
        link = tmp_path / "capabilities" / "1"
        link.parent.mkdir()
        link.symlink_to(outside, target_is_directory=True)
        assert not authoring.remove_workspace(link, tmp_path, created_by_request=True)
        assert outside.joinpath("keep.txt").exists()

        link.unlink()
        link.mkdir()
        link.joinpath("owned.txt").write_text("owned\n")
        assert not authoring.remove_workspace(link, tmp_path, created_by_request=False)
        assert link.joinpath("owned.txt").exists()

    def test_symlinked_capabilities_parent_escape_is_rejected(self, tmp_path: Path) -> None:
        outside = tmp_path.parent / "outside-capabilities"
        outside.mkdir(exist_ok=True)
        outside.joinpath("1").mkdir(exist_ok=True)
        link = tmp_path / "capabilities"
        link.symlink_to(outside, target_is_directory=True)
        workspace = link / "1"
        with pytest.raises(AuthoringError) as exc_info:
            authoring.ensure_workspace(workspace)
        assert exc_info.value.code == "PATH_SYMLINK"
        assert outside.joinpath("1").exists()

    def test_symlinked_source_parent_escape_is_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        outside = tmp_path / "outside-sources"
        outside.mkdir()
        outside.joinpath("system.md").write_text("x\n")
        behavior = workspace / "behavior"
        behavior.rmdir()
        behavior.symlink_to(outside, target_is_directory=True)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_source(workspace, SourceRequest(kind="behavior", content="prompt\n"))
        assert exc_info.value.code == "PATH_SYMLINK"
        assert outside.joinpath("system.md").read_text() == "x\n"


class TestSourcePathSafety:
    def test_sanitize_filename_rejects_traversal_and_controls(self) -> None:
        assert authoring.sanitize_source_filename("Remote Work Policy.md") == (
            "Remote Work Policy.md"
        )
        for bad in (
            "",
            "../secret.md",
            "a/b.md",
            "a\\b.md",
            "a\x00b.md",
            "a\tb.md",
            "\u00fcmlaut.md",
        ):
            with pytest.raises(AuthoringError):
                authoring.sanitize_source_filename(bad)

    def test_document_media_type_allowlist(self) -> None:
        assert authoring.document_media_type("guide.pdf") == "application/pdf"
        assert authoring.document_media_type("guide.MD") == "text/markdown"
        assert authoring.document_media_type("notes.txt") == "text/plain"
        with pytest.raises(AuthoringError) as exc_info:
            authoring.document_media_type("run.sh")
        assert exc_info.value.code == "SOURCE_KIND_UNSUPPORTED"

    def test_validate_local_source_path_rejects_hostile_input(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        safe = tmp_path / "approved.md"
        safe.write_text("approved\n")
        assert (
            authoring.validate_local_source_path(str(safe), max_bytes=1000, workspace=workspace)
            == safe
        )

        cases = {
            "relative": (str(Path("relative.md")), "SOURCE_PATH_RELATIVE"),
            "traversal": ("/tmp/../etc/hosts", "SOURCE_PATH_TRAVERSAL"),
            "backslash": ("/tmp/a\\b.md", "SOURCE_PATH_INVALID"),
            "nul": ("/tmp/a\x00b.md", "SOURCE_PATH_INVALID"),
            "workspace": (str(workspace / "behavior" / "system.md"), "SOURCE_PATH_WORKSPACE"),
            "directory": (str(tmp_path), "SOURCE_PATH_TYPE"),
        }
        for raw, code in cases.values():
            with pytest.raises(AuthoringError) as exc_info:
                authoring.validate_local_source_path(raw, max_bytes=1000, workspace=workspace)
            assert exc_info.value.code == code, raw

        symlink = tmp_path / "link.md"
        symlink.symlink_to(safe)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.validate_local_source_path(str(symlink), max_bytes=1000, workspace=workspace)
        assert exc_info.value.code == "SOURCE_PATH_SYMLINK"

        oversized = tmp_path / "big.md"
        oversized.write_bytes(b"x" * 64)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.validate_local_source_path(str(oversized), max_bytes=8, workspace=workspace)
        assert exc_info.value.code == "SOURCE_TOO_LARGE"


class TestStagingAndPublishing:
    def test_stage_text_content_hashes_and_enforces_bounds(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "evals" / "domain.jsonl"
        temp = authoring.new_temp_path(target)
        digest, size = authoring.stage_text_content("hello\n", temp, max_bytes=8)
        assert digest == sha256_bytes(b"hello\n")
        assert size == 6
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_text_content("hello\n", temp, max_bytes=4)
        assert exc_info.value.code == "CONTENT_TOO_LARGE"
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_text_content("a\x00b", temp, max_bytes=8)
        assert exc_info.value.code == "CONTENT_NUL"

    def test_stage_document_copy_never_modifies_original(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        source = tmp_path / "original.md"
        payload = b"# Original\n" * 10
        source.write_bytes(payload)
        staged = authoring.stage_source(
            workspace,
            SourceRequest(
                kind="document",
                local_path=str(source),
                user_approved=True,
            ),
        )
        assert source.read_bytes() == payload
        assert staged.sha256 == sha256_bytes(payload)
        assert staged.relative_path == "knowledge/sources/original.md"
        assert staged.media_type == "text/markdown"
        authoring.publish_staged(staged.temp_path, staged.target_path)
        assert (workspace / "knowledge" / "sources" / "original.md").read_bytes() == payload

    def test_document_copy_rejects_drift(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from types import SimpleNamespace

        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        source = tmp_path / "drift.md"
        source.write_text("one\n")
        temp = authoring.new_temp_path(workspace / "knowledge" / "sources" / "drift.md")
        original_lstat = Path.lstat
        fake = SimpleNamespace(
            st_mode=0o100644,
            st_size=999,
            st_dev=999,
            st_ino=999,
            st_mtime_ns=999,
        )

        def fake_lstat(self: Path) -> object:  # noqa: ANN001
            return fake if self == source else original_lstat(self)

        monkeypatch.setattr(
            Path,
            "lstat",
            fake_lstat,
        )
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_document_copy(source, temp, max_bytes=1000)
        assert exc_info.value.code == "SOURCE_DRIFT"

    def test_publish_rejects_symlink_target_and_publishes_atomically(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "system.md"
        target.write_text("old\n")
        temp = authoring.new_temp_path(target)
        authoring.stage_text_content("new\n", temp, max_bytes=8)
        authoring.publish_staged(temp, target)
        assert target.read_text() == "new\n"
        link = workspace / "behavior" / "link.md"
        link.symlink_to(target)
        temp2 = authoring.new_temp_path(target)
        authoring.stage_text_content("again\n", temp2, max_bytes=8)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.publish_staged(temp2, link)
        assert exc_info.value.code == "TARGET_SYMLINK"

    def test_stage_and_restore_backup_preserves_prior_file(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "system.md"
        target.write_text("prior\n")
        backup = authoring.stage_backup(target)
        assert backup is not None
        target.write_text("replacement\n")
        authoring.restore_backup(backup, target)
        assert target.read_text() == "prior\n"
        assert not backup.exists()

    def test_stage_backup_returns_none_for_missing_or_symlink(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "missing.md"
        assert authoring.stage_backup(target) is None
        real = workspace / "behavior" / "real.md"
        real.write_text("data\n")
        link = workspace / "behavior" / "link.md"
        link.symlink_to(real)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_backup(link)
        assert exc_info.value.code == "TARGET_SYMLINK"

    def test_stage_backup_is_bounded_and_private(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "system.md"
        target.write_text("prior\n")
        monkeypatch.setattr(authoring, "MAX_BEHAVIOR_BYTES", 4)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_backup(target)
        assert exc_info.value.code == "SOURCE_TOO_LARGE"
        assert not any(".zana-tmp-" in item.name for item in (workspace / "behavior").iterdir())
        monkeypatch.undo()
        target.write_text("prior\n")
        backup = authoring.stage_backup(target)
        assert backup is not None
        assert (backup.stat().st_mode & 0o777) == 0o600
        assert backup.read_text() == "prior\n"
        authoring.discard_temp(backup)

    def test_stage_backup_detects_swap_with_same_size(self, tmp_path: Path) -> None:
        from types import SimpleNamespace

        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "system.md"
        target.write_text("data1\n")
        original_lstat = Path.lstat
        fake = SimpleNamespace(
            st_mode=0o100644,
            st_size=6,
            st_dev=1,
            st_ino=2,
            st_mtime_ns=999,
        )

        def fake_lstat(self: Path) -> object:  # noqa: ANN001
            return fake if self == target else original_lstat(self)

        monkeypatch = pytest.MonkeyPatch()
        monkeypatch.setattr(Path, "lstat", fake_lstat)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_backup(target)
        assert exc_info.value.code == "SOURCE_DRIFT"
        monkeypatch.undo()
        assert not any(".zana-tmp-" in item.name for item in (workspace / "behavior").iterdir())

    def test_eval_stage_rejects_malformed_jsonl_with_canonical_label(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        with pytest.raises(AuthoringValidationError) as exc_info:
            authoring.stage_source(
                workspace,
                SourceRequest(kind="evaluation", eval_kind="domain", content="not-json\n"),
            )
        codes = [issue.code for issue in exc_info.value.issues]
        assert "EVALUATION_JSON" in codes
        assert all(issue.file == "evals/domain.jsonl" for issue in exc_info.value.issues)
        assert not list((workspace / "evals").iterdir())

    def test_eval_stage_accepts_real_jsonl(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        staged = authoring.stage_source(
            workspace,
            SourceRequest(kind="evaluation", eval_kind="domain", content=MATH_EVAL_JSONL),
        )
        assert staged.relative_path == "evals/domain.jsonl"
        assert staged.size_bytes == len(MATH_EVAL_JSONL.encode("utf-8"))


class TestManifestCoherence:
    def test_default_manifest_is_valid_and_deterministic(self) -> None:
        first = authoring.default_manifest("Math Tutor", "0.1.0", 3)
        second = authoring.default_manifest("Math Tutor", "0.1.0", 3)
        assert first == second
        assert first["schemaVersion"] == 1
        assert first["kind"] == "ZanaCapability"
        assert first["id"] == "zana.local.math-tutor"
        assert first["version"] == "0.1.0"

    def test_manifest_round_trip_and_atomic_write(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        manifest = {
            "schemaVersion": 1,
            "kind": "ZanaCapability",
            "id": "io.zana.test.one",
            "name": "One",
            "version": "0.1.0",
            "evaluation": {"domain": "evals/domain.jsonl"},
        }
        authoring.write_manifest(workspace, manifest)
        assert authoring.load_manifest_dict(workspace) == manifest
        authoring.remove_manifest(workspace)
        assert authoring.load_manifest_dict(workspace) is None

    def test_oversized_manifest_bounded_read_fails_closed(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        (workspace / "zana.yaml").write_bytes(b"x" * 64)
        monkeypatch.setattr(authoring, "MAX_MANIFEST_BYTES", 8)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.load_manifest_dict(workspace)
        assert exc_info.value.code == "MANIFEST_TOO_LARGE"

    def test_manifest_symlink_is_rejected(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        outside = tmp_path / "outside.yaml"
        outside.write_text("x\n")
        (workspace / "zana.yaml").symlink_to(outside)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.load_manifest_dict(workspace)
        assert exc_info.value.code == "MANIFEST_SYMLINK"
        assert outside.read_text() == "x\n"

    def test_update_manifest_for_source_declares_canonical_paths(self) -> None:
        manifest = {
            "schemaVersion": 1,
            "kind": "ZanaCapability",
            "id": "io.zana.test.one",
            "name": "One",
            "version": "0.1.0",
        }
        behavior = authoring.update_manifest_for_source(manifest, manifest_kind="behavior")
        assert behavior["behavior"] == {"system": "behavior/system.md"}
        document = authoring.update_manifest_for_source(manifest, manifest_kind="document")
        assert document["knowledge"] == {"sources": [{"path": "knowledge/sources"}]}
        domain = authoring.update_manifest_for_source(
            manifest, manifest_kind="evaluation", eval_kind="domain"
        )
        regression = authoring.update_manifest_for_source(
            domain, manifest_kind="evaluation", eval_kind="regression"
        )
        assert regression["evaluation"] == {
            "domain": "evals/domain.jsonl",
            "regression": "evals/regression.jsonl",
        }
        assert document == authoring.update_manifest_for_source(document, manifest_kind="document")

    def test_staged_manifest_rejects_unserializable_manifest(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_manifest(workspace, {"bad": object()})
        assert exc_info.value.code == "MANIFEST_INVALID"


class TestBehaviorSource:
    def test_behavior_stage_requires_content_and_bounds(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        with pytest.raises(AuthoringError):
            authoring.stage_source(workspace, SourceRequest(kind="behavior", content=None))
        monkeypatch.setattr(authoring, "MAX_BEHAVIOR_BYTES", 8)
        content = "x" * 16
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_source(workspace, SourceRequest(kind="behavior", content=content))
        assert exc_info.value.code == "CONTENT_TOO_LARGE"
        staged = authoring.stage_source(
            workspace, SourceRequest(kind="behavior", content="short\n")
        )
        assert staged.relative_path == "behavior/system.md"


class TestFailureHonesty:
    def test_restore_failure_is_not_swallowed(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        target = workspace / "behavior" / "system.md"
        target.write_text("prior\n")
        backup = authoring.stage_backup(target)
        assert backup is not None
        target.write_text("replacement\n")

        def fail_replace(source: Path, destination: Path) -> None:
            raise OSError("injected restore failure")

        monkeypatch.setattr(os, "replace", fail_replace)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.restore_backup(backup, target)
        assert exc_info.value.code == "SOURCE_RESTORE"
        assert target.read_text() == "replacement\n"

    def test_same_size_identity_drift_is_detected(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        from types import SimpleNamespace

        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        source = tmp_path / "same-size.md"
        source.write_text("data\n")
        temp = authoring.new_temp_path(workspace / "knowledge" / "sources" / "same-size.md")
        real_fstat = os.fstat
        calls = 0

        def fake_fstat(fd: int) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                return SimpleNamespace(
                    st_size=5,
                    st_dev=1,
                    st_ino=2,
                    st_mtime_ns=999,
                )
            return real_fstat(fd)

        monkeypatch.setattr(os, "fstat", fake_fstat)
        with pytest.raises(AuthoringError) as exc_info:
            authoring.stage_document_copy(source, temp, max_bytes=1000)
        assert exc_info.value.code == "SOURCE_DRIFT"

    def test_approved_path_errors_never_expose_host_path(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        missing = tmp_path.parent / "definitely-missing-approved.md"
        with pytest.raises(AuthoringError) as exc_info:
            authoring.validate_local_source_path(str(missing), max_bytes=1000, workspace=workspace)
        assert str(missing) not in exc_info.value.message
        sanitized = authoring.sanitize_message(
            "approved /Users/secret/path.md failed", workspace, tmp_path
        )
        assert "/Users/secret/path.md" not in sanitized
        assert "<path>" in sanitized

    def test_sanitize_message_never_throws_on_resolution_failure(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        def boom(path: Path, strict: bool = False) -> Path:  # noqa: ARG001
            raise RuntimeError("symlink loop")

        monkeypatch.setattr(Path, "resolve", boom)
        result = authoring.sanitize_message(
            "secret /Users/secret/path.md failed", tmp_path, tmp_path.parent
        )
        assert "/Users/secret/path.md" not in result
        assert "secret" in result
        assert "<path>" in result
        assert len(result) <= authoring.MAX_MESSAGE_CHARS + 3

        empty_result = authoring.sanitize_message("", tmp_path, tmp_path.parent)
        assert empty_result == ""


class TestValidationPreflight:
    def test_preflight_rejects_symlink_without_reading_external(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        outside = tmp_path / "outside"
        outside.mkdir()
        outside.joinpath("secret.md").write_text("secret\n")
        (workspace / "behavior" / "system.md").symlink_to(outside / "secret.md")
        preflight = authoring.validate_source_preflight(workspace, tmp_path)
        assert not preflight.ok
        assert any(issue.code == "PATH_SYMLINK" for issue in preflight.issues)
        assert outside.joinpath("secret.md").read_text() == "secret\n"

    def test_preflight_rejects_count_and_aggregate_limits(
        self, tmp_path: Path, monkeypatch
    ) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        for index in range(3):
            (workspace / "knowledge" / "sources" / f"d{index}.md").write_text("x\n")
        monkeypatch.setattr(authoring, "MAX_VALIDATION_FILE_COUNT", 2)
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=2, max_bytes=8
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_COUNT_LIMIT" for issue in preflight.issues)

        monkeypatch.setattr(authoring, "MAX_VALIDATION_FILE_COUNT", 100)
        monkeypatch.setattr(authoring, "MAX_VALIDATION_TREE_BYTES", 2)
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=100, max_bytes=2
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_TREE_LIMIT" for issue in preflight.issues)

    def test_preflight_stops_iterating_after_bound(self, tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        directory = workspace / "knowledge" / "sources"
        for index in range(5):
            (directory / f"d{index}.md").write_text("x\n")
        real_iterdir = Path.iterdir
        consumed = 0

        def counting_iterdir(self: Path):  # noqa: ANN001
            nonlocal consumed
            for entry in real_iterdir(self):
                consumed += 1
                yield entry

        monkeypatch.setattr(Path, "iterdir", counting_iterdir)
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=2, max_bytes=100
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_COUNT_LIMIT" for issue in preflight.issues)
        # The walk stops the overflow directory at the bound instead of
        # consuming all five entries (workspace + three managed dirs first).
        assert consumed <= 8

    def test_preflight_global_count_across_directories(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        for index in range(3):
            (workspace / "evals" / f"d{index}.jsonl").write_text("x\n")
        (workspace / "behavior" / "system.md").write_text("x\n")
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=3, max_bytes=100
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_COUNT_LIMIT" for issue in preflight.issues)

    def test_preflight_includes_root_auxiliary_and_nested_files(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        (workspace / "notes.txt").write_text("root\n")
        nested = workspace / "arbitrary" / "deep"
        nested.mkdir(parents=True)
        (nested / "payload.md").write_text("nested\n")
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=100, max_bytes=100
        )
        assert preflight.ok
        names = {path.name for path in preflight.files}
        assert "notes.txt" in names
        assert "payload.md" in names

    def test_preflight_rejects_huge_root_auxiliary_before_validator(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        (workspace / "huge.txt").write_text("x" * 64)
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=100, max_bytes=8
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_TREE_LIMIT" for issue in preflight.issues)

    def test_preflight_bounds_depth(self, tmp_path: Path) -> None:
        workspace = tmp_path / "capabilities" / "1"
        authoring.ensure_workspace(workspace)
        deep = workspace
        for _ in range(authoring.MAX_VALIDATION_DEPTH + 3):
            deep = deep / "d"
        deep.mkdir(parents=True)
        (deep / "payload.md").write_text("x\n")
        preflight = authoring.validate_source_preflight(
            workspace, tmp_path, max_files=100, max_bytes=100
        )
        assert not preflight.ok
        assert any(issue.code == "SOURCE_DEPTH_LIMIT" for issue in preflight.issues)
