"""Strict probe registry hardening tests (limits, threads, sanitization)."""

from __future__ import annotations

import threading
import time
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta, timezone

import pytest

from tests.runtimes.conftest import FakeTransport, json_response
from zana_core.domain.enums import ModelIdentityStrength, RuntimeKind, RuntimeSource, RuntimeStatus
from zana_core.runtimes.base import (
    AdapterType,
    ModelDescriptor,
    ProbeTarget,
    RuntimeDescriptor,
    RuntimeProbeError,
)
from zana_core.runtimes.executables import ExecutableDiscovery
from zana_core.runtimes.limits import RuntimeProbeLimits
from zana_core.runtimes.registry import RuntimeProbeRegistry, _TargetSnapshot, _truncate_text


def _target(
    runtime_id: str,
    *,
    endpoint: str = "http://127.0.0.1:11434",
    adapter: AdapterType = AdapterType.OLLAMA,
    timeout: float | None = None,
    bearer_token: str | None = None,
    kind: RuntimeKind = RuntimeKind.OLLAMA,
) -> ProbeTarget:
    return ProbeTarget(
        runtime_id=runtime_id,
        kind=kind,
        endpoint=endpoint,
        source=RuntimeSource.AUTO,
        adapter_type=adapter,
        timeout=timeout,
        bearer_token=bearer_token,
    )


def _baseline_threads() -> int:
    return len(threading.enumerate())


def _routes(*endpoints: str) -> dict:
    return {
        ("GET", f"{endpoint}/api/tags"): json_response({"models": []}) for endpoint in endpoints
    }


def _snapshot(runtime_id: str = "runtime-a") -> _TargetSnapshot:
    return _TargetSnapshot(
        runtime_id=runtime_id,
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        adapter_type=AdapterType.OLLAMA,
        bearer_token=None,
        timeout=None,
    )


def test_constructor_rejects_invalid_timeout_and_workers():
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(timeout=0)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(timeout=11.0)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(max_workers=0)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(max_workers=5)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(max_workers=-1)


def test_empty_targets_no_executor():
    registry = RuntimeProbeRegistry(max_workers=4)
    baseline = _baseline_threads()
    assert registry.probe([]) == []
    assert _baseline_threads() == baseline


def test_single_target_synchronous_zero_threads():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport, max_workers=4)
    baseline = _baseline_threads()
    descriptors = registry.probe([_target("only")])
    assert len(descriptors) == 1
    assert descriptors[0].runtime_id == "only"
    assert _baseline_threads() == baseline


def test_worker_one_synchronous_zero_threads():
    transport = FakeTransport(_routes("http://127.0.0.1:11434", "http://127.0.0.1:1234"))
    registry = RuntimeProbeRegistry(transport, max_workers=1)
    baseline = _baseline_threads()
    descriptors = registry.probe([_target("a"), _target("b", endpoint="http://127.0.0.1:1234")])
    assert len(descriptors) == 2
    assert _baseline_threads() == baseline


def test_multiple_targets_join_all_threads_before_return():
    transport = FakeTransport(_routes("http://127.0.0.1:11434", "http://127.0.0.1:1234"))
    registry = RuntimeProbeRegistry(transport, max_workers=2)
    baseline = _baseline_threads()
    descriptors = registry.probe([_target("a"), _target("b", endpoint="http://127.0.0.1:1234")])
    assert len(descriptors) == 2
    assert _baseline_threads() == baseline


def test_target_count_hard_limit_for_sequence():
    registry = RuntimeProbeRegistry()
    with pytest.raises(ValueError):
        registry.probe([_target(f"t-{index}") for index in range(17)])


def test_generator_stops_at_max_plus_one():
    registry = RuntimeProbeRegistry()
    consumed = 0

    def generator() -> Iterable[ProbeTarget]:
        nonlocal consumed
        for index in range(1000):
            consumed += 1
            yield _target(f"g-{index}")

    with pytest.raises(ValueError):
        registry.probe(generator())
    assert consumed == 17


def test_lying_sequence_consumption_stops_at_cap_plus_one():
    consumed = {"count": 0}

    class LyingSequence:
        def __len__(self):
            return 1

        def __iter__(self):
            def generator():
                for index in range(1000):
                    consumed["count"] += 1
                    yield _target(f"lying-{index}")

            return generator()

    registry = RuntimeProbeRegistry()
    with pytest.raises(ValueError):
        registry.probe(LyingSequence())
    assert consumed["count"] == 17


def test_duplicate_target_fails_deterministically():
    registry = RuntimeProbeRegistry()
    with pytest.raises(ValueError) as exc:
        registry.probe([_target("dup"), _target("dup")])
    assert "duplicate runtime_id" in str(exc.value)


def test_invalid_target_rejected():
    registry = RuntimeProbeRegistry()
    with pytest.raises(ValueError):
        registry.probe(["not-a-target"])
    with pytest.raises(ValueError):
        registry.probe([_target("x", timeout=20.0)])


