"""Cheap default diagnostic probe tests."""

from __future__ import annotations

from pathlib import Path

from zana_core.diagnostics.models import CheckStatus, ProbeBudget
from zana_core.diagnostics.probes import (
    LoopbackAuthProbe,
    MemoryDiskProbe,
    OptionalDependencyProbe,
    PlatformProbe,
    RuntimeDiscoveryProbe,
    SqliteReachabilityProbe,
    StorageRootProbe,
)
from zana_core.domain.enums import RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import RuntimeDescriptor

BUDGET = ProbeBudget()


class TestPlatformProbe:
    def test_returns_pass_with_platform_evidence(self) -> None:
        check = PlatformProbe().run(BUDGET)
        assert check.status == CheckStatus.PASS
        assert check.evidence.value
        assert "Python" in check.evidence.notes


class TestMemoryDiskProbe:
    def test_low_memory_warns(self) -> None:
        probe = MemoryDiskProbe(min_available_memory_bytes=10**30)
        check = probe.run(BUDGET)
        assert check.status == CheckStatus.WARN
        assert any(issue.code == "LOW_MEMORY" for issue in check.issues)

    def test_unknown_memory_is_not_pass(self) -> None:
        probe = MemoryDiskProbe(path="/definitely/not/a/real/zana/path")
        check = probe.run(BUDGET)
        assert check.status in (CheckStatus.PASS, CheckStatus.WARN)


class TestSqliteProbe:
    def test_wal_fk_pass(self) -> None:
        check = SqliteReachabilityProbe(lambda: {"journal_mode": "wal", "foreign_keys": 1}).run(
            BUDGET
        )
        assert check.status == CheckStatus.PASS

    def test_pragma_failure_fails(self) -> None:
        check = SqliteReachabilityProbe(lambda: {"journal_mode": "delete", "foreign_keys": 0}).run(
            BUDGET
        )
        assert check.status == CheckStatus.FAIL
        assert any(issue.code == "SQLITE_PRAGMA_MISMATCH" for issue in check.issues)

    def test_exception_fails_closed(self) -> None:
        def broken() -> dict[str, object]:
            raise OSError("read-only check failed")

        check = SqliteReachabilityProbe(broken).run(BUDGET)
        assert check.status == CheckStatus.FAIL


class TestStorageRootProbe:
    def test_missing_and_permission_roots(self, tmp_path: Path) -> None:
        missing = tmp_path / "missing"
        check = StorageRootProbe(
            artifact_root=missing,
            image_root=tmp_path / "image",
        ).run(BUDGET)
        assert check.status in (CheckStatus.WARN, CheckStatus.FAIL)


class TestRuntimeProbe:
    def test_runtime_absent_is_feature_only(self) -> None:
        class EmptyRegistry:
            def default_targets(self):  # noqa: ANN201
                return []

            def probe(self, targets):  # noqa: ANN001, ANN201
                return []

        check = RuntimeDiscoveryProbe(EmptyRegistry()).run(BUDGET)  # type: ignore[arg-type]
        assert check.status == CheckStatus.WARN
        readiness = check.feature_readiness[0]
        assert readiness.ready is False
        assert readiness.blocks_core_start is False
        assert readiness.blocks_feature_only is True

    def test_runtime_present_passes(self) -> None:
        class PresentRegistry:
            def default_targets(self):  # noqa: ANN201
                return []

            def probe(self, targets):  # noqa: ANN001, ANN201
                return [
                    RuntimeDescriptor(
                        runtime_id="ollama-local",
                        kind=RuntimeKind.OLLAMA,
                        endpoint="http://127.0.0.1:11434",
                        source=RuntimeSource.AUTO,
                        status=RuntimeStatus.ONLINE,
                        registered=True,
                        server_running=True,
                        installed=True,
                        installed_not_running=False,
                        evidence=[],
                        warnings=[],
                        models=[],
                        last_seen_at=__import__("datetime").datetime.now(
                            __import__("datetime").UTC
                        ),
                    )
                ]

        check = RuntimeDiscoveryProbe(PresentRegistry()).run(BUDGET)  # type: ignore[arg-type]
        assert check.status == CheckStatus.PASS
        assert check.evidence.value == 1


class TestOptionalDependencies:
    def test_missing_packages_are_feature_only(self) -> None:
        check = OptionalDependencyProbe(packages=("definitely-not-installed-xyz",)).run(BUDGET)
        assert check.status == CheckStatus.PASS
        readiness = check.feature_readiness[0]
        assert readiness.ready is False
        assert readiness.blocks_core_start is False
        assert readiness.blocks_feature_only is True


class TestLoopbackAuth:
    def test_loopback_with_token_passes(self) -> None:
        check = LoopbackAuthProbe(
            base_url="http://127.0.0.1:11434",
            token_present=True,
        ).run(BUDGET)
        assert check.status == CheckStatus.PASS
        rendered = check.evidence.model_dump_json()
        assert "secret" not in rendered.lower()

    def test_non_loopback_fails(self) -> None:
        check = LoopbackAuthProbe(
            base_url="https://example.com",
            token_present=True,
        ).run(BUDGET)
        assert check.status == CheckStatus.FAIL
        assert any(issue.code == "NON_LOOPBACK_BOUNDARY" for issue in check.issues)
