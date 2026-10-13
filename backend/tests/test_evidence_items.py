from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.exc import IntegrityError

from app.controls.models import Control
from app.environment.models import (
    TechAsset,
    TechAssetCategory,
    TechAssetEnvironment,
    TechAssetStatus,
)
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _fixture(db_session):
    user = User(
        email="owner@example.com",
        name="Owner",
        role=Role.CONTRIBUTOR,
        password_hash=hash_password("pw123456"),
    )
    control = Control(code="C-EVIDENCE", title="Access review", statement="Review access.")
    evidence_type = EvidenceType(
        name_zh="季度报表",
        name_en="Quarterly report",
        format="pdf",
        cadence=EvidenceCadence.QUARTERLY,
        typical_source="PAM",
    )
    asset = TechAsset(
        name="PAM",
        category=TechAssetCategory.PAM,
        vendor="Vendor",
        environment=TechAssetEnvironment.PROD,
        status=TechAssetStatus.ACTIVE,
    )
    db_session.add_all([user, control, evidence_type, asset])
    await db_session.flush()
    return user, control, evidence_type, asset


@pytest.mark.asyncio
async def test_collected_without_timestamp_is_rejected_by_database(db_session):
    _, control, evidence_type, _ = await _fixture(db_session)
    db_session.add(
        EvidenceItem(
            evidence_type_id=evidence_type.id,
            control_id=control.id,
            title="Missing timestamp",
            status=EvidenceStatus.COLLECTED,
        )
    )
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_evidence_list_returns_live_display_status_and_combined_filters(client, db_session):
    user, control, evidence_type, asset = await _fixture(db_session)
    db_session.add_all(
        [
            EvidenceItem(
                evidence_type_id=evidence_type.id,
                control_id=control.id,
                tech_asset_id=asset.id,
                owner_user_id=user.id,
                title="Expired",
                status=EvidenceStatus.COLLECTED,
                last_collected_at=datetime.now(UTC) - timedelta(days=90),
                valid_until=datetime.now(UTC) - timedelta(days=1),
            ),
            EvidenceItem(
                evidence_type_id=evidence_type.id,
                control_id=control.id,
                owner_user_id=user.id,
                title="Missing",
                status=EvidenceStatus.MISSING,
                valid_until=datetime.now(UTC) - timedelta(days=10),
            ),
        ]
    )
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.get(
        "/api/evidence?status=expired&owner_user_id=" + str(user.id), headers=headers
    )
    assert response.status_code == 200
    body = response.json()
    assert [row["title"] for row in body] == ["Expired"]
    assert body[0]["status"] == "expired"
    assert body[0]["intent_status"] == "collected"
