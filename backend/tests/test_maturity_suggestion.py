"""成熟度建议（maturity_suggestion）。

这个任务在总设计 §396 里定义了输入输出，但后端一直没实现——`llm/tasks.py`
把它标成 implemented=False，配置页如实显示"未实现"。这里把它补上。

输入是三路：覆盖度（映射）、实施状态（implementations）、证据新鲜度
（evidence_items + evidence/freshness）。第三路在证据库为空时**没有数据**，
不能当成"新鲜度为零"——那会把"还没开始登记证据"和"证据全过期"混成一件事，
而这两件事该给的分完全不同。
"""

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.controls.models import Control
from app.environment.models import HowEnforced, Implementation, ImplementationStatus
from app.evidence.models import EvidenceCadence, EvidenceItem, EvidenceStatus, EvidenceType
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.maturity.context import build_inputs


async def _framework_with_item(session, code: str = "PR.AA-05") -> tuple[Framework, FrameworkItem]:
    framework = Framework(
        key="suggest-test",
        name_zh="建议",
        name_en="Suggest",
        version="1",
        source="test",
        item_count=1,
    )
    session.add(framework)
    await session.flush()
    item = FrameworkItem(
        framework_id=framework.id,
        code=code,
        title="Access permissions are reviewed",
        description="Review privileged access periodically.",
        level=1,
        order_index=1,
    )
    session.add(item)
    await session.flush()
    return framework, item


async def _mapped_control(session, item: FrameworkItem, *, code: str = "AC-01") -> Control:
    control = Control(code=code, title="特权账号季度复核", statement="每季度复核特权账号。")
    session.add(control)
    await session.flush()
    session.add(
        Mapping(
            control_id=control.id,
            framework_item_id=item.id,
            strength=MappingStrength.FULL,
            rationale="",
            quote="",
        )
    )
    await session.flush()
    return control


@pytest.mark.asyncio
async def test_inputs_carry_coverage_and_implementation_for_a_mapped_item(db_session):
    _, item = await _framework_with_item(db_session)
    control = await _mapped_control(db_session, item)
    db_session.add(
        Implementation(
            control_id=control.id,
            description="VaultKeeper 季度报表",
            how_enforced=HowEnforced.SEMI_AUTOMATED,
            status=ImplementationStatus.PARTIAL,
        )
    )
    await db_session.flush()

    inputs = await build_inputs(db_session, framework_id=item.framework_id)

    assert len(inputs.items) == 1
    got = inputs.items[0]
    assert got.code == "PR.AA-05"
    assert [c.control_code for c in got.controls] == ["AC-01"]
    assert got.controls[0].mapping_strength == "full"
    assert got.controls[0].implementation_status == "partial"
    assert got.controls[0].how_enforced == "semi_automated"


@pytest.mark.asyncio
async def test_empty_evidence_library_is_reported_as_absent_not_as_zero_freshness(db_session):
    """证据库空 ≠ 证据全过期。前者是"这路输入缺席"，后者是"实打实的坏消息"。"""
    _, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)

    inputs = await build_inputs(db_session, framework_id=item.framework_id)

    assert inputs.evidence_available is False
    assert inputs.items[0].controls[0].evidence is None


