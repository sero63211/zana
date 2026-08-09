"""Cheap default diagnostic probes using existing libraries only."""

from __future__ import annotations

import importlib.metadata
import os
import platform
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

import psutil

from zana_core.diagnostics.models import (
    CheckStatus,
    DiagnosticCheck,
    DiagnosticIssue,
    Evidence,
    FeatureReadiness,
    ProbeBudget,
    RecoveryAction,
    Severity,
)
from zana_core.runtimes.base import RuntimeStatus
from zana_core.runtimes.registry import RuntimeProbeRegistry


class DiagnosticProbe(Protocol):
    """Protocol for one bounded sequential diagnostic probe."""

    check_id: str
    name: str

    def run(self, budget: ProbeBudget) -> DiagnosticCheck: ...


class ProbeTimeoutError(TimeoutError):
    """Raised when a probe exceeds its per-check timeout."""


@dataclass(frozen=True)
class PlatformFacts:
    os_name: str
    arch: str
    python_version: str
    user_name: str
    data_root: str | None = None


def collect_platform_facts() -> PlatformFacts:
    return PlatformFacts(
        os_name=platform.system(),
        arch=platform.machine(),
        python_version=platform.python_version(),
        user_name=os.environ.get("USER") or os.environ.get("USERNAME") or "unknown",
    )


class PlatformProbe:
    check_id = "platform"
    name = "OS/arch/Python and application paths"

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        facts = collect_platform_facts()
        data_root = _default_data_root()
        evidence = Evidence(
            observed_source="platform",
            value=f"{facts.os_name}/{facts.arch}",
            boolean_presence=bool(data_root),
            notes=["Python", facts.python_version],
        )
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            duration_seconds=0.0,
            observed_source="platform",
            evidence=evidence,
        )


class MemoryDiskProbe:
    check_id = "memory-disk"
    name = "Memory and disk headroom"

    def __init__(
        self,
        *,
        min_available_memory_bytes: int = 256 * 1024 * 1024,
        min_free_disk_bytes: int = 1 * 1024 * 1024 * 1024,
        path: str | Path | None = None,
    ) -> None:
        self.min_memory = min_available_memory_bytes
        self.min_disk = min_free_disk_bytes
        self.path = Path(path) if path else Path.cwd()

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        try:
            memory = psutil.virtual_memory()
            available = int(memory.available)
        except Exception:  # noqa: BLE001
            available = None
        try:
            usage = shutil.disk_usage(self.path)
            free = int(usage.free)
        except OSError:
            free = None
        issues: list[DiagnosticIssue] = []
        status = CheckStatus.PASS
        severity = Severity.INFO
        if available is not None and available < self.min_memory:
            issues.append(
                DiagnosticIssue(
                    code="LOW_MEMORY",
                    severity=Severity.WARN,
                    message="Available memory is below the recommended minimum.",
                    recovery_actions=[
                        RecoveryAction(
                            code="CLOSE_HEAVY_APPS",
                            message="Close memory-heavy applications before builds or training.",
                        )
                    ],
                )
            )
            status = CheckStatus.WARN
            severity = Severity.WARN
        if free is not None and free < self.min_disk:
            issues.append(
                DiagnosticIssue(
                    code="LOW_DISK",
                    severity=Severity.WARN,
                    message="Free disk space is below the recommended minimum.",
                    recovery_actions=[
                        RecoveryAction(
                            code="FREE_DISK",
                            message="Free disk space before model or build acquisition.",
                        )
                    ],
                )
            )
            status = CheckStatus.WARN
            severity = Severity.WARN
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=status,
            severity=severity,
            duration_seconds=0.0,
            observed_source="psutil/shutil",
            evidence=Evidence(
                observed_source="psutil/shutil",
                value=free,
                boolean_presence=available is not None and free is not None,
                notes=[
                    "available_bytes",
                    str(available) if available is not None else "unavailable",
                ],
            ),
            issues=issues,
        )


