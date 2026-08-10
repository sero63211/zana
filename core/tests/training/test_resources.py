"""Resource guard threshold tests."""

from __future__ import annotations

from zana_core.training.contracts import ResourceGuardDecision
from zana_core.training.resources import ResourceGuards


class TestResourceGuards:
    def test_all_allow_within_limits(self) -> None:
        guards = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=1000,
            disk_free_bytes=2000,
            max_memory_fraction=0.5,
            disk_reserve_bytes=100,
            dry_run_required=False,
        ).evaluate(
            required_ram_bytes=400,
            required_vram_bytes=800,
            required_disk_bytes=1500,
            provider_supports_dry_run=False,
        )
        assert ResourceGuards.all_allow(guards) is True

    def test_ram_fraction_blocks(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=None,
            disk_free_bytes=None,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=False,
        ).ram_guard(600)
        assert guard.decision == ResourceGuardDecision.BLOCK

    def test_unknown_ram_never_guesses_success(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=None,
            available_vram_bytes=None,
            disk_free_bytes=None,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=False,
        ).ram_guard(1)
        assert guard.decision == ResourceGuardDecision.UNKNOWN

    def test_unknown_is_never_allow(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=None,
            available_vram_bytes=1000,
            disk_free_bytes=1000,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=False,
        ).ram_guard(1)
        assert ResourceGuards.all_allow([guard]) is False

    def test_disk_reserve_blocks(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=1000,
            disk_free_bytes=100,
            max_memory_fraction=0.5,
            disk_reserve_bytes=200,
            dry_run_required=False,
        ).disk_guard(100)
        assert guard.decision == ResourceGuardDecision.BLOCK

    def test_dry_run_required_blocks_without_support(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=1000,
            disk_free_bytes=1000,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=True,
        ).dry_run_guard(provider_supports_dry_run=False)
        assert guard.decision == ResourceGuardDecision.BLOCK

    def test_dry_run_required_always_blocks_fail_closed(self) -> None:
        guard = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=1000,
            disk_free_bytes=1000,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=True,
        ).dry_run_guard(provider_supports_dry_run=True)
        assert guard.decision == ResourceGuardDecision.BLOCK
        assert "not supported" in guard.reason

    def test_required_values_must_be_positive(self) -> None:
        guards = ResourceGuards(
            available_ram_bytes=1000,
            available_vram_bytes=1000,
            disk_free_bytes=1000,
            max_memory_fraction=0.5,
            disk_reserve_bytes=0,
            dry_run_required=False,
        )
        try:
            guards.ram_guard(0)
        except ValueError:
            pass
        else:
            raise AssertionError("expected zero required RAM rejection")