@pytest.mark.asyncio
async def test_evidence_is_bucketed_by_freshness_when_the_library_has_content(db_session):
    """新鲜度按 evidence/freshness 的判定分桶，不是数一数有几条。"""
    now = datetime(2026, 9, 20, 12, tzinfo=UTC)
    _, item = await _framework_with_item(db_session)
    control = await _mapped_control(db_session, item)
    evidence_type = EvidenceType(
        name_zh="复核报表", name_en="Review report", cadence=EvidenceCadence.QUARTERLY
    )
    db_session.add(evidence_type)
    await db_session.flush()
    db_session.add_all(
        [
            EvidenceItem(
                evidence_type_id=evidence_type.id,
                control_id=control.id,
                title="2026 Q3 复核",
                status=EvidenceStatus.COLLECTED,
                last_collected_at=now - timedelta(days=10),
                valid_until=now + timedelta(days=30),
            ),
            EvidenceItem(
                evidence_type_id=evidence_type.id,
                control_id=control.id,
                title="2026 Q1 复核",
                status=EvidenceStatus.COLLECTED,
                last_collected_at=now - timedelta(days=200),
                valid_until=now - timedelta(days=1),
            ),
            EvidenceItem(
                evidence_type_id=evidence_type.id,
                control_id=control.id,
                title="2026 Q4 复核（待收）",
                status=EvidenceStatus.PLANNED,
                valid_until=None,
            ),
        ]
    )
    await db_session.flush()

    inputs = await build_inputs(db_session, framework_id=item.framework_id, now=now)

    assert inputs.evidence_available is True
    counts = inputs.items[0].controls[0].evidence
    assert counts is not None
    assert (counts.valid, counts.expired, counts.other) == (1, 1, 1)


# ── 提示词 ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_absent_evidence_is_stated_in_the_prompt_not_silently_dropped(db_session):
    """"这路输入没有数据"必须写进提示词。

    只在数据结构里把 evidence 设成 None、渲染时跳过，模型看到的就是一份
    "没提证据"的输入——它会按自己的先验去猜，而不是知道该在理由里说明。
    """
    from app.maturity.prompts import render_items

    _, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)

    inputs = await build_inputs(db_session, framework_id=item.framework_id)
    rendered = render_items(inputs)

    assert "evidence: not tracked" in rendered


@pytest.mark.asyncio
async def test_unmapped_item_is_rendered_as_explicitly_unmapped(db_session):
    """一个框架项一条控制点都没映射上，是最该报出来的情形，不能渲染成空白。"""
    from app.maturity.prompts import render_items

    _, item = await _framework_with_item(db_session, code="GV.OC-03")

    rendered = render_items(await build_inputs(db_session, framework_id=item.framework_id))

    assert "no internal control is mapped to this item" in rendered


# ── 落分 ──────────────────────────────────────────────────────


async def _assessment(session, framework: Framework):
    from app.maturity.models import AssessmentStatus, MaturityAssessment

    row = MaturityAssessment(
        framework_id=framework.id,
        name="2026 基线",
        as_of_date=datetime(2026, 9, 20, tzinfo=UTC).date(),
        status=AssessmentStatus.DRAFT,
    )
    session.add(row)
    await session.flush()
    return row


def _fake_run(suggestions: list[dict]):
    """替掉 llm.runner.run，只验落分逻辑，不打真的上游。"""
    from unittest.mock import AsyncMock

    from app.llm.runner import ValidatedResult

    return AsyncMock(
        return_value=ValidatedResult(
            payload={"suggestions": suggestions}, confidence=0.9, llm_call_id=None
        )
    )


@pytest.mark.asyncio
async def test_ai_suggestion_never_overwrites_a_human_score(db_session):
    """人工打过的分是人的判断，AI 不许改写它。

    这是这个任务最要紧的一条：成熟度分会进审计准备包，被 AI 悄悄改掉一个
    人工定过的分，报告里就出现了没人负责的数字。
    """
    from unittest.mock import patch

    from app.maturity.models import MaturityScore, ScoreSource
    from app.maturity.tasks import suggest_scores

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)
    db_session.add(
        MaturityScore(
            assessment_id=assessment.id,
            framework_item_id=item.id,
            doc_score=4,
            impl_score=4,
            doc_rationale="人工定的",
            impl_rationale="人工定的",
            doc_score_source=ScoreSource.HUMAN,
            impl_score_source=ScoreSource.HUMAN,
        )
    )
    await db_session.flush()

    fake = _fake_run(
        [
            {
                "framework_item_id": item.id,
                "doc_score": 1,
                "impl_score": 0,
                "doc_rationale": "AI 想改成 1",
                "impl_rationale": "AI 想改成 0",
            }
        ]
    )
    with patch("app.maturity.tasks.run", fake):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    row = await db_session.scalar(
        select(MaturityScore).where(MaturityScore.framework_item_id == item.id)
    )
    assert (row.doc_score, row.impl_score) == (4, 4)
    assert row.doc_score_source is ScoreSource.HUMAN
    assert summary["skipped_human"] == 1
    assert summary["written"] == 0


