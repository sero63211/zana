"""Cross-platform explicit routing and unsupported-probe tests."""

from __future__ import annotations

from zana_core.diagnostics.models import CheckStatus, ProbeBudget
from zana_core.diagnostics.probes import PlatformProbe, collect_platform_facts


class TestCrossPlatform:
    def test_platform_facts_are_unknown_not_zero(self) -> None:
        facts = collect_platform_facts()
        assert facts.os_name
        assert facts.arch
        assert facts.python_version
        assert facts.user_name != ""

    def test_platform_probe_does_not_assume_admin_or_os_commands(self) -> None:
        check = PlatformProbe().run(ProbeBudget())
        assert check.status == CheckStatus.PASS
        assert check.evidence.value
        # No macOS-only command evidence can leak into the generic report.
        rendered = check.model_dump_json()
        assert "system_profiler" not in rendered
        assert "sudo" not in rendered
