"""合并计划：先算清楚会转挂什么、会丢弃什么，再让人决定（OQ-8）。"""

from itertools import count

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clauses.models import Clause
from app.controls.merge import plan_merge
from app.controls.models import (
    Control,
    ControlRelation,
    ControlSource,
    RelationType,
    SourceRelation,
)
from app.environment.models import (
    HowEnforced,
    Implementation,
    ImplementationStatus,
    TechAsset,
    TechAssetCategory,
    TechAssetEnvironment,
    TechAssetStatus,
)
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.ingest.models import DocType, Document
from app.relations.models import ControlEmbedding
from app.risk.models import RiskEntry, RiskSource, RiskStatus

_DOC_N = count(1)


async def _control(db_session, code: str, statement: str = "S") -> Control:
    control = Control(code=code, title=f"T {code}", statement=statement)
    db_session.add(control)
    await db_session.flush()
    return control


async def _framework_item(db_session, code: str) -> FrameworkItem:
    framework = Framework(
        key=f"fw-{code}", name_zh="框架", name_en="Framework", version="2.0", source="nist.gov"
    )
    db_session.add(framework)
    await db_session.flush()
    item = FrameworkItem(
        framework_id=framework.id, parent_id=None, code=code, title=code, level=1, order_index=0
    )
    db_session.add(item)
    await db_session.flush()
    return item


async def _mapping(
    db_session, control_id: int, framework_item_id: int, strength: MappingStrength
) -> Mapping:
    mapping = Mapping(
        control_id=control_id,
        framework_item_id=framework_item_id,
        strength=strength,
        rationale="",
        quote="",
    )
    db_session.add(mapping)
    await db_session.flush()
    return mapping


async def _relation(db_session, src: Control, dst: Control, kind: RelationType) -> ControlRelation:
    relation = ControlRelation(
        from_control_id=src.id,
        to_control_id=dst.id,
        relation_type=kind,
        rationale="because",
        confidence=0.9,
    )
    db_session.add(relation)
    await db_session.flush()
    return relation


async def _clause(db_session, number: str = "4.1") -> Clause:
    n = next(_DOC_N)
    doc = Document(
        title=f"Doc {number}",
        doc_type=DocType.POLICY,
        file_hash=f"{n:064d}",
        file_path=f"/{n}.pdf",
        original_filename=f"{n}.pdf",
    )
    db_session.add(doc)
    await db_session.flush()
    clause = Clause(
        document_id=doc.id,
        number=number,
        heading="H",
        heading_path=f"H › {number}",
        citation_label=number,
        text=f"Clause {number} text.",
        order_index=0,
        level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    return clause


async def _source(db_session, control_id: int, clause_id: int) -> ControlSource:
    row = ControlSource(
        control_id=control_id, clause_id=clause_id, relation=SourceRelation.DEFINES
    )
    db_session.add(row)
    await db_session.flush()
    return row


async def _snapshot(db_session) -> dict[str, list[tuple]]:
    """七张表的 id 以及合并会改的外键。条数变化或转挂都会让前后快照对不上。"""
    specs: tuple[tuple[type, tuple[str, ...]], ...] = (
        (Control, ("id", "status", "merged_into_id", "code", "title", "statement")),
        (ControlEmbedding, ("id", "control_id")),
        (ControlRelation, ("id", "from_control_id", "to_control_id", "relation_type")),
        (ControlSource, ("id", "control_id", "clause_id")),
        (EvidenceItem, ("id", "control_id")),
        (Implementation, ("id", "control_id", "tech_asset_id")),
        (Mapping, ("id", "control_id", "framework_item_id")),
        (RiskEntry, ("id", "control_id")),
    )
    out: dict[str, list[tuple]] = {}
    for model, attrs in specs:
        rows = list(await db_session.scalars(select(model).order_by(model.id)))
        out[model.__tablename__] = [tuple(getattr(row, attr) for attr in attrs) for row in rows]
    return out


async def test_a_control_can_point_at_the_one_it_was_merged_into(db_session):
    winner = await _control(db_session, "C-0001")
    loser = await _control(db_session, "C-0002")

    loser.merged_into_id = winner.id
    loser.status = "merged"
    await db_session.flush()

    assert loser.merged_into_id == winner.id


async def test_the_merge_target_must_exist(db_session):
    loser = await _control(db_session, "C-0003")
    loser.merged_into_id = 9_999_999
    with pytest.raises(IntegrityError):
        await db_session.flush()


async def test_a_shared_framework_item_is_reported_as_a_discard(db_session):
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    item = await _framework_item(db_session, "PR.AA-01")
    await _mapping(db_session, winner.id, item.id, MappingStrength.FULL)
    await _mapping(db_session, loser.id, item.id, MappingStrength.PARTIAL)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("mappings", 0) == 0
    assert [d.table for d in plan.discards] == ["mappings"]
    # 句子由前端按语言组；后端给 code 与变量，中文 detail 只是兜底和审计留痕。
    assert plan.discards[0].code == "merge.discard.mapping_duplicate"
    assert plan.discards[0].params == {"item": "PR.AA-01", "strength": "partial", "existing": "full"}
    assert "PR.AA-01" in plan.discards[0].detail


async def test_a_framework_item_only_the_loser_has_is_reported_as_a_move(db_session):
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    item = await _framework_item(db_session, "PR.AA-01")
    await _mapping(db_session, loser.id, item.id, MappingStrength.FULL)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.loser_code == "C-0002"
    assert plan.winner_code == "C-0001"
    assert plan.moves["mappings"] == 1
    assert plan.discards == []
    assert plan.blockers == []


async def test_a_relation_between_the_two_becomes_a_self_loop_and_is_discarded(db_session):
    # duplicates 关系正是合并的来源：确认完它才知道该合并，合并后它就自指了。
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    await _relation(db_session, loser, winner, RelationType.DUPLICATES)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 0
    assert [d.table for d in plan.discards] == ["control_relations"]
    assert plan.discards[0].code == "merge.discard.relation_self_loop"
    assert plan.discards[0].params == {"relation": "duplicates"}


async def test_a_retargeted_relation_that_the_winner_already_has_is_discarded(db_session):
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    other = await _control(db_session, "C-0003")
    await _relation(db_session, winner, other, RelationType.DEPENDS_ON)
    await _relation(db_session, loser, other, RelationType.DEPENDS_ON)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_relations", 0) == 0
    assert [d.table for d in plan.discards] == ["control_relations"]
    assert plan.discards[0].code == "merge.discard.relation_duplicate"


async def test_a_shared_clause_is_reported_as_a_discard(db_session):
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    clause = await _clause(db_session)
    await _source(db_session, winner.id, clause.id)
    await _source(db_session, loser.id, clause.id)

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_sources", 0) == 0
    assert [d.table for d in plan.discards] == ["control_sources"]
    assert plan.discards[0].code == "merge.discard.source_duplicate"
    assert plan.discards[0].params == {"clause": clause.citation_label}


async def test_the_loser_embedding_is_always_discarded(db_session):
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    db_session.add(ControlEmbedding(control_id=winner.id))
    loser_row = ControlEmbedding(control_id=loser.id)
    db_session.add(loser_row)
    await db_session.flush()

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.moves.get("control_embeddings", 0) == 0
    assert [d.table for d in plan.discards] == ["control_embeddings"]
    assert plan.discards[0].id == loser_row.id
    assert plan.discards[0].code == "merge.discard.embedding"


async def test_merging_a_control_into_itself_is_blocked(db_session):
    c = await _control(db_session, "C-0001")
    plan = await plan_merge(db_session, loser_id=c.id, winner_id=c.id)
    assert [b.code for b in plan.blockers] == ["merge.blocked.self"]
    assert plan.moves == {}
    assert plan.discards == []


async def test_an_already_merged_control_cannot_be_merged_again(db_session):
    winner = await _control(db_session, "C-0001")
    loser = await _control(db_session, "C-0002")
    already = await _control(db_session, "C-0003")
    loser.merged_into_id = already.id
    loser.status = "merged"
    await db_session.flush()

    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)

    assert plan.blockers
    assert "merge.blocked.already_merged" in [b.code for b in plan.blockers]
    assert plan.moves == {}
    assert plan.discards == []


