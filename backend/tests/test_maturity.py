import pytest
from sqlalchemy import select

from app.frameworks.models import Framework, FrameworkItem
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import create_access_token, hash_password


async def _user(session, role: Role) -> User:
    row = User(
        email=f"{role.value}@maturity.test",
        name=role.value,
        role=role,
        password_hash=hash_password("pw"),
    )
    session.add(row)
    await session.flush()
    return row


def _headers(user: User) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.mark.asyncio
async def test_human_score_can_be_saved_and_finalized(client, db_session):
    lead = await _user(db_session, Role.GRC_LEAD)
    framework = Framework(
        key="maturity-test",
        name_zh="成熟度",
        name_en="Maturity",
        version="1",
        source="test",
        item_count=1,
        imported_by=lead.id,
    )
    db_session.add(framework)
    await db_session.flush()
    item = FrameworkItem(
        framework_id=framework.id,
        code="GV.OC-01",
        title="Context",
        description="",
        level=1,
        order_index=1,
    )
    db_session.add(item)
    await db_session.flush()

    created = await client.post(
        "/api/maturity/assessments",
        headers=_headers(lead),
        json={
            "framework_id": framework.id,
            "name": "2026 baseline",
            "as_of_date": "2026-09-11",
        },
    )
    assessment_id = created.json()["id"]
    scored = await client.put(
        f"/api/maturity/assessments/{assessment_id}/scores",
        headers=_headers(lead),
        json={
            "framework_item_id": item.id,
            "doc_score": 3,
            "impl_score": 2,
            "doc_rationale": "Policy is explicit.",
            "impl_rationale": "Partially evidenced.",
        },
    )
    finalized = await client.post(
        f"/api/maturity/assessments/{assessment_id}/finalize", headers=_headers(lead)
    )
    locked = await client.put(
        f"/api/maturity/assessments/{assessment_id}/scores",
        headers=_headers(lead),
        json={
            "framework_item_id": item.id,
            "doc_score": 4,
            "impl_score": 4,
        },
    )

    assert created.status_code == 201
    assert scored.json()["doc_score_source"] == "human"
    assert finalized.json()["status"] == "final"
    assert locked.status_code == 409
    actions = set(await db_session.scalars(select(AuditLog.action)))
    assert {
        "maturity_assessment.create",
        "maturity_score.upsert",
        "maturity_assessment.finalize",
    } <= actions


@pytest.mark.asyncio
async def test_contributor_cannot_score_maturity(client, db_session):
    contributor = await _user(db_session, Role.CONTRIBUTOR)
    response = await client.post(
        "/api/maturity/assessments",
        headers=_headers(contributor),
        json={
            "framework_id": 1,
            "name": "Blocked",
            "as_of_date": "2026-09-11",
        },
    )
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_summary_rolls_up_scored_leaf_items_without_treating_missing_as_zero(
    client, db_session
):
    lead = await _user(db_session, Role.GRC_LEAD)
    framework = Framework(
        key="maturity-summary",
        name_zh="汇总测试",
        name_en="Summary test",
        version="1",
        source="test",
        item_count=5,
        imported_by=lead.id,
    )
    db_session.add(framework)
    await db_session.flush()
    governance = FrameworkItem(
        framework_id=framework.id,
        code="GV",
        title="Governance",
        level=1,
        order_index=1,
    )
    protect = FrameworkItem(
        framework_id=framework.id,
        code="PR",
        title="Protect",
        level=1,
        order_index=4,
    )
    db_session.add_all([governance, protect])
    await db_session.flush()
    policy = FrameworkItem(
        framework_id=framework.id,
        parent_id=governance.id,
        code="GV.PO-01",
        title="Policy",
        level=2,
        order_index=2,
    )
    roles = FrameworkItem(
        framework_id=framework.id,
        parent_id=governance.id,
        code="GV.RR-01",
        title="Roles",
        level=2,
        order_index=3,
    )
    access = FrameworkItem(
        framework_id=framework.id,
        parent_id=protect.id,
        code="PR.AA-01",
        title="Access",
        level=2,
        order_index=5,
    )
    db_session.add_all([policy, roles, access])
    await db_session.flush()

    created = await client.post(
        "/api/maturity/assessments",
        headers=_headers(lead),
        json={
            "framework_id": framework.id,
            "name": "Summary",
            "as_of_date": "2026-09-11",
        },
    )
    assessment_id = created.json()["id"]
    for item, doc_score, impl_score in [(policy, 4, 2), (access, 2, 1)]:
        response = await client.put(
            f"/api/maturity/assessments/{assessment_id}/scores",
            headers=_headers(lead),
            json={
                "framework_item_id": item.id,
                "doc_score": doc_score,
                "impl_score": impl_score,
            },
        )
        assert response.status_code == 200

    summary = await client.get(
        f"/api/maturity/assessments/{assessment_id}/summary", headers=_headers(lead)
    )
    body = summary.json()

    assert summary.status_code == 200
    assert body["overall"] == {
        "total_items": 3,
        "scored_items": 2,
        "doc_average": 3.0,
        "impl_average": 1.5,
    }
    assert body["groups"][0]["total_items"] == 2
    assert body["groups"][0]["scored_items"] == 1
    assert body["groups"][0]["doc_average"] == 4.0
    assert next(row for row in body["items"] if row["framework_item_id"] == roles.id)[
        "doc_score"
    ] is None

    parent_score = await client.put(
        f"/api/maturity/assessments/{assessment_id}/scores",
        headers=_headers(lead),
        json={"framework_item_id": governance.id, "doc_score": 4, "impl_score": 4},
    )
    assert parent_score.status_code == 409
