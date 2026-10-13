import pytest
from sqlalchemy import func, select

from app.errors import AppError
from app.frameworks.importer import import_framework
from app.frameworks.models import Framework, FrameworkItem
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password

HEADERS = ["code", "title", "description", "parent_code", "attributes_json"]
ROWS = [
    ["PR", "Protect", "", "", ""],
    ["PR.AA", "Identity Management", "", "PR", ""],
    ["PR.AA-01", "Identities are managed", "Identities and credentials are managed.",
     "PR.AA", '{"is_requirement": true}'],
]


async def _actor(db_session) -> User:
    user = User(email="lead@example.com", name="Lead", role=Role.GRC_LEAD,
                password_hash=hash_password("pw123456"))
    db_session.add(user)
    await db_session.flush()
    return user


async def _import(db_session, actor, *, key="nist-csf-2.0", rows=None):
    return await import_framework(
        db_session, HEADERS, rows if rows is not None else ROWS,
        key=key, name_zh="NIST CSF 2.0", name_en="NIST CSF 2.0",
        version="2.0", source="nist.gov", actor=actor,
    )


@pytest.mark.asyncio
async def test_import_builds_the_tree_and_counts_items(db_session):
    actor = await _actor(db_session)
    framework = await _import(db_session, actor)

    assert framework.item_count == 3
    items = list(await db_session.scalars(
        select(FrameworkItem).order_by(FrameworkItem.order_index)))
    assert [i.code for i in items] == ["PR", "PR.AA", "PR.AA-01"]
    assert items[0].parent_id is None
    assert items[1].parent_id == items[0].id
    assert items[2].parent_id == items[1].id
    assert [i.level for i in items] == [1, 2, 3]
    assert items[2].attributes == {"is_requirement": True}


@pytest.mark.asyncio
async def test_import_is_audited(db_session):
    actor = await _actor(db_session)
    framework = await _import(db_session, actor)
    entry = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "framework.import"))
    assert entry.user_id == actor.id
    assert entry.entity_type == "Framework"
    assert entry.entity_id == str(framework.id)
    assert entry.after["item_count"] == 3


@pytest.mark.asyncio
async def test_a_validation_failure_writes_nothing(db_session):
    actor = await _actor(db_session)
    bad = [["A", "a", "", "MISSING", ""]]
    with pytest.raises(AppError):
        await _import(db_session, actor, rows=bad)
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 0
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 0


@pytest.mark.asyncio
async def test_reimport_creates_a_new_framework_row_rather_than_overwriting(db_session):
    actor = await _actor(db_session)
    first = await _import(db_session, actor, key="nist-csf-2.0")
    second = await _import(db_session, actor, key="nist-csf-2.1")
    assert first.id != second.id
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 2
    assert await db_session.scalar(select(func.count()).select_from(FrameworkItem)) == 6


@pytest.mark.asyncio
async def test_duplicate_key_is_rejected(db_session):
    actor = await _actor(db_session)
    await _import(db_session, actor, key="nist-csf-2.0")
    with pytest.raises(AppError):
        await _import(db_session, actor, key="nist-csf-2.0")


TREE_LAST = [
    # 模板把所有顶层写在前面，子项排在后面——800-53 的官方导出就是这个形状
    ["AC", "Access Control", "", "", ""],
    ["AU", "Audit", "", "", ""],
    ["AC-1", "Policy", "Body.", "AC", ""],
    ["AC-2", "Accounts", "Body.", "AC", ""],
    ["AC-2.1", "Automated", "Body.", "AC-2", ""],
    ["AU-1", "Audit Policy", "Body.", "AU", ""],
]


@pytest.mark.asyncio
async def test_order_index_follows_the_tree_not_the_template_row_order(db_session):
    """order_index 必须表示树的阅读顺序。

    分批按 level-1 子树切分，靠的就是 order_index；若沿用模板行号，
    「所有顶层在前」的模板会让每个顶层各自成为一个没有子项的批次，
    而全部子项挤进最后一个顶层的批次里——按族分批等于没生效。
    """
    actor = await _actor(db_session)
    framework = await _import(db_session, actor, key="sp-800-53", rows=TREE_LAST)

    items = list(await db_session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework.id)
        .order_by(FrameworkItem.order_index)
    ))
    assert [i.code for i in items] == ["AC", "AC-1", "AC-2", "AC-2.1", "AU", "AU-1"]
    assert [i.order_index for i in items] == [0, 1, 2, 3, 4, 5]
    assert [i.level for i in items] == [1, 2, 2, 3, 1, 2]


@pytest.mark.asyncio
async def test_sibling_order_within_a_parent_follows_the_template(db_session):
    actor = await _actor(db_session)
    rows = [
        ["R", "Root", "", "", ""],
        ["R-B", "Second", "Body.", "R", ""],
        ["R-A", "First", "Body.", "R", ""],
    ]
    framework = await _import(db_session, actor, key="ord", rows=rows)
    items = list(await db_session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework.id)
        .order_by(FrameworkItem.order_index)
    ))
    assert [i.code for i in items] == ["R", "R-B", "R-A"]


@pytest.mark.asyncio
async def test_batching_groups_each_family_with_its_own_controls(db_session):
    """导入之后，按族分批必须真的按族分。"""
    from app.mapping.batching import build_batches

    actor = await _actor(db_session)
    framework = await _import(db_session, actor, key="sp-800-53-b", rows=TREE_LAST)
    items = list(await db_session.scalars(
        select(FrameworkItem)
        .where(FrameworkItem.framework_id == framework.id)
        .order_by(FrameworkItem.order_index, FrameworkItem.id)
    ))
    batches = build_batches(items)
    assert [[i.code for i in b.items] for b in batches] == [
        ["AC", "AC-1", "AC-2", "AC-2.1"], ["AU", "AU-1"]]
    # 容器留作上下文，不作映射目标
    assert all("AC" not in {i.code for i in b.items if i.id in b.mappable_ids} for b in batches)
