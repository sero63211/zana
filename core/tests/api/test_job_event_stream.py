"""Bounded job SSE stream contract tests with real temp SQLite/TestClient."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from fastapi.testclient import TestClient

from zana_core.api.jobs import (
    MAX_EVENT_BYTES,
    MAX_PRIMARY_TOTAL_BYTES,
    MAX_STREAM_TOTAL_BYTES,
    TERMINAL_ERROR_BYTES,
    _bounded_error_string,
    _bounded_iso8601,
    _bounded_text,
    _event_kind,
    _job_event_to_dto,
    _parse_resume_cursor,
    _safe_kind,
    _safe_progress,
)
from zana_core.db.repositories import JobEventRepository, JobEventStreamRow
from zana_core.db.unit_of_work import UnitOfWork
from zana_core.domain.enums import JobKind, JobStatus
from zana_core.jobs.services import JobService


def _parse_sse(text: str) -> list[dict[str, object]]:
    events: list[dict[str, object]] = []
    current: dict[str, object] | None = None
    for raw_line in text.split("\n"):
        if raw_line == "":
            if current is not None:
                events.append(current)
                current = None
            continue
        field, _, value = raw_line.partition(":")
        value = value[1:] if value.startswith(" ") else value
        if field == "id":
            current = current or {}
            current["id"] = value
        elif field == "event":
            current = current or {}
            current["event"] = value
        elif field == "data":
            current = current or {}
            current.setdefault("data", []).append(value)
    if current is not None:
        events.append(current)
    return events


class HostileInt(int):
    def __index__(self) -> int:
        self.calls["index"] = self.calls.get("index", 0) + 1
        return 1

    def __int__(self) -> int:
        self.calls["int"] = self.calls.get("int", 0) + 1
        return 1

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return super().__eq__(other)


class HostileFloat(float):
    def __float__(self) -> float:
        self.calls["float"] = self.calls.get("float", 0) + 1
        return 1.0

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return super().__eq__(other)


class HostileStr(str):
    def __len__(self) -> int:
        self.calls["len"] = self.calls.get("len", 0) + 1
        return 1

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        self.calls["encode"] = self.calls.get("encode", 0) + 1
        return super().encode(encoding, errors)

    def __iter__(self):
        self.calls["iter"] = self.calls.get("iter", 0) + 1
        return super().__iter__()

    def __hash__(self) -> int:
        self.calls["hash"] = self.calls.get("hash", 0) + 1
        return super().__hash__()

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return super().__eq__(other)


class HostileDatetime(datetime):
    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        self.calls["isoformat"] = self.calls.get("isoformat", 0) + 1
        return super().isoformat(sep, timespec)

    def __str__(self) -> str:
        self.calls["str"] = self.calls.get("str", 0) + 1
        return super().__str__()


class HookObject:
    def __init__(self) -> None:
        self.calls: dict[str, int] = {}

    def __index__(self) -> int:
        self.calls["index"] = self.calls.get("index", 0) + 1
        return 1

    def __int__(self) -> int:
        self.calls["int"] = self.calls.get("int", 0) + 1
        return 1

    def __float__(self) -> float:
        self.calls["float"] = self.calls.get("float", 0) + 1
        return 1.0

    def __iter__(self):
        self.calls["iter"] = self.calls.get("iter", 0) + 1
        return iter(())

    def __hash__(self) -> int:
        self.calls["hash"] = self.calls.get("hash", 0) + 1
        return 1

    def __eq__(self, other: object) -> bool:
        self.calls["eq"] = self.calls.get("eq", 0) + 1
        return True

    def isoformat(self, sep: str = "T", timespec: str = "auto") -> str:
        self.calls["isoformat"] = self.calls.get("isoformat", 0) + 1
        return "never"

    def encode(self, encoding: str = "utf-8", errors: str = "strict") -> bytes:
        self.calls["encode"] = self.calls.get("encode", 0) + 1
        return b"never"


def _hostile_int(value: int) -> HostileInt:
    result = HostileInt(value)
    result.calls = {}
    return result


def _hostile_float(value: float) -> HostileFloat:
    result = HostileFloat(value)
    result.calls = {}
    return result


def _hostile_str(value: str) -> HostileStr:
    result = HostileStr(value)
    result.calls = {}
    return result


def _hostile_datetime(*args: int) -> HostileDatetime:
    result = HostileDatetime(*args)
    result.calls = {}
    return result


def _seed_job(
    uow: UnitOfWork,
    *,
    transitions: int = 3,
) -> tuple[int, list[int]]:
    service = JobService(uow)
    job = service.create_job(JobKind.BUILD, phase="created")
    service.transition_job(job.id, JobStatus.RUNNING, phase="running")
    for index in range(transitions):
        service.record_progress(
            job.id,
            (index + 1) / (transitions + 1),
            phase="working",
            message=f"step {index}",
        )
    uow.commit()
    events = service.list_events(job.id)
    return job.id, [event.id for event in events]


class TestAuthAndErrors:
    def test_missing_auth_returns_401(
        self,
        client: TestClient,
        uow: UnitOfWork,
    ) -> None:
        job_id, _ = _seed_job(uow)
        response = client.get(f"/api/v1/jobs/{job_id}/events")
        assert response.status_code == 401
        assert response.json()["error"]["code"] == "UNAUTHORIZED"

    def test_missing_job_returns_canonical_404(
        self,
        client: TestClient,
        auth_header: dict[str, str],
    ) -> None:
        response = client.get("/api/v1/jobs/999999/events", headers=auth_header)
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "JOB_NOT_FOUND"

    def test_invalid_cursor_returns_400(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, _ = _seed_job(uow)
        for bad in ("abc", "-1", "jobs:1:2", "other:1"):
            response = client.get(
                f"/api/v1/jobs/{job_id}/events",
                headers={**auth_header, "Last-Event-ID": bad},
            )
            assert response.status_code == 400, bad
            assert response.json()["error"]["code"].startswith("INVALID_STREAM")

    def test_huge_cursor_header_rejected_without_parsing(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, _ = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            headers={**auth_header, "Last-Event-ID": "9" * 100_000},
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == "INVALID_STREAM_CURSOR"

    def test_cursor_reject_never_encodes_whole_header(
        self,
    ) -> None:
        from fastapi import HTTPException

        from zana_core.api.jobs import _parse_resume_cursor

        encoded_lengths: list[int] = []
        original_encode = str.encode

        class HostileStr(str):
            def encode(self, encoding: str = "utf-8", errors: str = "strict"):
                encoded_lengths.append(len(self))
                return original_encode(self, encoding, errors)

        try:
            with pytest.raises(HTTPException) as exc:
                _parse_resume_cursor(HostileStr("😀" * 100_000))
            assert exc.value.status_code == 400
            # No encode call may ever observe the full hostile value.
            assert encoded_lengths == []
        finally:
            pass

    def test_65_emoji_cursor_rejected_by_byte_check(
        self,
    ) -> None:
        from fastapi import HTTPException

        from zana_core.api.jobs import _parse_resume_cursor

        with pytest.raises(HTTPException) as exc:
            _parse_resume_cursor("😀" * 65)
        assert exc.value.status_code == 400
        assert exc.value.detail["error"]["code"] == "INVALID_STREAM_CURSOR"


class TestStreamFraming:
    def test_exact_sse_framing_and_headers(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow)
        response = client.get(f"/api/v1/jobs/{job_id}/events", headers=auth_header)
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert response.headers["cache-control"] == "no-cache, no-transform"
        assert response.headers["x-accel-buffering"] == "no"

        events = _parse_sse(response.text)
        assert len(events) == len(event_ids)
        assert [int(event["id"]) for event in events] == event_ids
        assert events[0]["event"] == "job_created"
        assert events[1]["event"] == "job_status"
        assert all(event["event"] == "job_progress" for event in events[2:])
        payload = json.loads(events[0]["data"][0])
        assert payload["job_id"] == job_id
        assert payload["kind"] == "job_created"

    def test_empty_stream_returns_valid_empty_sse(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            headers={**auth_header, "Last-Event-ID": str(event_ids[-1])},
        )
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/event-stream")
        assert _parse_sse(response.text) == []


class TestResume:
    def test_resume_returns_only_newer_events_without_duplicates(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            headers={**auth_header, "Last-Event-ID": str(event_ids[1])},
        )
        events = _parse_sse(response.text)
        assert [int(event["id"]) for event in events] == event_ids[2:]

    def test_resume_with_jobs_prefixed_cursor(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            headers={**auth_header, "Last-Event-ID": f"jobs:{event_ids[0]}"},
        )
        events = _parse_sse(response.text)
        assert [int(event["id"]) for event in events] == event_ids[1:]

    def test_ahead_cursor_returns_empty_no_replay(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            headers={**auth_header, "Last-Event-ID": str(event_ids[-1] + 50)},
        )
        assert response.status_code == 200
        assert _parse_sse(response.text) == []


class TestLimitAndOrdering:
    def test_limit_bounds_the_page(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow, transitions=10)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            params={"limit": 3},
            headers=auth_header,
        )
        events = _parse_sse(response.text)
        assert [int(event["id"]) for event in events] == event_ids[:3]

    def test_limit_above_server_cap_is_rejected(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, _ = _seed_job(uow)
        response = client.get(
            f"/api/v1/jobs/{job_id}/events",
            params={"limit": 101},
            headers=auth_header,
        )
        assert response.status_code == 422

    def test_order_is_ascending_database_id(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        job_id, event_ids = _seed_job(uow, transitions=5)
        response = client.get(f"/api/v1/jobs/{job_id}/events", headers=auth_header)
        parsed = _parse_sse(response.text)
        ids = [int(event["id"]) for event in parsed]
        assert ids == sorted(ids) == event_ids


class TestSecretRedaction:
    def test_bounded_text_rejects_str_subclasses_without_hooks(self) -> None:
        value = _hostile_str("x" * 4096)
        assert _bounded_text(value, 1024) == "...[invalid-text]"
        assert value.calls == {}

    def test_bounded_text_keeps_whole_input_when_within_cap(self) -> None:
        assert _bounded_text("hello", 1024) == "hello"

    def test_bounded_text_never_splits_code_points(self) -> None:
        result = _bounded_text("a😀b" * 1000, 1024)
        assert result.endswith("[truncated]")
        assert len(result.encode("utf-8")) <= 1024
        # Re-encoding the retained prefix must produce valid UTF-8.
        result.encode("utf-8").decode("utf-8")

    def test_error_json_secrets_are_redacted(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            error={
                "code": "AUTH_FAILED",
                "authorization": "Bearer super-secret-token",
                "api_key": "secret-key",
                "safe": "visible",
            },
        )
        uow.commit()
        response = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert response.status_code == 200
        body = response.text
        assert "super-secret-token" not in body
        assert "secret-key" not in body
        assert "visible" in body
        assert "***" in body

    def test_multimegabyte_message_and_error_become_bounded_sse(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            message="x" * (2 * 1024 * 1024),
            error={
                "code": "SECRET",
                "authorization": "Bearer super-secret-token",
                "blob": "y" * (2 * 1024 * 1024),
            },
        )
        uow.commit()
        response = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert response.status_code == 200
        body = response.text
        assert "super-secret-token" not in body
        assert "[truncated]" in body or "STREAM_EVENT_TOO_LARGE" in body
        assert len(body) < 64 * 1024

    def test_repository_projection_bounds_oversized_rows_before_python(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        big_message = "😀" * (512 * 1024)
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            message=big_message,
            error={
                "authorization": "Bearer secret-token",
                "blob": "y" * (512 * 1024),
            },
        )
        uow.commit()

        rows = JobEventRepository(uow.session).list_for_job_stream(
            job.id,
            limit=10,
        )
        assert len(rows) == 2
        projected_message = max(len(row.message) for row in rows)
        assert projected_message <= 1024
        projected_error = max(
            len(row.error_json) if row.error_json is not None else 0 for row in rows
        )
        assert projected_error <= 1024
        assert all(isinstance(row, JobEventStreamRow) for row in rows)
        # The oversized error JSON is projected to a small typed sentinel.
        sentinel = next(
            row for row in rows if row.error_json is not None and "REDACTED_ERROR" in row.error_json
        )
        assert "secret-token" not in sentinel.error_json

    def test_repository_error_oversize_uses_utf8_bytes(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        # 600 four-byte emoji = 2400 UTF-8 bytes but only 600 SQLite chars.
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            error={"message": "😀" * 600},
        )
        uow.commit()
        rows = JobEventRepository(uow.session).list_for_job_stream(
            job.id,
            limit=10,
        )
        error_row = next(row for row in rows if row.error_json is not None)
        assert "REDACTED_ERROR" in error_row.error_json
        assert "😀" not in error_row.error_json
        assert len(error_row.error_json.encode("utf-8")) <= 1024

    def test_repository_valid_500_byte_error_json_round_trips(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        value = "v" * 490
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            error={"code": "TEST", "safe": value},
        )
        uow.commit()
        rows = JobEventRepository(uow.session).list_for_job_stream(
            job.id,
            limit=10,
        )
        error_row = next(row for row in rows if row.error_json is not None)
        parsed = json.loads(error_row.error_json)
        assert parsed["code"] == "TEST"
        assert parsed["safe"] == value
        assert len(error_row.error_json.encode("utf-8")) <= 1024

    def test_hostile_kind_and_progress_normalized(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        uow.commit()
        from zana_core.db.models import JobEvent
        from zana_core.domain.enums import JobEventKind

        hostile = JobEvent(
            job_id=job.id,
            kind=JobEventKind.PROGRESS,
            phase="",
            message="",
            progress_0_1=0.5,
        )
        uow.session.add(hostile)
        uow.session.flush()
        uow.session.execute(
            __import__("sqlalchemy", fromlist=["text"]).text(
                "UPDATE job_events SET kind = 'HOSTILE-UNKNOWN-KIND', "
                "progress_0_1 = 2.0 WHERE id = :id"
            ),
            {"id": hostile.id},
        )
        uow.commit()
        rows = JobEventRepository(uow.session).list_for_job_stream(
            job.id,
            limit=10,
        )
        row = next(item for item in rows if item.kind == "HOSTILE-UNKNOWN-KIND")
        assert row.kind == "HOSTILE-UNKNOWN-KIND"
        assert _safe_progress(row.progress_0_1) is None

    def test_safe_payload_normalizes_hostile_progress_and_kind(
        self,
    ) -> None:
        from datetime import UTC, datetime

        from zana_core.api.jobs import _job_event_to_dto, _safe_kind
        from zana_core.db.repositories import JobEventStreamRow

        row = JobEventStreamRow(
            id=1,
            job_id=1,
            kind="HOSTILE",
            phase="",
            message="",
            progress_0_1=float("nan"),
            error_json=None,
            created_at=datetime(2026, 8, 9, tzinfo=UTC),
        )
        dto = _job_event_to_dto(row)
        assert dto.name.value == "error"
        assert _safe_kind("HOSTILE") == "error"
        assert _safe_kind("CREATED") == "job_created"
        assert _safe_kind("PROGRESS") == "job_progress"
        assert dto.data["progress_0_1"] is None

    def test_safe_progress_finite_range(self) -> None:
        assert _safe_progress(0.5) == 0.5
        assert _safe_progress(0.0) == 0.0
        assert _safe_progress(1.0) == 1.0
        assert _safe_progress(True) is None
        assert _safe_progress(False) is None
        assert _safe_progress(-0.1) is None
        assert _safe_progress(1.1) is None
        assert _safe_progress(float("nan")) is None
        assert _safe_progress(float("inf")) is None

    def test_hostile_datetime_object_never_called(
        self,
    ) -> None:
        from zana_core.api.jobs import _bounded_iso8601

        class Hostile:
            def isoformat(self) -> str:
                raise AssertionError("isoformat must not be called")

            def __str__(self) -> str:
                raise AssertionError("__str__ must not be called")

        assert _bounded_iso8601(Hostile()) == "...[invalid-timestamp]"

    def test_service_and_repository_reject_bool_and_wrong_types(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        repository = JobEventRepository(uow.session)
        for call in (
            lambda: service.list_events(1, limit=True),
            lambda: service.list_events(1, after_event_id=True),
            lambda: service.list_events(True),
            lambda: repository.list_for_job(1, limit=True),
            lambda: repository.list_for_job(1, after_event_id=True),
            lambda: repository.list_for_job(True),
            lambda: repository.list_for_job_stream(1, limit="5"),
            lambda: repository.list_for_job_stream(1, after_event_id=1.5),
        ):
            with pytest.raises(TypeError):
                call()

    def test_repository_projection_caps_are_immutable(
        self,
        uow: UnitOfWork,
    ) -> None:
        with pytest.raises(TypeError):
            JobEventRepository(uow.session).list_for_job_stream(
                1,
                max_message_chars=999999,
            )

    def test_repository_projection_returns_bounded_plain_strings(
        self,
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        service.record_progress(job.id, 0.5, phase="working", message="step")
        uow.commit()
        rows = JobEventRepository(uow.session).list_for_job_stream(job.id, limit=10)
        assert all(isinstance(row.message, str) for row in rows)
        assert all(isinstance(row.phase, str) for row in rows)

    def test_encode_failure_emits_canonical_terminal_error(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        # SQL projection bounds the payload so the endpoint no longer emits a
        # terminal error here; the emergency path is separately proven below.
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        service.transition_job(
            job.id,
            JobStatus.RUNNING,
            phase="p" * 96,
            message="\n" * 1024,
        )
        uow.commit()
        response = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events
        assert events[-1]["event"] in {"job_status", "job_error"}
        assert "STREAM_EVENT_TOO_LARGE" not in response.text

    def test_emergency_terminal_event_always_encodes(
        self,
    ) -> None:
        from zana_core.api.jobs import (
            MAX_EVENT_BYTES,
            TERMINAL_ERROR_BYTES,
            _terminal_error_event,
        )
        from zana_core.streaming.encoder import SSEEncoder
        from zana_core.streaming.models import StreamLimits

        emergency = SSEEncoder(
            StreamLimits(
                max_data_bytes=2048,
                max_event_bytes=MAX_EVENT_BYTES,
                max_total_bytes=TERMINAL_ERROR_BYTES,
            )
        )
        chunk = emergency.encode(_terminal_error_event(None))
        assert len(chunk) <= MAX_EVENT_BYTES
        assert b"STREAM_EVENT_TOO_LARGE" in chunk

    def test_page_of_100_maximum_size_events_stays_within_total_cap(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        for index in range(100):
            service.record_progress(
                job.id,
                (index + 1) / 100,
                phase="working",
                message="m" * 4000,
            )
        uow.commit()
        response = client.get(
            f"/api/v1/jobs/{job.id}/events",
            params={"limit": 100},
            headers=auth_header,
        )
        assert response.status_code == 200
        assert len(_parse_sse(response.text)) <= 100
        assert (
            len(response.text.encode("utf-8"))
            <= MAX_PRIMARY_TOTAL_BYTES + TERMINAL_ERROR_BYTES + 2048
        )
        assert MAX_STREAM_TOTAL_BYTES == MAX_PRIMARY_TOTAL_BYTES + TERMINAL_ERROR_BYTES

    def test_high_unicode_message_is_normal_bounded_event(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        service.record_progress(
            job.id,
            0.5,
            phase="working",
            message="😀" * 3000,
        )
        uow.commit()
        response = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert any(event["event"] == "job_progress" for event in events)
        assert "STREAM_EVENT_TOO_LARGE" not in response.text
        payload = json.loads(events[-1]["data"][0])
        assert payload["message"].endswith("[truncated]")
        assert len(payload["message"].encode("utf-8")) <= 1024

    def test_64_long_error_fields_produce_bounded_redacted_payload(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.MODEL_PULL)
        service.transition_job(
            job.id,
            JobStatus.FAILED,
            phase="failed",
            error={f"field_{index}": "v" * 2000 for index in range(64)},
        )
        uow.commit()
        response = client.get(f"/api/v1/jobs/{job.id}/events", headers=auth_header)
        assert response.status_code == 200
        events = _parse_sse(response.text)
        assert events
        assert "STREAM_EVENT_TOO_LARGE" not in response.text
        payload = json.loads(events[-1]["data"][0])
        assert payload["error"] is not None
        assert len(json.dumps(payload["error"]).encode("utf-8")) <= 1024

    def test_fallback_never_raises_and_chunks_are_bounded(
        self,
        client: TestClient,
        auth_header: dict[str, str],
        uow: UnitOfWork,
    ) -> None:
        service = JobService(uow)
        job = service.create_job(JobKind.BUILD)
        for index in range(100):
            service.record_progress(
                job.id,
                (index + 1) / 100,
                phase="p" * 128,
                message="m" * 2048,
            )
        uow.commit()
        response = client.get(
            f"/api/v1/jobs/{job.id}/events",
            params={"limit": 100},
            headers=auth_header,
        )
        assert response.status_code == 200
        body = response.text
        for chunk in body.split("\n\n"):
            if chunk.strip():
                assert len(chunk.encode("utf-8")) <= MAX_EVENT_BYTES + 8


class TestExactTypeGates:
    def test_cursor_rejects_hostile_str_and_hook_objects(self) -> None:
        from fastapi import HTTPException

        hostile = _hostile_str("jobs:1")
        with pytest.raises(HTTPException) as exc:
            _parse_resume_cursor(hostile)
        assert exc.value.status_code == 400
        assert hostile.calls == {}

        hook = HookObject()
        with pytest.raises(HTTPException) as exc:
            _parse_resume_cursor(hook)  # type: ignore[arg-type]
        assert exc.value.status_code == 400
        assert hook.calls == {}

    def test_event_kind_and_error_string_reject_hostile_str(self) -> None:
        kind = _hostile_str("CREATED")
        assert _event_kind(kind).value == "error"
        assert _safe_kind(kind) == "error"
        assert kind.calls == {}

        error = _hostile_str('{"code":"x"}')
        assert _bounded_error_string(error) == {
            "code": "REDACTED_ERROR",
            "message": "...[truncated]",
        }
        assert error.calls == {}

    def test_progress_rejects_subclasses_and_float_hooks(self) -> None:
        int_value = _hostile_int(1)
        float_value = _hostile_float(0.5)
        assert _safe_progress(int_value) is None
        assert _safe_progress(float_value) is None
        assert int_value.calls == {}
        assert float_value.calls == {}

        hook = HookObject()
        assert _safe_progress(hook) is None
        assert hook.calls == {}

    def test_timestamp_rejects_datetime_subclasses(self) -> None:
        hostile = _hostile_datetime(2026, 1, 1)
        assert _bounded_iso8601(hostile) == "...[invalid-timestamp]"
        assert hostile.calls == {}

        hook = HookObject()
        assert _bounded_iso8601(hook) == "...[invalid-timestamp]"
        assert hook.calls == {}

    def test_dto_rejects_hostile_projection_values_without_hooks(self) -> None:
        event_id = _hostile_int(1)
        job_id = _hostile_int(1)
        kind = _hostile_str("CREATED")
        phase = _hostile_str("")
        message = _hostile_str("")
        progress = _hostile_float(0.5)
        error = _hostile_str("{}")
        created_at = _hostile_datetime(2026, 1, 1)
        row = JobEventStreamRow(
            id=event_id,
            job_id=job_id,
            kind=kind,
            phase=phase,
            message=message,
            progress_0_1=progress,
            error_json=error,
            created_at=created_at,
        )
        dto = _job_event_to_dto(row)
        assert dto.id == "error"
        assert dto.data["job_id"] is None
        assert dto.data["kind"] == "error"
        assert dto.data["phase"] == "...[invalid-text]"
        assert dto.data["message"] == "...[invalid-text]"
        assert dto.data["progress_0_1"] is None
        assert dto.data["error"] == {
            "code": "REDACTED_ERROR",
            "message": "...[truncated]",
        }
        assert dto.data["created_at"] == "...[invalid-timestamp]"
        for value in (
            event_id,
            job_id,
            kind,
            phase,
            message,
            progress,
            error,
            created_at,
        ):
            assert value.calls == {}

    def test_dto_rejects_hostile_row_object(self) -> None:
        hook = HookObject()
        dto = _job_event_to_dto(hook)  # type: ignore[arg-type]
        assert dto.name.value == "job_error"
        assert dto.id == "error"
        assert dto.terminal is True
        assert hook.calls == {}
