"""补充测试：合并路径对称关系规范化的计划/执行单测、端到端集成与指纹一致性。

对应 bugfix「merge-duplicates-canonicalization」任务 4（design「Testing Strategy」
的 Unit / Integration / Digest 部分）。与任务 1（fix-checking）、任务 2
（preservation）两文件互补，不重叠改动它们。

覆盖：
- 计划 `_plan_relations`：镜像碰撞判 relation_duplicate；无碰撞 DUPLICATES 计
  move；自环判 relation_self_loop；非对称关系不重排。
- 执行 `merge_controls`：镜像碰撞后无镜像行且端点规范序；无碰撞转挂端点规范序；
  非对称端点不变。
- 集成（端到端）：造镜像场景，预览取指纹，带指纹执行合并，断言成功、无镜像行、
  审计完整；混合场景（自环/精确重复/镜像碰撞/无碰撞 DUPLICATES/非对称）各自归类
  正确且唯一约束不触发。
- 指纹一致性：预览 plan_digest 与执行重算一致，带它作 expected_digest 不触发
  control.merge_plan_changed。
"""

from sqlalchemy import and_, or_, select

from app.controls.merge import plan_digest, plan_merge
from app.controls.models import Control, ControlRelation, RelationType
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.materialize import merge_controls
from tests.test_control_merge_plan import _control, _relation


async def _lead(db_session) -> User:
    """合并需要 GRC_LEAD 身份；与既有合并测试的 actor 布法一致。"""
    user = User(
        email="lead@example.com",
        name="lead@example.com",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _duplicates_between(db_session, a_id: int, b_id: int) -> list[ControlRelation]:
    """查 a/b 之间（任一端点顺序）的所有 DUPLICATES 行，用于镜像检测。"""
    return list(
        await db_session.scalars(
            select(ControlRelation).where(
                ControlRelation.relation_type == RelationType.DUPLICATES,
                or_(
                    and_(
                        ControlRelation.from_control_id == a_id,
                        ControlRelation.to_control_id == b_id,
                    ),
                    and_(
                        ControlRelation.from_control_id == b_id,
                        ControlRelation.to_control_id == a_id,
                    ),
                ),
            )
        )
    )


# ── 计划 _plan_relations 单测 ─────────────────────────────────


async def test_计划_镜像碰撞判为relation_duplicate且不计move(db_session):
    """L 持 (L, X)，W 持 (X, W)；转挂 (W, X) 规范形 (X, W) 与赢家碰撞。

    计划应报 merge.discard.relation_duplicate，不对 control_relations 计 move。
    """
    loser = await _control(db_session, "C-0001")  # L
    other = await _control(db_session, "C-0002")  # X
    winner = await _control(db_session, "C-0003")  # W
    assert loser.id < other.id < winner.id

    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, other, winner, RelationType.DUPLICATES)  # (X, W)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 0
    rel_discards = [d for d in plan.discards if d.table == "control_relations"]
    assert [d.code for d in rel_discards] == ["merge.discard.relation_duplicate"]


async def test_计划_无碰撞DUPLICATES仍计一次move(db_session):
    """W < L < Y：输家 (L, Y) 转挂为 (W, Y)（规范序），赢家无相关 DUPLICATES。

    计划应计一次 control_relations move、不丢弃。
    """
    winner = await _control(db_session, "C-0001")  # W
    loser = await _control(db_session, "C-0002")  # L
    other = await _control(db_session, "C-0003")  # Y
    assert winner.id < loser.id < other.id

    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, Y)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 1
    assert [d for d in plan.discards if d.table == "control_relations"] == []