def test_unexpected_probe_failure_isolated_and_sanitized():
    class ExplodingTransport:
        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            raise RuntimeError("secret endpoint=user:pass@host response=<body> traceback")

    registry = RuntimeProbeRegistry(ExplodingTransport(), max_workers=2)
    descriptors = registry.probe(
        [_target("bad"), _target("also-bad", endpoint="http://127.0.0.1:1234")]
    )
    assert len(descriptors) == 2
    for descriptor in descriptors:
        assert descriptor.status == RuntimeStatus.ERROR
        assert descriptor.registered is False
        assert "user:pass" not in descriptor.error
        assert "<body>" not in descriptor.error
        assert "traceback" not in descriptor.error
        assert "secret" not in descriptor.error


def test_probe_error_failure_sanitized_without_raw_details():
    class RawErrorTransport:
        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            raise RuntimeProbeError("http://user:pass@host/secret-token leaked")

    registry = RuntimeProbeRegistry(RawErrorTransport())
    descriptor = registry.probe([_target("one")])[0]
    assert descriptor.status == RuntimeStatus.ERROR
    assert "user:pass" not in descriptor.error
    assert "[REDACTED]" in descriptor.error


def test_stable_output_ordering_by_runtime_id():
    transport = FakeTransport(_routes("http://127.0.0.1:11434", "http://127.0.0.1:1234"))
    registry = RuntimeProbeRegistry(transport, max_workers=2)
    descriptors = registry.probe(
        [_target("z-last"), _target("a-first", endpoint="http://127.0.0.1:1234")]
    )
    assert [d.runtime_id for d in descriptors] == ["a-first", "z-last"]


def test_custom_limits_injected():
    limits = RuntimeProbeLimits(max_targets=8, max_workers=2)
    registry = RuntimeProbeRegistry(max_workers=2, limits=limits)
    assert registry.limits.max_targets == 8
    with pytest.raises(ValueError):
        registry.probe([_target(f"t-{i}") for i in range(9)])


def test_bearer_token_never_leaks_in_error_descriptor():
    class FailingTransport:
        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            raise RuntimeProbeError("bearer=supersecret failed")

    registry = RuntimeProbeRegistry(FailingTransport())
    descriptor = registry.probe([_target("one")])[0]
    assert "supersecret" not in descriptor.error
    assert "bearer=" not in descriptor.error


def test_hostile_str_error_never_invoked_and_genericized():
    invoked = {"calls": 0}

    class HostileError(RuntimeProbeError):
        def __str__(self):
            invoked["calls"] += 1
            return "hostile-secret " + ("x" * 1_000_000)

    registry = RuntimeProbeRegistry()
    sanitized = registry._sanitize_error(HostileError(object()), limits=registry.limits)
    assert "hostile-secret" not in sanitized
    assert sanitized == "Unexpected probe failure; details are not exposed."
    assert invoked["calls"] == 0


def test_non_string_evidence_and_warnings_genericized_without_str():
    invoked = {"calls": 0}

    class HostileObject:
        def __str__(self):
            invoked["calls"] += 1
            return "leaked-raw"

    registry = RuntimeProbeRegistry()
    bounded = registry._bounded_strings([HostileObject()], limits=registry.limits)
    assert bounded == ["[non-string]"]
    assert invoked["calls"] == 0


def test_non_finite_constructor_timeout_rejected_before_transport():
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            RuntimeProbeRegistry(timeout=value)


def test_non_finite_per_target_timeout_rejected_before_transport():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(ValueError):
            registry.probe([_target("bad", timeout=value)])
        assert transport.calls == []


def test_bearer_token_byte_and_control_limits_enforced():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    oversized = "t" * 5000
    with pytest.raises(ValueError):
        registry.probe([_target("one", bearer_token=oversized)])
    with pytest.raises(ValueError):
        registry.probe([_target("one", bearer_token="abc\x01def")])
    assert transport.calls == []


def test_oversized_bearer_token_rejected_before_full_encode():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    huge = "\U0001f600" * 100_000  # 400k bytes; codepoint cap rejects first
    with pytest.raises(ValueError):
        registry.probe([_target("one", bearer_token=huge)])
    assert transport.calls == []


def test_dangling_empty_port_rejected_before_scheduling():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    for endpoint in (
        "http://host:",
        "http://127.0.0.1:",
        "http://[::1]:",
    ):
        with pytest.raises(ValueError):
            registry.probe([_target("dangling", endpoint=endpoint)])
    assert transport.calls == []


def test_bracketed_ipv6_and_valid_paths_still_accepted():
    transport = FakeTransport(_routes("http://[::1]:8080"))
    registry = RuntimeProbeRegistry(transport)
    for endpoint in (
        "http://[::1]:8080",
        "http://[::1]",
        "http://127.0.0.1:11434/v1/models",
    ):
        descriptor = registry.probe(
            [_target("ok", endpoint=endpoint, adapter=AdapterType.OPENAI_COMPATIBLE)]
        )[0]
        assert descriptor.registered is False  # honest probe result, no crash


