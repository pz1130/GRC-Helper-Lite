"""补充测试：批量接受跳过已确认冲突的事务完整性、逐条跳过边界与路由端点。

本文件对应 bugfix spec `bulk-accept-skip-conflicts` 的任务 4。

任务 1（复现）与任务 2（保留）已覆盖「同批兄弟冲突 / 关系已确认 / 批外已确认」
与各类非缺陷输入的保留行为。本文件**不重复**那些场景，专注补充：

- 事务完整性属性（核心，需求 2.6）：后置条冲突时，靠前已接受的兄弟真正落库、
  状态为 ACCEPTED；冲突条仍 PENDING（未被接受也未被错误裁定），且不抛异常。
- 逐条跳过边界（需求 2.1 / 2.2 / 2.4）：`proposal.stale_conflict` 也按跳过处理
  并继续；允许清单常量 `_BULK_SKIP_CONFLICT_CODES` 恰好是预期的三个 code
  （防止将来误扩大跳过边界）。
- 路由端点集成（需求 2.3 / 2.4 / 2.5）：经 HTTP 端点 `POST /api/proposals/bulk-accept`，
  同批兄弟冲突返回 200 且 `{"accepted": 1, "skipped": 1}`（而非 409）。
- 路由端点内容非法整批不落（需求 3.1）：经端点提交含内容非法的合格提案时，
  端点返回错误状态（非 200），且什么都不落库。
"""

import pytest
from sqlalchemy import select

from app.controls.models import ControlRelation, RelationType
from app.errors import Conflict
from app.frameworks.models import Mapping
from app.review import service
from app.review.models import ProposalStatus
from app.review.service import bulk_accept

# 复用任务 1 复现测试里的 fixtures/helpers，保持造数风格一致。
from tests.test_bulk_accept_skip_conflicts import (
    _control,
    _mapping_proposal,
    _relation_proposal,
    _setup,
)