class SqliteReachabilityProbe:
    """Uses an injected read-only checker; never mutates the database."""

    check_id = "sqlite"
    name = "SQLite reachability and pragmas"

    def __init__(self, checker: Any) -> None:
        self.checker = checker

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        try:
            state = self.checker()
        except Exception:  # noqa: BLE001
            return DiagnosticCheck(
                check_id=self.check_id,
                name=self.name,
                status=CheckStatus.FAIL,
                severity=Severity.ERROR,
                duration_seconds=0.0,
                observed_source="injected-sqlite-checker",
                evidence=Evidence(
                    observed_source="injected-sqlite-checker",
                    boolean_presence=False,
                    notes=["unreachable"],
                ),
                issues=[
                    DiagnosticIssue(
                        code="SQLITE_UNREACHABLE",
                        severity=Severity.ERROR,
                        message="SQLite could not be reached for a read-only diagnostic.",
                        recovery_actions=[
                            RecoveryAction(
                                code="RESTART_CORE",
                                message="Restart ZANA Core to reopen the local database.",
                            )
                        ],
                    )
                ],
            )
        ok = (
            isinstance(state, dict)
            and state.get("journal_mode") == "wal"
            and state.get("foreign_keys") == 1
        )
        issues = []
        if not ok:
            issues.append(
                DiagnosticIssue(
                    code="SQLITE_PRAGMA_MISMATCH",
                    severity=Severity.ERROR,
                    message="SQLite is not using the required WAL/foreign-key settings.",
                    recovery_actions=[
                        RecoveryAction(
                            code="MIGRATE_OR_RECREATE",
                            message="Run migrations or restore the local database from backup.",
                        )
                    ],
                )
            )
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            severity=Severity.INFO if ok else Severity.ERROR,
            duration_seconds=0.0,
            observed_source="injected-sqlite-checker",
            evidence=Evidence(
                observed_source="injected-sqlite-checker",
                boolean_presence=ok,
                notes=["wal", str(state.get("journal_mode"))],
            ),
            issues=issues,
        )


class StorageRootProbe:
    """Metadata-only storage root check; never scans or hashes artifacts."""

    check_id = "storage-roots"
    name = "Artifact and image store roots"

    def __init__(
        self,
        *,
        artifact_root: str | Path,
        image_root: str | Path,
        max_path_count: int = 32,
    ) -> None:
        self.artifact_root = Path(artifact_root)
        self.image_root = Path(image_root)
        self.max_path_count = max_path_count

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        roots = [self.artifact_root, self.image_root]
        if len(roots) > budget.max_path_count:
            return self._failed("too many storage roots")
        failures: list[DiagnosticIssue] = []
        observed: list[str] = []
        for root in roots:
            try:
                if not root.exists():
                    failures.append(
                        DiagnosticIssue(
                            code="STORAGE_ROOT_MISSING",
                            severity=Severity.WARN,
                            message="A storage root does not exist yet.",
                            recovery_actions=[
                                RecoveryAction(
                                    code="CREATE_STORAGE_ROOT",
                                    message="ZANA will create the root when first used.",
                                    optional=True,
                                )
                            ],
                        )
                    )
                elif not os.access(root, os.R_OK | os.W_OK):
                    failures.append(
                        DiagnosticIssue(
                            code="STORAGE_ROOT_PERMISSIONS",
                            severity=Severity.ERROR,
                            message="A storage root is not readable and writable.",
                            recovery_actions=[
                                RecoveryAction(
                                    code="FIX_STORAGE_PERMISSIONS",
                                    message="Repair the storage root permissions and retry.",
                                )
                            ],
                        )
                    )
                observed.append(root.name)
            except OSError:
                failures.append(
                    DiagnosticIssue(
                        code="STORAGE_ROOT_UNREADABLE",
                        severity=Severity.ERROR,
                        message="A storage root could not be inspected.",
                        recovery_actions=[
                            RecoveryAction(
                                code="REPAIR_STORAGE_ROOT",
                                message="Repair the storage root path and retry.",
                            )
                        ],
                    )
                )
        has_error = any(item.severity == Severity.ERROR for item in failures)
        status = (
            CheckStatus.FAIL if has_error else CheckStatus.WARN if failures else CheckStatus.PASS
        )
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=status,
            severity=Severity.INFO if status == CheckStatus.PASS else Severity.WARN,
            duration_seconds=0.0,
            observed_source="pathlib/os.access",
            evidence=Evidence(
                observed_source="pathlib/os.access",
                boolean_presence=not failures,
                notes=observed[:4],
            ),
            issues=failures,
        )

    def _failed(self, message: str) -> DiagnosticCheck:
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.FAIL,
            severity=Severity.ERROR,
            duration_seconds=0.0,
            observed_source="pathlib",
            evidence=Evidence(
                observed_source="pathlib",
                boolean_presence=False,
                notes=[message],
            ),
            issues=[
                DiagnosticIssue(
                    code="STORAGE_PATH_BUDGET",
                    severity=Severity.ERROR,
                    message=message,
                    recovery_actions=[
                        RecoveryAction(
                            code="REDUCE_STORAGE_ROOTS",
                            message="Configure fewer storage roots.",
                        )
                    ],
                )
            ],
        )


