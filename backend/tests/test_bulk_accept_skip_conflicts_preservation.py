"""Preservation 测试：批量接受在非缺陷输入下的既有行为必须保持不变。

本文件对应 bugfix spec `bulk-accept-skip-conflicts` 的任务 2（Property 2: Preservation）。

方法论：observation-first —— 先在**未修复**代码上观察各非缺陷输入的真实行为，
再写测试把该行为钉住。因此以下所有断言都在**未修复**代码上成立（全部 PASS），
修复后（任务 3）它们必须继续通过，守住：

- 3.1 内容非法（payload/校验类 AppError，非 already_confirmed）→ 整批回滚、整批不落
- 3.2 全部合格且无冲突 → 全部接受（accepted == 批大小，skipped == 0）
- 3.3 提案编号不存在 → 跳过
- 3.4 不合格（未达阈值 / OCR 存疑 / 非 PENDING / 类型不符 / origin==matrix）→ 跳过
- 3.5 锁顺序与咨询锁：lock_control_writes 被调用；行锁按 sorted(id) 顺序获取
- 3.6 bulk-ids 校验：空 / >200 / 非正整数 / 重复 → review.bulk_ids_invalid 拒绝整批

注：完整的「兄弟 A 落库保留、后置 B 冲突时 B 仍 PENDING」这类事务完整性断言
只能在修复后成立（任务 4），本文件**不**涉及。

内容非法整批不落（3.1）的主守卫是 `test_review_service.py` 的
`test_bulk_rolls_back_prior_accepts_on_invalid_content`（CONTROL_EXTRACT 路径）。
本文件不盲目重复它，而是补一条聚焦测试：用 MAPPING 提案钉住「更靠前的合格兄弟
不落库 + 抛出的 code 确为 payload/校验类（不在跳过允许清单内）」，增加覆盖面。
"""

import pytest
from sqlalchemy import select

from app.errors import AppError
from app.frameworks.models import Mapping
from app.iam.models import AuditLog
from app.review import service
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import Decision, bulk_accept, decide

# 复用任务 1 复现测试里的 fixtures/helpers，保持造数风格一致。
from tests.test_bulk_accept_skip_conflicts import (
    _control,
    _mapping_proposal,
    _setup,
)

# ---------------------------------------------------------------------------
# 3.1 内容非法 → 整批回滚、整批不落（聚焦补充，主守卫在 test_review_service.py）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_内容非法映射使整批回滚_靠前合格兄弟不落库(db_session):
    """3.1：批内一条合格映射内容非法（strength 非法 → mapping.payload_invalid）时，
    `bulk_accept` 抛 AppError、整批回滚；更靠前已处理的合格兄弟不落库。

    与现有 `test_bulk_rolls_back_prior_accepts_on_invalid_content`（CONTROL_EXTRACT）
    互补：这里走 MAPPING 路径，并额外断言抛出的 code 属于 payload/校验类
    （不在「目标已确认 / 过期冲突」跳过允许清单内），确认这是必须中止的那一类。
    """
    actor, _fw, item = await _setup(db_session)
    good_control = await _control(db_session, "C-0001")
    bad_control = await _control(db_session, "C-0002")

    good = await _mapping_proposal(db_session, item, good_control)
    bad = await _mapping_proposal(db_session, item, bad_control)
    # 让 bad 内容非法：strength 取一个非枚举值，materialize 校验必抛 payload_invalid。
    # 它仍通过资格检查（置信度高、非 OCR、非 matrix、PENDING），从而真正走到
    # materialize 这条「内容非法」路径。
    bad.payload = {**bad.payload, "strength": "definitely-not-a-valid-strength"}
    await db_session.flush()

    good_id, bad_id = good.id, bad.id
    assert good_id < bad_id  # sorted(id) 保证合格的 good 先处理

    with pytest.raises(AppError) as excinfo:
        await bulk_accept(db_session, [good_id, bad_id], actor=actor)

    # 抛出的不是「目标已确认 / 过期冲突」类（那几类修复后会被跳过）；
    # 这是内容非法，必须中止整批。
    assert excinfo.value.code not in {
        "mapping.already_confirmed",
        "relation.already_confirmed",
        "proposal.stale_conflict",
    }
    assert excinfo.value.code == "mapping.payload_invalid"

    await db_session.refresh(good)
    await db_session.refresh(bad)
    assert good.status is ProposalStatus.PENDING
    assert bad.status is ProposalStatus.PENDING
    # 靠前的 good 没有因整批回滚留下任何映射。
    assert list(await db_session.scalars(select(Mapping))) == []


