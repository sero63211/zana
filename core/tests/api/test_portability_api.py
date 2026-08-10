"""Focused authenticated router tests for the portability product API."""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from tests.portability.helpers import archive_file, build_layout
from zana_core.api import portability as portability_api
from zana_core.api.deps import ServerConfig
from zana_core.api.portability import router as portability_router
from zana_core.db.database import Database
from zana_core.db.models import Image
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import VerificationStatus
from zana_core.images import archive as images_archive
from zana_core.images.models import RunnableState
from zana_core.portability import models as portability_models


@pytest.fixture
def app_and_roots(tmp_path: Path):
    data_root = tmp_path / "data"
    data_root.mkdir()
    database = Database(tmp_path / "zana.sqlite3")
    database.upgrade()
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.state.server_config = ServerConfig(token="test-token", version="0.1.0")
    app.state.session_factory = database.session_factory
    app.state.data_root = data_root

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(_request, exc: StarletteHTTPException) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content=exc.detail)

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(_request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            status_code=422,
            content={
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed.",
                    "details": {"errors": exc.errors()},
                    "recoverable": True,
                    "actions": ["fix_request_payload"],
                }
            },
        )

    app.include_router(portability_router)
    yield app, data_root, database.session_factory
    database.close()


def client_for(app: FastAPI, *, token: str | None = "test-token") -> TestClient:
    return TestClient(app, headers={"Authorization": f"Bearer {token}"} if token else {})


def test_all_portability_routes_require_auth(app_and_roots) -> None:
    app, _data_root, _session_factory = app_and_roots
    client = client_for(app, token=None)
    digest = "sha256:" + "a" * 64
    assert client.post(f"/api/v1/images/{digest}/verify").status_code == 401
    assert client.post(f"/api/v1/images/{digest}/export").status_code == 401
    assert client.post("/api/v1/images/import").status_code == 401
    assert client.delete(f"/api/v1/images/{digest}").status_code == 401


def test_verify_and_export_round_trip_without_host_paths(
    app_and_roots,
    tmp_path: Path,
) -> None:
    app, data_root, session_factory = app_and_roots
    client = client_for(app)
    layout, image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    manifest = (layout / "manifest.json").read_text(encoding="utf-8")
    config_digest = json.loads(manifest)["config"]["digest"]
    target = data_root / "portability" / "layouts" / image_digest.removeprefix("sha256:")
    shutil.copytree(layout, target)
    with UnitOfWork(session_factory) as uow:
        uow.images.add(
            Image(
                digest=image_digest,
                name="policy-assistant",
                version="1.0.0",
                config_digest=config_digest,
                verification_status=VerificationStatus.VERIFIED_LOCAL,
                base_model_key="ollama:example",
                base_model_digest="",
            )
        )

    verified = client.post(f"/api/v1/images/{image_digest}/verify")
    assert verified.status_code == 200
    body = verified.json()
    assert body["digest"] == image_digest
    assert body["base_model_available"] is False
    assert body["layout_source"] == "persisted"
    assert str(data_root) not in verified.text

    exported = client.post(
        f"/api/v1/images/{image_digest}/export",
        json={
            "output_path": str(data_root / "portability" / "exports" / "image.tar"),
            "codec": "tar",
            "user_approved": True,
        },
    )
    assert exported.status_code == 200
    export_body = exported.json()
    assert export_body["digest"] == image_digest
    assert export_body["archive_path"] == "portability/exports/image.tar"
    assert export_body["report_path"] == "portability/exports/image.tar.report.json"
    assert export_body["report_digest"].startswith("sha256:")
    assert str(data_root) not in exported.text
    assert (data_root / "portability" / "exports" / "image.tar").exists()
    assert (data_root / "portability" / "exports" / "image.tar.report.json").exists()

    denied = client.post(
        f"/api/v1/images/{image_digest}/export",
        json={
            "output_path": str(tmp_path / "outside.tar"),
            "codec": "tar",
            "user_approved": True,
        },
    )
    assert denied.status_code == 422
    assert denied.json()["error"]["code"] == "PATH_NOT_APPROVED"


