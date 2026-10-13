import pytest
from sqlalchemy import event
from sqlalchemy.engine import Engine

from app.controls.models import Control
from app.environment.models import HowEnforced, Implementation, ImplementationStatus
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


class _QueryCounter:
    def __init__(self) -> None:
        self.count = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.count += 1

    def __enter__(self):
        event.listen(Engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(Engine, "before_cursor_execute", self)


@pytest.mark.asyncio
async def test_control_detail_includes_implementations_and_evidence(client, db_session):
    user = User(
        email="viewer@example.com",
        name="Viewer",
        role=Role.VIEWER,
        password_hash=hash_password("pw123456"),
    )
    control = Control(code="C-DETAIL", title="Access", statement="Review access.")
    evidence_type = EvidenceType(
        name_zh="报表",
        name_en="Report",
        format="pdf",
        cadence=EvidenceCadence.MONTHLY,
        typical_source="SIEM",
    )
    db_session.add_all([user, control, evidence_type])
    await db_session.flush()
    db_session.add(
        Implementation(
            control_id=control.id,
            how_enforced=HowEnforced.MANUAL,
            status=ImplementationStatus.PLANNED,
            description="Process.",
        )
    )
    db_session.add(
        EvidenceItem(
            evidence_type_id=evidence_type.id,
            control_id=control.id,
            title="Monthly report",
            status=EvidenceStatus.PLANNED,
        )
    )
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    body = (await client.get(f"/api/controls/{control.id}", headers=headers)).json()
    assert len(body["implementations"]) == 1
    assert len(body["evidence"]) == 1
    assert body["evidence"][0]["status"] == "planned"


@pytest.mark.asyncio
async def test_environment_context_uses_fixed_batch_queries(db_session):
    from app.environment import service as environment_service
    from app.evidence import service as evidence_service

    controls = [
        Control(code=f"C-BATCH-{index:02d}", title="Batch", statement="Batch.")
        for index in range(23)
    ]
    db_session.add_all(controls)
    await db_session.flush()
    with _QueryCounter() as few:
        await environment_service.implementations_for_controls(
            db_session, [control.id for control in controls[:3]]
        )
        await evidence_service.items_for_controls(
            db_session, [control.id for control in controls[:3]]
        )
    with _QueryCounter() as many:
        await environment_service.implementations_for_controls(
            db_session, [control.id for control in controls]
        )
        await evidence_service.items_for_controls(db_session, [control.id for control in controls])
    assert many.count == few.count == 2
