import pytest

from app.evidence.models import EvidenceCadence


async def _admin(client, db_session) -> dict[str, str]:
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    user = User(
        email="admin-types@example.com",
        name="Admin",
        role=Role.ADMIN,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    return {"Authorization": f"Bearer {login.json()['access_token']}"}


async def _a_control(db_session) -> int:
    from app.controls.models import Control

    control = Control(
        code="CTRL-0001",
        title="Dual approval",
        statement="Two approvers are required.",
        category="Access Control",
        status="active",
    )
    db_session.add(control)
    await db_session.flush()
    return control.id


@pytest.mark.asyncio
async def test_evidence_type_crud_is_available_to_contributors(client, db_session):
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    user = User(
        email="contributor@example.com",
        name="Contributor",
        role=Role.CONTRIBUTOR,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
    response = await client.post(
        "/api/evidence-types",
        headers=headers,
        json={
            "name_zh": "PAM 季度抽样",
            "name_en": "PAM quarterly sample",
            "format": "mp4",
            "cadence": EvidenceCadence.QUARTERLY.value,
            "typical_source": "VaultKeeper",
            "description": "Session recordings.",
        },
    )
    assert response.status_code == 201
    assert response.json()["cadence"] == "quarterly"
    assert (await client.get("/api/evidence-types", headers=headers)).json()[0][
        "name_en"
    ] == "PAM quarterly sample"


# ── 删除证据类型不能把登记的证据一起带走 ──────────────────────────
#
# 2026-09-19 在生产栈上清理数据时实测：`DELETE /api/evidence-types/2` 返回 204，
# 而 `evidence_items` 从 1 行变成 0 行——外键是 CASCADE，那条已登记的审计证据
# 被静默删掉了，审计日志里只有 evidence_type.delete，**没有任何一条记录那份证据
# 也没了**。这违反铁律 2（不删行、每次改动都要留审计）。
#
# 对照组就在同一个代码库里：provider 删除的外键是 RESTRICT，被任务路由指着时
# 后端返 409 并说明还有谁在用它。证据类型应当照着这个来。


@pytest.mark.asyncio
async def test_deleting_a_type_in_use_is_refused_and_keeps_the_evidence(client, db_session):
    from sqlalchemy import func, select

    from app.evidence.models import EvidenceItem

    headers = await _admin(client, db_session)
    type_id = (await client.post(
        "/api/evidence-types",
        json={"name_zh": "审计日志", "name_en": "Audit log", "cadence": "quarterly"},
        headers=headers,
    )).json()["id"]
    control_id = await _a_control(db_session)

    created = await client.post(
        "/api/evidence",
        json={
            "evidence_type_id": type_id,
            "control_id": control_id,
            "title": "2026-Q3 审计证据",
            "status": "collected",
            "last_collected_at": "2026-09-19T00:00:00Z",
        },
        headers=headers,
    )
    assert created.status_code == 201

    refused = await client.delete(f"/api/evidence-types/{type_id}", headers=headers)
    assert refused.status_code == 409, "被证据引用的类型不能删——删了证据就没了"
    assert "证据" in refused.json().get("message", "")

    # 最要紧的一条：证据还在
    assert await db_session.scalar(select(func.count()).select_from(EvidenceItem)) == 1


@pytest.mark.asyncio
async def test_an_unused_type_can_still_be_deleted(client, db_session):
    headers = await _admin(client, db_session)
    type_id = (await client.post(
        "/api/evidence-types",
        json={"name_zh": "临时类型", "name_en": "Scratch", "cadence": "ad_hoc"},
        headers=headers,
    )).json()["id"]

    assert (await client.delete(f"/api/evidence-types/{type_id}", headers=headers)).status_code == 204


async def test_an_evidence_type_needs_no_chinese_name(client, db_session):
    # 表单上已经没有中文名这一栏；建出来的类型
    # name_zh 为空，编辑时原样送回空串也必须能存。
    from app.evidence.models import EvidenceCadence
    from app.iam.models import User
    from app.iam.permissions import Role
    from app.iam.security import hash_password

    user = User(email="c2@example.com", name="C", role=Role.CONTRIBUTOR, password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": "pw123456"})
    headers = {"Authorization": f"Bearer {login.json()['access_token']}"}

    created = await client.post("/api/evidence-types", headers=headers, json={
        "name_en": "Access review export", "cadence": EvidenceCadence.QUARTERLY.value,
    })
    assert created.status_code == 201
    assert created.json()["name_zh"] == ""

    edited = await client.patch(f"/api/evidence-types/{created.json()['id']}", headers=headers, json={
        "name_zh": "", "name_en": "Access review export (Q)",
    })
    assert edited.status_code == 200
