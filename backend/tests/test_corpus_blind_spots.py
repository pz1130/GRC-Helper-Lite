"""语料盲区：你自己的制度里，有多少要求还没变成控制点。

与框架覆盖度方向相反——那个问「外部要求有没有被满足」，这个问「我们写下的要求
有没有被系统看见」。两种盲区都是静默的：不扫就没有任何地方会提示。

实测撞到过：6 份文档里 2 份从未产生任何抽取提案，而界面上毫无迹象。
"""

import pytest

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource, SourceRelation
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.coverage import document_coverage, uncovered_clauses
from app.ingest.models import DocStatus, DocType, Document
from app.review.models import Proposal, ProposalKind, ProposalStatus


async def _document(db_session, title: str, clauses: list[str]) -> Document:
    document = Document(title=title, doc_type=DocType.PROCEDURE,
                        file_hash=title.ljust(64, "0")[:64], file_path=f"/{title}.pdf",
                        original_filename=f"{title}.pdf", status=DocStatus.ACTIVE)
    db_session.add(document)
    await db_session.flush()
    for index, text in enumerate(clauses):
        db_session.add(Clause(document_id=document.id, number=str(index), heading="H",
                              heading_path=f"S › {index}", citation_label=str(index),
                              text=text, order_index=index, level=2))
    await db_session.flush()
    return document


NORM = ("Access to production systems must be approved by the service owner "
        "before the change window opens.")
PLAIN = ("This section describes the layout of the document and lists the "
         "annexes that accompany it for reference purposes only.")


async def _control_from(db_session, clause_text_index: int, document: Document) -> None:
    from sqlalchemy import select

    clause = await db_session.scalar(
        select(Clause).where(Clause.document_id == document.id,
                             Clause.order_index == clause_text_index))
    control = Control(code=f"C-{document.id}-{clause_text_index}", title="t", statement="s")
    db_session.add(control)
    await db_session.flush()
    db_session.add(ControlSource(control_id=control.id, clause_id=clause.id,
                                 relation=SourceRelation.DEFINES))
    await db_session.flush()


@pytest.mark.asyncio
async def test_a_document_that_was_never_extracted_is_called_out(db_session):
    """不是「抽了但都被拒」，是根本没跑过——这两种要分得开。"""
    await _document(db_session, "never", [NORM, NORM])
    rows = {r.title: r for r in await document_coverage(db_session)}

    row = rows["never"]
    assert row.proposals == 0
    assert row.never_extracted is True
    assert row.normative_clauses == 2 and row.controls == 0


@pytest.mark.asyncio
async def test_extracted_but_all_rejected_is_not_the_same_as_never_extracted(db_session):
    document = await _document(db_session, "rejected", [NORM])
    db_session.add(Proposal(kind=ProposalKind.CONTROL_EXTRACT, status=ProposalStatus.REJECTED,
                            document_id=document.id, payload={"title": "t", "statement": "s"},
                            citations=[], confidence=0.5, reject_reason="不成立"))
    await db_session.flush()

    row = next(r for r in await document_coverage(db_session) if r.title == "rejected")
    assert row.proposals == 1
    assert row.never_extracted is False, "跑过但被拒，和从没跑过是两回事"
    assert row.controls == 0


@pytest.mark.asyncio
async def test_normative_clauses_without_a_control_are_counted(db_session):
    document = await _document(db_session, "partial", [NORM, NORM, PLAIN])
    await _control_from(db_session, 0, document)

    row = next(r for r in await document_coverage(db_session) if r.title == "partial")
    assert row.clauses == 3
    assert row.normative_clauses == 2, "PLAIN 不含情态词，不算规范条款"
    assert row.controls == 1
    assert row.uncovered_normative == 1


@pytest.mark.asyncio
async def test_uncovered_detail_lists_the_clauses_and_flags_obligations(db_session):
    document = await _document(db_session, "detail", [
        NORM,
        "Teams should review the dashboard weekly and record any anomalies found.",
    ])
    rows = await uncovered_clauses(db_session, document.id)

    assert [r.citation_label for r in rows] == ["0", "1"]
    assert rows[0].strong is True, "must 是义务"
    assert rows[1].strong is False, "should 是建议——漏一条义务和漏一条建议不是一回事"
    assert rows[0].heading_path


# 2026-09-30 实测（KNOWN_ISSUES K18）：只认 shall/must/should 会漏掉约五分之一的要求。
# 下面这些写法在开发集上几乎不带误报，所以收进来；祈使句、陈述句写的做法仍然认不出。
EXPANDED = [
    "The supervisor expects each team to review privileged access every quarter.",
    "The service owner is responsible for approving every change to production.",
    "Teams are encouraged to rehearse the recovery plan with their suppliers.",
    "Directors may not sit on more than two other boards at the same time.",
    "Principle 4: teams keep an inventory of every system they operate.",
    "A second approval is required before any payment above the threshold.",
]
STILL_PLAIN = [
    "This annex needs to be read together with the glossary at the end of the document.",
    "The framework was introduced to ensure a consistent view across all business lines.",
]


@pytest.mark.asyncio
async def test_requirements_without_shall_or_must_are_counted(db_session):
    await _document(db_session, "expanded", EXPANDED + STILL_PLAIN)
    row = next(r for r in await document_coverage(db_session) if r.title == "expanded")
    assert row.normative_clauses == len(EXPANDED), "needs to / to ensure 不算——开发集上误报多于命中"