@pytest.mark.asyncio
async def test_ai_score_lands_with_its_source_marked_ai(db_session):
    """落分要标来源。summary 里同时报出证据那路在不在，审核时才知道这分怎么来的。"""
    from unittest.mock import patch

    from app.maturity.models import MaturityScore, ScoreSource
    from app.maturity.tasks import suggest_scores

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)

    fake = _fake_run(
        [
            {
                "framework_item_id": item.id,
                "doc_score": 3,
                "impl_score": 1,
                "doc_rationale": "制度写到了季度复核",
                "impl_rationale": "实施只到 partial，且证据未登记",
            }
        ]
    )
    with patch("app.maturity.tasks.run", fake):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    row = await db_session.scalar(
        select(MaturityScore).where(MaturityScore.framework_item_id == item.id)
    )
    assert (row.doc_score, row.impl_score) == (3, 1)
    assert row.doc_score_source is ScoreSource.AI
    assert row.impl_score_source is ScoreSource.AI
    assert summary["written"] == 1
    assert summary["evidence_available"] is False


@pytest.mark.asyncio
async def test_a_framework_item_id_the_model_invented_is_dropped(db_session):
    """模型回一个不在这批里的 id，不能凭空建一行分。"""
    from unittest.mock import patch

    from app.maturity.models import MaturityScore
    from app.maturity.tasks import suggest_scores

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)

    fake = _fake_run(
        [
            {
                "framework_item_id": item.id + 9999,
                "doc_score": 4,
                "impl_score": 4,
                "doc_rationale": "凭空来的",
                "impl_rationale": "凭空来的",
            }
        ]
    )
    with patch("app.maturity.tasks.run", fake):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    assert summary["unknown_items"] == 1
    assert summary["written"] == 0
    assert (await db_session.scalars(select(MaturityScore))).all() == []


@pytest.mark.asyncio
async def test_parent_items_are_not_sent_for_scoring(db_session):
    """只在末级项上打分——和人工那条路同一条规矩（router 拒绝非末级项）。"""
    from unittest.mock import patch

    from app.maturity.tasks import suggest_scores

    framework, parent = await _framework_with_item(db_session, code="PR.AA")
    child = FrameworkItem(
        framework_id=framework.id,
        parent_id=parent.id,
        code="PR.AA-05",
        title="Leaf",
        description="",
        level=2,
        order_index=2,
    )
    db_session.add(child)
    await db_session.flush()
    assessment = await _assessment(db_session, framework)

    fake = _fake_run([])
    with patch("app.maturity.tasks.run", fake):
        await suggest_scores(db_session, assessment_id=assessment.id)

    sent_prompt = fake.await_args.kwargs["prompt"]
    assert f"[{child.id}]" in sent_prompt
    assert f"[{parent.id}]" not in sent_prompt


