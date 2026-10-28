"""缺陷条件探索测试：合并路径缺少写入路径已有的 DUPLICATES 规范化。

本文件对应 bugfix「merge-duplicates-canonicalization」的任务 1（Property 1: Bug
Condition）。写入路径 `_materialize_relation` 对 `DUPLICATES`（对称关系）会把端点
规范化为 `from_control_id < to_control_id` 落库，但合并路径（`controls/merge.py` 的
`_plan_relations` 与 `review/materialize.py` 的 `merge_controls`）没有这一步。

缺陷判定（design「Bug Condition」）：一条输家的 `DUPLICATES` 关系转挂到赢家后，
原始端点 `rawFrom ≠ rawTo` 且 `rawFrom > rawTo`（落入非规范序）。

**这些测试在未修复代码上必须 FAIL —— 失败正是对缺陷存在的佐证。**
它们编码的是「期望行为」，修复后应全部转为 PASS（Fix Checking）。
"""

from sqlalchemy import and_, or_, select

from app.controls.merge import plan_merge
from app.controls.models import ControlRelation, RelationType
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.materialize import merge_controls
from tests.test_control_merge_plan import _control, _relation


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


async def _count_relation(db_session, from_id: int, to_id: int, kind: RelationType) -> int:
    """按确切端点顺序数 control_relations 行数（不折叠镜像）。"""
    n = await db_session.scalar(
        select(ControlRelation)
        .where(
            and_(
                ControlRelation.from_control_id == from_id,
                ControlRelation.to_control_id == to_id,
                ControlRelation.relation_type == kind,
            )
        )
        .with_only_columns(ControlRelation.id)
    )
    return 1 if n is not None else 0


async def test_镜像碰撞在计划中应判为重复丢弃而非转挂(db_session):
    """镜像碰撞计划误判：输家 L 持 DUPLICATES (L, X)（L<X），赢家 W 已持 (X, W)（X<W）。

    把 L 并入 W，该行转挂为原始元组 (W, X)，其规范形 (X, W) 已存在于赢家。
    期望：计划报 merge.discard.relation_duplicate，且不对 control_relations 计 move。
    未修复代码用原始元组 (W, X, DUPLICATES) 比对 occupied={(X, W, DUPLICATES)} 未命中，
    会计成一次 move —— 故此断言在未修复代码上 FAIL。
    """
    # 建点顺序决定 id：L < X < W，使转挂后 rawFrom=W > rawTo=X 落入非规范序。
    loser = await _control(db_session, "C-0001")  # L
    other = await _control(db_session, "C-0002")  # X
    winner = await _control(db_session, "C-0003")  # W
    assert loser.id < other.id < winner.id

    # 两条都以规范序（from < to）落库，模拟写入路径已规范化后的既有数据。
    await _relation(db_session, loser, other, RelationType.DUPLICATES)  # (L, X)
    await _relation(db_session, other, winner, RelationType.DUPLICATES)  # (X, W)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    # 期望：镜像碰撞判为重复丢弃，不计 move。
    assert plan.moves.get("control_relations", 0) == 0
    relation_discards = [d for d in plan.discards if d.table == "control_relations"]
    assert [d.code for d in relation_discards] == ["merge.discard.relation_duplicate"]


async def test_合并执行后不应同时存在镜像的两条DUPLICATES行(db_session):
    """镜像行落库：同一场景执行 merge_controls 后，control_relations 中只应存在

    一条规范序的 DUPLICATES 行 (X, W)，不应同时出现 (X, W) 与 (W, X) 两条镜像。
    未修复代码把输家行端点直接改写为 (W, X) 落库，于是库里并存两条互为镜像、
    唯一约束 uq_control_relation 无法识别的行 —— 故此断言在未修复代码上 FAIL。
    """
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

    # 转挂到赢家后，X 与 W 之间的 DUPLICATES 只应剩一条规范序行 (X, W)。
    rows = list(
        await db_session.scalars(
            select(ControlRelation).where(
                ControlRelation.relation_type == RelationType.DUPLICATES,
                or_(
                    and_(
                        ControlRelation.from_control_id == x_id,
                        ControlRelation.to_control_id == w_id,
                    ),
                    and_(
                        ControlRelation.from_control_id == w_id,
                        ControlRelation.to_control_id == x_id,
                    ),
                ),
            )
        )
    )
    # 期望仅一条且为规范序 (X, W)；未修复代码会出现两条镜像行。
    assert len(rows) == 1
    assert (rows[0].from_control_id, rows[0].to_control_id) == (x_id, w_id)


async def test_无碰撞但转挂后非规范序的DUPLICATES应以规范序落库(db_session):
    """非规范序落库：输家 L 持 DUPLICATES (A, L)（A<L），赢家 W 满足 W<A，赢家无相关

    DUPLICATES。转挂后原始元组 (A, W) 满足 A>W（非规范序）。期望：规范化为 (W, A)
    落库，端点满足 from_control_id < to_control_id。未修复代码直接以 (A, W) 落库，
    端点未按规范序 —— 故此断言在未修复代码上 FAIL。
    """
    lead = await _lead(db_session)
    winner = await _control(db_session, "C-0001")  # W
    third = await _control(db_session, "C-0002")  # A
    loser = await _control(db_session, "C-0003")  # L
    assert winner.id < third.id < loser.id

    # 输家持有规范序 (A, L)（A < L）；赢家不持有与 A 的 DUPLICATES，无碰撞。
    await _relation(db_session, third, loser, RelationType.DUPLICATES)  # (A, L)
    w_id, a_id = winner.id, third.id

    await merge_controls(db_session, loser_id=loser.id, winner_id=winner.id, actor=lead)
    db_session.expire_all()

    # 转挂后 (A, W) 应规范化为 (W, A)；查询既有行的端点顺序。
    row = await db_session.scalar(
        select(ControlRelation).where(
            ControlRelation.relation_type == RelationType.DUPLICATES,
            or_(
                and_(
                    ControlRelation.from_control_id == w_id,
                    ControlRelation.to_control_id == a_id,
                ),
                and_(
                    ControlRelation.from_control_id == a_id,
                    ControlRelation.to_control_id == w_id,
                ),
            ),
        )
    )
    assert row is not None
    # 期望规范序 from < to；未修复代码落库为 (A, W)，即 from=A > to=W。
    assert row.from_control_id < row.to_control_id
    assert (row.from_control_id, row.to_control_id) == (w_id, a_id)