async def test_计划_转挂成自环判为relation_self_loop(db_session):
    """L 与 W 之间的 DUPLICATES 转挂后两端都落到 W（自环）。

    计划应报 merge.discard.relation_self_loop、不计 move。
    """
    winner = await _control(db_session, "C-0001")  # W
    loser = await _control(db_session, "C-0002")  # L
    await _relation(db_session, loser, winner, RelationType.DUPLICATES)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 0
    rel_discards = [d for d in plan.discards if d.table == "control_relations"]
    assert [d.code for d in rel_discards] == ["merge.discard.relation_self_loop"]
    assert rel_discards[0].params == {"relation": "duplicates"}


async def test_计划_非对称关系不重排且无碰撞时计move(db_session):
    """Y < L < W：输家 DEPENDS_ON (L, Y) 转挂为 (W, Y)，赢家无相关关系。

    非对称关系不参与规范化。计划应计一次 move、不丢弃（且后续执行不交换端点）。
    """
    other = await _control(db_session, "C-0001")  # Y
    loser = await _control(db_session, "C-0002")  # L
    winner = await _control(db_session, "C-0003")  # W
    assert other.id < loser.id < winner.id

    await _relation(db_session, loser, other, RelationType.DEPENDS_ON)  # (L, Y)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 1
    assert [d for d in plan.discards if d.table == "control_relations"] == []


# ── 执行 merge_controls 单测 ──────────────────────────────────


async def test_执行_镜像碰撞后无镜像行且端点规范序(db_session):
    """L 持 (L, X)，W 持 (X, W)；合并后 X 与 W 之间只应剩一条规范序 (X, W)。"""
    lead = await _lead(db_session)
    loser = await _control(db_session, "C-0001")  # L
    other = await _control(db_session, "C-0002")  # X
    winner = await _control(db_session, "C-0003")  # W
    assert loser.id < other.id < winner.id

    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, other, winner, RelationType.DUPLICATES)  # (X, W)
    x_id, w_id = other.id, winner.id

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    rows = await _duplicates_between(db_session, x_id, w_id)
    assert len(rows) == 1
    assert (rows[0].from_control_id, rows[0].to_control_id) == (x_id, w_id)
    assert rows[0].from_control_id < rows[0].to_control_id


async def test_执行_无碰撞转挂后端点规范序落库(db_session):
    """W < A < L：输家 (A, L) 转挂为 (A, W)（A>W，非规范序），赢家无碰撞。

    执行应规范化为 (W, A) 落库，满足 from_control_id < to_control_id。
    """
    lead = await _lead(db_session)
    winner = await _control(db_session, "C-0001")  # W
    third = await _control(db_session, "C-0002")  # A
    loser = await _control(db_session, "C-0003")  # L
    assert winner.id < third.id < loser.id

    await _relation(db_session, third, loser, RelationType.DUPLICATES)  # (A, L)
    w_id, a_id = winner.id, third.id

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    rows = await _duplicates_between(db_session, w_id, a_id)
    assert len(rows) == 1
    assert rows[0].from_control_id < rows[0].to_control_id
    assert (rows[0].from_control_id, rows[0].to_control_id) == (w_id, a_id)


async def test_执行_非对称关系端点不变(db_session):
    """Y < L < W：输家 DEPENDS_ON (L, Y) 转挂为 (W, Y)，不因 W > Y 被交换。"""
    lead = await _lead(db_session)
    other = await _control(db_session, "C-0001")  # Y
    loser = await _control(db_session, "C-0002")  # L
    winner = await _control(db_session, "C-0003")  # W
    assert other.id < loser.id < winner.id

    relation = await _relation(db_session, loser, other, RelationType.DEPENDS_ON)  # (L, Y)
    relation_id = relation.id
    w_id, y_id = winner.id, other.id

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    moved = await db_session.get(ControlRelation, relation_id)
    assert (moved.from_control_id, moved.to_control_id) == (w_id, y_id)
    assert moved.relation_type == RelationType.DEPENDS_ON


# ── 集成：端到端合并流程（预览取指纹 → 带指纹执行） ─────────────


