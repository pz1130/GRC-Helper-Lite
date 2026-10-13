import pytest
from sqlalchemy import select

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.frameworks.models import Framework, FrameworkItem, Mapping
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import create_access_token, hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.llm.models import AppSetting
from app.review.models import Proposal, ProposalKind, ProposalStatus
from app.review.service import ReviewTier, assess, auto_process
from app.review.thresholds import Thresholds


def _proposal(kind, payload, *, confidence=0.95, proposal_id=10):
    return Proposal(
        id=proposal_id,
        kind=kind,
        payload=payload,
        citations=[],
        confidence=confidence,
        status=ProposalStatus.PENDING,
    )


def test_clean_high_confidence_mapping_is_auto_eligible():
    proposal = _proposal(ProposalKind.MAPPING, {"rationale": "Direct coverage."})

    result = assess(
        proposal, Thresholds(0.9, 0.6, 0), ocr_flag=False
    )

    assert result.tier is ReviewTier.AUTO


def test_hedged_mapping_is_not_auto_accepted():
    proposal = _proposal(
        ProposalKind.MAPPING,
        {"rationale": "The control does not specifically address this requirement."},
    )

    result = assess(
        proposal, Thresholds(0.9, 0.6, 0), ocr_flag=False
    )

    assert result.tier is ReviewTier.DEFERRED
    assert "hedged_rationale" in result.reasons


def test_only_exact_statement_duplicate_relations_can_auto_accept():
    proposal = _proposal(
        ProposalKind.RELATION,
        {"relation_type": "duplicates"},
    )

    exact = assess(
        proposal, Thresholds(0.9, 0.6, 0), ocr_flag=False, exact_duplicate=True
    )
    similar = assess(
        proposal, Thresholds(0.9, 0.6, 0), ocr_flag=False, exact_duplicate=False
    )

    assert exact.tier is ReviewTier.AUTO
    assert similar.tier is ReviewTier.DEFERRED


async def _seed_mapping(db_session, *, ocr=False, confidence=0.95):
    actor = User(
        email=f"lead-{confidence}-{ocr}@example.com",
        name="Lead",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw"),
    )
    document = Document(
        title="Policy",
        doc_type=DocType.POLICY,
        file_hash=("b" if ocr else "a") * 64,
        file_path="/policy.pdf",
        original_filename="policy.pdf",
        status=DocStatus.ACTIVE,
        ocr_quality_flag=ocr,
    )
    framework = Framework(
        key=f"framework-{confidence}-{ocr}",
        name_zh="框架",
        name_en="Framework",
        version="1",
        source="test",
        item_count=1,
    )
    db_session.add_all([actor, document, framework])
    await db_session.flush()
    clause = Clause(
        document_id=document.id,
        number="1",
        heading="Access",
        heading_path="Access",
        citation_label="1",
        text="Access must be reviewed.",
        order_index=0,
        level=1,
    )
    control = Control(code=f"C-{int(confidence * 1000)}-{int(ocr)}", title="Access", statement="Review access.")
    item = FrameworkItem(
        framework_id=framework.id,
        code="AC-1",
        title="Access",
        description="Review access periodically.",
        level=1,
        order_index=0,
    )
    db_session.add_all([clause, control, item])
    await db_session.flush()
    db_session.add(
        ControlSource(
            control_id=control.id,
            clause_id=clause.id,
            relation=SourceRelation.DEFINES,
        )
    )
    proposal = Proposal(
        kind=ProposalKind.MAPPING,
        payload={
            "framework_item_id": item.id,
            "control_id": control.id,
            "strength": "full",
            "framework_item_quote": "Review access periodically.",
            "rationale": "Direct coverage.",
            "confidence": confidence,
        },
        citations=[],
        confidence=confidence,
    )
    db_session.add(proposal)
    await db_session.flush()
    return actor, proposal


