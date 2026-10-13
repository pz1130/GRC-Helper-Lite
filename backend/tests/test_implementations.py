from datetime import UTC, datetime

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.controls.models import Control
from app.environment.models import (
    HowEnforced,
    Implementation,
    ImplementationStatus,
    TechAsset,
    TechAssetCategory,
    TechAssetEnvironment,
    TechAssetStatus,
)
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed_user(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _control_and_asset(db_session, tag: str = "a") -> tuple[Control, TechAsset]:
    control = Control(code=f"C-{tag}", title="Access", statement="Access is reviewed.")
    asset = TechAsset(
        name=f"PAM-{tag}",
        category=TechAssetCategory.PAM,
        vendor="Vendor",
        environment=TechAssetEnvironment.PROD,
        status=TechAssetStatus.ACTIVE,
    )
    db_session.add_all([control, asset])
    await db_session.flush()
    return control, asset


def _implementation(control_id: int, tech_asset_id: int | None = None, **changes) -> Implementation:
    values = {
        "control_id": control_id,
        "tech_asset_id": tech_asset_id,
        "description": "Quarterly review",
        "how_enforced": HowEnforced.SEMI_AUTOMATED,
        "status": ImplementationStatus.IMPLEMENTED,
        "last_verified_at": datetime.now(UTC),
    }
    values.update(changes)
    return Implementation(**values)


@pytest.mark.asyncio
async def test_not_applicable_without_justification_is_rejected_by_database(db_session):
    control, _ = await _control_and_asset(db_session)
    db_session.add(_implementation(control.id, status=ImplementationStatus.NOT_APPLICABLE))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_only_one_process_implementation_is_allowed(db_session):
    control, _ = await _control_and_asset(db_session)
    db_session.add_all([_implementation(control.id), _implementation(control.id)])
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_api_validates_na_justification_and_audits_writes(client, db_session):
    actor = await _seed_user(db_session, Role.CONTRIBUTOR, "contributor@example.com")
    control, asset = await _control_and_asset(db_session, "api")
    login = await client.post(
        "/api/auth/login", json={"email": actor.email, "password": "pw123456"}
    )
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    payload = {
        "control_id": control.id,
        "tech_asset_id": asset.id,
        "description": "PAM enforces the review.",
        "how_enforced": "automated",
        "status": "not_applicable",
    }
    assert (
        await client.post("/api/implementations", json=payload, headers=headers)
    ).status_code == 422
    payload["status"] = "implemented"
    response = await client.post("/api/implementations", json=payload, headers=headers)
    assert response.status_code == 201
    audit = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "implementation.create")
    )
    assert audit is not None