# ── 端点 ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_suggest_endpoint_records_an_audit_entry(client, db_session):
    """铁律二：投递本身也是一次要留痕的动作。

    落分的审计记在任务里（suggest_maturity_scores），因为分是在那里写的；
    这条管的是请求侧：谁、什么时候、对哪个评估发起了一轮 AI 打分。
    """
    from unittest.mock import AsyncMock, patch

    from app.iam.models import AuditLog
    from app.iam.models import User as UserModel
    from app.iam.permissions import Role
    from app.iam.security import create_access_token, hash_password

    lead = UserModel(
        email="lead@suggest.test",
        name="lead",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw"),
    )
    db_session.add(lead)
    await db_session.flush()
    headers = {"Authorization": f"Bearer {create_access_token(lead.id, lead.role)}"}

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)
    await db_session.commit()

    fake = _fake_run(
        [
            {
                "framework_item_id": item.id,
                "doc_score": 2,
                "impl_score": 1,
                "doc_rationale": "制度有，颗粒度一般",
                "impl_rationale": "实施 partial",
            }
        ]
    )
    with (
        patch("app.maturity.tasks.run", fake),
        patch("app.maturity.router.enqueue", AsyncMock(return_value="job-1")),
    ):
        resp = await client.post(
            f"/api/maturity/assessments/{assessment.id}/suggest", headers=headers
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"job_id": "job-1"}

    entries = (
        await db_session.scalars(
            select(AuditLog).where(AuditLog.action == "maturity_score.suggest_enqueue")
        )
    ).all()
    assert len(entries) == 1
    # AuditLog.entity_id 是字符串（按 id 记实体，不删行——CLAUDE.md 铁律二）
    assert entries[0].entity_id == str(assessment.id)


@pytest.mark.asyncio
async def test_contributor_cannot_trigger_suggestions(client, db_session):
    from app.iam.models import User as UserModel
    from app.iam.permissions import Role
    from app.iam.security import create_access_token, hash_password

    user = UserModel(
        email="contrib@suggest.test",
        name="c",
        role=Role.CONTRIBUTOR,
        password_hash=hash_password("pw"),
    )
    db_session.add(user)
    await db_session.flush()
    framework, _ = await _framework_with_item(db_session)
    assessment = await _assessment(db_session, framework)
    await db_session.commit()

    resp = await client.post(
        f"/api/maturity/assessments/{assessment.id}/suggest",
        headers={"Authorization": f"Bearer {create_access_token(user.id, user.role)}"},
    )
    assert resp.status_code == 403


# ── 注册表 ────────────────────────────────────────────────────


def test_maturity_suggestion_is_registered_as_implemented_and_bulk_assignable():
    """实现完了就得把标记翻过来，否则配置页继续说"未实现"、批量绑定继续跳过它。

    这条同时钉住反向：`implemented` 不是装饰，它决定"一键应用到全部推理任务"
    覆不覆盖这个任务（llm/tasks.py）。
    """
    from app.llm.tasks import TASK_SPECS, bulk_assignable_keys

    spec = next(s for s in TASK_SPECS if s.key == "maturity_suggestion")
    assert spec.implemented is True
    assert spec.capability == "chat"
    assert "maturity_suggestion" in bulk_assignable_keys("chat")
    assert "maturity_suggestion" not in bulk_assignable_keys("embedding")


@pytest.mark.asyncio
async def test_suggest_endpoint_enqueues_instead_of_calling_the_model(client, db_session):
    """suggest 必须进队列，不能在 HTTP 请求里跑完整轮模型调用。

    800-53 是 1034 条末级项，MAX_SUGGESTIONS=20 → 一个请求 52 次串行调用。
    worker.py 写着这类逐批任务「全都远超 15 分钟」，所以抽取/映射/冲突检测
    全是 LONG_JOB_TIMEOUT 的队列任务。这条断言的是同形：返回 job_id，
    且请求期间一次模型都不调——半途超时会让已落的分随 commit 一起回滚，
    而 token 已经花掉了。
    """
    from unittest.mock import AsyncMock, patch

    from app.iam.models import User as UserModel
    from app.iam.permissions import Role
    from app.iam.security import create_access_token, hash_password

    lead = UserModel(
        email="lead@enqueue.test",
        name="lead",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw"),
    )
    db_session.add(lead)
    await db_session.flush()
    headers = {"Authorization": f"Bearer {create_access_token(lead.id, lead.role)}"}

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)
    await db_session.commit()

    enqueue = AsyncMock(return_value="job-maturity-1")
    never_called = _fake_run([])
    with (
        patch("app.maturity.router.enqueue", enqueue),
        patch("app.maturity.tasks.run", never_called) as model,
    ):
        resp = await client.post(
            f"/api/maturity/assessments/{assessment.id}/suggest", headers=headers
        )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"job_id": "job-maturity-1"}
    enqueue.assert_awaited_once_with("suggest_maturity_scores", assessment.id, lead.id)
    assert model.call_count == 0, "请求期间不该调用模型"


