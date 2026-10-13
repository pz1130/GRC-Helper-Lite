import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.environment.models import (
    TechAsset,
    TechAssetCategory,
    TechAssetEnvironment,
    TechAssetStatus,
)
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


async def _seed_user(db_session, role: Role, email: str) -> User:
    user = User(email=email, name=email, role=role, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


def _asset(name: str = "VaultKeeper", **changes) -> TechAsset:
    values = {
        "name": name,
        "category": TechAssetCategory.PAM,
        "vendor": "VaultKeeper",
        "environment": TechAssetEnvironment.PROD,
        "status": TechAssetStatus.ACTIVE,
    }
    values.update(changes)
    return TechAsset(**values)


@pytest.mark.asyncio
async def test_tech_asset_enum_values_are_stored_as_values_and_name_is_unique(db_session):
    db_session.add(_asset())
    await db_session.flush()
    raw = await db_session.scalar(text("SELECT category FROM tech_assets WHERE name = 'VaultKeeper'"))
    assert raw == "pam"

    db_session.add(_asset("VaultKeeper"))
    with pytest.raises(IntegrityError):
        await db_session.flush()
    await db_session.rollback()


@pytest.mark.asyncio
async def test_owner_delete_sets_tech_asset_owner_to_null(db_session):
    owner = await _seed_user(db_session, Role.CONTRIBUTOR, "owner@example.com")
    asset = _asset(owner_user_id=owner.id)
    db_session.add(asset)
    await db_session.flush()
    await db_session.delete(owner)
    await db_session.flush()
    assert (
        await db_session.scalar(select(TechAsset.owner_user_id).where(TechAsset.id == asset.id))
        is None
    )


@pytest.mark.asyncio
async def test_invalid_category_is_rejected_by_database(db_session):
    db_session.add(_asset(category="not-a-category"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_viewer_reads_but_contributor_writes_and_viewer_cannot_write(client, db_session):
    viewer = await _seed_user(db_session, Role.VIEWER, "viewer@example.com")
    contributor = await _seed_user(db_session, Role.CONTRIBUTOR, "contributor@example.com")

    async def auth(email: str) -> dict[str, str]:
        response = await client.post(
            "/api/auth/login", json={"email": email, "password": "pw123456"}
        )
        return {"Authorization": f"Bearer {response.json()['access_token']}"}

    payload = {
        "name": "Azure",
        "category": "cloud",
        "vendor": "Microsoft",
        "environment": "all",
        "status": "active",
    }
    assert (
        await client.get("/api/tech-assets", headers=await auth(viewer.email))
    ).status_code == 200
    assert (
        await client.post("/api/tech-assets", json=payload, headers=await auth(viewer.email))
    ).status_code == 403
    response = await client.post(
        "/api/tech-assets", json=payload, headers=await auth(contributor.email)
    )
    assert response.status_code == 201