@pytest.mark.asyncio
async def test_auto_process_materializes_without_impersonating_a_human(db_session):
    actor, proposal = await _seed_mapping(db_session)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    await db_session.flush()

    result = await auto_process(db_session, actor=actor)

    assert result["accepted"] == 1
    assert proposal.status is ProposalStatus.ACCEPTED
    assert proposal.decided_by is None
    assert await db_session.scalar(select(Mapping)) is not None
    system_log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "proposal.auto_accept")
    )
    batch_log = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "proposal.auto_process")
    )
    assert system_log.user_id is None
    assert batch_log.user_id == actor.id


@pytest.mark.asyncio
async def test_ocr_source_forces_mapping_into_manual_review(db_session):
    actor, proposal = await _seed_mapping(db_session, ocr=True)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    await db_session.flush()

    result = await auto_process(db_session, actor=actor)

    assert result["accepted"] == 0
    assert result["manual"] == 1
    assert proposal.status is ProposalStatus.PENDING
    assert await db_session.scalar(select(Mapping)) is None


@pytest.mark.asyncio
async def test_actionable_queue_hides_auto_and_deferred_items(client, db_session):
    actor, proposal = await _seed_mapping(db_session)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    await db_session.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"
    }

    all_items = (await client.get("/api/proposals", headers=headers)).json()
    actionable = (
        await client.get("/api/proposals?actionable_only=true", headers=headers)
    ).json()

    assert next(item for item in all_items if item["id"] == proposal.id)["review_tier"] == "auto"
    assert actionable == []


@pytest.mark.asyncio
async def test_automation_preview_is_read_only_and_shows_impact(client, db_session):
    actor, proposal = await _seed_mapping(db_session)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    await db_session.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"
    }

    response = await client.get(
        "/api/proposals/auto-process/preview", headers=headers
    )

    assert response.status_code == 200
    body = response.json()
    assert body["by_tier"]["auto"] == 1
    assert body["estimated_changes"] == {"mappings": 1, "relations": 0}
    assert body["auto_items"][0]["id"] == proposal.id
    assert proposal.status is ProposalStatus.PENDING
    assert await db_session.scalar(select(Mapping)) is None
    assert await db_session.scalar(
        select(AuditLog).where(AuditLog.action.like("proposal.auto%"))
    ) is None


@pytest.mark.asyncio
async def test_manual_bulk_mapping_is_consumed_and_cannot_be_accepted_twice(db_session):
    from app.errors import Conflict
    from app.review.service import Decision, bulk_accept, decide, pending

    actor, proposal = await _seed_mapping(db_session)
    assert await bulk_accept(db_session, [proposal.id], actor=actor) == {
        "accepted": 1, "skipped": 0,
    }
    assert proposal.status is ProposalStatus.ACCEPTED
    assert not await pending(db_session, kind=ProposalKind.MAPPING)
    assert len(list(await db_session.scalars(select(Mapping)))) == 1
    with pytest.raises(Conflict):
        await decide(db_session, proposal.id, actor=actor, decision=Decision.ACCEPT)
    assert len(list(await db_session.scalars(select(Mapping)))) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("ocr, confidence", [(True, 0.95), (False, 0.8)])
async def test_bulk_mapping_keeps_ocr_and_confidence_guards(db_session, ocr, confidence):
    from app.review.service import bulk_accept

    actor, proposal = await _seed_mapping(db_session, ocr=ocr, confidence=confidence)
    assert await bulk_accept(db_session, [proposal.id], actor=actor) == {
        "accepted": 0, "skipped": 1,
    }