@pytest.mark.asyncio
async def test_ai_overwrite_leaves_the_old_scores_in_the_audit_log(db_session):
    """铁律二：AI 改写一条已有的 AI 分，旧分必须留得下来。

    这张表的设计是「AI 直接落分、标明来源、人工可随时改写」，所以重跑一轮就会
    就地改写上一轮的分。没有逐行审计的话，上一轮打了多少、理由是什么，全部消失，
    而这些分要进审计准备包。同时 scored_at 只有 server_default、没有 onupdate，
    改写后仍报第一次的时间——报告里的「打分时间」会是错的。
    """
    from unittest.mock import patch

    from app.iam.models import AuditLog
    from app.iam.models import User as UserModel
    from app.iam.permissions import Role
    from app.iam.security import hash_password
    from app.maturity.models import MaturityScore
    from app.maturity.tasks import suggest_scores

    actor = UserModel(email="lead@overwrite.test", name="lead", role=Role.GRC_LEAD,
                      password_hash=hash_password("pw"))
    db_session.add(actor)
    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)
    await db_session.flush()

    first = _fake_run([{  # 第一轮
        "framework_item_id": item.id, "doc_score": 1, "impl_score": 1,
        "doc_rationale": "初评：制度缺口大", "impl_rationale": "初评：几乎没落地",
    }])
    with patch("app.maturity.tasks.run", first):
        await suggest_scores(db_session, assessment_id=assessment.id, actor=actor)
    await db_session.flush()
    row = (await db_session.scalars(select(MaturityScore).where(
        MaturityScore.assessment_id == assessment.id))).one()
    first_scored_at = row.scored_at

    second = _fake_run([{  # 第二轮改写
        "framework_item_id": item.id, "doc_score": 3, "impl_score": 2,
        "doc_rationale": "复评：制度补齐", "impl_rationale": "复评：多数资产已落地",
    }])
    with patch("app.maturity.tasks.run", second):
        await suggest_scores(db_session, assessment_id=assessment.id, actor=actor)
    await db_session.flush()
    await db_session.refresh(row)

    assert (row.doc_score, row.impl_score) == (3, 2)
    assert row.scored_at > first_scored_at, "改写后 scored_at 必须前进"

    entries = (await db_session.scalars(select(AuditLog).where(
        AuditLog.action == "maturity_score.ai_overwrite"))).all()
    assert len(entries) == 1, "每改写一行留一条"
    assert entries[0].before["doc_score"] == 1
    assert entries[0].before["doc_rationale"] == "初评：制度缺口大"
    assert entries[0].after["doc_score"] == 3


@pytest.mark.asyncio
async def test_a_suggestion_from_another_batch_is_rejected(db_session):
    """守卫要挡的是「不在这批里」，不是「不在这个框架里」。

    末级项按 MAX_SUGGESTIONS 分批，每批只把自己那几条渲进提示词。模型若回了
    上一批的 id，它是拿这一批的上下文在给另一条打分——分会落错行，而
    unknown_items 还是 0，没人看得见。控制点原文是逐字渲进提示词的，所以这
    同时是注入面：一段被注入的 `[<别的 id>] …` 能把分引到无关的框架项上。
    """
    from unittest.mock import AsyncMock, patch

    from app.llm.runner import ValidatedResult
    from app.maturity.models import MaturityScore
    from app.maturity.tasks import suggest_scores

    framework, first_item = await _framework_with_item(db_session, code="PR.AA-05")
    await _mapped_control(db_session, first_item)
    second_item = FrameworkItem(
        framework_id=framework.id, code="PR.AA-06", title="第二条末级项", parent_id=None
    )
    db_session.add(second_item)
    await db_session.flush()
    await _mapped_control(db_session, second_item, code="AC-02")
    assessment = await _assessment(db_session, framework)
    await db_session.flush()

    # 每批一条 → 两批。两批都回「第二条」的 id：第一批那次是跨批的，该被拒。
    run = AsyncMock(return_value=ValidatedResult(
        payload={"suggestions": [{
            "framework_item_id": second_item.id, "doc_score": 3, "impl_score": 3,
            "doc_rationale": "越批打的分", "impl_rationale": "越批打的分",
        }]}, confidence=0.9, llm_call_id=None))

    with (
        patch("app.maturity.tasks.run", run),
        patch("app.maturity.tasks.MAX_SUGGESTIONS", 1),
    ):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)
    await db_session.flush()

    assert summary["unknown_items"] == 1, "第一批回的是第二批的 id，必须计入 unknown_items"
    assert summary["written"] == 1, "只有第二批那次是合法的"
    rows = (await db_session.scalars(select(MaturityScore).where(
        MaturityScore.assessment_id == assessment.id))).all()
    assert len(rows) == 1 and rows[0].framework_item_id == second_item.id