# ---------------------------------------------------------------------------
# 3.2 全部合格且无冲突 → 全部接受
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_全部合格无冲突_全部接受(db_session):
    """3.2：批内三条合格映射，各指向不同 (control, item)，互不冲突。
    钉住 `{"accepted": 3, "skipped": 0}`，三条全部 ACCEPTED，三条映射落库。"""
    actor, _fw, item = await _setup(db_session)
    c1 = await _control(db_session, "C-0001")
    c2 = await _control(db_session, "C-0002")
    c3 = await _control(db_session, "C-0003")
    p1 = await _mapping_proposal(db_session, item, c1)
    p2 = await _mapping_proposal(db_session, item, c2)
    p3 = await _mapping_proposal(db_session, item, c3)
    ids = [p1.id, p2.id, p3.id]

    result = await bulk_accept(db_session, ids, actor=actor)

    assert result == {"accepted": 3, "skipped": 0}
    for proposal in (p1, p2, p3):
        await db_session.refresh(proposal)
        assert proposal.status is ProposalStatus.ACCEPTED
    assert len(list(await db_session.scalars(select(Mapping)))) == 3


# ---------------------------------------------------------------------------
# 3.3 提案编号不存在 → 跳过
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_不存在的编号计跳过_合格条照常接受(db_session):
    """3.3：批内含一个不存在的提案编号，应计为跳过；同批合格条照常接受。"""
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    good = await _mapping_proposal(db_session, item, control)
    missing_id = good.id + 100_000  # 必然不存在

    result = await bulk_accept(db_session, [good.id, missing_id], actor=actor)

    assert result == {"accepted": 1, "skipped": 1}
    await db_session.refresh(good)
    assert good.status is ProposalStatus.ACCEPTED


# ---------------------------------------------------------------------------
# 3.4 不合格 → 跳过（阈值 / OCR / 非 PENDING / 类型不符 / origin==matrix）
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_未达阈值的提案计跳过(db_session):
    """3.4：置信度低于自动接受阈值（默认 0.90）的映射应被跳过。"""
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    # 默认 auto_accept=0.90；0.50 必然未达阈值。
    low = await _mapping_proposal(db_session, item, control, confidence=0.50)

    result = await bulk_accept(db_session, [low.id], actor=actor)

    assert result == {"accepted": 0, "skipped": 1}
    await db_session.refresh(low)
    assert low.status is ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_非PENDING的提案计跳过(db_session):
    """3.4：已被裁定（非 PENDING）的提案不再合格，应被跳过。"""
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    already = await _mapping_proposal(db_session, item, control)
    # 先把它裁定掉（走 decide，确认映射），它随后在批里应被当作不合格跳过。
    await decide(db_session, already.id, actor=actor, decision=Decision.ACCEPT)
    await db_session.refresh(already)
    assert already.status is ProposalStatus.ACCEPTED

    result = await bulk_accept(db_session, [already.id], actor=actor)

    assert result == {"accepted": 0, "skipped": 1}


@pytest.mark.asyncio
async def test_origin为matrix的提案计跳过(db_session):
    """3.4：origin == 'matrix' 的提案禁止批量，应被跳过。"""
    actor, _fw, item = await _setup(db_session)
    control = await _control(db_session, "C-0001")
    matrix_origin = await _mapping_proposal(db_session, item, control)
    matrix_origin.payload = {**matrix_origin.payload, "origin": "matrix"}
    await db_session.flush()

    result = await bulk_accept(db_session, [matrix_origin.id], actor=actor)

    assert result == {"accepted": 0, "skipped": 1}
    await db_session.refresh(matrix_origin)
    assert matrix_origin.status is ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_类型不符的提案计跳过(db_session):
    """3.4：eligible() 只放行 CONTROL_EXTRACT/MAPPING/RELATION；ANSWER 类型应被跳过。"""
    actor, _fw, _item = await _setup(db_session)
    answer = Proposal(
        kind=ProposalKind.ANSWER,
        status=ProposalStatus.PENDING,
        payload={"question_id": 1, "text": "x"},
        citations=[],
        confidence=0.99,
    )
    db_session.add(answer)
    await db_session.flush()

    result = await bulk_accept(db_session, [answer.id], actor=actor)

    assert result == {"accepted": 0, "skipped": 1}
    await db_session.refresh(answer)
    assert answer.status is ProposalStatus.PENDING