def test_malformed_target_values_fail_closed_before_scheduling():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    bad_targets = [
        ProbeTarget(
            runtime_id=None,
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint=None,
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint="",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint=123,
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind="ollama",
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source="auto",
            adapter_type=AdapterType.OLLAMA,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type="ollama",
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
            timeout=0,
        ),
        ProbeTarget(
            runtime_id="x",
            kind=RuntimeKind.OLLAMA,
            endpoint="http://127.0.0.1:11434",
            source=RuntimeSource.AUTO,
            adapter_type=AdapterType.OLLAMA,
            timeout=True,
        ),
    ]
    for target in bad_targets:
        with pytest.raises(ValueError):
            registry.probe([target])
    assert transport.calls == []


def test_valid_mixed_batch_with_one_bad_target_is_rejected_before_scheduling():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    bad = ProbeTarget(
        runtime_id="bad",
        kind=RuntimeKind.OLLAMA,
        endpoint=None,
        source=RuntimeSource.AUTO,
        adapter_type=AdapterType.OLLAMA,
    )
    with pytest.raises(ValueError):
        registry.probe([_target("good"), bad])
    assert transport.calls == []


def test_constructor_rejects_bool_and_wrong_types_with_value_error():
    for bad_timeout in (True, False, "1.5", None):
        with pytest.raises(ValueError):
            RuntimeProbeRegistry(timeout=bad_timeout)
    for bad_workers in (True, False, "4", 4.0, None):
        with pytest.raises(ValueError):
            RuntimeProbeRegistry(max_workers=bad_workers)


def test_bounded_strings_stops_at_item_cap_even_with_empty_strings():
    registry = RuntimeProbeRegistry()
    empty_list = ["" for _ in range(100_000)]
    bounded = registry._bounded_strings(empty_list, limits=registry.limits)
    assert len(bounded) <= registry.limits.max_evidence_items
    assert len(bounded) == registry.limits.max_evidence_items


def test_warnings_are_item_capped():
    registry = RuntimeProbeRegistry()
    warnings = [f"w-{index}" for index in range(100_000)]
    bounded = registry._bounded_strings(warnings, limits=registry.limits)
    assert len(bounded) <= registry.limits.max_evidence_items


def test_projected_model_runtime_id_is_bounded_and_counted():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("m", capabilities=0)
    model = model.model_copy(update={"runtime_id": "\U0001f600" * 5000})
    projected = registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())[0]
    assert len(projected.runtime_id.encode("utf-8")) <= registry.limits.max_model_field_bytes


def test_identified_vendor_bounded_before_final_reconstruction():
    registry = RuntimeProbeRegistry()
    from zana_core.runtimes.base import build_runtime_descriptor

    original = build_runtime_descriptor(
        runtime_id="adapter-id",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        evidence=[],
    )
    original = original.model_copy(update={"identified_vendor": "\U0001f600" * 5000})
    rebuilt = registry._bound_descriptor(original, _target("adapter-id"), limits=registry.limits)
    assert len(rebuilt.identified_vendor.encode("utf-8")) <= registry.limits.max_model_field_bytes
    assert rebuilt is not original


def test_error_boundary_never_leaks_partial_credential():
    registry = RuntimeProbeRegistry()
    # The @ boundary sits just beyond the retained prefix.
    text = "http://user:pass" + "@" + ("x" * 1000)
    sanitized = registry._sanitize_text(text, registry.limits)
    assert "user" not in sanitized
    assert "pass" not in sanitized


def test_boundary_sanitization_remains_fixed_work():
    registry = RuntimeProbeRegistry()
    huge = "http://u:p@" + ("y" * 1_000_000)
    sanitized = registry._sanitize_text(huge, registry.limits)
    assert "u:p" not in sanitized
    assert len(sanitized) <= registry.limits.max_error_chars


def test_long_userinfo_crossing_boundary_never_leaks_prefix():
    registry = RuntimeProbeRegistry()
    # Credential starts near index 0; password is far longer than the
    # retained prefix and the '@' is beyond it.
    text = "http://user:" + ("p" * 600) + "@host"
    sanitized = registry._sanitize_text(text, registry.limits)
    assert "user" not in sanitized
    assert "p" * 100 not in sanitized
    assert len(sanitized) <= registry.limits.max_error_chars


def test_make_adapter_uses_explicit_none_timeout_semantics():
    from zana_core.runtimes.registry import _make_adapter

    registry = RuntimeProbeRegistry(timeout=2.5, max_workers=1)
    target = _target("t", timeout=1.5)
    adapter = _make_adapter(registry, target, timeout=1.5, limits=registry.limits)
    assert adapter.timeout == 1.5
    target_none = _target("t2")
    adapter_none = _make_adapter(registry, target_none, timeout=2.5, limits=registry.limits)
    assert adapter_none.timeout == 2.5


