"""Explicit resource guard decisions without guessed success or duration."""

from __future__ import annotations

from zana_core.training.contracts import ResourceGuard, ResourceGuardDecision


class ResourceGuards:
    """Evaluates RAM/VRAM, disk reserve, and dry-run requirements."""

    def __init__(
        self,
        *,
        available_ram_bytes: int | None,
        available_vram_bytes: int | None,
        disk_free_bytes: int | None,
        max_memory_fraction: float,
        disk_reserve_bytes: int,
        dry_run_required: bool,
    ) -> None:
        self.available_ram_bytes = available_ram_bytes
        self.available_vram_bytes = available_vram_bytes
        self.disk_free_bytes = disk_free_bytes
        self.max_memory_fraction = max_memory_fraction
        self.disk_reserve_bytes = disk_reserve_bytes
        self.dry_run_required = dry_run_required

    def ram_guard(self, required_bytes: int) -> ResourceGuard:
        if self.available_ram_bytes is None:
            return ResourceGuard(
                resource="ram",
                decision=ResourceGuardDecision.UNKNOWN,
                reason="available RAM is unknown",
            )
        limit = int(self.available_ram_bytes * self.max_memory_fraction)
        allowed = required_bytes <= limit
        return ResourceGuard(
            resource="ram",
            decision=ResourceGuardDecision.ALLOW if allowed else ResourceGuardDecision.BLOCK,
            available=self.available_ram_bytes,
            required=required_bytes,
            reason=(
                "RAM within configured fraction"
                if allowed
                else "RAM exceeds configured maximum fraction"
            ),
        )

    def vram_guard(self, required_bytes: int) -> ResourceGuard:
        if self.available_vram_bytes is None:
            return ResourceGuard(
                resource="vram",
                decision=ResourceGuardDecision.UNKNOWN,
                reason="available VRAM is unknown",
            )
        allowed = required_bytes <= self.available_vram_bytes
        return ResourceGuard(
            resource="vram",
            decision=ResourceGuardDecision.ALLOW if allowed else ResourceGuardDecision.BLOCK,
            available=self.available_vram_bytes,
            required=required_bytes,
            reason=(
                "VRAM within available capacity" if allowed else "VRAM exceeds available capacity"
            ),
        )

    def disk_guard(self, required_bytes: int) -> ResourceGuard:
        if self.disk_free_bytes is None:
            return ResourceGuard(
                resource="disk",
                decision=ResourceGuardDecision.UNKNOWN,
                reason="available disk space is unknown",
            )
        allowed = required_bytes + self.disk_reserve_bytes <= self.disk_free_bytes
        return ResourceGuard(
            resource="disk",
            decision=ResourceGuardDecision.ALLOW if allowed else ResourceGuardDecision.BLOCK,
            available=self.disk_free_bytes,
            required=required_bytes + self.disk_reserve_bytes,
            reason=(
                "disk reserve satisfied"
                if allowed
                else "disk checkpoint/temp reserve would be exceeded"
            ),
        )

    def dry_run_guard(self, provider_supports_dry_run: bool) -> ResourceGuard:
        if not self.dry_run_required:
            return ResourceGuard(
                resource="dry_run",
                decision=ResourceGuardDecision.ALLOW,
                reason="dry run not required",
            )
        if not provider_supports_dry_run:
            return ResourceGuard(
                resource="dry_run",
                decision=ResourceGuardDecision.BLOCK,
                reason="provider dry-run is required but unsupported",
            )
        return ResourceGuard(
            resource="dry_run",
            decision=ResourceGuardDecision.ALLOW,
            reason="provider dry-run support available",
        )

    def evaluate(
        self,
        *,
        required_ram_bytes: int,
        required_vram_bytes: int,
        required_disk_bytes: int,
        provider_supports_dry_run: bool,
    ) -> list[ResourceGuard]:
        return [
            self.ram_guard(required_ram_bytes),
            self.vram_guard(required_vram_bytes),
            self.disk_guard(required_disk_bytes),
            self.dry_run_guard(provider_supports_dry_run),
        ]

    @staticmethod
    def all_allow(guards: list[ResourceGuard]) -> bool:
        return all(guard.decision == ResourceGuardDecision.ALLOW for guard in guards)