@pytest.mark.asyncio
async def test_several_implementations_collapse_to_the_most_conservative_one(db_session):
    """一个控制点在多个技术资产上各有一条实施记录，喂给模型的不能是任意一条。

    `implementations` 的唯一约束是 (control_id, tech_asset_id)——一个控制点每个
    资产一行是正常形态。按 control_id 收进字典会留下 SELECT 恰好最后返回的那条，
    而那条查询没有 ORDER BY：同一份数据两次跑可以喂进不同的状态，分就跟着变。
    取最保守的那条，并把分布告诉模型——成熟度评估要看的正是「还有哪个资产没落地」。
    """
    from app.environment.models import (
        TechAsset,
        TechAssetCategory,
        TechAssetEnvironment,
        TechAssetStatus,
    )

    framework, item = await _framework_with_item(db_session)
    control = await _mapped_control(db_session, item)
    assets = []
    for name in ("堡垒机", "域控"):
        asset = TechAsset(name=name, category=TechAssetCategory.PAM, vendor="",
                          environment=TechAssetEnvironment.PROD,
                          status=TechAssetStatus.ACTIVE)
        db_session.add(asset)
        assets.append(asset)
    await db_session.flush()
    for asset_id, status in ((None, ImplementationStatus.IMPLEMENTED),
                             (assets[0].id, ImplementationStatus.IMPLEMENTED),
                             (assets[1].id, ImplementationStatus.PLANNED)):
        db_session.add(
            Implementation(
                control_id=control.id,
                tech_asset_id=asset_id,
                description=f"资产 {asset_id}",
                how_enforced=HowEnforced.MANUAL
                if status is ImplementationStatus.PLANNED
                else HowEnforced.AUTOMATED,
                status=status,
            )
        )
    await db_session.flush()

    inputs = await build_inputs(db_session, framework_id=framework.id)
    got = inputs.items[0].controls[0]

    assert got.implementation_status == "planned", "有一个资产还没落地就不能报 implemented"
    assert got.how_enforced == "manual", "how_enforced 要跟着被选中的那条走"
    assert got.implementation_tally == {"implemented": 2, "planned": 1}


def test_the_prompt_tells_the_model_how_the_assets_are_distributed():
    """取最保守的那条会掩盖分布：5 个资产 4 个已落地，只说 planned 就是误导。

    模型看到的必须既有「按最差算」的结论，也有「差在哪几个」的事实，
    否则它没法在理由里说清楚扣分扣在哪。
    """
    from types import SimpleNamespace

    from app.maturity.prompts import _render_control

    rendered = _render_control(
        SimpleNamespace(
            control_code="AC-1", title="访问控制", mapping_strength="full",
            implementation_status="planned", how_enforced="manual",
            implementation_tally={"implemented": 4, "planned": 1},
            evidence=None,
        )
    )
    assert "planned" in rendered
    assert "4 implemented" in rendered and "1 planned" in rendered