def test_endpoint_credentials_fragment_scheme_and_malformed_rejected():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    invalid_endpoints = [
        "http://user:pass@127.0.0.1:11434",
        "http://127.0.0.1:11434#fragment",
        "http://127.0.0.1:11434/?token=secret",
        "ftp://127.0.0.1:11434",
        "not a url",
        "http://127.0.0.1:11434\tabc",
        "http://127.0.0.1:11434\\abc",
    ]
    for endpoint in invalid_endpoints:
        with pytest.raises(ValueError):
            registry.probe([_target("one", endpoint=endpoint)])
    assert transport.calls == []


def test_endpoint_and_reference_byte_and_control_limits_enforced():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    four_byte_endpoint = "http://127.0.0.1:11434/" + ("\U0001f600" * 1200)
    with pytest.raises(ValueError):
        registry.probe([_target("two", endpoint=four_byte_endpoint)])
    with pytest.raises(ValueError):
        registry.probe([_target("bad\x01id")])
    assert transport.calls == []


def test_models_over_policy_fails_target_honestly():
    entries = [{"id": f"model-{index}"} for index in range(200)]
    transport = FakeTransport(
        {("GET", "http://127.0.0.1:8080/v1/models"): json_response({"data": entries})}
    )
    registry = RuntimeProbeRegistry(transport, max_workers=1)
    descriptor = registry.probe(
        [
            _target(
                "many",
                endpoint="http://127.0.0.1:8080/v1",
                adapter=AdapterType.OPENAI_COMPATIBLE,
            )
        ]
    )[0]
    assert descriptor.status == RuntimeStatus.ERROR
    assert descriptor.registered is False
    assert descriptor.models == []
    assert "model count" in descriptor.error


def test_endpoint_port_validation_before_scheduling():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    for endpoint in (
        "http://127.0.0.1:notaport/v1",
        "http://127.0.0.1:99999/v1",
        "http://127.0.0.1:-1/v1",
    ):
        with pytest.raises(ValueError):
            registry.probe(
                [
                    _target(
                        "port-bad",
                        endpoint=endpoint,
                        adapter=AdapterType.OPENAI_COMPATIBLE,
                    )
                ]
            )
    assert transport.calls == []


def _synthetic_model(model_id: str, *, capabilities: int = 0, huge_field: bool = False):
    value = ("x" * 5000) if huge_field else model_id
    capability_values = (
        [("y" * 5000) for _ in range(capabilities)]
        if huge_field
        else [f"cap-{index}" for index in range(capabilities)]
    )
    return ModelDescriptor(
        runtime_id="openai-compatible",
        model_id=value,
        display_name=value,
        metadata_source="runtime",
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
        identity_strength=ModelIdentityStrength.RUNTIME_MODEL_ID,
        capabilities=capability_values,
    )


def test_model_strings_and_capabilities_projection_bounded():
    registry = RuntimeProbeRegistry()
    projected = registry._bound_models(
        [_synthetic_model("huge", capabilities=30, huge_field=True)],
        limits=registry.limits,
        snapshot=_snapshot(),
    )
    assert len(projected) == 1
    assert len(projected[0].model_id.encode("utf-8")) <= registry.limits.max_model_field_bytes
    assert len(projected[0].capabilities) == registry.limits.max_model_capabilities


def test_model_total_chars_over_policy_fails_target():
    registry = RuntimeProbeRegistry()
    models = [
        _synthetic_model(f"m-{index}", capabilities=16, huge_field=True) for index in range(100)
    ]
    with pytest.raises(RuntimeProbeError):
        registry._bound_models(models, limits=registry.limits, snapshot=_snapshot())


def test_model_field_emoji_truncated_by_bytes_without_broken_codepoint():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("emoji")
    model = model.model_copy(update={"model_id": "\U0001f600" * 200})
    projected = registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())[0]
    encoded = projected.model_id.encode("utf-8")
    assert len(encoded) <= registry.limits.max_model_field_bytes
    encoded.decode("utf-8")
    assert "\ufffd" not in projected.model_id


def test_model_aggregate_emoji_byte_budget():
    registry = RuntimeProbeRegistry()
    models = [
        _synthetic_model("\U0001f600" * 200, capabilities=16, huge_field=True) for _ in range(100)
    ]
    with pytest.raises(RuntimeProbeError):
        registry._bound_models(models, limits=registry.limits, snapshot=_snapshot())


def test_projected_models_are_fresh_validated_descriptors():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("fresh", capabilities=2, huge_field=True)
    projected = registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())[0]
    assert projected is not model
    assert projected.runtime_id == "runtime-a"
    assert projected.last_seen_at == model.last_seen_at
    assert projected.identity_strength == model.identity_strength


