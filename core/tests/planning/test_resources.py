"""Conservative disk/memory estimate and policy/hardware comparison tests."""

from __future__ import annotations

from tests.planning.helpers import plan_inputs, policy
from zana_core.planning.planner import BuildPlanner


def plan(**inputs):
    return BuildPlanner().plan(**plan_inputs(**inputs))


def test_estimate_has_ranges_assumptions_and_no_duration_promise():
    result = plan()
    estimate = result.resource_estimate
    assert estimate.disk_bytes_min is not None and estimate.disk_bytes_min > 0
    assert estimate.disk_bytes_max >= estimate.disk_bytes_min
    assert estimate.memory_bytes_min is not None
    assert estimate.memory_bytes_max >= estimate.memory_bytes_min
    assert estimate.duration_estimate == "unknown"
    assert estimate.safety_reserve_fraction == 0.15
    assert estimate.assumptions
    assert estimate.disk_bytes_max_with_reserve > estimate.disk_bytes_max


def test_disk_over_policy_limit_blocks_build():
    result = plan(policy=policy(max_disk_gb=0.001))
    assert result.resource_check.disk_within_policy is False
    assert any("DISK_OVER_POLICY_LIMIT" in blocker for blocker in result.blockers)
    assert result.approvable is False


def test_disk_over_available_space_blocks_build():
    result = plan(
        hardware=plan_inputs()["hardware"].model_copy(update={"disk_free_bytes": 1_000_000})
    )
    assert result.resource_check.disk_within_available is False
    assert any("DISK_INSUFFICIENT" in blocker for blocker in result.blockers)


def test_memory_over_policy_fraction_blocks_build():
    tiny_memory = plan_inputs()["hardware"].model_copy(update={"memory_total_bytes": 2_000_000_000})
    result = plan(hardware=tiny_memory)
    assert result.resource_check.memory_within_policy is False
    assert any("MEMORY_OVER_POLICY_LIMIT" in blocker for blocker in result.blockers)


def test_memory_over_available_blocks_build():
    result = plan(
        hardware=plan_inputs()["hardware"].model_copy(
            update={"memory_available_bytes": 500_000_000}
        )
    )
    assert result.resource_check.memory_within_available is False
    assert any("MEMORY_INSUFFICIENT" in blocker for blocker in result.blockers)


def test_unknown_model_size_is_honest_not_claimed_safe():
    result = plan(
        model=plan_inputs()["model"].model_copy(
            update={"size_bytes": None, "parameter_count": None}
        )
    )
    estimate = result.resource_estimate
    assert estimate.disk_bytes_min is None
    assert estimate.disk_bytes_max is None
    assert estimate.memory_bytes_min is None
    assert any("unknown" in warning for warning in result.resource_check.warnings)


def test_unknown_disk_space_warns_not_false_ok():
    result = plan(hardware=plan_inputs()["hardware"].model_copy(update={"disk_free_bytes": None}))
    assert result.resource_check.disk_within_available is None
    assert any("unconfirmed" in warning for warning in result.resource_check.warnings)


def test_provider_arch_incompatible_blocks_adapter():
    from zana_core.planning.models import TrainingProviderCompatibility

    result = plan(
        provider=TrainingProviderCompatibility(
            provider_id="hf_peft",
            supported=True,
            installed=True,
            compatible_arch=False,
        )
    )
    assert any("PROVIDER_ARCH_INCOMPATIBLE" in blocker for blocker in result.blockers)
