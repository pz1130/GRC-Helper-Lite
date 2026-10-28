"""保持性属性测试：合并路径规范化修复不得改变非缺陷转挂的行为。

本文件对应 bugfix「merge-duplicates-canonicalization」的任务 2（Property 2:
Preservation）。缺陷判定（design「Bug Condition」）只针对「一条输家 DUPLICATES
关系转挂到赢家后 rawFrom ≠ rawTo 且 rawFrom > rawTo」。所有 `isBugCondition`
返回 false 的输入都应完全不受修复影响。

方法：观察优先（observation-first）。下面每个用例先在**未修复**代码上观察真实
行为，再把它固化成断言 —— 故这些测试在未修复代码上必须 PASS，修复后应继续 PASS
（Preservation Checking，无回归）。

覆盖（来自 design「Preservation Requirements」，bugfix 需求 3.1–3.6）：
- 3.1 非对称关系（DEPENDS_ON）转挂不重排端点
- 3.2 转挂成自环仍走 merge.discard.relation_self_loop
- 3.3 转挂后原始元组与赢家既有精确相同仍走 merge.discard.relation_duplicate
- 3.4 无碰撞 DUPLICATES（转挂后 rawFrom < rawTo）仍计 move 且规范序落库
- 3.5 写入路径 _materialize_relation 对 DUPLICATES 仍规范化落库
- 3.6 合并后输家 status=merged、merged_into_id 已写、审计记录存在
"""

from sqlalchemy import and_, or_, select

from app.controls.merge import plan_merge
from app.controls.models import Control, ControlRelation, RelationType
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.materialize import merge_controls
from app.review.service import Decision, decide
from tests.test_control_merge_plan import _control, _relation
from tests.test_relation_materialize import _proposal as _relation_proposal
from tests.test_relation_materialize import _setup as _relation_setup


async def _lead(db_session) -> User:
    """合并需要 GRC_LEAD 身份；与 test_control_merge.py 的 actor 布法一致。"""
    user = User(
        email="lead@example.com",
        name="lead@example.com",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _duplicates_row(
    db_session, winner_id: int, loser_id: int
) -> ControlRelation | None:
    """查 winner/loser（任一端点顺序）之间唯一的那条 DUPLICATES 行。"""
    return await db_session.scalar(
        select(ControlRelation).where(
            ControlRelation.relation_type == RelationType.DUPLICATES,
            or_(
                and_(
                    ControlRelation.from_control_id == winner_id,
                    ControlRelation.to_control_id == loser_id,
                ),
                and_(
                    ControlRelation.from_control_id == loser_id,
                    ControlRelation.to_control_id == winner_id,
                ),
            ),
        )
    )


# ── 3.1 非对称关系转挂不重排端点 ──────────────────────────────


async def test_非对称DEPENDS_ON转挂到赢家端点顺序不变(db_session):
    """3.1：输家 L 持 DEPENDS_ON (L, Y)，转挂为 (W, Y)。

    DEPENDS_ON 有方向，isBugCondition 为 false，不参与规范化。观察并固化：
    计划计一次 control_relations move；执行后该行端点为 (W, Y)，顺序不因端点大小
    被交换（即便 W > Y 也不交换）。
    """
    lead = await _lead(db_session)
    # 建点顺序：Y < L < W，使转挂后 W > Y；若错误地按大小规范化会被交换成 (Y, W)。
    other = await _control(db_session, "C-0001")  # Y
    loser = await _control(db_session, "C-0002")  # L
    winner = await _control(db_session, "C-0003")  # W
    assert other.id < loser.id < winner.id

    relation = await _relation(db_session, loser, other, RelationType.DEPENDS_ON)  # (L, Y)
    relation_id = relation.id
    w_id, y_id = winner.id, other.id

    # 计划：这是一次转挂（move），不丢弃。
    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert plan.moves.get("control_relations", 0) == 1
    assert [d for d in plan.discards if d.table == "control_relations"] == []

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    moved = await db_session.get(ControlRelation, relation_id)
    # 端点顺序保持转挂后的 (W, Y)，不因 W > Y 而重排。
    assert (moved.from_control_id, moved.to_control_id) == (w_id, y_id)
    assert moved.relation_type == RelationType.DEPENDS_ON


# ── 3.2 转挂成自环仍丢弃 ──────────────────────────────────────


async def test_转挂成自环的关系仍以self_loop丢弃(db_session):
    """3.2：输家 L 与赢家 W 之间的 DUPLICATES，转挂后两端都落到 W（自环）。

    rawFrom == rawTo，isBugCondition 为 false。观察并固化：计划走
    merge.discard.relation_self_loop、不计 move；执行后库中无 W 的自环行。
    """
    lead = await _lead(db_session)
    winner = await _control(db_session, "C-0001")
    loser = await _control(db_session, "C-0002")
    await _relation(db_session, loser, winner, RelationType.DUPLICATES)
    w_id = winner.id

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert plan.moves.get("control_relations", 0) == 0
    relation_discards = [d for d in plan.discards if d.table == "control_relations"]
    assert [d.code for d in relation_discards] == ["merge.discard.relation_self_loop"]
    assert relation_discards[0].params == {"relation": "duplicates"}

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    # 自环行被丢弃；库中不存在 (W, W) 的 DUPLICATES。
    self_loop = await db_session.scalar(
        select(ControlRelation).where(
            ControlRelation.from_control_id == w_id,
            ControlRelation.to_control_id == w_id,
            ControlRelation.relation_type == RelationType.DUPLICATES,
        )
    )
    assert self_loop is None


# ── 3.3 转挂后原始元组与赢家既有精确相同仍丢弃 ────────────────


async def test_转挂后与赢家既有精确重复的关系仍以duplicate丢弃(db_session):
    """3.3：赢家 W 与第三点 Y 已有 DEPENDS_ON (W, Y)，输家 L 持 (L, Y)。

    转挂后原始元组 (W, Y) 与赢家既有精确相同（非对称关系，无规范化介入），
    isBugCondition 为 false。观察并固化：计划走 merge.discard.relation_duplicate、
    不计 move；执行后 W→Y 的 DEPENDS_ON 仍只有一条。
    """
    lead = await _lead(db_session)
    winner = await _control(db_session, "C-0001")  # W
    loser = await _control(db_session, "C-0002")  # L
    other = await _control(db_session, "C-0003")  # Y
    await _relation(db_session, winner, other, RelationType.DEPENDS_ON)  # 赢家既有 (W, Y)
    await _relation(db_session, loser, other, RelationType.DEPENDS_ON)  # 输家 (L, Y)
    w_id, y_id = winner.id, other.id

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert plan.moves.get("control_relations", 0) == 0
    relation_discards = [d for d in plan.discards if d.table == "control_relations"]
    assert [d.code for d in relation_discards] == ["merge.discard.relation_duplicate"]

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    rows = list(
        await db_session.scalars(
            select(ControlRelation).where(
                ControlRelation.from_control_id == w_id,
                ControlRelation.to_control_id == y_id,
                ControlRelation.relation_type == RelationType.DEPENDS_ON,
            )
        )
    )
    assert len(rows) == 1


# ── 3.4 无碰撞 DUPLICATES（转挂后已是规范序）仍计 move ──────────


async def test_无碰撞且转挂后已规范序的DUPLICATES仍计move并按规范序落库(db_session):
    """3.4：输家 L 持 DUPLICATES (L, Y)（L<Y），赢家 W 满足 W<L，赢家无相关 DUPLICATES。

    转挂后原始元组 (W, Y) 满足 W < Y（已是规范序），isBugCondition 为 false
    （rawFrom < rawTo）。观察并固化：计划计一次 control_relations move、不丢弃；
    执行后该行落库为 (W, Y) 且满足 from_control_id < to_control_id。
    """
    lead = await _lead(db_session)
    # 建点顺序：W < L < Y，使转挂后 rawFrom=W < rawTo=Y（规范序，非缺陷）。
    winner = await _control(db_session, "C-0001")  # W
    loser = await _control(db_session, "C-0002")  # L
    other = await _control(db_session, "C-0003")  # Y
    assert winner.id < loser.id < other.id

    relation = await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, Y)
    relation_id = relation.id
    w_id, y_id = winner.id, other.id

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert plan.moves.get("control_relations", 0) == 1
    assert [d for d in plan.discards if d.table == "control_relations"] == []

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    moved = await db_session.get(ControlRelation, relation_id)
    assert (moved.from_control_id, moved.to_control_id) == (w_id, y_id)
    assert moved.from_control_id < moved.to_control_id
    assert moved.relation_type == RelationType.DUPLICATES