def test_final_descriptor_is_fresh_validated_reconstruction():
    from zana_core.runtimes.base import RuntimeDescriptor, build_runtime_descriptor
    from zana_core.runtimes.registry import _rebuild_descriptor

    original = build_runtime_descriptor(
        runtime_id="adapter-id",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        evidence=["original"],
        warnings=[],
        error=None,
    )
    rebuilt = _rebuild_descriptor(
        original,
        runtime_id="target-id",
        endpoint="http://127.0.0.1:1234",
        kind=RuntimeKind.OLLAMA,
        source_value=RuntimeSource.AUTO,
        status=original.status,
        registered=original.registered,
        server_running=original.server_running,
        installed=original.installed,
        installed_not_running=original.installed_not_running,
        evidence=["bounded"],
        warnings=[],
        error=None,
        models=[],
        identified_vendor=None,
        last_seen_at=original.last_seen_at,
    )
    assert rebuilt is not original
    assert rebuilt.runtime_id == "target-id"
    assert rebuilt.endpoint == "http://127.0.0.1:1234"
    assert rebuilt.evidence == ["bounded"]
    assert rebuilt.registered is True
    # The reconstruction validates through RuntimeDescriptor's model contract.
    assert RuntimeDescriptor.model_validate(rebuilt.model_dump()) == rebuilt


def test_probe_one_applies_validated_target_identity_override():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    descriptor = registry.probe([_target("custom-id")])[0]
    assert descriptor.runtime_id == "custom-id"
    assert descriptor.endpoint == "http://127.0.0.1:11434"
    assert descriptor.registered is True


def test_truncate_text_never_encodes_full_untrusted_string():
    huge = "\U0001f600" * 1_000_000
    truncated = _truncate_text(huge, 64)
    assert len(truncated.encode("utf-8")) <= 64
    truncated.encode("utf-8")
    assert "\ufffd" not in truncated


def test_large_exception_string_sanitized_with_bounded_prefix():
    class HugeErrorTransport:
        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            raise RuntimeProbeError("x" * 1_000_000)

    registry = RuntimeProbeRegistry(HugeErrorTransport())
    descriptor = registry.probe([_target("one")])[0]
    assert len(descriptor.error) <= registry.limits.max_error_chars


def test_credential_redaction_within_retained_prefix():
    registry = RuntimeProbeRegistry()
    text = "http://user:pass@host/ " + ("x" * 1000)
    sanitized = registry._sanitize_text(text, registry.limits)
    assert "user:pass" not in sanitized
    assert "[REDACTED]" in sanitized
    assert len(sanitized) <= registry.limits.max_error_chars


def test_executable_discovery_failure_isolated_from_successful_target():
    class PartialExplodingExecutables(ExecutableDiscovery):
        def installed(self, kind):
            if kind == RuntimeKind.OLLAMA:
                raise RuntimeError("executable discovery failed")
            return False

    transport = FakeTransport(
        {
            ("GET", "http://127.0.0.1:8080/v1/models"): json_response(
                {"data": [{"id": "ok-model", "object": "model"}]}
            )
        }
    )
    registry = RuntimeProbeRegistry(
        transport,
        max_workers=2,
        executables=PartialExplodingExecutables(),
    )
    descriptors = registry.probe(
        [
            _target("failing"),
            _target(
                "ok",
                endpoint="http://127.0.0.1:8080/v1",
                adapter=AdapterType.OPENAI_COMPATIBLE,
                kind=RuntimeKind.OPENAI_COMPATIBLE,
            ),
        ]
    )
    by_id = {descriptor.runtime_id: descriptor for descriptor in descriptors}
    assert by_id["failing"].status == RuntimeStatus.ERROR
    assert by_id["failing"].error
    assert by_id["ok"].registered is True
    assert by_id["ok"].status == RuntimeStatus.ONLINE


def test_constructor_never_calls_transport_or_executables_bool():
    """Falsy injected objects are retained; hostile __bool__ is never invoked."""
    calls = {"bool": 0}

    class HostileTransport:
        def __bool__(self):
            calls["bool"] += 1
            return False

    transport = HostileTransport()
    registry = RuntimeProbeRegistry(transport=transport)
    assert registry.transport is transport
    assert calls["bool"] == 0

    class HostileExecutables(ExecutableDiscovery):
        def __bool__(self):
            calls["bool"] += 1
            return False

    executables = HostileExecutables()
    registry = RuntimeProbeRegistry(executables=executables)
    assert registry.executables is executables
    assert calls["bool"] == 0


def test_falsy_transport_never_falls_back_to_real_transport():
    class FalsyTransport:
        def __bool__(self):
            return False

        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            raise AssertionError("transport request should never run")

    registry = RuntimeProbeRegistry(transport=FalsyTransport())
    descriptor = registry.probe([_target("one")])[0]
    assert descriptor.status == RuntimeStatus.ERROR
    assert "hostile truthiness" in (descriptor.error or "")