@pytest.mark.asyncio
async def test_required_and_may_not_are_obligations_not_advice(db_session):
    document = await _document(db_session, "strength", [
        EXPANDED[3], EXPANDED[5], EXPANDED[0], EXPANDED[2],
    ])
    rows = await uncovered_clauses(db_session, document.id)
    assert [r.strong for r in rows] == [True, True, False, False], (
        "may not / is required 是义务；expects / encouraged 是期望与建议"
    )


@pytest.mark.asyncio
async def test_a_clause_backing_a_control_is_not_reported_as_uncovered(db_session):
    document = await _document(db_session, "covered", [NORM])
    await _control_from(db_session, 0, document)
    assert await uncovered_clauses(db_session, document.id) == []


@pytest.mark.asyncio
async def test_coverage_endpoint_is_readable_by_any_read_user(client, db_session):
    db_session.add(User(email="v@example.com", name="V", role=Role.VIEWER,
                        password_hash=hash_password("pw123456")))
    await _document(db_session, "api", [NORM])
    await db_session.flush()
    token = (await client.post("/api/auth/login",
                               json={"email": "v@example.com", "password": "pw123456"})
             ).json()["access_token"]

    body = (await client.get("/api/documents/coverage",
                             headers={"Authorization": f"Bearer {token}"})).json()
    row = next(r for r in body if r["title"] == "api")
    assert row["never_extracted"] is True and row["normative_clauses"] == 1


# ---- 结论强于原文（OQ-11）----


def test_a_description_upgraded_to_an_obligation_is_flagged():
    """闸 4 查不到这个：被改的情态动词在生成的 statement 里，不在引文里。"""
    from app.extraction.drift import upgraded_from

    assert upgraded_from(
        "The review board must meet every quarter.",
        ["The review board meets every quarter."],
    )


def test_a_faithful_restatement_is_not_flagged():
    from app.extraction.drift import upgraded_from

    assert not upgraded_from(
        "Changes must be approved by the CAB before implementation.",
        ["Every change must be approved by the CAB before implementation."],
    )
    assert not upgraded_from(
        "Teams should review the dashboard weekly.",
        ["Teams should review the dashboard weekly."],
    ), "结论没用 must/shall，谈不上升格"


def test_a_table_rendered_as_an_obligation_is_not_flagged_by_modal_alone():
    """SLA 表格不含情态词，义务由「这是一张 SLA 表」的语境承载。

    做成硬闸门会把它们全拒掉，所以原文侧从宽——
    只要有任何情态/义务的迹象就不算升格，宁可漏报也不要制造噪声。
    """
    from app.extraction.drift import upgraded_from

    # 表格确实会被标记（它真的没有情态词）——这是已知的误报，靠人判
    assert upgraded_from(
        "P1 incidents must be responded to within 10 minutes.",
        ["Priority Level | Response Time | Target Metric P1 | 10 minutes | 90%"],
    )
    # 但只要原文出现任何义务迹象就不再标记
    assert not upgraded_from(
        "P1 incidents must be responded to within 10 minutes.",
        ["The team is responsible for responding to P1 incidents within 10 minutes."],
    )


@pytest.mark.asyncio
async def test_the_review_card_carries_the_drift_flag(client, db_session):
    from sqlalchemy import select

    db_session.add(User(email="lead2@example.com", name="L", role=Role.GRC_LEAD,
                        password_hash=hash_password("pw123456")))
    document = await _document(db_session, "drift", [
        "The review board meets every quarter for change review.",
    ])
    clause = await db_session.scalar(
        select(Clause).where(Clause.document_id == document.id))
    db_session.add(Proposal(
        kind=ProposalKind.CONTROL_EXTRACT, status=ProposalStatus.PENDING,
        document_id=document.id, confidence=0.9,
        payload={"title": "Review board cadence",
                 "statement": "The review board must meet every quarter.",
                 "citations": [{"clause_id": clause.id, "quote": "The review board meets"}]},
        citations=[{"clause_id": clause.id, "quote": "The review board meets"}]))
    await db_session.flush()

    token = (await client.post("/api/auth/login",
                               json={"email": "lead2@example.com", "password": "pw123456"})
             ).json()["access_token"]
    body = (await client.get("/api/proposals?kind=control_extract&limit=200",
                             headers={"Authorization": f"Bearer {token}"})).json()

    row = next(r for r in body if r["payload"]["title"] == "Review board cadence")
    assert row["normative_drift"] is True
    assert all("_text" not in c for c in row["citations"]), "内部键不该泄到接口外"


@pytest.mark.asyncio
async def test_a_superseded_document_is_not_a_blind_spot(db_session):
    # 被新版替换的文档不再是现行要求：它的条款没有控制点不算盲区，
    # 否则每换一次版，旧版的全部条款都会冒出来当成「漏了」。
    old = await _document(db_session, "old", [NORM])
    old.status = DocStatus.SUPERSEDED
    await _document(db_session, "new", [NORM])
    await db_session.flush()

    titles = [r.title for r in await document_coverage(db_session)]
    assert "old" not in titles
    assert "new" in titles