@pytest.mark.asyncio
async def test_manual_bulk_relation_is_consumed(db_session):
    from app.controls.models import ControlRelation
    from app.review.service import bulk_accept, pending

    actor, mapping = await _seed_mapping(db_session)
    other = Control(code="C-OTHER", title="Approve", statement="Approve access first.")
    db_session.add(other)
    await db_session.flush()
    proposal = Proposal(
        kind=ProposalKind.RELATION,
        payload={
            "from_control_id": mapping.payload["control_id"],
            "to_control_id": other.id,
            "relation_type": "depends_on",
            "from_quote": "Review access.",
            "to_quote": "Approve access first.",
            "rationale": "Approval precedes the review.",
            "confidence": 0.95,
        },
        citations=[], confidence=0.95,
    )
    db_session.add(proposal)
    await db_session.flush()
    assert await bulk_accept(db_session, [proposal.id], actor=actor) == {
        "accepted": 1, "skipped": 0,
    }
    assert not await pending(db_session, kind=ProposalKind.RELATION)
    assert len(list(await db_session.scalars(select(ControlRelation)))) == 1


@pytest.mark.asyncio
async def test_auto_process_accepts_only_what_the_preview_showed(client, db_session):
    # 预览给人看一张清单，确认之后执行。执行若重新扫整个队列，接受的就是
    # 「此刻合格的那些」——预览之后新来的、或预览列表之外的，都没人看过。
    actor, proposal = await _seed_mapping(db_session)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    await db_session.flush()
    headers = {
        "Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"
    }

    unseen = await client.post(
        "/api/proposals/auto-process", json={"ids": [proposal.id + 1000]}, headers=headers
    )
    assert unseen.status_code == 200
    assert unseen.json()["accepted"] == 0
    assert proposal.status is ProposalStatus.PENDING

    seen = await client.post(
        "/api/proposals/auto-process", json={"ids": [proposal.id]}, headers=headers
    )
    assert seen.json()["accepted"] == 1
    await db_session.refresh(proposal)
    assert proposal.status is ProposalStatus.ACCEPTED


@pytest.mark.asyncio
async def test_auto_process_without_a_previewed_list_is_refused(client, db_session):
    actor, proposal = await _seed_mapping(db_session)
    headers = {
        "Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"
    }

    response = await client.post("/api/proposals/auto-process", json={}, headers=headers)

    assert response.status_code == 422
    assert proposal.status is ProposalStatus.PENDING


@pytest.mark.asyncio
async def test_automation_preview_is_not_crowded_out_by_other_kinds(client, db_session, monkeypatch):
    # 预览先取置信度最低的 N 条（不分类型），再只留映射与关系。控制点抽取提案一多，
    # 映射全被挤出去——预览说「没有可自动接受的」，而 truncated 是按过滤之后的条数算的，
    # 也说「没截断」。
    import app.review.router as review_router
    from app.review.models import Proposal, ProposalKind

    monkeypatch.setattr(review_router, "PREVIEW_SCAN_LIMIT", 3)
    actor, mapping = await _seed_mapping(db_session)
    setting = await db_session.get(AppSetting, "review_sample_rate")
    setting.value = {"value": 0}
    for index in range(3):
        db_session.add(Proposal(
            kind=ProposalKind.CONTROL_EXTRACT, payload={"title": f"t{index}"}, citations=[],
            confidence=0.1, status=ProposalStatus.PENDING,
        ))
    await db_session.flush()
    headers = {"Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"}

    body = (await client.get("/api/proposals/auto-process/preview", headers=headers)).json()

    assert [item["id"] for item in body["auto_items"]] == [mapping.id]
    assert body["truncated"] is False


@pytest.mark.asyncio
async def test_automation_preview_says_when_it_stopped_scanning(client, db_session, monkeypatch):
    import app.review.router as review_router

    monkeypatch.setattr(review_router, "PREVIEW_SCAN_LIMIT", 1)
    actor, _ = await _seed_mapping(db_session)
    await _seed_mapping(db_session, ocr=True, confidence=0.96)
    headers = {"Authorization": f"Bearer {create_access_token(actor.id, actor.role)}"}

    body = (await client.get("/api/proposals/auto-process/preview", headers=headers)).json()
    assert body["truncated"] is True