async def test_集成_镜像场景带预览指纹执行合并成功且无镜像行与审计完整(db_session):
    """端到端：造镜像场景，plan_merge 取 plan_digest，带它作 expected_digest 执行。

    断言：合并不抛 control.merge_plan_changed（成功）、control_relations 无镜像
    DUPLICATES 行、输家标记 merged 且 control.merge 审计记录完整。
    """
    lead = await _lead(db_session)
    loser = await _control(db_session, "C-0001")  # L
    other = await _control(db_session, "C-0002")  # X
    winner = await _control(db_session, "C-0003")  # W
    assert loser.id < other.id < winner.id

    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, other, winner, RelationType.DUPLICATES)  # (X, W)
    loser_id, x_id, w_id = loser.id, other.id, winner.id

    # 预览取指纹。
    plan = await plan_merge(db_session, loser_id=loser_id, winner_id=w_id)
    digest = plan_digest(plan)

    # 带指纹执行：数据未变，分类一致，不应触发 control.merge_plan_changed。
    await merge_controls(
        db_session,
        loser_id=loser_id,
        winner_id=w_id,
        actor=lead,
        expected_digest=digest,
    )
    db_session.expire_all()

    # 无镜像：X 与 W 之间只剩一条规范序 (X, W)。
    rows = await _duplicates_between(db_session, x_id, w_id)
    assert len(rows) == 1
    assert (rows[0].from_control_id, rows[0].to_control_id) == (x_id, w_id)

    # 输家标记与审计完整。
    kept = await db_session.get(Control, loser_id)
    assert kept.status == "merged"
    assert kept.merged_into_id == w_id

    entry = await db_session.scalar(
        select(AuditLog).where(
            AuditLog.action == "control.merge",
            AuditLog.entity_id == str(loser_id),
        )
    )
    assert entry is not None
    assert entry.before["code"] == "C-0001"
    assert entry.after["merged_into"] == "C-0003"