async def test_the_plan_writes_nothing(db_session):
    # 预览不写库——这是「先看后做」的全部意义。
    winner, loser = await _control(db_session, "C-0001"), await _control(db_session, "C-0002")
    shared = await _framework_item(db_session, "PR.AA-01")
    unique = await _framework_item(db_session, "PR.AA-02")
    await _mapping(db_session, winner.id, shared.id, MappingStrength.FULL)
    await _mapping(db_session, loser.id, shared.id, MappingStrength.PARTIAL)
    await _mapping(db_session, loser.id, unique.id, MappingStrength.FULL)
    await _relation(db_session, loser, winner, RelationType.DUPLICATES)
    clause = await _clause(db_session)
    await _source(db_session, winner.id, clause.id)
    await _source(db_session, loser.id, clause.id)
    db_session.add_all(
        [ControlEmbedding(control_id=winner.id), ControlEmbedding(control_id=loser.id)]
    )
    evidence_type = EvidenceType(
        name_zh="证",
        name_en="Ev",
        format="pdf",
        cadence=EvidenceCadence.ANNUAL,
    )
    asset = TechAsset(
        name="PAM-merge",
        category=TechAssetCategory.PAM,
        vendor="V",
        environment=TechAssetEnvironment.PROD,
        status=TechAssetStatus.ACTIVE,
    )
    db_session.add_all([evidence_type, asset])
    await db_session.flush()
    db_session.add(
        EvidenceItem(
            evidence_type_id=evidence_type.id,
            control_id=loser.id,
            title="E",
            status=EvidenceStatus.PLANNED,
        )
    )
    db_session.add(
        Implementation(
            control_id=loser.id,
            tech_asset_id=asset.id,
            description="d",
            how_enforced=HowEnforced.MANUAL,
            status=ImplementationStatus.IMPLEMENTED,
        )
    )
    db_session.add(
        RiskEntry(
            title="R",
            source=RiskSource.MANUAL,
            source_ref={},
            control_id=loser.id,
            likelihood=2,
            impact=3,
            inherent_score=6,
            status=RiskStatus.OPEN,
        )
    )
    await db_session.flush()

    before = await _snapshot(db_session)
    plan = await plan_merge(db_session, loser_id=loser.id, winner_id=winner.id)
    assert await _snapshot(db_session) == before
    assert [d.table for d in plan.discards] == [
        "control_embeddings",
        "control_relations",
        "control_sources",
        "mappings",
    ]
    assert plan.moves["evidence_items"] == 1
    assert plan.moves["implementations"] == 1
    assert plan.moves["mappings"] == 1
    assert plan.moves["risk_entries"] == 1
    assert plan.blockers == []
