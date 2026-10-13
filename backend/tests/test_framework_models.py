import pytest
from sqlalchemy import select, text

from app.controls.models import Control
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength


async def _framework(db_session) -> Framework:
    fw = Framework(
        key="nist-csf-2.0", name_zh="NIST 网络安全框架 2.0", name_en="NIST CSF 2.0",
        version="2.0", source="nist.gov", item_count=0,
    )
    db_session.add(fw)
    await db_session.flush()
    return fw


@pytest.mark.asyncio
async def test_items_form_a_tree_with_self_referencing_parent(db_session):
    fw = await _framework(db_session)
    root = FrameworkItem(
        framework_id=fw.id, code="PR", title="Protect", description="",
        level=1, order_index=0,
    )
    db_session.add(root)
    await db_session.flush()
    child = FrameworkItem(
        framework_id=fw.id, parent_id=root.id, code="PR.AA-01",
        title="Identities are managed", description="Identities and credentials are managed.",
        level=2, order_index=1,
    )
    db_session.add(child)
    await db_session.flush()
    assert child.parent_id == root.id
    assert child.attributes is None


@pytest.mark.asyncio
async def test_code_is_unique_within_a_framework_not_across(db_session):
    from sqlalchemy.exc import IntegrityError

    first = await _framework(db_session)
    second = Framework(
        key="iso-27001-2022", name_zh="ISO 27001", name_en="ISO 27001",
        version="2022", source="upload", item_count=0,
    )
    db_session.add(second)
    await db_session.flush()

    db_session.add(FrameworkItem(
        framework_id=first.id, code="A.5.1", title="x", description="", level=1, order_index=0))
    db_session.add(FrameworkItem(
        framework_id=second.id, code="A.5.1", title="y", description="", level=1, order_index=0))
    await db_session.flush()

    db_session.add(FrameworkItem(
        framework_id=first.id, code="A.5.1", title="dup", description="", level=1, order_index=1))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_attributes_hold_baselines_and_requirement_flag(db_session):
    fw = await _framework(db_session)
    item = FrameworkItem(
        framework_id=fw.id, code="AC-2", title="Account Management",
        description="Manage system accounts.", level=2, order_index=0,
        attributes={"baselines": ["low", "moderate", "high"], "is_requirement": True},
    )
    db_session.add(item)
    await db_session.flush()
    assert item.attributes["baselines"] == ["low", "moderate", "high"]
    assert item.attributes["is_requirement"] is True


@pytest.mark.asyncio
async def test_mapping_strength_persists_spec_value_not_enum_name(db_session):
    fw = await _framework(db_session)
    item = FrameworkItem(
        framework_id=fw.id, code="PR.AA-01", title="x", description="y", level=1, order_index=0)
    control = Control(code="C-0001", title="Dual approval", statement="Two approvers.")
    db_session.add_all([item, control])
    await db_session.flush()
    db_session.add(Mapping(
        control_id=control.id, framework_item_id=item.id,
        strength=MappingStrength.PARTIAL, rationale="covers part", quote="y",
    ))
    await db_session.flush()
    raw = await db_session.scalar(text("select strength from mappings limit 1"))
    assert raw == "partial"


@pytest.mark.asyncio
async def test_a_control_maps_to_an_item_at_most_once(db_session):
    from sqlalchemy.exc import IntegrityError

    fw = await _framework(db_session)
    item = FrameworkItem(
        framework_id=fw.id, code="PR.AA-01", title="x", description="y", level=1, order_index=0)
    control = Control(code="C-0001", title="t", statement="s")
    db_session.add_all([item, control])
    await db_session.flush()
    for strength in (MappingStrength.FULL, MappingStrength.SUPPORTING):
        db_session.add(Mapping(
            control_id=control.id, framework_item_id=item.id,
            strength=strength, rationale="r", quote="y"))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_deleting_a_framework_removes_its_items_and_mappings(db_session):
    fw = await _framework(db_session)
    item = FrameworkItem(
        framework_id=fw.id, code="PR.AA-01", title="x", description="y", level=1, order_index=0)
    control = Control(code="C-0001", title="t", statement="s")
    db_session.add_all([item, control])
    await db_session.flush()
    db_session.add(Mapping(
        control_id=control.id, framework_item_id=item.id,
        strength=MappingStrength.FULL, rationale="r", quote="y"))
    await db_session.flush()

    await db_session.delete(fw)
    await db_session.flush()
    assert (await db_session.scalars(select(FrameworkItem))).all() == []
    assert (await db_session.scalars(select(Mapping))).all() == []
