"""用依赖覆盖测 HTTP 权限与入队；不用数据库 fixture。"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from app.db import get_session
from app.errors import install_error_handlers
from app.extraction import router as module
from app.iam.deps import current_user
from app.iam.permissions import Role
from app.ingest.models import DocStatus


@pytest.fixture
async def extraction_http(monkeypatch):
    app = FastAPI()
    install_error_handlers(app)
    app.include_router(module.router)
    actor = SimpleNamespace(id=1, role=Role.GRC_LEAD)
    session = SimpleNamespace(
        get=AsyncMock(return_value=SimpleNamespace(status=DocStatus.ACTIVE)),
        commit=AsyncMock(),
    )
    app.dependency_overrides[current_user] = lambda: actor
    app.dependency_overrides[get_session] = lambda: session
    enqueue = AsyncMock(return_value="job-1")
    record = AsyncMock()
    monkeypatch.setattr(module, "enqueue", enqueue)
    monkeypatch.setattr(module, "record", record)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield SimpleNamespace(client=client, session=session, actor=actor,
                              enqueue=enqueue, record=record)


async def test_lead_enqueues_and_audits(extraction_http):
    h = extraction_http
    response = await h.client.post("/api/extraction/documents/7")
    assert response.status_code == 200
    assert response.json() == {"job_id": "job-1"}
    h.enqueue.assert_awaited_once_with("extract_controls", 7)
    assert h.record.await_args.kwargs["user"] is h.actor
    assert h.record.await_args.kwargs["entity_id"] == 7
    h.session.commit.assert_awaited_once()


@pytest.mark.parametrize("role", [Role.VIEWER, Role.CONTRIBUTOR])
async def test_control_write_required(extraction_http, role):
    h = extraction_http
    h.actor.role = role
    response = await h.client.post("/api/extraction/documents/7")
    assert response.status_code == 403
    h.enqueue.assert_not_awaited()


async def test_missing_document(extraction_http):
    h = extraction_http
    h.session.get.return_value = None
    assert (await h.client.post("/api/extraction/documents/7")).status_code == 404
    h.enqueue.assert_not_awaited()


async def test_inactive_document(extraction_http):
    h = extraction_http
    h.session.get.return_value.status = DocStatus.UPLOADED
    assert (await h.client.post("/api/extraction/documents/7")).status_code == 409
    h.enqueue.assert_not_awaited()


@pytest.mark.parametrize("identifier", ["0", "-1", "invalid"])
async def test_invalid_id(extraction_http, identifier):
    h = extraction_http
    response = await h.client.post(f"/api/extraction/documents/{identifier}")
    assert response.status_code == 422
    h.enqueue.assert_not_awaited()


async def test_queue_failure_not_reported_as_success(extraction_http):
    h = extraction_http
    h.enqueue.return_value = ""
    assert (await h.client.post("/api/extraction/documents/7")).status_code == 409
    h.record.assert_not_awaited()


async def test_viewer_can_read_coverage_without_triggering_extraction(extraction_http, monkeypatch):
    from app.extraction import coverage

    h = extraction_http
    h.actor.role = Role.VIEWER
    report = {"document_id": 7, "counts": {"cited": 0, "uncited": 0, "not_completed": 1},
              "clauses": []}
    read = AsyncMock(return_value=report)
    monkeypatch.setattr(coverage, "extraction_coverage", read)
    response = await h.client.get("/api/extraction/documents/7/coverage")
    assert response.status_code == 200
    assert response.json() == report
    h.enqueue.assert_not_awaited()


async def test_coverage_missing_document(extraction_http):
    h = extraction_http
    h.session.get.return_value = None
    assert (await h.client.get("/api/extraction/documents/7/coverage")).status_code == 404
