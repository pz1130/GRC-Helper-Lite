import pytest

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
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password


@pytest.mark.asyncio
async def test_asset_controls_reverse_lookup_excludes_not_applicable(client, db_session):
    user = User(
        email="viewer@example.com",
        name="Viewer",
        role=Role.VIEWER,
        password_hash=hash_password("pw123456"),
    )
    asset = TechAsset(
        name="VaultKeeper",
        category=TechAssetCategory.PAM,
        vendor="VaultKeeper",
        environment=TechAssetEnvironment.PROD,
        status=TechAssetStatus.ACTIVE,
    )
    first = Control(code="C-0001", title="Review access", statement="Review access.")
    second = Control(code="C-0002", title="Rotate secrets", statement="Rotate secrets.")
    db_session.add_all([user, asset, first, second])
    await db_session.flush()
    db_session.add_all(
        [
            Implementation(
                control_id=first.id,
                tech_asset_id=asset.id,
                how_enforced=HowEnforced.AUTOMATED,
                status=ImplementationStatus.IMPLEMENTED,
                description="Reviews.",
            ),
            Implementation(
                control_id=second.id,
                tech_asset_id=asset.id,
                how_enforced=HowEnforced.MANUAL,
                status=ImplementationStatus.NOT_APPLICABLE,
                na_justification="Out of scope.",
                description="",
            ),
        ]
    )
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.get(f"/api/tech-assets/{asset.id}/controls", headers=headers)
    assert response.status_code == 200
    assert [row["control_code"] for row in response.json()] == ["C-0001"]


@pytest.mark.asyncio
async def test_an_asset_in_use_cannot_be_deleted_out_from_under_its_implementations(client, db_session):
    # 外键原来是 ON DELETE SET NULL：删掉一个技术资产返回 204，而引用它的控制点实现
    # 与证据被数据库悄悄清掉了资产——「这个控制点靠 VaultKeeper 落地」就这么没了，
    # 审计日志里只有 tech_asset.delete。与 0025 修证据类型是同一个病。
    from sqlalchemy import func, select

    user = User(email="c@example.com", name="C", role=Role.CONTRIBUTOR, password_hash=hash_password("pw123456"))
    asset = TechAsset(name="VaultKeeper", category=TechAssetCategory.PAM, vendor="VaultKeeper",
                      environment=TechAssetEnvironment.PROD, status=TechAssetStatus.ACTIVE)
    spare = TechAsset(name="Unused", category=TechAssetCategory.OTHER, vendor="",
                      environment=TechAssetEnvironment.PROD, status=TechAssetStatus.ACTIVE)
    control = Control(code="C-0001", title="Review access", statement="Review access.")
    db_session.add_all([user, asset, spare, control])
    await db_session.flush()
    implementation = Implementation(control_id=control.id, tech_asset_id=asset.id,
                                    how_enforced=HowEnforced.AUTOMATED,
                                    status=ImplementationStatus.IMPLEMENTED, description="Reviews.")
    db_session.add(implementation)
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    refused = await client.delete(f"/api/tech-assets/{asset.id}", headers=headers)
    assert refused.status_code == 409
    assert refused.json()["code"] == "tech_asset.in_use"
    assert refused.json()["params"] == {"implementations": 1, "evidence": 0}
    await db_session.refresh(implementation)
    assert implementation.tech_asset_id == asset.id

    unused = await client.delete(f"/api/tech-assets/{spare.id}", headers=headers)
    assert unused.status_code == 204
    assert await db_session.scalar(select(func.count()).select_from(TechAsset)) == 1
