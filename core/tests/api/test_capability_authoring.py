"""Authenticated Capability Source authoring API tests."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from tests.capabilities.helpers import MATH_EVAL_JSONL
from zana_core.capabilities import authoring
from zana_core.capabilities.manifest import parse_safe_yaml
from zana_core.db.repositories import CapabilitySourceRepository
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.main import create_app

VALID_MANIFEST = {
    "schemaVersion": 1,
    "kind": "ZanaCapability",
    "id": "io.zana.test.tutor",
    "name": "Math Tutor",
    "version": "0.1.0",
    "goal": {"type": "domain-assistant"},
}

BEHAVIOR_V1 = "You are a careful arithmetic tutor.\n"
BEHAVIOR_V2 = "You are a precise arithmetic tutor. Show every step.\n"

DOMAIN_EVAL = (
    '{"id":"math-001","prompt":"What is 17 * 23? Return only the number.",'
    '"scorer":{"type":"numeric_exact","expected":391}}\n'
)
REGRESSION_EVAL = (
    '{"id":"reg-001","prompt":"What is 144 / 12? Return only the number.",'
    '"scorer":{"type":"numeric_exact","expected":12}}\n'
)


def _create(
    client,
    auth_header: dict[str, str],
    *,
    name: str = "math-tutor",
    version: str = "0.1.0",
    manifest_json: dict | None = None,
) -> dict:
    payload: dict = {"name": name, "version": version}
    if manifest_json is not None:
        payload["manifest_json"] = manifest_json
    response = client.post("/api/v1/capabilities", json=payload, headers=auth_header)
    assert response.status_code == 201, response.text
    return response.json()


def _add(
    client,
    auth_header: dict[str, str],
    capability_id: int,
    payload: dict,
) -> dict:
    response = client.post(
        f"/api/v1/capabilities/{capability_id}/sources",
        json=payload,
        headers=auth_header,
    )
    assert response.status_code == 201, response.text
    return response.json()


def _digest(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def _workspace_path(capability: dict, database) -> Path:  # noqa: ANN001
    return Path(capability["working_dir"])


def _no_temp_files(workspace: Path) -> bool:
    return not any(".zana-tmp-" in path.name for path in workspace.rglob("*"))


class TestDraftCreation:
    def test_mutating_models_are_strict_and_forbid_extra_fields(
        self,
        client,
        auth_header,  # noqa: ANN001
    ) -> None:
        extra = client.post(
            "/api/v1/capabilities",
            json={
                "name": "math-tutor",
                "version": "0.1.0",
                "extra_field": True,
            },
            headers=auth_header,
        )
        assert extra.status_code == 422
        wrong_type = client.post(
            "/api/v1/capabilities",
            json={"name": 12, "version": "0.1.0"},
            headers=auth_header,
        )
        assert wrong_type.status_code == 422
        manifest = dict(
            VALID_MANIFEST,
            evaluation={"domain": "evals/domain.jsonl"},
        )
        created = _create(client, auth_header, manifest_json=manifest)
        extra_update = client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"name": "new-name", "extra_field": True},
            headers=auth_header,
        )
        assert extra_update.status_code == 422

    def test_create_draft_creates_real_workspace_and_manifest(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header)
        workspace = _workspace_path(created, database)
        assert workspace.is_dir()
        assert workspace == database.path.parent / "capabilities" / str(created["id"])
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        assert on_disk == created["manifest_json"]
        assert on_disk["schemaVersion"] == 1
        assert on_disk["kind"] == "ZanaCapability"
        assert on_disk["id"] == "zana.local.math-tutor"
        assert on_disk["name"] == "math-tutor"
        assert on_disk["version"] == "0.1.0"

    def test_provided_manifest_is_persisted_on_disk(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        assert on_disk == VALID_MANIFEST
        assert created["manifest_json"] == VALID_MANIFEST

    def test_create_failure_rolls_back_without_claimed_draft(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        def boom(workspace: Path) -> None:
            raise authoring.AuthoringError("WORKSPACE_CREATE", "injected failure")

        monkeypatch.setattr(authoring, "ensure_workspace", boom)
        response = client.post(
            "/api/v1/capabilities",
            json={"name": "broken", "version": "0.1.0"},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert client.get("/api/v1/capabilities", headers=auth_header).json() == []
        assert not (database.path.parent / "capabilities" / "1").exists()


class TestFullAuthoringFlow:
    def test_create_behavior_document_evals_get_reopen_validate(
        self,
        client,
        auth_header,
        database,
        tmp_path,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)

        behavior = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        assert behavior["local_path"] == "behavior/system.md"
        assert behavior["sha256"] == _digest(BEHAVIOR_V1)
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1

        original = tmp_path / "Remote Work Policy.md"
        original.write_text("# Remote Work Policy\nUp to two remote days.\n")
        document = _add(
            client,
            auth_header,
            created["id"],
            {
                "kind": "document",
                "local_path": str(original),
                "user_approved": True,
            },
        )
        assert document["local_path"] == "knowledge/sources/Remote Work Policy.md"
        copied = workspace / "knowledge" / "sources" / "Remote Work Policy.md"
        assert copied.read_bytes() == original.read_bytes()
        assert document["sha256"] == hashlib.sha256(original.read_bytes()).hexdigest()
        assert document["media_type"] == "text/markdown"

        domain = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "evaluation", "eval_kind": "domain", "content": DOMAIN_EVAL},
        )
        regression = _add(
            client,
            auth_header,
            created["id"],
            {
                "kind": "evaluation",
                "eval_kind": "regression",
                "content": REGRESSION_EVAL,
            },
        )
        assert domain["local_path"] == "evals/domain.jsonl"
        assert regression["local_path"] == "evals/regression.jsonl"

        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert [source["local_path"] for source in sources] == [
            "behavior/system.md",
            "evals/domain.jsonl",
            "evals/regression.jsonl",
            "knowledge/sources/Remote Work Policy.md",
        ]

        detail = client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        assert detail["workspace_relative"] == f"capabilities/{created['id']}"
        assert len(detail["sources"]) == 4
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        assert on_disk == detail["manifest_json"]
        assert on_disk["behavior"] == {"system": "behavior/system.md"}
        assert on_disk["knowledge"] == {"sources": [{"path": "knowledge/sources"}]}
        assert on_disk["evaluation"] == {
            "domain": "evals/domain.jsonl",
            "regression": "evals/regression.jsonl",
        }

        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is True
        assert report["issue_count"] == 0
        assert report["issues"] == []
        assert report["root_relative"] == f"capabilities/{created['id']}"
        roles = {item["role"] for item in report["provenance"]}
        assert {"manifest", "behavior", "knowledge", "evaluation"} <= roles
        assert _no_temp_files(workspace)

    def test_validate_reports_real_issues_and_sanitized_paths(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        shutil.rmtree(workspace)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert report["issues"][0]["code"] == "WORKSPACE_MISSING"
        assert str(database.path.parent) not in json.dumps(report)

    def test_validate_and_sources_reject_manifest_divergence(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        drifted = dict(VALID_MANIFEST, id="io.zana.test.drifted")
        (workspace / "zana.yaml").write_text(yaml.safe_dump(drifted), encoding="utf-8")
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert any(issue["code"] == "MANIFEST_DIVERGED" for issue in report["issues"])
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V1},
            headers=auth_header,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "MANIFEST_DIVERGED"

        (workspace / "zana.yaml").unlink()
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V1},
            headers=auth_header,
        )
        assert response.status_code == 409
        assert response.json()["error"]["code"] == "MANIFEST_DIVERGED"


class TestSourceSafety:
    def test_document_copy_requires_explicit_approval(
        self,
        client,
        auth_header,
        tmp_path,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        source = tmp_path / "approved.md"
        source.write_text("data\n")
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "document", "local_path": str(source), "user_approved": False},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "USER_APPROVAL_REQUIRED"
        assert (
            client.get(f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header).json()
            == []
        )

    def test_approved_path_errors_never_expose_host_path(
        self,
        client,
        auth_header,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        missing = "/tmp/zana-does-not-exist-8f3e/approved.md"
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "document", "local_path": missing, "user_approved": True},
            headers=auth_header,
        )
        assert response.status_code == 422
        body = response.json()
        assert "zana-does-not-exist-8f3e" not in json.dumps(body)
        assert body["error"]["code"] == "SOURCE_PATH_READ"

    def test_symlinked_source_parent_is_rejected_without_external_write(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        outside = workspace.parent.parent / "outside-behavior"
        outside.mkdir(exist_ok=True)
        outside.joinpath("system.md").write_text("external\n")
        behavior = workspace / "behavior"
        behavior.rmdir()
        behavior.symlink_to(outside, target_is_directory=True)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": "prompt\n"},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "PATH_SYMLINK"
        assert outside.joinpath("system.md").read_text() == "external\n"

    @pytest.mark.parametrize(
        ("prepare", "expected_code"),
        [
            (lambda p: (p / "x.md", "relative.md"), "SOURCE_PATH_RELATIVE"),
            (lambda p: (p / "x.md", str(p.parent) + "/../hosts"), "SOURCE_PATH_TRAVERSAL"),
            (lambda p: (p / "x.md", str(p)), "SOURCE_PATH_TYPE"),
            (lambda p: (p / "x.md", "/tmp/a\\b.md"), "SOURCE_PATH_INVALID"),
            (lambda p: (p / "x.md", "/tmp/a\x00b.md"), "SOURCE_PATH_INVALID"),
            (lambda p: (p / "x.sh", str(p / "x.sh")), "SOURCE_KIND_UNSUPPORTED"),
        ],
    )
    def test_document_rejects_unsafe_source_paths(
        self,
        client,
        auth_header,
        tmp_path,
        prepare,  # noqa: ANN001
        expected_code: str,
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        file_path, local_path = prepare(tmp_path)
        file_path.write_text("data\n")
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "document", "local_path": local_path, "user_approved": True},
            headers=auth_header,
        )
        assert response.status_code == 422, response.text
        assert response.json()["error"]["code"] == expected_code

    def test_document_rejects_symlink_and_oversize(
        self,
        client,
        auth_header,
        tmp_path,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        real = tmp_path / "real.md"
        real.write_text("data\n")
        link = tmp_path / "link.md"
        link.symlink_to(real)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "document", "local_path": str(link), "user_approved": True},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SOURCE_PATH_SYMLINK"

        monkeypatch.setattr(authoring, "MAX_DOCUMENT_BYTES", 4)
        big = tmp_path / "big.md"
        big.write_text("x" * 16)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "document", "local_path": str(big), "user_approved": True},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SOURCE_TOO_LARGE"

    def test_behavior_and_eval_bounds(
        self,
        client,
        auth_header,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        monkeypatch.setattr(authoring, "MAX_BEHAVIOR_BYTES", 4)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": "x" * 16},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CONTENT_TOO_LARGE"
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": "a\u0000b"},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "CONTENT_NUL"

    def test_malformed_eval_jsonl_rejected_without_claims(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "evaluation", "eval_kind": "domain", "content": "not-json\n"},
            headers=auth_header,
        )
        assert response.status_code == 422
        body = response.json()
        assert body["error"]["code"] == "SOURCE_INVALID"
        issue_codes = [issue["code"] for issue in body["error"]["details"]["issues"]]
        assert "EVALUATION_JSON" in issue_codes
        assert not (workspace / "evals" / "domain.jsonl").exists()
        assert (
            client.get(f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header).json()
            == []
        )
        assert _no_temp_files(workspace)


class TestAtomicReplacement:
    def test_behavior_and_eval_replacement_keep_single_rows(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        replaced = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V2},
        )
        assert replaced["sha256"] == _digest(BEHAVIOR_V2)
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V2
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "evaluation", "eval_kind": "domain", "content": DOMAIN_EVAL},
        )
        _add(
            client,
            auth_header,
            created["id"],
            {
                "kind": "evaluation",
                "eval_kind": "domain",
                "content": REGRESSION_EVAL,
            },
        )
        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        behavior_rows = [row for row in sources if row["local_path"] == "behavior/system.md"]
        domain_rows = [row for row in sources if row["local_path"] == "evals/domain.jsonl"]
        assert len(behavior_rows) == 1
        assert len(domain_rows) == 1
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        assert on_disk["behavior"] == {"system": "behavior/system.md"}
        assert on_disk["evaluation"]["domain"] == "evals/domain.jsonl"
        assert _no_temp_files(workspace)

    def test_document_replacement_keeps_original_untouched(
        self,
        client,
        auth_header,
        tmp_path,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        original = tmp_path / "policy.md"
        original.write_text("v1\n")
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "document", "local_path": str(original), "user_approved": True},
        )
        original.write_text("v2\n")
        replaced = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "document", "local_path": str(original), "user_approved": True},
        )
        assert (workspace / "knowledge" / "sources" / "policy.md").read_text() == "v2\n"
        assert original.read_text() == "v2\n"
        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert len([row for row in sources if row["local_path"].endswith("policy.md")]) == 1
        assert replaced["sha256"] == hashlib.sha256(b"v2\n").hexdigest()

    def test_db_failure_preserves_prior_good_source(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        first = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )

        def fail_delete(
            repository: CapabilitySourceRepository, capability_id: int, local_path: str
        ) -> int:
            raise RuntimeError("injected db failure")

        monkeypatch.setattr(
            CapabilitySourceRepository, "delete_for_capability_and_path", fail_delete
        )
        failing_client = TestClient(
            create_app(token="test-token-abc123", database_path=database.path),
            raise_server_exceptions=False,
        )
        response = failing_client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1
        sources = failing_client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert len(sources) == 1
        assert sources[0]["sha256"] == first["sha256"]
        assert _no_temp_files(workspace)

    def test_publish_failure_preserves_prior_good_source(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        first = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )

        def fail_publish(temp_path: Path, target: Path) -> None:
            raise authoring.AuthoringError("SOURCE_PUBLISH", "injected publish failure")

        monkeypatch.setattr(authoring, "publish_staged", fail_publish)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "SOURCE_PUBLISH"
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1
        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert len(sources) == 1
        assert sources[0]["sha256"] == first["sha256"]
        assert _no_temp_files(workspace)

    def test_manifest_publish_failure_restores_source_and_manifest(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        prior_manifest = (workspace / "zana.yaml").read_bytes()
        calls = 0
        real_publish = authoring.publish_staged

        def fail_second(temp_path: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise authoring.AuthoringError("SOURCE_PUBLISH", "injected manifest failure")
            real_publish(temp_path, target)

        monkeypatch.setattr(authoring, "publish_staged", fail_second)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "SOURCE_PUBLISH"
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1
        assert (workspace / "zana.yaml").read_bytes() == prior_manifest
        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert len(sources) == 1
        assert sources[0]["sha256"] == _digest(BEHAVIOR_V1)
        assert _no_temp_files(workspace)

    def test_first_source_second_publish_failure_removes_new_target(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        prior_manifest = (workspace / "zana.yaml").read_bytes()
        calls = 0
        real_publish = authoring.publish_staged

        def fail_second(temp_path: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise authoring.AuthoringError("SOURCE_PUBLISH", "injected manifest failure")
            real_publish(temp_path, target)

        monkeypatch.setattr(authoring, "publish_staged", fail_second)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V1},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "SOURCE_PUBLISH"
        assert not (workspace / "behavior" / "system.md").exists()
        assert (workspace / "zana.yaml").read_bytes() == prior_manifest
        assert (
            client.get(f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header).json()
            == []
        )
        assert _no_temp_files(workspace)

    def test_restore_failure_is_honest(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        calls = 0
        real_publish = authoring.publish_staged

        def fail_second(temp_path: Path, target: Path) -> None:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise authoring.AuthoringError("SOURCE_PUBLISH", "injected manifest failure")
            real_publish(temp_path, target)

        def fail_restore(backup: Path, target: Path) -> None:
            raise authoring.AuthoringError("SOURCE_RESTORE", "injected restore failure")

        monkeypatch.setattr(authoring, "publish_staged", fail_second)
        monkeypatch.setattr(authoring, "restore_backup", fail_restore)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "ROLLBACK_UNCONFIRMED"
        assert "preserved" not in response.json()["error"]["message"].lower()


class TestExplicitDatabaseCommit:
    def _fail_first_commit(self, monkeypatch) -> None:  # noqa: ANN001
        calls = 0
        real_commit = UnitOfWork.commit

        def failing_commit(self: UnitOfWork) -> None:
            nonlocal calls
            calls += 1
            if calls == 1:
                raise RuntimeError("injected commit failure")
            real_commit(self)

        monkeypatch.setattr(UnitOfWork, "commit", failing_commit)

    def _failing_client(self, database) -> TestClient:  # noqa: ANN001
        return TestClient(
            create_app(token="test-token-abc123", database_path=database.path),
            raise_server_exceptions=False,
        )

    def test_create_commit_failure_removes_new_workspace(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        self._fail_first_commit(monkeypatch)
        failing_client = self._failing_client(database)
        response = failing_client.post(
            "/api/v1/capabilities",
            json={"name": "broken", "version": "0.1.0"},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "DATABASE_COMMIT_FAILED"
        assert not (database.path.parent / "capabilities" / "1").exists()
        assert failing_client.get("/api/v1/capabilities", headers=auth_header).json() == []

    def test_update_commit_failure_restores_prior_manifest(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        prior = (workspace / "zana.yaml").read_bytes()
        self._fail_first_commit(monkeypatch)
        failing_client = self._failing_client(database)
        response = failing_client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": dict(VALID_MANIFEST, version="0.2.0")},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "DATABASE_COMMIT_FAILED"
        assert (workspace / "zana.yaml").read_bytes() == prior
        detail = failing_client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        assert detail["manifest_json"] == VALID_MANIFEST

    def test_update_commit_failure_removes_new_manifest_keeps_workspace(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        (workspace / "zana.yaml").unlink()
        (workspace / "keep.txt").write_text("keep\n")
        self._fail_first_commit(monkeypatch)
        failing_client = self._failing_client(database)
        response = failing_client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": dict(VALID_MANIFEST, version="0.2.0")},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "DATABASE_COMMIT_FAILED"
        assert not (workspace / "zana.yaml").exists()
        assert (workspace / "keep.txt").read_text() == "keep\n"
        detail = failing_client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        assert detail["manifest_json"] == VALID_MANIFEST

    def test_source_commit_failure_restores_prior_source(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        first = _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        prior_manifest = (workspace / "zana.yaml").read_bytes()
        self._fail_first_commit(monkeypatch)
        failing_client = self._failing_client(database)
        response = failing_client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "DATABASE_COMMIT_FAILED"
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1
        assert (workspace / "zana.yaml").read_bytes() == prior_manifest
        sources = failing_client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert len(sources) == 1
        assert sources[0]["sha256"] == first["sha256"]
        assert _no_temp_files(workspace)


class TestTruncationTruth:
    def test_validate_issue_counts_report_total_and_returned(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        import zana_core.api.capabilities as api_capabilities

        manifest = dict(
            VALID_MANIFEST,
            evaluation={"domain": "evals/domain.jsonl"},
        )
        created = _create(client, auth_header, manifest_json=manifest)
        workspace = _workspace_path(created, database)
        (workspace / "evals" / "domain.jsonl").write_text(
            "not-json-1\nnot-json-2\n", encoding="utf-8"
        )
        monkeypatch.setattr(api_capabilities, "_MAX_REPORT_ISSUES", 1)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert report["issue_count"] >= 2
        assert report["returned_issue_count"] == 1
        assert len(report["issues"]) == 1


class TestValidationGating:
    def test_validation_gates_on_huge_root_auxiliary(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        from zana_core.capabilities.validator import CapabilitySourceValidator

        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        (workspace / "huge-root.txt").write_text("x" * 64)
        monkeypatch.setattr(authoring, "MAX_VALIDATION_TREE_BYTES", 8)
        calls = 0

        def fail_if_called(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal calls
            calls += 1
            raise AssertionError("validator must not run")

        monkeypatch.setattr(CapabilitySourceValidator, "validate", fail_if_called)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert calls == 0
        assert any(issue["code"] == "SOURCE_TREE_LIMIT" for issue in report["issues"])

    def test_validation_gates_on_symlink_without_validator_call(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        from zana_core.capabilities.validator import CapabilitySourceValidator

        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        outside = workspace.parent.parent / "outside-canary"
        outside.mkdir(exist_ok=True)
        outside.joinpath("canary.md").write_text("canary\n")
        (workspace / "behavior").rmdir()
        (workspace / "behavior").symlink_to(outside, target_is_directory=True)
        calls = 0

        def fail_if_called(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal calls
            calls += 1
            raise AssertionError("validator must not run")

        monkeypatch.setattr(CapabilitySourceValidator, "validate", fail_if_called)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert calls == 0
        assert any(issue["code"] == "PATH_SYMLINK" for issue in report["issues"])
        assert outside.joinpath("canary.md").read_text() == "canary\n"

    def test_validation_gates_on_oversize_preflight(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        from zana_core.capabilities.validator import CapabilitySourceValidator

        manifest = dict(
            VALID_MANIFEST,
            evaluation={"domain": "evals/domain.jsonl"},
        )
        created = _create(client, auth_header, manifest_json=manifest)
        workspace = _workspace_path(created, database)
        (workspace / "evals" / "domain.jsonl").write_text("x" * 64)
        # The route uses the module constants for its bounded preflight.
        monkeypatch.setattr(authoring, "MAX_VALIDATION_FILE_COUNT", 512)
        monkeypatch.setattr(authoring, "MAX_VALIDATION_TREE_BYTES", 8)
        calls = 0

        def fail_if_called(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal calls
            calls += 1
            raise AssertionError("validator must not run")

        monkeypatch.setattr(CapabilitySourceValidator, "validate", fail_if_called)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert calls == 0
        assert any(issue["code"] == "SOURCE_TREE_LIMIT" for issue in report["issues"])

    def test_validation_gates_on_bounded_manifest_safety_failure(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        from zana_core.capabilities.validator import CapabilitySourceValidator

        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        (workspace / "zana.yaml").write_bytes(b"x" * 64)
        monkeypatch.setattr(authoring, "MAX_MANIFEST_BYTES", 8)
        calls = 0

        def fail_if_called(*args, **kwargs):  # noqa: ANN001, ANN002, ANN003
            nonlocal calls
            calls += 1
            raise AssertionError("validator must not run")

        monkeypatch.setattr(CapabilitySourceValidator, "validate", fail_if_called)
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert calls == 0
        assert any(
            issue["code"] in ("MANIFEST_TOO_LARGE", "SOURCE_TREE_LIMIT")
            for issue in report["issues"]
        )


class TestBackupLeak:
    def test_manifest_backup_failure_discards_source_backup(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        calls = 0
        real_stage = authoring.stage_backup

        def fail_manifest(target: Path) -> object:
            nonlocal calls
            calls += 1
            if calls == 2:
                raise authoring.AuthoringError("SOURCE_BACKUP", "injected manifest backup failure")
            return real_stage(target)

        monkeypatch.setattr(authoring, "stage_backup", fail_manifest)
        response = client.post(
            f"/api/v1/capabilities/{created['id']}/sources",
            json={"kind": "behavior", "content": BEHAVIOR_V2},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert not any(".zana-tmp-" in item.name for item in workspace.rglob("*"))
        assert (workspace / "behavior" / "system.md").read_text() == BEHAVIOR_V1


class TestCreateCleanupHonesty:
    def test_authoring_failure_cleanup_failure_returns_rollback_unconfirmed(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        def fail_workspace(workspace: Path, data_root: Path | None = None) -> None:
            raise authoring.AuthoringError("WORKSPACE_CREATE", "injected create failure")

        def fail_remove(workspace: Path, data_root: Path, *, created_by_request: bool) -> bool:
            return False

        monkeypatch.setattr(authoring, "ensure_workspace", fail_workspace)
        monkeypatch.setattr(authoring, "remove_workspace", fail_remove)
        response = client.post(
            "/api/v1/capabilities",
            json={"name": "broken", "version": "0.1.0"},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "ROLLBACK_UNCONFIRMED"

    def test_oserror_failure_cleanup_failure_returns_rollback_unconfirmed(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        def fail_write(workspace: Path, manifest: dict) -> None:  # noqa: ANN001
            raise OSError("injected manifest write failure")

        def fail_remove(workspace: Path, data_root: Path, *, created_by_request: bool) -> bool:
            return False

        monkeypatch.setattr(authoring, "write_manifest", fail_write)
        monkeypatch.setattr(authoring, "remove_workspace", fail_remove)
        response = client.post(
            "/api/v1/capabilities",
            json={"name": "broken", "version": "0.1.0"},
            headers=auth_header,
        )
        assert response.status_code == 500
        assert response.json()["error"]["code"] == "ROLLBACK_UNCONFIRMED"


class TestDisclosureAndAuth:
    def test_detail_and_sources_never_expose_host_paths(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "behavior", "content": BEHAVIOR_V1},
        )
        detail = client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        serialized = json.dumps(detail)
        assert str(workspace) not in serialized
        assert str(database.path.parent) not in serialized
        assert "working_dir" not in detail
        assert all(not source["local_path"].startswith("/") for source in detail["sources"])
        sources = client.get(
            f"/api/v1/capabilities/{created['id']}/sources", headers=auth_header
        ).json()
        assert str(workspace) not in json.dumps(sources)

    def test_new_routes_require_auth(self, client) -> None:
        assert client.get("/api/v1/capabilities/1/sources").status_code == 401
        assert client.get("/api/v1/capabilities/1/detail").status_code == 401
        assert client.post("/api/v1/capabilities/1/sources", json={}).status_code == 401
        assert client.post("/api/v1/capabilities/1/validate").status_code == 401


class TestManifestUpdate:
    def test_update_backup_failure_preserves_untouched_manifest(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        prior = (workspace / "zana.yaml").read_bytes()

        def fail_backup(target: Path) -> object:
            raise authoring.AuthoringError("SOURCE_TOO_LARGE", "injected backup failure")

        monkeypatch.setattr(authoring, "stage_backup", fail_backup)
        response = client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": dict(VALID_MANIFEST, version="0.2.0")},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SOURCE_TOO_LARGE"
        assert (workspace / "zana.yaml").read_bytes() == prior
        detail = client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        assert detail["manifest_json"] == VALID_MANIFEST

    def test_update_backup_failure_oserror_preserves_untouched_manifest(
        self,
        client,
        auth_header,
        database,
        monkeypatch,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        prior = (workspace / "zana.yaml").read_bytes()

        def fail_backup(target: Path) -> object:
            raise authoring.AuthoringError("SOURCE_BACKUP", "cannot stage source rollback")

        monkeypatch.setattr(authoring, "stage_backup", fail_backup)
        response = client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": dict(VALID_MANIFEST, version="0.2.0")},
            headers=auth_header,
        )
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "SOURCE_BACKUP"
        assert (workspace / "zana.yaml").read_bytes() == prior

    def test_update_manifest_writes_and_removes_disk_coherently(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        next_manifest = dict(VALID_MANIFEST, version="0.2.0", name="Math Tutor v2")
        response = client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": next_manifest},
            headers=auth_header,
        )
        assert response.status_code == 200
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        assert on_disk == next_manifest
        assert response.json()["manifest_json"] == next_manifest

        cleared = client.put(
            f"/api/v1/capabilities/{created['id']}",
            json={"manifest_json": {}},
            headers=auth_header,
        )
        assert cleared.status_code == 200
        assert not (workspace / "zana.yaml").exists()
        assert cleared.json()["manifest_json"] == {}
        report = client.post(
            f"/api/v1/capabilities/{created['id']}/validate", headers=auth_header
        ).json()
        assert report["valid"] is False
        assert any(issue["code"] == "MANIFEST_MISSING" for issue in report["issues"])

    def test_manifest_stays_coherent_after_source_add(
        self,
        client,
        auth_header,
        database,  # noqa: ANN001
    ) -> None:
        created = _create(client, auth_header, manifest_json=VALID_MANIFEST)
        workspace = _workspace_path(created, database)
        _add(
            client,
            auth_header,
            created["id"],
            {"kind": "evaluation", "eval_kind": "domain", "content": MATH_EVAL_JSONL},
        )
        on_disk = parse_safe_yaml((workspace / "zana.yaml").read_text(encoding="utf-8"))[0]
        detail = client.get(
            f"/api/v1/capabilities/{created['id']}/detail", headers=auth_header
        ).json()
        assert on_disk == detail["manifest_json"]
