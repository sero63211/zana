"""Workspace path safety and cleanup-plan tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from zana_core.builds.workspaces import BuildWorkspace, WorkspaceEscapeError


class TestBuildWorkspace:
    def test_derives_jobs_path_under_data_root(self, tmp_path: Path) -> None:
        workspace = BuildWorkspace(tmp_path)
        path = workspace.job_path("job-1")
        assert path == tmp_path / "jobs" / "job-1"
        assert path.is_relative_to(tmp_path)

    def test_rejects_traversal_job_id(self, tmp_path: Path) -> None:
        workspace = BuildWorkspace(tmp_path)
        with pytest.raises(WorkspaceEscapeError):
            workspace.job_path("../escape")

    def test_rejects_absolute_and_dot_job_ids(self, tmp_path: Path) -> None:
        workspace = BuildWorkspace(tmp_path)
        with pytest.raises(WorkspaceEscapeError):
            workspace.job_path("/etc/passwd")
        with pytest.raises(WorkspaceEscapeError):
            workspace.job_path("..")

    def test_cleanup_plan_is_data_only(self, tmp_path: Path) -> None:
        workspace = BuildWorkspace(tmp_path)
        plan = workspace.cleanup_plan("job-1")
        assert plan["remove_paths"] == [str(tmp_path / "jobs" / "job-1")]
        assert plan["notes"]