def test_constructor_rejects_falsy_limits_without_hostile_bool():
    calls = {"bool": 0}

    class FalsyLimits(RuntimeProbeLimits):
        def __bool__(self):
            calls["bool"] += 1
            return False

    with pytest.raises(ValueError):
        RuntimeProbeRegistry(limits=FalsyLimits())
    assert calls["bool"] == 0


def test_limits_corrupted_through_object_setattr_rejected():
    limits = RuntimeProbeLimits()
    object.__setattr__(limits, "max_targets", 1000)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(limits=limits)


def test_limits_model_construct_corruption_rejected():
    limits = RuntimeProbeLimits.model_construct(max_targets=1000, max_workers=4)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(limits=limits)


def test_exact_limits_revalidated_into_fresh_instance():
    limits = RuntimeProbeLimits(max_targets=8, max_workers=2)
    registry = RuntimeProbeRegistry(max_workers=2, limits=limits)
    assert registry.limits is not limits
    assert registry.limits.max_targets == 8


def test_hostile_target_bool_eq_hash_repr_and_getattribute_never_used():
    """Duplicate IDs are rejected after exact raw validation with zero hooks."""

    class HostileTarget(ProbeTarget):
        def __bool__(self):
            raise AssertionError("__bool__ called")

        def __eq__(self, other):
            raise AssertionError("__eq__ called")

        def __hash__(self):
            raise AssertionError("__hash__ called")

        def __repr__(self):
            raise AssertionError("__repr__ called")

        def __getattribute__(self, name):
            raise AssertionError(f"__getattribute__ called for {name}")

    registry = RuntimeProbeRegistry()
    target = HostileTarget(
        runtime_id="dup",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        adapter_type=AdapterType.OLLAMA,
    )
    with pytest.raises(ValueError):
        registry.probe([target, _target("dup")])


def test_hostile_target_subclass_rejected_without_hooks():
    class SubclassTarget(ProbeTarget):
        pass

    with pytest.raises(ValueError):
        RuntimeProbeRegistry().probe(
            [
                SubclassTarget(
                    runtime_id="sub",
                    kind=RuntimeKind.OLLAMA,
                    endpoint="http://127.0.0.1:11434",
                    source=RuntimeSource.AUTO,
                    adapter_type=AdapterType.OLLAMA,
                )
            ]
        )


def test_object_mutated_target_rejected_without_hooks():
    target = _target("mut")
    object.__setattr__(target, "endpoint", None)
    with pytest.raises(ValueError):
        RuntimeProbeRegistry().probe([target])


def test_remote_and_lan_endpoints_rejected():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    invalid = [
        "http://192.168.1.1:11434",
        "http://10.0.0.5:11434",
        "http://localhost.evil.com:11434",
        "http://user:pass@127.0.0.1:11434",
    ]
    for endpoint in invalid:
        with pytest.raises(ValueError):
            registry.probe([_target("remote", endpoint=endpoint)])
    assert transport.calls == []


def test_loopback_hosts_still_accepted():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    for endpoint in (
        "http://localhost:11434",
        "http://127.0.0.1:11434",
        "http://127.0.0.2:11434",
        "http://[::1]:11434",
        "http://localhost.",
    ):
        descriptor = registry.probe([_target("ok", endpoint=endpoint)])[0]
        assert descriptor.registered is False or descriptor.registered is True


def test_shared_deadline_prevents_transport_after_expiry():
    """A blocked worker must never call the transport after the batch deadline."""
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    calls = {"count": 0}

    class SlowFakeTransport:
        def request(self, method, url, *, headers=None, body=None, timeout=1.0):
            calls["count"] += 1
            if url == "http://127.0.0.1:11434/api/tags":
                time.sleep(0.35)
                return transport.routes[("GET", url)]
            return transport.routes[("GET", url)]

    registry = RuntimeProbeRegistry(SlowFakeTransport(), timeout=0.05, max_workers=1)
    descriptors = registry.probe(
        [_target("slow"), _target("fast", endpoint="http://127.0.0.1:1234")]
    )
    assert len(descriptors) == 2
    # The slow worker times out at the shared deadline; the fast target never
    # starts after that point.
    assert calls["count"] == 1
    assert any(descriptor.status == RuntimeStatus.ERROR for descriptor in descriptors)


def test_hostile_descriptor_getattribute_and_bool_never_used():
    class HostileDescriptor(RuntimeDescriptor):
        def __getattribute__(self, name):
            raise AssertionError(f"__getattribute__ called for {name}")

        def __bool__(self):
            raise AssertionError("__bool__ called")

    source = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        evidence=[],
        warnings=[],
        models=[],
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    hostile = object.__new__(HostileDescriptor)
    object.__setattr__(hostile, "__dict__", dict(object.__getattribute__(source, "__dict__")))
    registry = RuntimeProbeRegistry()
    with pytest.raises(RuntimeProbeError):
        registry._bound_descriptor(hostile, _target("x"), limits=registry.limits)


