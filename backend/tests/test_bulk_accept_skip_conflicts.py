"""Bug Condition 复现测试：批量接受应跳过「目标已确认」的冲突条，而非整批回滚。

本文件对应 bugfix spec `bulk-accept-skip-conflicts` 的任务 1。

缺陷：`bulk_accept` 在单个 `begin_nested()` 事务里逐条 `decide(...)`，且没有逐条
异常处理。当批内某条合格提案的目标已被确认，`decide -> materialize` 抛出 `Conflict`
（`mapping.already_confirmed` / `relation.already_confirmed`）。`Conflict` 是 `AppError`
而非 `IntegrityError`，`decide` 只捕获 `IntegrityError`，于是该 `Conflict` 穿透整个
批次事务，路由层回滚整批、返回 409，`accepted` 为 0——即使其余提案本可接受。

以下三个用例断言**期望（修复后）行为**，因此在**未修复**代码上必然失败；失败即
确认缺陷存在（Property 1）。不要在失败时去改测试或改生产代码。
"""

import pytest
from sqlalchemy import select

from app.controls.models import Control, ControlRelation, RelationType
from app.frameworks.models import Framework, FrameworkItem, Mapping
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import Decision, bulk_accept, decide

# 映射用框架项正文——引文必须是它的子串才能过闸 4。
ITEM_TEXT = "Identities and credentials are managed for authorized devices."
ITEM_QUOTE = "Identities and credentials are managed"

# 关系两端控制点正文——from/to 引文必须分别是各自正文的子串。
LEFT_TEXT = "Every change must be approved by the CAB before implementation."
RIGHT_TEXT = "The implementer shall follow the approved implementation plan."
LEFT_QUOTE = "approved by the CAB"
RIGHT_QUOTE = "follow the approved implementation plan"