# ---------------------------------------------------------------------------
# 3.5 锁顺序与咨询锁：lock_control_writes 被调用；行锁按 sorted(id) 获取
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_按排序id加锁并调用咨询锁(db_session, monkeypatch):
    """3.5：`bulk_accept` 必须调用 `lock_control_writes`（事务级咨询锁），
    并按 sorted(proposal_ids) 顺序逐条取行锁（locked_proposal）。

    用监视器包住两个真实函数——既保留原行为（锁真的生效），又记录调用，
    从而断言咨询锁被调用、且 bulk 层对各提案的首次行锁顺序是排好序的。"""
    actor, _fw, item = await _setup(db_session)
    c1 = await _control(db_session, "C-0001")
    c2 = await _control(db_session, "C-0002")
    c3 = await _control(db_session, "C-0003")
    p1 = await _mapping_proposal(db_session, item, c1)
    p2 = await _mapping_proposal(db_session, item, c2)
    p3 = await _mapping_proposal(db_session, item, c3)
    bulk_ids = {p1.id, p2.id, p3.id}
    # 故意乱序传入，验证内部会先排序再加锁。
    shuffled = [p3.id, p1.id, p2.id]

    advisory_calls = {"count": 0}
    original_lock = service.lock_control_writes

    async def _spy_lock(session):
        advisory_calls["count"] += 1
        return await original_lock(session)

    locked_order: list[int] = []
    original_locked = service.locked_proposal

    async def _spy_locked(session, proposal_id):
        locked_order.append(proposal_id)
        return await original_locked(session, proposal_id)

    monkeypatch.setattr(service, "lock_control_writes", _spy_lock)
    monkeypatch.setattr(service, "locked_proposal", _spy_locked)

    result = await bulk_accept(db_session, shuffled, actor=actor)

    assert result == {"accepted": 3, "skipped": 0}
    # 咨询锁被调用（至少 bulk 外层一次）。
    assert advisory_calls["count"] >= 1
    # 取每个 bulk 提案在 locked_proposal 调用序列里的首次出现位置，
    # 其顺序必须是 sorted(id)（decide 内部对同一 id 的二次加锁不影响这个顺序）。
    first_seen: dict[int, int] = {}
    for position, pid in enumerate(locked_order):
        if pid in bulk_ids and pid not in first_seen:
            first_seen[pid] = position
    ordered_ids = sorted(first_seen, key=lambda pid: first_seen[pid])
    assert ordered_ids == sorted(bulk_ids)


# ---------------------------------------------------------------------------
# 3.6 bulk-ids 校验：空 / >200 / 非正整数 / 重复 → review.bulk_ids_invalid
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad_ids",
    [
        [],                      # 空
        list(range(1, 202)),     # 201 个，超过 200 上限
        [1, 0, 2],               # 含非正整数（0）
        [1, -3, 2],              # 含非正整数（负数）
        [1, 2, 2],               # 含重复
    ],
    ids=["空", "超过200", "含零", "含负数", "含重复"],
)
async def test_非法ids列表以bulk_ids_invalid拒绝整批(db_session, bad_ids):
    """3.6：空 / 超过 200 / 含非正整数 / 含重复 的编号列表，
    `bulk_accept` 以 `review.bulk_ids_invalid` 拒绝整批。"""
    actor, _fw, _item = await _setup(db_session)

    with pytest.raises(AppError) as excinfo:
        await bulk_accept(db_session, bad_ids, actor=actor)
    assert excinfo.value.code == "review.bulk_ids_invalid"
    # 拒绝即中止：没有任何落库、没有审计。
    assert await db_session.scalar(select(AuditLog)) is None