def test_object_mutated_descriptor_rejected():
    descriptor = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    object.__setattr__(descriptor, "evidence", None)
    with pytest.raises(RuntimeProbeError):
        RuntimeProbeRegistry()._bound_descriptor(
            descriptor, _target("x"), limits=RuntimeProbeRegistry().limits
        )


def test_descriptor_model_construct_missing_required_field_rejected():
    descriptor = RuntimeDescriptor.model_construct(
        runtime_id="x",
        endpoint="http://127.0.0.1:11434",
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
    )
    with pytest.raises(RuntimeProbeError):
        RuntimeProbeRegistry()._bound_descriptor(
            descriptor, _target("x"), limits=RuntimeProbeRegistry().limits
        )


def test_object_mutated_model_rejected():
    model = _synthetic_model("m")
    object.__setattr__(model, "model_id", None)
    with pytest.raises(RuntimeProbeError):
        RuntimeProbeRegistry()._bound_models(
            [model], limits=RuntimeProbeRegistry().limits, snapshot=_snapshot()
        )


def test_model_construct_missing_required_field_rejected():
    model = ModelDescriptor.model_construct(
        model_id="m",
        display_name="m",
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(RuntimeProbeError):
        RuntimeProbeRegistry()._bound_models(
            [model], limits=RuntimeProbeRegistry().limits, snapshot=_snapshot()
        )


def test_hostile_model_bool_and_getattribute_never_used():
    class HostileModel(ModelDescriptor):
        def __bool__(self):
            raise AssertionError("__bool__ called")

        def __getattribute__(self, name):
            raise AssertionError(f"__getattribute__ called for {name}")

    source = ModelDescriptor(
        runtime_id="r",
        model_id="m",
        display_name="m",
        metadata_source="runtime",
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    model = object.__new__(HostileModel)
    object.__setattr__(model, "__dict__", dict(object.__getattribute__(source, "__dict__")))
    with pytest.raises(RuntimeProbeError):
        RuntimeProbeRegistry()._bound_models(
            [model], limits=RuntimeProbeRegistry().limits, snapshot=_snapshot()
        )


def test_exact_revalidation_keeps_valid_descriptor_values():
    descriptor = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        evidence=["ok"],
        models=[],
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    rebuilt = RuntimeProbeRegistry()._bound_descriptor(
        descriptor, _target("x"), limits=RuntimeProbeRegistry().limits
    )
    assert rebuilt.runtime_id == "x"
    assert rebuilt.evidence == ["ok"]


def test_normal_runtime_probe_error_redacted_without_hostile_path():
    registry = RuntimeProbeRegistry()
    sanitized = registry._sanitize_error(
        RuntimeProbeError("model count exceeds policy limit 128"), limits=registry.limits
    )
    assert sanitized == "model count exceeds policy limit 128"

    redacted = registry._sanitize_error(
        RuntimeProbeError("http://user:pass@host/token=supersecret failed"),
        limits=registry.limits,
    )
    assert "user:pass" not in redacted
    assert "supersecret" not in redacted


def test_evil_numeric_subclasses_rejected_in_constructor():
    class EvilInt(int):
        pass

    class EvilFloat(float):
        pass

    with pytest.raises(ValueError):
        RuntimeProbeRegistry(timeout=EvilFloat(1.0))
    with pytest.raises(ValueError):
        RuntimeProbeRegistry(max_workers=EvilInt(1))


def test_evil_timeout_subclass_rejected_before_transport():
    class EvilFloat(float):
        pass

    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    with pytest.raises(ValueError):
        registry.probe([_target("evil", timeout=EvilFloat(1.0))])
    assert transport.calls == []


def test_utc_timestamp_validation():
    registry = RuntimeProbeRegistry()
    descriptor = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        last_seen_at=datetime.now(timezone(timedelta(hours=2))),
    )
    with pytest.raises(RuntimeProbeError):
        registry._bound_descriptor(descriptor, _target("x"), limits=registry.limits)

    descriptor = descriptor.model_copy(update={"last_seen_at": datetime.now(UTC)})
    rebuilt = registry._bound_descriptor(descriptor, _target("x"), limits=registry.limits)
    assert rebuilt.runtime_id == "x"


def test_model_numeric_fields_must_be_exact_nonnegative_int():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("m")
    model = model.model_copy(update={"size_bytes": -1})
    with pytest.raises(RuntimeProbeError):
        registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())

    model = model.model_copy(update={"size_bytes": True})
    with pytest.raises(RuntimeProbeError):
        registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())

    class EvilInt(int):
        pass

    model = model.model_copy(update={"size_bytes": EvilInt(10)})
    with pytest.raises(RuntimeProbeError):
        registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())


