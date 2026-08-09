"""Tests for instance export/import schema reservation and secret exclusion."""

from datetime import UTC, datetime

import pytest

from zana_core.memory.export import (
    EXPORT_SCHEMA_VERSION,
    InstanceExportEnvelope,
    InstanceImportEnvelope,
    SecretRequirement,
    UnsupportedExportSchemaError,
    build_import_envelope,
    build_instance_export,
    scan_for_secret_values,
)
from zana_core.memory.models import (
    ImagePointer,
    InstancePointer,
    MutableInstanceState,
)

FIXED = datetime(2026, 8, 9, 12, 0, tzinfo=UTC)


def pointer() -> InstancePointer:
    return InstancePointer(
        instance_id="inst-1",
        image=ImagePointer(digest="sha256:image-v1"),
        snapshot_revision=3,
        state_schema_version=1,
        updated_at=FIXED,
    )


def state() -> MutableInstanceState:
    return MutableInstanceState(
        instance_id="inst-1",
        state_revision=2,
        updated_at=FIXED,
    )


def test_export_never_contains_secret_values() -> None:
    envelope = build_instance_export(pointer(), state())
    payload = envelope.model_dump(mode="json")
    assert payload["contains_secret_values"] is False
    assert scan_for_secret_values(payload) == []


def test_export_fields_do_not_include_secret_value_keys() -> None:
    build_instance_export(pointer(), state())
    fields = InstanceExportEnvelope.model_fields
    assert "secret_values" not in fields
    assert "credentials" not in fields
    assert "token" not in fields
    assert "password" not in fields


def test_export_carries_unresolved_secret_requirements() -> None:
    requirement = SecretRequirement(
        key="runtime.endpoint_token",
        description="Ollama endpoint token stored in OS keychain",
        resolved=False,
    )
    envelope = build_instance_export(pointer(), state(), [requirement])
    assert envelope.secret_requirements == [requirement]
    assert envelope.secret_requirements[0].resolved is False


def test_export_rejects_resolved_secret_requirement() -> None:
    resolved = SecretRequirement(key="api_key", description="already present", resolved=True)
    with pytest.raises(ValueError):
        build_instance_export(pointer(), state(), [resolved])


def test_envelope_validator_rejects_resolved_secret() -> None:
    with pytest.raises(ValueError):
        InstanceExportEnvelope(
            exported_at=FIXED,
            instance_id="inst-1",
            image=ImagePointer(digest="sha256:image-v1"),
            snapshot_revision=1,
            state=state(),
            secret_requirements=[SecretRequirement(key="token", resolved=True)],
        )


def test_envelope_validator_rejects_contains_secret_values_flag() -> None:
    with pytest.raises(ValueError):
        InstanceExportEnvelope(
            exported_at=FIXED,
            instance_id="inst-1",
            image=ImagePointer(digest="sha256:image-v1"),
            snapshot_revision=1,
            state=state(),
            contains_secret_values=True,
        )


def test_import_round_trip_preserves_state_and_image() -> None:
    envelope = build_instance_export(pointer(), state())
    imported = build_import_envelope(envelope.model_dump(mode="json"))
    assert isinstance(imported, InstanceImportEnvelope)
    assert imported.schema_version == EXPORT_SCHEMA_VERSION
    assert imported.instance_id == "inst-1"
    assert imported.image.digest == "sha256:image-v1"
    assert imported.snapshot_revision == 3
    assert imported.state.state_revision == 2


def test_import_unsupported_schema_raises() -> None:
    payload = {
        "schema_version": 999,
        "exported_at": FIXED.isoformat(),
        "instance_id": "inst-1",
        "image": {"digest": "sha256:image-v1", "schema_version": 1},
        "snapshot_revision": 1,
        "state": state().model_dump(mode="json"),
        "secret_requirements": [],
        "unresolved_requirements": [],
    }
    with pytest.raises(UnsupportedExportSchemaError):
        build_import_envelope(payload)


def test_import_unsupported_schema_validator_raises_value_error() -> None:
    with pytest.raises(ValueError):
        InstanceImportEnvelope(
            schema_version=2,
            exported_at=FIXED,
            instance_id="inst-1",
            image=ImagePointer(digest="sha256:image-v1"),
            snapshot_revision=1,
            state=state(),
        )


def test_import_lists_unresolved_requirement_keys() -> None:
    envelope = build_instance_export(
        pointer(),
        state(),
        [
            SecretRequirement(key="runtime.token"),
            SecretRequirement(key="embedding.token"),
        ],
    )
    imported = build_import_envelope(envelope.model_dump(mode="json"))
    assert imported.secret_requirement_keys() == [
        "embedding.token",
        "runtime.token",
    ]


def test_scan_detects_sensitive_nested_values() -> None:
    payload = {
        "endpoint": {"url": "http://127.0.0.1", "api_key": "sk-live"},
        "headers": {"authorization": "Bearer xyz"},
        "state": {"note": "no secret here"},
    }
    hits = scan_for_secret_values(payload)
    assert "endpoint.api_key" in hits
    assert "headers.authorization" in hits
    assert all(".note" not in hit for hit in hits)


def test_export_schema_version_is_one() -> None:
    assert EXPORT_SCHEMA_VERSION == 1
    assert build_instance_export(pointer(), state()).schema_version == 1