async def test_集成_单次合并混合五类关系各自归类正确且唯一约束不触发(db_session):
    """同一合并里混合：自环、精确重复、镜像碰撞、无碰撞 DUPLICATES、非对称关系。

    断言计划把五类分别归类为 self_loop / duplicate(精确) / duplicate(镜像) / move
    / move，执行成功落库（唯一约束 uq_control_relation 不触发），且镜像碰撞后
    不留镜像行、无碰撞 DUPLICATES 以规范序落库、非对称端点不变。
    """
    lead = await _lead(db_session)
    # 建一批控制点，id 递增，便于精确控制规范序。
    winner = await _control(db_session, "C-0001")  # W（最小 id）
    loser = await _control(db_session, "C-0002")  # L
    x = await _control(db_session, "C-0003")  # X：镜像碰撞的第三点
    dep = await _control(db_session, "C-0004")  # 精确重复用的第三点
    asym = await _control(db_session, "C-0005")  # 非对称关系第三点
    freed = await _control(db_session, "C-0006")  # 无碰撞 DUPLICATES 第三点
    assert winner.id < loser.id < x.id < dep.id < asym.id < freed.id

    # 1) 自环：L↔W 的 DUPLICATES，转挂后 (W, W)。
    await _relation(db_session, loser, winner, RelationType.DUPLICATES)
    # 2) 镜像碰撞：L 持 (L, X)，W 已持 (X, W)；转挂 (W, X) 规范形与 (X, W) 碰撞。
    await _relation(db_session, loser, x, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, x, winner, RelationType.DUPLICATES)  # (X, W)
    # 3) 精确重复：非对称 DEPENDS_ON，W 已持 (W, dep)，L 持 (L, dep) → 转挂 (W, dep) 精确重复。
    await _relation(db_session, winner, dep, RelationType.DEPENDS_ON)
    await _relation(db_session, loser, dep, RelationType.DEPENDS_ON)
    # 4) 非对称不重排：DEPENDS_ON (L, asym) → (W, asym)，W < asym，端点不变。
    asym_rel = await _relation(db_session, loser, asym, RelationType.DEPENDS_ON)
    # 5) 无碰撞 DUPLICATES：(L, freed) → (W, freed)，W < freed，规范序，赢家无碰撞。
    freed_rel = await _relation(db_session, loser, freed, RelationType.DUPLICATES)

    asym_rel_id, freed_rel_id = asym_rel.id, freed_rel.id
    w_id, x_id, asym_id, freed_id, loser_id = (
        winner.id,
        x.id,
        asym.id,
        freed.id,
        loser.id,
    )

    plan = await plan_merge(db_session, loser_id=loser_id, winner_id=w_id)

    # 计划归类：2 条 move（非对称 asym + 无碰撞 DUPLICATES freed），
    # 3 条丢弃（self_loop + 镜像 duplicate + 精确 duplicate）。
    assert plan.moves.get("control_relations", 0) == 2
    rel_codes = sorted(d.code for d in plan.discards if d.table == "control_relations")
    assert rel_codes == [
        "merge.discard.relation_duplicate",
        "merge.discard.relation_duplicate",
        "merge.discard.relation_self_loop",
    ]

    # 带指纹执行：分类一致，唯一约束不触发（否则会抛 IntegrityError）。
    digest = plan_digest(plan)
    await merge_controls(
        db_session,
        loser_id=loser_id,
        winner_id=w_id,
        actor=lead,
        expected_digest=digest,
    )
    db_session.expire_all()

    # 镜像碰撞后 X 与 W 之间只剩一条规范序 (X, W)。
    mirror_rows = await _duplicates_between(db_session, x_id, w_id)
    assert len(mirror_rows) == 1
    assert (mirror_rows[0].from_control_id, mirror_rows[0].to_control_id) == (x_id, w_id)

    # 无碰撞 DUPLICATES 以规范序 (W, freed) 落库。
    freed_moved = await db_session.get(ControlRelation, freed_rel_id)
    assert (freed_moved.from_control_id, freed_moved.to_control_id) == (w_id, freed_id)
    assert freed_moved.from_control_id < freed_moved.to_control_id

    # 非对称端点不变 (W, asym)。
    asym_moved = await db_session.get(ControlRelation, asym_rel_id)
    assert (asym_moved.from_control_id, asym_moved.to_control_id) == (w_id, asym_id)
    assert asym_moved.relation_type == RelationType.DEPENDS_ON

    # 输家已合并。
    kept = await db_session.get(Control, loser_id)
    assert kept.status == "merged"
    assert kept.merged_into_id == w_id


# ── 指纹一致性：预览与执行重算不发散 ──────────────────────────


async def test_指纹一致性_镜像场景预览指纹等于执行重算指纹(db_session):
    """核心保证：镜像场景下预览 plan_digest 与执行时重算的指纹一致。

    执行时带预览指纹作 expected_digest，merge_controls 内部重算 plan_digest 比对，
    若两边分类发散会抛 control.merge_plan_changed。此处断言不抛即证明一致。
    """
    lead = await _lead(db_session)
    loser = await _control(db_session, "C-0001")  # L
    other = await _control(db_session, "C-0002")  # X
    winner = await _control(db_session, "C-0003")  # W
    assert loser.id < other.id < winner.id

    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, other, winner, RelationType.DUPLICATES)  # (X, W)
    loser_id, w_id = loser.id, winner.id

    preview = await plan_merge(db_session, loser_id=loser_id, winner_id=w_id)
    preview_digest = plan_digest(preview)

    # 不抛 Conflict(control.merge_plan_changed) 即证明预览与执行重算的指纹一致。
    returned = await merge_controls(
        db_session,
        loser_id=loser_id,
        winner_id=w_id,
        actor=lead,
        expected_digest=preview_digest,
    )
    # merge_controls 返回其内部重算的计划，其指纹应与预览完全相同。
    assert plan_digest(returned) == preview_digest