async def _setup(db_session):
    """造一个有权批量接受的审核者 + 一个框架项，供映射用例引用。"""
    actor = User(
        email="lead@example.com", name="L", role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    fw = Framework(key="k", name_zh="z", name_en="e", version="1", source="s", item_count=0)
    db_session.add_all([actor, fw])
    await db_session.flush()
    item = FrameworkItem(
        framework_id=fw.id, code="PR.AA-01", title="Ident",
        description=ITEM_TEXT, level=1, order_index=0,
    )
    db_session.add(item)
    await db_session.flush()
    return actor, fw, item


async def _control(db_session, code, *, title="t", statement="s") -> Control:
    control = Control(code=code, title=title, statement=statement)
    db_session.add(control)
    await db_session.flush()
    return control


async def _mapping_proposal(db_session, item, control, *, confidence=0.95) -> Proposal:
    """一条高置信度、非 OCR、非 matrix 的 PENDING 映射提案——通过资格检查。"""
    payload = {
        "framework_item_id": item.id,
        "control_id": control.id,
        "strength": "partial",
        "framework_item_quote": ITEM_QUOTE,
        "rationale": "covers identities",
        "confidence": confidence,
    }
    proposal = Proposal(
        kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING,
        payload=payload,
        citations=[{"framework_item_id": item.id, "framework_item_quote": ITEM_QUOTE}],
        confidence=confidence,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


async def _relation_proposal(
    db_session, left, right, *, relation_type="depends_on", confidence=0.95
) -> Proposal:
    """一条高置信度、非 OCR、非 matrix 的 PENDING 关系提案——通过资格检查。"""
    payload = {
        "from_control_id": left.id,
        "to_control_id": right.id,
        "from_quote": LEFT_QUOTE,
        "to_quote": RIGHT_QUOTE,
        "rationale": "implementation depends on prior approval",
        "confidence": confidence,
        "relation_type": relation_type,
    }
    proposal = Proposal(
        kind=ProposalKind.RELATION, status=ProposalStatus.PENDING,
        payload=payload,
        citations=[
            {"control_id": left.id, "quote": LEFT_QUOTE},
            {"control_id": right.id, "quote": RIGHT_QUOTE},
        ],
        confidence=confidence,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


@pytest.mark.asyncio
async def test_同批两条映射指向同一目标_接受首条跳过次条(db_session):
    """用例 A（同批兄弟映射冲突，需求 1.3 / 2.3）。

    两条合格 `MAPPING` 提案指向同一 `(control, framework_item)`。按 `sorted(id)`
    靠前者先处理并确认映射；靠后者随后在 `_materialize_mapping` 撞上判重、抛
    `mapping.already_confirmed`。

    期望（修复后）：`{"accepted": 1, "skipped": 1}`，第一条 ACCEPTED（映射落库），
    第二条仍 PENDING，`bulk_accept` 不抛异常。

    未修复代码：第二条的 `Conflict` 穿透批次事务，第一条一并回滚，整批 0 接受
    或抛 `Conflict` —— 本测试因此失败。
    """
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    first = await _mapping_proposal(db_session, item, control)
    second = await _mapping_proposal(db_session, item, control)
    first_id, second_id = first.id, second.id
    # sorted(id) 保证先建的 first 先处理。
    assert first_id < second_id

    result = await bulk_accept(db_session, [first_id, second_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}
    await db_session.refresh(first)
    await db_session.refresh(second)
    assert first.status is ProposalStatus.ACCEPTED
    assert second.status is ProposalStatus.PENDING
    # 第一条的映射确实落库，没有因兄弟冲突被回滚。
    mappings = list(await db_session.scalars(select(Mapping)))
    assert len(mappings) == 1
    assert mappings[0].control_id == control.id
    assert mappings[0].framework_item_id == item.id


@pytest.mark.asyncio
async def test_关系目标已确认_跳过冲突条接受其余(db_session):
    """用例 B（关系已确认，需求 1.2 / 2.2）。

    控制点对 `(left, right)` 已有一条 `depends_on` 的确认关系。批内放入一条再次
    指向该对、同类型的合格关系提案（会撞 `relation.already_confirmed`），外加一条
    指向另一对、无冲突的合格关系提案。

    期望（修复后）：冲突条被跳过、无冲突条被接受，`{"accepted": 1, "skipped": 1}`，
    `bulk_accept` 不抛异常。

    未修复代码：`relation.already_confirmed` 穿透批次事务、整批回滚 —— 本测试失败。
    """
    actor, _fw, _item = await _setup(db_session)
    left = await _control(db_session, "C-0001", statement=LEFT_TEXT)
    right = await _control(db_session, "C-0002", statement=RIGHT_TEXT)
    # 另造一对无冲突目标的控制点（正文复用，使引文仍然成立）。
    other_left = await _control(db_session, "C-0003", statement=LEFT_TEXT)
    other_right = await _control(db_session, "C-0004", statement=RIGHT_TEXT)

    # 先确认 (left, right) 的 depends_on 关系（走 decide，与既有测试一致）。
    seed = await _relation_proposal(db_session, left, right)
    await decide(db_session, seed.id, actor=actor, decision=Decision.ACCEPT)
    await db_session.refresh(seed)
    assert seed.status is ProposalStatus.ACCEPTED

    conflicting = await _relation_proposal(db_session, left, right)
    clean = await _relation_proposal(db_session, other_left, other_right)
    conflicting_id, clean_id = conflicting.id, clean.id

    result = await bulk_accept(db_session, [conflicting_id, clean_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}
    await db_session.refresh(conflicting)
    await db_session.refresh(clean)
    assert conflicting.status is ProposalStatus.PENDING
    assert clean.status is ProposalStatus.ACCEPTED
    # seed + clean 两条关系落库；冲突条没有造出第二条 (left, right)。
    relations = list(await db_session.scalars(select(ControlRelation)))
    assert len(relations) == 2
    assert all(r.relation_type is RelationType.DEPENDS_ON for r in relations)


@pytest.mark.asyncio
async def test_映射目标批外已确认_跳过冲突条接受其余(db_session):
    """用例 C（目标已被批外确认的映射，需求 1.1 / 2.1）。

    `(control, framework_item)` 已在批外被确认过映射。批内放入一条再次指向它的
    合格映射提案（会撞 `mapping.already_confirmed`），外加一条指向另一控制点、
    无冲突的合格映射提案。

    期望（修复后）：冲突条被跳过、无冲突条被接受，`{"accepted": 1, "skipped": 1}`，
    `bulk_accept` 不抛异常。

    未修复代码：`mapping.already_confirmed` 穿透批次事务、整批回滚 —— 本测试失败。
    """
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    other = await _control(db_session, "C-0002")

    # 先在批外确认 (control, item) 的映射（走 decide）。
    seed = await _mapping_proposal(db_session, item, control)
    await decide(db_session, seed.id, actor=actor, decision=Decision.ACCEPT)
    await db_session.refresh(seed)
    assert seed.status is ProposalStatus.ACCEPTED

    conflicting = await _mapping_proposal(db_session, item, control)
    clean = await _mapping_proposal(db_session, item, other)
    conflicting_id, clean_id = conflicting.id, clean.id

    result = await bulk_accept(db_session, [conflicting_id, clean_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}
    await db_session.refresh(conflicting)
    await db_session.refresh(clean)
    assert conflicting.status is ProposalStatus.PENDING
    assert clean.status is ProposalStatus.ACCEPTED
    # seed + clean 两条映射落库；冲突条没有造出 (control, item) 的第二条。
    mappings = list(await db_session.scalars(select(Mapping)))
    assert len(mappings) == 2
    assert {(m.control_id, m.framework_item_id) for m in mappings} == {
        (control.id, item.id),
        (other.id, item.id),
    }