def _run_that_meanwhile(session, sql: str, params: dict, suggestions: list[dict]):
    """模型调用期间，「别的事务」改了库：用裸 SQL 绕过本会话的身份缓存来模拟。"""
    from sqlalchemy import text as sql_text

    from app.llm.runner import ValidatedResult

    async def run(*_args, **_kwargs):
        await session.execute(sql_text(sql), params)
        return ValidatedResult(payload={"suggestions": suggestions}, confidence=0.9, llm_call_id=None)

    return run


@pytest.mark.asyncio
async def test_a_human_score_saved_during_the_run_is_not_overwritten(db_session):
    # 一轮 suggest 要几分钟、原来整轮一个事务，「是不是人工分」在开头拍一次快照。
    # 跑的期间有人手动改了这一项：AI 最后提交时按旧快照判断成 AI 分，把人工分覆盖掉。
    from unittest.mock import patch

    from app.maturity.models import MaturityScore, ScoreSource
    from app.maturity.tasks import suggest_scores

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)
    row = MaturityScore(assessment_id=assessment.id, framework_item_id=item.id, doc_score=1, impl_score=1,
                        doc_rationale="old AI", impl_rationale="old AI",
                        doc_score_source=ScoreSource.AI, impl_score_source=ScoreSource.AI)
    db_session.add(row)
    await db_session.flush()

    run = _run_that_meanwhile(
        db_session,
        "UPDATE maturity_scores SET doc_score = 4, impl_score = 4, doc_score_source = 'human', "
        "impl_score_source = 'human' WHERE id = :id",
        {"id": row.id},
        [{"framework_item_id": item.id, "doc_score": 2, "impl_score": 2,
          "doc_rationale": "AI", "impl_rationale": "AI"}],
    )
    with patch("app.maturity.tasks.run", run):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    await db_session.refresh(row)
    assert (row.doc_score, row.impl_score, row.doc_score_source) == (4, 4, ScoreSource.HUMAN)
    assert summary["skipped_human"] == 1 and summary["written"] == 0


@pytest.mark.asyncio
async def test_an_assessment_finalized_during_the_run_takes_no_more_ai_scores(db_session):
    # 开头检查过「没定稿」，跑的期间有人定了稿：原来最后照样把分写进已定稿的评估。
    from unittest.mock import patch

    from app.maturity.models import MaturityScore
    from app.maturity.tasks import suggest_scores

    framework, item = await _framework_with_item(db_session)
    await _mapped_control(db_session, item)
    assessment = await _assessment(db_session, framework)

    run = _run_that_meanwhile(
        db_session,
        "UPDATE maturity_assessments SET status = 'final' WHERE id = :id",
        {"id": assessment.id},
        [{"framework_item_id": item.id, "doc_score": 2, "impl_score": 2,
          "doc_rationale": "AI", "impl_rationale": "AI"}],
    )
    with patch("app.maturity.tasks.run", run):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    assert summary["stopped_finalized"] is True and summary["written"] == 0
    assert await db_session.scalar(
        select(MaturityScore).where(MaturityScore.assessment_id == assessment.id)
    ) is None


# ---- 中途出错（K7）：已提交的批次留着，这一轮停在哪、为什么停，要能从审计里看出来 ----


async def _two_leaves(session):
    framework, first = await _framework_with_item(session, code="PR.AA-01")
    second = FrameworkItem(
        framework_id=framework.id, code="PR.AA-02", title="Second item",
        description="Another leaf.", level=1, order_index=2,
    )
    session.add(second)
    await session.flush()
    return framework, first, second


def _scored(item: FrameworkItem):
    from app.llm.runner import ValidatedResult

    return ValidatedResult(
        payload={"suggestions": [{
            "framework_item_id": item.id, "doc_score": 2, "impl_score": 2,
            "doc_rationale": "AI", "impl_rationale": "AI",
        }]},
        confidence=0.9, llm_call_id=None,
    )