def test_model_capabilities_len_gated_before_iteration():
    registry = RuntimeProbeRegistry()

    class HostileCapability:
        def __str__(self):
            raise AssertionError("capabilities past the cap were traversed")

    capabilities = ["ok"] * registry.limits.max_model_capabilities + [HostileCapability()]
    model = _synthetic_model("m", capabilities=0)
    model = model.model_copy(update={"capabilities": capabilities})
    projected = registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())[0]
    assert len(projected.capabilities) == registry.limits.max_model_capabilities


def test_corrupted_registry_fields_revalidated_at_probe_start():
    registry = RuntimeProbeRegistry()
    object.__setattr__(registry, "max_workers", 100)
    with pytest.raises(ValueError):
        registry.probe([_target("x")])

    object.__setattr__(registry, "max_workers", 2)
    object.__setattr__(registry, "timeout", float("nan"))
    with pytest.raises(RuntimeProbeError):
        registry.probe([_target("x")])


def test_custom_endpoint_and_reference_limits_are_distinct():
    limits = RuntimeProbeLimits(
        max_targets=4,
        max_workers=1,
        max_endpoint_length=10,
        max_reference_length=200,
    )
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport, max_workers=1, limits=limits)
    with pytest.raises(ValueError):
        registry.probe([_target("id", endpoint="http://127.0.0.1:11434")])
    long_id = "r" * 250
    with pytest.raises(ValueError):
        registry.probe([_target(long_id, endpoint="http://x")])


def test_evidence_and_warnings_are_sanitized_display_text():
    registry = RuntimeProbeRegistry()
    descriptor = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        evidence=[
            "token=super-secret",
            "http://user:password@localhost/private",
            "bad\x01ctl",
        ],
        warnings=["api_key=abc123", "ok\x00line"],
        models=[],
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    rebuilt = registry._bound_descriptor(descriptor, _snapshot(), limits=registry.limits)
    joined_evidence = " ".join(rebuilt.evidence)
    joined_warnings = " ".join(rebuilt.warnings)
    assert "super-secret" not in joined_evidence
    assert "user:password" not in joined_evidence
    assert "abc123" not in joined_warnings
    assert "\x00" not in joined_evidence + joined_warnings
    assert "\x01" not in joined_evidence + joined_warnings
    assert all(type(item) is str for item in rebuilt.evidence + rebuilt.warnings)


def test_identified_vendor_control_chars_fail_closed():
    registry = RuntimeProbeRegistry()
    descriptor = RuntimeDescriptor(
        runtime_id="x",
        kind=RuntimeKind.OLLAMA,
        endpoint="http://127.0.0.1:11434",
        source=RuntimeSource.AUTO,
        status=RuntimeStatus.ONLINE,
        registered=True,
        server_running=True,
        installed=False,
        installed_not_running=False,
        identified_vendor="vendor\nbad",
        models=[],
        last_seen_at=datetime(2026, 8, 9, 12, 0, 0, tzinfo=UTC),
    )
    with pytest.raises(RuntimeProbeError):
        registry._bound_descriptor(descriptor, _snapshot(), limits=registry.limits)


def test_model_text_control_chars_fail_closed():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("m")
    model = model.model_copy(update={"display_name": "line\nbreak"})
    with pytest.raises(RuntimeProbeError):
        registry._bound_models([model], limits=registry.limits, snapshot=_snapshot())


def test_model_runtime_id_bound_to_target_snapshot():
    registry = RuntimeProbeRegistry()
    model = _synthetic_model("m")
    model = model.model_copy(update={"runtime_id": "other-runtime"})
    projected = registry._bound_models(
        [model], limits=registry.limits, snapshot=_snapshot("runtime-a")
    )[0]
    assert projected.runtime_id == "runtime-a"
    assert "other-runtime" not in projected.runtime_id


def test_endpoint_query_rejected_before_transport():
    transport = FakeTransport(_routes("http://127.0.0.1:11434"))
    registry = RuntimeProbeRegistry(transport)
    with pytest.raises(ValueError):
        registry.probe([_target("one", endpoint="http://127.0.0.1:11434/?token=secret")])
    with pytest.raises(ValueError):
        registry.probe([_target("one", endpoint="http://127.0.0.1:11434/v1/models?q=1")])
    assert transport.calls == []


def test_missing_registry_fields_fail_typed():
    registry = RuntimeProbeRegistry()
    object.__delattr__(registry, "limits")
    with pytest.raises(ValueError):
        registry.probe([])

    registry = RuntimeProbeRegistry()
    object.__delattr__(registry, "timeout")
    with pytest.raises(ValueError):
        registry.probe([])

    registry = RuntimeProbeRegistry()
    object.__delattr__(registry, "max_workers")
    with pytest.raises(ValueError):
        registry.probe([])
