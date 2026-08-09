"""Approved build workspace derivation and path safety."""

from __future__ import annotations

from pathlib import Path


class WorkspaceError(ValueError):
    """Base workspace failure."""


class WorkspaceEscapeError(WorkspaceError):
    """Raised when a job path escapes the approved data root."""


class BuildWorkspace:
    """Derives data/jobs/<validated-job-id> under an injected approved root."""

    def __init__(self, data_root: str | Path) -> None:
        self.data_root = Path(data_root).resolve(strict=True)

    def job_path(self, job_id: str) -> Path:
        self._validate_job_id(job_id)
        candidate = (self.data_root / "jobs" / job_id).resolve(strict=False)
        try:
            candidate.relative_to(self.data_root)
        except ValueError as error:
            raise WorkspaceEscapeError("Job path escapes the approved data root.") from error
        return candidate

    def cleanup_plan(self, job_id: str) -> dict[str, object]:
        path = self.job_path(job_id)
        return {
            "remove_paths": [str(path)],
            "retain_paths": [],
            "notes": ["Temporary job workspace may be cleaned after cancellation/failure."],
        }

    @staticmethod
    def _validate_job_id(job_id: str) -> None:
        if not job_id or "/" in job_id or "\\" in job_id or job_id in {".", ".."}:
            raise WorkspaceEscapeError("Job id must be a single safe path segment.")