@pytest.mark.asyncio
async def test_a_rejected_batch_is_counted_and_the_next_batch_still_runs(db_session):
    # 原来校验失败直接冲出整轮：后面的批次不跑，失败那次调用的 llm_call（连同花费）
    # 随会话一起丢掉，预算因此少计。映射任务早就是逐批计数、继续。
    from unittest.mock import AsyncMock, patch

    from app.llm.validation import ValidationFailure
    from app.maturity.tasks import suggest_scores

    framework, _first, second = await _two_leaves(db_session)
    assessment = await _assessment(db_session, framework)
    run = AsyncMock(side_effect=[ValidationFailure("schema: bad"), _scored(second)])

    with patch("app.maturity.tasks.run", run), patch("app.maturity.tasks.MAX_SUGGESTIONS", 1):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    assert summary["rejected"] == 1
    assert summary["written"] == 1


@pytest.mark.asyncio
async def test_a_provider_failure_is_counted_apart_from_rejections(db_session):
    # 供应商故障不是质量信号：与 rejected 分开计，不让一次超时作废整轮。
    from unittest.mock import AsyncMock, patch

    from app.llm.providers.base import ProviderError
    from app.maturity.tasks import suggest_scores

    framework, _first, second = await _two_leaves(db_session)
    assessment = await _assessment(db_session, framework)
    run = AsyncMock(side_effect=[ProviderError("timeout", retryable=True), _scored(second)])

    with patch("app.maturity.tasks.run", run), patch("app.maturity.tasks.MAX_SUGGESTIONS", 1):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    assert summary["failed"] == 1
    assert summary["rejected"] == 0
    assert summary["written"] == 1


@pytest.mark.asyncio
async def test_budget_running_out_mid_run_stops_with_the_reason_in_the_summary(db_session):
    from unittest.mock import AsyncMock, patch

    from app.llm.budget import BudgetExceeded
    from app.maturity.models import MaturityScore
    from app.maturity.tasks import suggest_scores

    framework, first, _second = await _two_leaves(db_session)
    assessment = await _assessment(db_session, framework)
    run = AsyncMock(side_effect=[
        _scored(first), BudgetExceeded("exhausted", code="budget.exhausted"),
    ])

    with patch("app.maturity.tasks.run", run), patch("app.maturity.tasks.MAX_SUGGESTIONS", 1):
        summary = await suggest_scores(db_session, assessment_id=assessment.id)

    assert summary["stopped_by"] == "budget.exhausted"
    assert summary["written"] == 1
    kept = await db_session.scalar(
        select(MaturityScore).where(MaturityScore.framework_item_id == first.id)
    )
    assert kept is not None


@pytest.mark.asyncio
async def test_a_run_stopped_midway_still_leaves_its_summary_in_the_audit_log(db_session):
    from unittest.mock import AsyncMock, patch

    from app.iam.models import AuditLog, User
    from app.iam.permissions import Role
    from app.iam.security import hash_password
    from app.llm.budget import BudgetExceeded
    from app.maturity.tasks import suggest_maturity_scores

    lead = User(email="lead@k7.test", name="lead", role=Role.GRC_LEAD, password_hash=hash_password("pw"))
    db_session.add(lead)
    framework, first, _second = await _two_leaves(db_session)
    assessment = await _assessment(db_session, framework)
    run = AsyncMock(side_effect=[
        _scored(first), BudgetExceeded("exhausted", code="budget.exhausted"),
    ])

    with (
        patch("app.maturity.tasks.session_factory") as factory,
        patch("app.maturity.tasks.run", run),
        patch("app.maturity.tasks.MAX_SUGGESTIONS", 1),
    ):
        factory.return_value.__aenter__.return_value = db_session
        await suggest_maturity_scores({}, assessment.id, lead.id)

    entry = await db_session.scalar(
        select(AuditLog).where(AuditLog.action == "maturity_score.suggest")
    )
    assert entry is not None
    assert entry.after["stopped_by"] == "budget.exhausted"
    assert entry.after["written"] == 1