def test_import_idempotency_and_delete_flow(app_and_roots, tmp_path: Path) -> None:
    app, data_root, session_factory = app_and_roots
    client = client_for(app)
    layout, image_digest = build_layout(tmp_path / "lay", layer_bytes=b"knowledge")
    archive = archive_file(tmp_path / "image.tar", layout)
    imports_root = data_root / "portability" / "imports"
    imports_root.mkdir(parents=True)
    source = imports_root / "image.tar"
    shutil.copy2(archive, source)

    unapproved = client.post(
        "/api/v1/images/import",
        json={"local_path": str(source), "codec": "tar", "user_approved": False},
    )
    assert unapproved.status_code == 428
    assert unapproved.json()["error"]["code"] == "APPROVAL_REQUIRED"

    created = client.post(
        "/api/v1/images/import",
        json={"local_path": str(source), "codec": "tar", "user_approved": True},
    )
    assert created.status_code == 201
    created_body = created.json()
    assert created_body["digest"] == image_digest
    assert created_body["created"] is True
    assert created_body["idempotent"] is False
    assert created_body["runnable"] == "not-runnable-weak-identity"
    assert created_body["base_model_available"] is False
    assert created_body["artifact_count"] >= 4
    assert str(data_root) not in created.text

    duplicate = client.post(
        "/api/v1/images/import",
        json={"local_path": str(source), "codec": "tar", "user_approved": True},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["idempotent"] is True

    with UnitOfWork(session_factory) as uow:
        image = uow.images.get(image_digest)
        assert image is not None
        image.config_digest = "sha256:" + "b" * 64
    conflicted = client.post(
        "/api/v1/images/import",
        json={"local_path": str(source), "codec": "tar", "user_approved": True},
    )
    assert conflicted.status_code == 409
    assert conflicted.json()["error"]["code"] == "IMPORT_CONFLICT"

    with UnitOfWork(session_factory) as uow:
        image = uow.images.get(image_digest)
        assert image is not None
        image.config_digest = created_body["config_digest"]

    unconfirmed_delete = client.delete(f"/api/v1/images/{image_digest}")
    assert unconfirmed_delete.status_code == 428
    deleted = client.delete(f"/api/v1/images/{image_digest}?confirmed=true")
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    assert deleted.json()["artifacts_retained"] is True

    missing = client.delete(f"/api/v1/images/{image_digest}?confirmed=true")
    assert missing.status_code == 404
    assert missing.json()["error"]["code"] == "IMAGE_NOT_FOUND"


def test_corrupt_archive_import_returns_actionable_error(app_and_roots, tmp_path: Path) -> None:
    app, data_root, _session_factory = app_and_roots
    client = client_for(app)
    layout, _digest = build_layout(tmp_path / "lay", layer_bytes=b"layer")
    blob = next((layout / "blobs" / "sha256").iterdir())
    blob.write_bytes(blob.read_bytes() + b"x")
    archive = archive_file(tmp_path / "corrupt.tar", layout)
    imports_root = data_root / "portability" / "imports"
    imports_root.mkdir(parents=True)
    source = imports_root / "corrupt.tar"
    shutil.copy2(archive, source)
    response = client.post(
        "/api/v1/images/import",
        json={"local_path": str(source), "codec": "tar", "user_approved": True},
    )
    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "OCI_VALIDATION_FAILED"
    assert "corrupt.tar" not in body["error"]["message"]


def test_import_base_availability_is_not_guessed_from_runnable(
    app_and_roots,
    monkeypatch,
) -> None:
    app, _data_root, _session_factory = app_and_roots
    client = client_for(app)
    base_digest = "sha256:" + "c" * 64
    fake_import = SimpleNamespace(
        result=SimpleNamespace(
            operation_id="op-1",
            codec=portability_models.CodecKind.TAR,
            registration=SimpleNamespace(
                image_digest="sha256:" + "a" * 64,
                config_digest="sha256:" + "b" * 64,
                codec=portability_models.CodecKind.TAR,
                runnable=RunnableState.NOT_RUNNABLE_UNKNOWN,
                runnable_reason="another dependency is missing",
                base_model_digest=base_digest,
            ),
            archive_digest="sha256:" + "d" * 64,
        ),
        idempotent=True,
        created=False,
        base_model_available=True,
        artifact_count=7,
    )
    fake_service = SimpleNamespace(import_archive=lambda **kwargs: fake_import)
    with patch.object(portability_api, "_service", return_value=fake_service):
        response = client.post(
            "/api/v1/images/import",
            json={
                "local_path": "/data/portability/imports/fake.tar",
                "codec": "tar",
                "user_approved": True,
            },
        )
    assert response.status_code == 200
    body = response.json()
    assert body["runnable"] == "not-runnable-unknown"
    assert body["base_model_available"] is True
    assert body["artifact_count"] == 7


@pytest.mark.skipif(
    images_archive.zstd_available(),
    reason="honest zstd unavailable assertion requires no zstandard",
)
def test_default_zstd_export_is_honest_when_unavailable(app_and_roots) -> None:
    app, data_root, _session_factory = app_and_roots
    client = client_for(app)
    digest = "sha256:" + "a" * 64
    response = client.post(
        f"/api/v1/images/{digest}/export",
        json={
            "output_path": str(data_root / "portability" / "exports" / "image.tar.zst"),
            "user_approved": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CODEC_UNAVAILABLE"
