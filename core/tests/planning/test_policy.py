"""BuildPolicy validation tests: unsafe and contradictory settings fail closed."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from zana_core.planning.models import (
    AcquisitionMode,
    BuildPolicy,
    DownloadMode,
    StrategyMode,
)


def test_default_policy_is_valid_and_frozen():
    policy = BuildPolicy()
    assert policy.strategy == StrategyMode.AUTO
    assert policy.acquisition == AcquisitionMode.DENY_AFTER_ACQUISITION
    assert policy.max_memory_fraction == 0.75
    assert policy.require_verification is True
    with pytest.raises(ValidationError):
        policy.max_disk_gb = 30


def test_prefer_training_without_adapter_allowed_is_contradictory():
    with pytest.raises(ValidationError):
        BuildPolicy(prefer_training=True, allow_adapter_training=False)


def test_offline_acquisition_with_downloads_allowed_is_contradictory():
    with pytest.raises(ValidationError):
        BuildPolicy(
            acquisition=AcquisitionMode.OFFLINE,
            allow_external_artifact_downloads=DownloadMode.ALLOWED,
        )
    with pytest.raises(ValidationError):
        BuildPolicy(
            acquisition=AcquisitionMode.OFFLINE,
            allow_external_artifact_downloads=DownloadMode.ASK,
        )


def test_unsafe_fraction_and_disk_values_fail_closed():
    for fraction in (0, -0.1, 1.01):
        with pytest.raises(ValidationError):
            BuildPolicy(max_memory_fraction=fraction)
    for disk in (0, -5):
        with pytest.raises(ValidationError):
            BuildPolicy(max_disk_gb=disk)


def test_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        BuildPolicy(allow_hidden_install_hooks=True)