# ---------------------------------------------------------------------------
# 事务完整性属性（核心，需求 2.6）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_后置条冲突时靠前已接受的映射不被回滚(db_session):
    """2.6：构造 [A, B]，A 合格且目标未确认、B 合格但与 A 指向同一
    `(control, framework_item)`（A 确认后 B 冲突）。按 sorted(id) 保证 A 先处理。

    断言：`{"accepted": 1, "skipped": 1}`；提交/刷新后 A 对应的 mapping 确实落库、
    `A.status == ACCEPTED`；`B.status` 仍为 PENDING（未被接受也未被错误裁定）；
    `bulk_accept` 未抛异常。这证明兄弟 A 的落库不因后置 B 的冲突被回滚。
    """
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    a = await _mapping_proposal(db_session, item, control)
    b = await _mapping_proposal(db_session, item, control)
    a_id, b_id = a.id, b.id
    assert a_id < b_id  # sorted(id) 保证 A 先处理、先确认该映射

    # 不抛异常即为通过的一部分（没有 pytest.raises 包裹）。
    result = await bulk_accept(db_session, [a_id, b_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}

    # 提交到 savepoint 后再刷新，确认 A 的落库是真实持久的、不是未提交的幻象。
    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.status is ProposalStatus.ACCEPTED
    assert b.status is ProposalStatus.PENDING  # 既没接受，也没被错误裁定

    mappings = list(await db_session.scalars(select(Mapping)))
    assert len(mappings) == 1
    assert (mappings[0].control_id, mappings[0].framework_item_id) == (control.id, item.id)


@pytest.mark.asyncio
async def test_后置条冲突时靠前已接受的关系不被回滚(db_session):
    """2.6（关系路径）：构造 [A, B]，两条合格关系提案指向同一控制点对、同类型。
    A 先处理并确认关系；B 随后撞 `relation.already_confirmed`。

    断言与映射路径一致：A 落库且 ACCEPTED、B 仍 PENDING、`{"accepted": 1, "skipped": 1}`、
    不抛异常——关系路径下兄弟 A 的落库同样不被 B 的冲突回滚。
    """
    actor, _fw, _item = await _setup(db_session)
    from tests.test_bulk_accept_skip_conflicts import LEFT_TEXT, RIGHT_TEXT

    left = await _control(db_session, "C-0001", statement=LEFT_TEXT)
    right = await _control(db_session, "C-0002", statement=RIGHT_TEXT)
    a = await _relation_proposal(db_session, left, right)
    b = await _relation_proposal(db_session, left, right)
    a_id, b_id = a.id, b.id
    assert a_id < b_id

    result = await bulk_accept(db_session, [a_id, b_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}

    await db_session.commit()
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.status is ProposalStatus.ACCEPTED
    assert b.status is ProposalStatus.PENDING

    relations = list(await db_session.scalars(select(ControlRelation)))
    assert len(relations) == 1
    assert relations[0].relation_type is RelationType.DEPENDS_ON


# ---------------------------------------------------------------------------
# 逐条跳过边界：允许清单 + 各 code 跳过（需求 2.1 / 2.2 / 2.4）
# ---------------------------------------------------------------------------


def test_跳过允许清单恰好包含三个预期code():
    """需求 2.4（边界稳健性）：允许清单常量必须**恰好**是这三个「目标已确认 /
    过期冲突」类 code。多一个会把本应中止整批的错误误吞成跳过（违反 3.1），
    少一个会让真正的冲突条把整批打翻。本断言守住这条跳过边界不被将来改动误扩大。
    """
    assert service._BULK_SKIP_CONFLICT_CODES == {
        "mapping.already_confirmed",
        "relation.already_confirmed",
        "proposal.stale_conflict",
    }


@pytest.mark.asyncio
async def test_过期冲突stale_conflict被跳过且整批继续(db_session, monkeypatch):
    """需求 2.4：`proposal.stale_conflict`（decide 捕获底层 IntegrityError 转换而来）
    也在允许清单内，应按跳过处理并继续整批。

    自然触发 stale_conflict 需要判重查询与落库之间的数据库竞态，测试里不可靠；
    这里用确定性打桩：让第一条 decide 抛 `Conflict(code="proposal.stale_conflict")`，
    其余条走真实路径。断言冲突条被跳过、其余合格条照常接受、`bulk_accept` 不抛异常。

    同时覆盖 `mapping.already_confirmed` / `relation.already_confirmed` 已由任务 1
    的复现测试以真实路径钉住，这里对第三个 code 做互补的最小化覆盖。
    """
    actor, _fw, item = await _setup(db_session)
    c1 = await _control(db_session, "C-0001")
    c2 = await _control(db_session, "C-0002")
    stale = await _mapping_proposal(db_session, item, c1)
    clean = await _mapping_proposal(db_session, item, c2)
    stale_id, clean_id = stale.id, clean.id
    assert stale_id < clean_id  # 让会冲突的那条先处理

    real_decide = service.decide

    async def _decide_with_stale(session, proposal_id, **kwargs):
        if proposal_id == stale_id:
            raise Conflict(
                "确认内容与现有数据冲突，请刷新后重试",
                code="proposal.stale_conflict",
            )
        return await real_decide(session, proposal_id, **kwargs)

    monkeypatch.setattr(service, "decide", _decide_with_stale)

    result = await bulk_accept(db_session, [stale_id, clean_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}
    await db_session.refresh(stale)
    await db_session.refresh(clean)
    assert stale.status is ProposalStatus.PENDING  # 过期冲突 → 跳过，不裁定
    assert clean.status is ProposalStatus.ACCEPTED  # 其余合格条照常接受
    # 只有 clean 的映射落库，stale 这条没有留下任何映射。
    mappings = list(await db_session.scalars(select(Mapping)))
    assert len(mappings) == 1
    assert mappings[0].control_id == c2.id


# ---------------------------------------------------------------------------
# 路由端点集成（需求 2.3 / 2.4 / 2.5）
# ---------------------------------------------------------------------------


async def _login(client, email: str = "lead@example.com") -> dict[str, str]:
    """用任务 1 `_setup` 造出的 GRC_LEAD（口令 pw123456）换取 Bearer 头。"""
    resp = await client.post(
        "/api/auth/login", json={"email": email, "password": "pw123456"}
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_端点_同批兄弟冲突返回200且接受一条跳过一条(client, db_session):
    """2.3 / 2.5：经 HTTP 端点提交两条指向同一 `(control, framework_item)` 的合格
    映射提案，端点返回 **200** 且 `{"accepted": 1, "skipped": 1}`（而非 409）。
    """
    _actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    a = await _mapping_proposal(db_session, item, control)
    b = await _mapping_proposal(db_session, item, control)
    assert a.id < b.id
    await db_session.commit()

    headers = await _login(client)
    resp = await client.post(
        "/api/proposals/bulk-accept", json={"ids": [a.id, b.id]}, headers=headers
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"accepted": 1, "skipped": 1}

    # 端点 commit 后，A 的映射确实落库、唯有一条。
    await db_session.refresh(a)
    await db_session.refresh(b)
    assert a.status is ProposalStatus.ACCEPTED
    assert b.status is ProposalStatus.PENDING
    assert len(list(await db_session.scalars(select(Mapping)))) == 1


@pytest.mark.asyncio
async def test_端点_内容非法整批失败且不落库(client, db_session):
    """3.1：经 HTTP 端点提交一批含「内容非法」的合格映射提案（strength 非法 →
    `mapping.payload_invalid`），端点返回错误状态（非 200），且什么都不落库。

    内容非法不在跳过允许清单内，必须外抛让整批回滚——保留「内容非法就整批不落」
    的既有刻意行为。端点捕获异常后 rollback 并重抛，由全局处理器转成错误响应。
    """
    _actor, _fw, item = await _setup(db_session)
    good_control = await _control(db_session, "C-0001")
    bad_control = await _control(db_session, "C-0002")
    good = await _mapping_proposal(db_session, item, good_control)
    bad = await _mapping_proposal(db_session, item, bad_control)
    # bad 仍通过资格检查（高置信度、非 OCR、非 matrix、PENDING），但在 materialize
    # 因非法 strength 抛 payload_invalid。
    bad.payload = {**bad.payload, "strength": "definitely-not-a-valid-strength"}
    await db_session.flush()
    assert good.id < bad.id  # 靠前的 good 先处理
    await db_session.commit()

    headers = await _login(client)
    resp = await client.post(
        "/api/proposals/bulk-accept",
        json={"ids": [good.id, bad.id]},
        headers=headers,
    )

    assert resp.status_code != 200
    # 内容非法是 payload/校验类 AppError（status 400），其 code 不在跳过允许清单内。
    assert resp.status_code == 400
    assert resp.json()["code"] == "mapping.payload_invalid"

    # 整批回滚：靠前的 good 也没落库，一条映射都没有。
    await db_session.refresh(good)
    await db_session.refresh(bad)
    assert good.status is ProposalStatus.PENDING
    assert bad.status is ProposalStatus.PENDING
    assert list(await db_session.scalars(select(Mapping))) == []
