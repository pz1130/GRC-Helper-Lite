from datetime import date

import pytest
from sqlalchemy import select

from app.frameworks.models import Framework, FrameworkItem
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import create_access_token, hash_password
from app.maturity.models import (
    AssessmentStatus,
    MaturityAssessment,
    MaturityScore,
    ScoreSource,
)


async def _user(session, role: Role, suffix: str = "") -> User:
    row = User(
        email=f"{role.value}{suffix}@risk.test",
        name=f"{role.value}{suffix}",
        role=role,
        password_hash=hash_password("pw"),
    )
    session.add(row)
    await session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_manual_risk_scores_are_computed_and_updates_are_audited(client, db_session):
    lead = await _user(db_session, Role.GRC_LEAD)
    created = await client.post(
        "/api/risks",
        headers=_headers(lead),
        json={
            "title": "Access review coverage",
            "description": "Privileged accounts are not fully covered.",
            "likelihood": 4,
            "impact": 5,
            "owner_user_id": lead.id,
            "due_date": "2026-12-10",
        },
    )
    risk_id = created.json()["id"]
    updated = await client.patch(
        f"/api/risks/{risk_id}",
        headers=_headers(lead),
        json={
            "status": "mitigating",
            "mitigation": "Expand the quarterly review.",
            "residual_likelihood": 2,
            "residual_impact": 3,
        },
    )

    assert created.status_code == 201
    assert created.json()["inherent_score"] == 20
    assert created.json()["source"] == "manual"
    assert updated.status_code == 200
    assert updated.json()["residual_score"] == 6
    assert updated.json()["status"] == "mitigating"
    actions = set(await db_session.scalars(select(AuditLog.action)))
    assert {"risk.create", "risk.update"} <= actions


@pytest.mark.asyncio
async def test_maturity_gap_converts_once_with_traceable_defaults(client, db_session):
    lead = await _user(db_session, Role.GRC_LEAD, "gap")
    framework = Framework(
        key="risk-gap",
        name_zh="差距风险",
        name_en="Gap risk",
        version="1",
        source="test",
        item_count=1,
        imported_by=lead.id,
    )
    db_session.add(framework)
    await db_session.flush()
    item = FrameworkItem(
        framework_id=framework.id,
        code="PR.AA-01",
        title="Access is managed",
        description="",
        level=1,
        order_index=1,
    )
    db_session.add(item)
    await db_session.flush()
    assessment = MaturityAssessment(
        framework_id=framework.id,
        name="Baseline",
        as_of_date=date(2026, 9, 11),
        status=AssessmentStatus.FINAL,
        created_by=lead.id,
    )
    db_session.add(assessment)
    await db_session.flush()
    db_session.add(
        MaturityScore(
            assessment_id=assessment.id,
            framework_item_id=item.id,
            doc_score=3,
            impl_score=1,
            doc_rationale="Defined",
            impl_rationale="Only a pilot exists.",
            doc_score_source=ScoreSource.HUMAN,
            impl_score_source=ScoreSource.HUMAN,
            scored_by=lead.id,
        )
    )
    await db_session.flush()
    payload = {"assessment_id": assessment.id, "framework_item_id": item.id}

    created = await client.post(
        "/api/risks/from-maturity-gap", headers=_headers(lead), json=payload
    )
    duplicate = await client.post(
        "/api/risks/from-maturity-gap", headers=_headers(lead), json=payload
    )

    assert created.status_code == 201
    assert created.json()["source_ref"] == payload
    assert created.json()["description"] == "Only a pilot exists."
    assert created.json()["owner_user_id"] == lead.id
    assert created.json()["due_date"] == "2026-12-10"
    assert created.json()["inherent_score"] == 12
    assert duplicate.status_code == 409


@pytest.mark.asyncio
async def test_contributor_can_read_but_cannot_write_risks(client, db_session):
    contributor = await _user(db_session, Role.CONTRIBUTOR)
    listed = await client.get("/api/risks", headers=_headers(contributor))
    created = await client.post(
        "/api/risks",
        headers=_headers(contributor),
        json={"title": "Blocked", "likelihood": 3, "impact": 3},
    )
    assert listed.status_code == 200
    assert created.status_code == 403


@pytest.mark.asyncio
async def test_a_request_that_fails_validation_says_which_field_and_why(client, db_session):
    # 默认的 422 只有 detail 列表、没有 code/message，前端只能显示「请求失败」——
    # 比如风险登记只填了剩余可能性，用户不知道错在哪。现在带 code 与一行可读说明。
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import create_access_token, hash_password

    user = User(email="lead@example.com", name="Lead", role=Role.GRC_LEAD, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    response = await client.post(
        "/api/risks",
        headers={"Authorization": f"Bearer {create_access_token(user.id, user.role)}"},
        json={"title": "R", "likelihood": 3, "impact": 3, "residual_likelihood": 2},
    )

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "request.invalid"
    assert body["params"]["detail"] == "(root): residual likelihood and residual impact must be given together"
    assert "detail" in body  # 原来的结构化 detail 仍在