# ── 3.5 写入路径对 DUPLICATES 仍规范化落库 ────────────────────


async def test_写入路径materialize对DUPLICATES仍规范化落库(db_session):
    """3.5：通过确认流程走 _materialize_relation 落一条乱序 DUPLICATES。

    本修复只收敛写入路径的规范化实现、不改其行为。观察并固化：对
    right→left 的 DUPLICATES 提案，落库端点仍被规范化为 from_control_id < to_control_id。
    """
    actor, left, right = await _relation_setup(db_session)
    # right→left 乱序提案；写入路径应把它规范化为 (min, max)。
    proposal = await _relation_proposal(db_session, right, left, relation_type="duplicates")
    await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)

    row = await db_session.scalar(
        select(ControlRelation).where(
            ControlRelation.relation_type == RelationType.DUPLICATES
        )
    )
    assert row is not None
    assert row.from_control_id < row.to_control_id
    assert {row.from_control_id, row.to_control_id} == {left.id, right.id}


# ── 3.6 合并后输家标记与审计记录不变 ──────────────────────────


async def test_合并后输家标记merged并写merged_into_id且留审计(db_session):
    """3.6：合并后输家仍 status=merged、merged_into_id 指向赢家，且审计记录存在。

    本修复不触及标记与审计轨迹。观察并固化：无论关系如何归类，合并后输家留行
    并被标记、control.merge 审计记录可查到。
    """
    lead = await _lead(db_session)
    winner = await _control(db_session, "C-0001")
    loser = await _control(db_session, "C-0002")
    # 顺带带一条无碰撞的非对称关系，确保有转挂发生，审计 after 里有内容。
    other = await _control(db_session, "C-0003")
    await _relation(db_session, loser, other, RelationType.DEPENDS_ON)
    winner_id, loser_id = winner.id, loser.id

    await merge_controls(db_session, loser_id=loser_id, winner_id=winner_id, actor=lead)
    db_session.expire_all()

    kept = await db_session.get(Control, loser_id)
    assert kept is not None
    assert kept.status == "merged"
    assert kept.merged_into_id == winner_id

    entry = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "control.merge",
            AuditLog.entity_id == str(loser_id),
        )
    )
    assert entry is not None
    assert entry.before["code"] == "C-0002"
    assert entry.after["merged_into"] == "C-0001"