class RuntimeDiscoveryProbe:
    """Bounded existing discovery interface only; no model load/start/pull."""

    check_id = "runtimes"
    name = "Available runtime endpoints"

    def __init__(self, registry: RuntimeProbeRegistry) -> None:
        self.registry = registry

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        descriptors = self.registry.probe(self.registry.default_targets())
        online = [item.runtime_id for item in descriptors if item.status == RuntimeStatus.ONLINE]
        installed_not_running = [
            item.runtime_id for item in descriptors if item.installed_not_running
        ]
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS if online else CheckStatus.WARN,
            severity=Severity.INFO if online else Severity.WARN,
            duration_seconds=0.0,
            observed_source="runtime-registry",
            evidence=Evidence(
                observed_source="runtime-registry",
                value=len(online),
                boolean_presence=bool(online),
                notes=[
                    *online[:4],
                    *(f"{name}:installed-not-running" for name in installed_not_running[:2]),
                ],
            ),
            feature_readiness=[
                FeatureReadiness(
                    feature="runtime_discovery",
                    ready=bool(online),
                    blocks_core_start=False,
                    blocks_feature_only=True,
                    missing_reason="No local runtime is online." if not online else "",
                )
            ],
            issues=[]
            if online
            else [
                DiagnosticIssue(
                    code="NO_RUNTIME_ONLINE",
                    severity=Severity.WARN,
                    message="No supported local runtime endpoint is currently online.",
                    recovery_actions=[
                        RecoveryAction(
                            code="START_RUNTIME_MANUALLY",
                            message="Start your local runtime or add a manual endpoint.",
                            optional=True,
                        )
                    ],
                )
            ],
        )


class OptionalDependencyProbe:
    """Metadata-only optional dependency check; never imports heavy packages."""

    OPTIONAL_PACKAGES = ("lancedb", "docling", "zstandard", "mlx_lm", "peft")

    check_id = "optional-dependencies"
    name = "Optional dependency metadata"

    def __init__(self, packages: tuple[str, ...] = OPTIONAL_PACKAGES) -> None:
        self.packages = packages

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        findings: list[tuple[str, bool]] = []
        for package in self.packages:
            try:
                importlib.metadata.version(package)
                findings.append((package, True))
            except importlib.metadata.PackageNotFoundError:
                findings.append((package, False))
        readiness = [
            FeatureReadiness(
                feature=package,
                ready=found,
                blocks_core_start=False,
                blocks_feature_only=True,
                missing_reason="" if found else "optional package not installed",
            )
            for package, found in findings
        ]
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS,
            severity=Severity.INFO,
            duration_seconds=0.0,
            observed_source="importlib.metadata",
            evidence=Evidence(
                observed_source="importlib.metadata",
                value=sum(1 for _, found in findings if found),
                boolean_presence=True,
                notes=[
                    f"{name}:{'present' if found else 'absent'}" for name, found in findings[:8]
                ],
            ),
            feature_readiness=readiness,
        )


class LoopbackAuthProbe:
    """Checks loopback/auth configuration presence without exposing the token."""

    check_id = "loopback-auth"
    name = "Loopback and authentication configuration"

    def __init__(self, *, base_url: str, token_present: bool) -> None:
        self.base_url = base_url
        self.token_present = token_present

    def run(self, budget: ProbeBudget) -> DiagnosticCheck:
        loopback = self.base_url.startswith("http://127.0.0.1") or self.base_url.startswith(
            "http://localhost"
        )
        ok = loopback and self.token_present
        issues = []
        if not loopback:
            issues.append(
                DiagnosticIssue(
                    code="NON_LOOPBACK_BOUNDARY",
                    severity=Severity.ERROR,
                    message="Core API is not configured for loopback only.",
                    recovery_actions=[
                        RecoveryAction(
                            code="BIND_LOOPBACK",
                            message="Configure Core to bind to 127.0.0.1.",
                        )
                    ],
                )
            )
        if not self.token_present:
            issues.append(
                DiagnosticIssue(
                    code="AUTH_TOKEN_MISSING",
                    severity=Severity.ERROR,
                    message="Per-launch authentication is not configured.",
                    recovery_actions=[
                        RecoveryAction(
                            code="RESTART_DESKTOP",
                            message="Restart the desktop app to rotate the local token.",
                        )
                    ],
                )
            )
        return DiagnosticCheck(
            check_id=self.check_id,
            name=self.name,
            status=CheckStatus.PASS if ok else CheckStatus.FAIL,
            severity=Severity.INFO if ok else Severity.ERROR,
            duration_seconds=0.0,
            observed_source="server-config",
            evidence=Evidence(
                observed_source="server-config",
                boolean_presence=ok,
                notes=["loopback", str(loopback), "token_present", str(self.token_present)],
            ),
            issues=issues,
        )


def _default_data_root() -> str | None:
    try:
        import platformdirs

        return platformdirs.user_data_dir("zana", appauthor=False)
    except Exception:  # noqa: BLE001
        return None
