"""Project-root-relative path resolution with traversal and symlink escape rejection."""

from __future__ import annotations

import os
import re
import stat
from pathlib import Path

from zana_core.capabilities.errors import CapabilityIssue, relative_label

_DRIVE_PREFIX = re.compile(r"^[A-Za-z]:[/\\]")

PROHIBITED_SUFFIXES = frozenset(
    {
        ".a",
        ".app",
        ".bash",
        ".bat",
        ".bin",
        ".cjs",
        ".class",
        ".cmd",
        ".com",
        ".csh",
        ".deb",
        ".dll",
        ".dmg",
        ".dylib",
        ".exe",
        ".fish",
        ".ipa",
        ".jar",
        ".js",
        ".jsx",
        ".ksh",
        ".lua",
        ".mjs",
        ".msi",
        ".node",
        ".o",
        ".php",
        ".pkg",
        ".pl",
        ".ps1",
        ".py",
        ".pyc",
        ".pyo",
        ".rb",
        ".rlib",
        ".rpm",
        ".sh",
        ".so",
        ".ts",
        ".tsx",
        ".war",
        ".wasm",
        ".zsh",
    }
)

PROHIBITED_BASENAMES = frozenset(
    {"dockerfile", "justfile", "makefile", "procfile", "requirements.txt"}
)


class PathResolutionError(Exception):
    """Raised when a declared path is unsafe or does not exist."""

    def __init__(self, issue: CapabilityIssue) -> None:
        self.issue = issue
        super().__init__(issue.render())


def _is_within(root: Path, target: Path) -> bool:
    return target == root or root in target.parents


def resolve_project_path(root: Path, declared: str, *, allow_directory: bool = False) -> Path:
    """Resolve ``declared`` against ``root`` and verify it stays inside it.

    Rejects absolute paths, drive prefixes, backslash separators, empty and
    dot segments, ``..`` traversal, symlink escapes, and missing targets.
    """
    if not declared:
        raise PathResolutionError(CapabilityIssue("PATH_INVALID", "declared path is empty"))
    if "\x00" in declared:
        raise PathResolutionError(CapabilityIssue("PATH_INVALID", "declared path contains NUL"))
    if declared.startswith("/") or declared.startswith("\\") or _DRIVE_PREFIX.match(declared):
        raise PathResolutionError(
            CapabilityIssue(
                "PATH_ABSOLUTE",
                f"declared path {declared!r} must be project-root-relative, not absolute",
            )
        )
    if "\\" in declared:
        raise PathResolutionError(
            CapabilityIssue(
                "PATH_ABSOLUTE",
                f"declared path {declared!r} must use forward slashes without backslashes",
            )
        )
    parts = declared.split("/")
    for part in parts:
        if part in ("", ".", ".."):
            raise PathResolutionError(
                CapabilityIssue(
                    "PATH_TRAVERSAL",
                    f"declared path {declared!r} must not contain empty, dot, or "
                    "parent-directory segments",
                )
            )

    root_resolved = root.resolve()
    candidate = root_resolved.joinpath(*parts)
    candidate_resolved = candidate.resolve(strict=False)
    if not _is_within(root_resolved, candidate_resolved):
        raise PathResolutionError(
            CapabilityIssue(
                "PATH_ESCAPE",
                f"declared path {declared!r} resolves outside the capability root via a symlink",
            )
        )
    if not candidate.exists():
        raise PathResolutionError(
            CapabilityIssue("PATH_NOT_FOUND", f"declared path {declared!r} does not exist")
        )
    if allow_directory:
        if not candidate.is_file() and not candidate.is_dir():
            raise PathResolutionError(
                CapabilityIssue(
                    "PATH_TYPE",
                    f"declared path {declared!r} is neither a file nor a directory",
                )
            )
    elif candidate.is_dir():
        raise PathResolutionError(
            CapabilityIssue(
                "PATH_TYPE",
                f"declared path {declared!r} must be a file, not a directory",
            )
        )
    return candidate_resolved


def scan_package_files(root: Path, issues: list[CapabilityIssue]) -> list[Path]:
    """Return regular files inside ``root`` after safety checks.

    Deterministically walks the package, rejects directory symlinks and
    ``hooks`` directories, and records symlink escapes as issues instead of
    following them.
    """
    root_resolved = root.resolve()
    files: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root_resolved):
        dirnames.sort()
        for name in sorted(dirnames):
            candidate = Path(dirpath) / name
            label = relative_label(root_resolved, candidate)
            if candidate.name.lower() == "hooks":
                issues.append(
                    CapabilityIssue(
                        "HOOK_PROHIBITED",
                        "hooks directories are not allowed in capability sources",
                        label,
                    )
                )
                dirnames.remove(name)
            elif candidate.is_symlink():
                issues.append(
                    CapabilityIssue(
                        "PATH_SYMLINK_DIR",
                        "directory symlinks are not allowed inside capability sources",
                        label,
                    )
                )
                dirnames.remove(name)
        for name in sorted(filenames):
            path = Path(dirpath) / name
            label = relative_label(root_resolved, path)
            if path.is_symlink():
                resolved = path.resolve(strict=False)
                if not _is_within(root_resolved, resolved):
                    issues.append(
                        CapabilityIssue(
                            "PATH_ESCAPE",
                            "symlink resolves outside the capability root",
                            label,
                        )
                    )
                    continue
            files.append(path)
    return files


def is_prohibited_executable(path: Path) -> tuple[bool, str | None]:
    """Detect executable or script content that capability sources must not carry."""
    lowered = path.name.lower()
    if lowered in PROHIBITED_BASENAMES:
        return True, f"prohibited build/install file name {path.name!r}"
    if path.suffix.lower() in PROHIBITED_SUFFIXES:
        return True, f"prohibited code/executable suffix {path.suffix!r}"
    mode = stat.S_IMODE(path.stat().st_mode)
    if mode & 0o111:
        return True, "executable permission bit is set"
    try:
        with path.open("rb") as handle:
            head = handle.read(2)
    except OSError:
        return False, None
    if head.startswith(b"#!"):
        return True, "file starts with a shebang script marker"
    return False, None
