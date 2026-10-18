"""被 RCSA 引用的现有实体：删除返回明确的 409，而不是数据库异常。

外键是 RESTRICT，数据库本身会拦；这里测的是用户拿到的是什么——一条带 code 的
冲突，而不是 500。也确认证据行还在，评估响应的证据快照没有被带走。
"""

from datetime import UTC, date, datetime

from sqlalchemy import func, select

from app.controls.models import Control
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.frameworks.models import Framework
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.rcsa.models import (
    RcsaCampaign,
    RcsaItem,
    RcsaResponse,
    RcsaResponseEvidence,
    RcsaScoringProfile,
)


async def test_deleting_evidence_cited_by_an_rcsa_response_is_refused(client, db_session):
    admin = User(
        email="admin-rcsa@example.com", name="Admin", role=Role.ADMIN,
        password_hash=hash_password("pw123456"),
    )
    framework = Framework(key="acme-it", name_zh="Acme", name_en="Acme", version="1")
    control = Control(code="C-9101", title="Access review", statement="Access is reviewed.")
    evidence_type = EvidenceType(name_zh="Log", name_en="Log", cadence=EvidenceCadence.QUARTERLY)
    db_session.add_all([admin, framework, control, evidence_type])
    await db_session.flush()
    evidence = EvidenceItem(
        evidence_type_id=evidence_type.id, control_id=control.id, title="Q1 export",
        status=EvidenceStatus.PLANNED,
    )
    profile = RcsaScoringProfile(
        key="acme-5x5", revision=1, algorithm="product", definitions={}, bands=[], horizon="",
        status="draft", created_by=admin.id, updated_by=admin.id,
    )
    db_session.add_all([evidence, profile])
    await db_session.flush()
    campaign = RcsaCampaign(
        name="Acme Q1", framework_id=framework.id, profile_id=profile.id,
        period_start=date(2026, 1, 1), period_end=date(2026, 3, 31), due_at=date(2026, 4, 15),
        timezone="UTC", owner_id=admin.id, status="draft", created_by=admin.id, updated_by=admin.id,
    )
    db_session.add(campaign)
    await db_session.flush()
    item = RcsaItem(
        campaign_id=campaign.id, control_id=control.id, status="draft",
        created_by=admin.id, updated_by=admin.id,
    )
    db_session.add(item)
    await db_session.flush()
    response = RcsaResponse(
        item_id=item.id, revision=1, status="draft", answer={}, created_by=admin.id,
        updated_by=admin.id,
    )
    db_session.add(response)
    await db_session.flush()
    db_session.add(
        RcsaResponseEvidence(
            response_id=response.id, evidence_item_id=evidence.id, purpose="self_assessment",
            snapshot={"title": "Q1 export", "captured_at": datetime(2026, 4, 1, tzinfo=UTC).isoformat()},
            created_by=admin.id,
        )
    )
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": admin.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    refused = await client.delete(f"/api/evidence/{evidence.id}", headers=headers)

    assert refused.status_code == 409
    assert refused.json()["code"] == "evidence.in_use_by_rcsa"
    assert refused.json()["params"] == {"count": 1}
    assert await db_session.scalar(
        select(func.count()).select_from(EvidenceItem).where(EvidenceItem.id == evidence.id)
    ) == 1
