"""确认队列列表页的查询预算：往返次数不得随提案条数增长。

M5 留下的已知欠账——「`GET /api/proposals` 的 N+1（映射提案每条 3 次查询）」。
412 条待审映射提案下那是 1200 多次往返，而这一页正是要被人反复翻的那一页。

**这个测试量得到什么、量不到什么。** 测试夹具让 API 与建数据共用同一个
session，所以框架项与控制点在身份映射里，旧代码的 `session.get()` 在这里
本来就不会发查询——真实请求里 session 是全新的，那三次里的两次才会真的
往返。因此本测试可靠覆盖的是 OCR 标记那一路（`session.scalar`，从不走缓存）。
改回逐条查时它确实会失败：23 条 29 次 / 3 条 9 次。
"""

from typing import Self

import pytest
from sqlalchemy import event, select
from sqlalchemy.engine import Engine

from app.controls.models import Control
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.review.models import Proposal, ProposalKind, ProposalStatus


class _Counter:
    def __init__(self) -> None:
        self.n = 0

    def __call__(self, conn, cursor, statement, parameters, context, executemany) -> None:
        self.n += 1

    def __enter__(self) -> Self:
        event.listen(Engine, "before_cursor_execute", self)
        return self

    def __exit__(self, *exc) -> None:
        event.remove(Engine, "before_cursor_execute", self)


async def _framework(db_session, how_many: int, tag: str) -> list[FrameworkItem]:
    framework = Framework(key=f"f-{tag}", name_zh="框架", name_en="Framework", version="1")
    db_session.add(framework)
    await db_session.flush()
    items = [
        FrameworkItem(framework_id=framework.id, code=f"AC-{tag}-{n}", title=f"Item {n}",
                      description=f"Requirement text {n}.", level=1, order_index=n)
        for n in range(how_many)
    ]
    db_session.add_all(items)
    await db_session.flush()
    return items


async def _mapping_proposals(db_session, how_many: int, tag: str = "a") -> None:
    """每条提案都指向**不同**的控制点与框架项——同一个会被身份映射缓存掉，测不出 N+1。"""
    items = await _framework(db_session, how_many, tag)
    controls = [
        Control(code=f"C-{tag}-{n:04d}", title=f"Control {n}", statement=f"Statement {n}.")
        for n in range(how_many)
    ]
    db_session.add_all(controls)
    await db_session.flush()
    db_session.add_all([
        Proposal(
            kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING, confidence=0.7,
            payload={
                "control_id": control.id, "framework_item_id": item.id,
                "strength": "partial", "framework_item_quote": f"Requirement text {n}.",
                "rationale": "because", "confidence": 0.7,
            },
            citations=[],
        )
        for n, (control, item) in enumerate(zip(controls, items, strict=True))
    ])
    await db_session.flush()


async def _auth(client, db_session) -> dict[str, str]:
    db_session.add(User(email="lead@example.com", name="Lead", role=Role.GRC_LEAD,
                        password_hash=hash_password("pw123456")))
    await db_session.flush()
    resp = await client.post(
        "/api/auth/login", json={"email": "lead@example.com", "password": "pw123456"}
    )
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


@pytest.mark.asyncio
async def test_listing_cost_does_not_grow_with_the_number_of_proposals(client, db_session):
    headers = await _auth(client, db_session)

    await _mapping_proposals(db_session, 3, tag="few")
    with _Counter() as few:
        small = await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)
    assert len(small.json()) == 3

    await _mapping_proposals(db_session, 20, tag="many")
    with _Counter() as many:
        large = await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)
    assert len(large.json()) == 23

    assert many.n == few.n, (
        f"23 条提案用了 {many.n} 次查询，3 条用了 {few.n} 次——查询数随条数增长即为 N+1"
    )
    print(f"\n列表页固定 {many.n} 次查询（3 条与 23 条相同）")


@pytest.mark.asyncio
async def test_the_listing_still_carries_full_mapping_context(client, db_session):
    """批量取数不能把旁路数据取丢了。"""
    headers = await _auth(client, db_session)
    await _mapping_proposals(db_session, 4)

    body = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    assert len(body) == 4
    for row in body:
        context = row["mapping_context"]
        assert context["framework_item"]["code"].startswith("AC-")
        assert context["control"]["code"].startswith("C-")
        assert context["control"]["statement"]
        assert row["bulk_acceptable"] is False      # 映射提案永远不可批量接受
        assert row["ocr_quality_flag"] is False


@pytest.mark.asyncio
async def test_a_proposal_says_whether_its_item_is_already_covered(client, db_session):
    """审 supporting 时最要紧的上下文：目标项是不是已经被关掉了。

    412 条待审映射里 119 条是 supporting，其中 54 条指向的框架项已被 full/partial
    覆盖——那些项根本不在差距清单上，has_supporting 永远不会被读到，确认它们
    产生零信息。卡片上看得见这件事，就不必靠审阅顺序去躲。
    """
    headers = await _auth(client, db_session)
    await _mapping_proposals(db_session, 2, tag="cov")
    items = list(await db_session.scalars(select(FrameworkItem).order_by(FrameworkItem.id)))
    covering = Control(code="C-COVER", title="Covering", statement="Covers it.")
    db_session.add(covering)
    await db_session.flush()
    db_session.add(Mapping(
        control_id=covering.id, framework_item_id=items[0].id,
        strength=MappingStrength.PARTIAL, rationale="r", quote="q",
    ))
    await db_session.flush()

    body = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    by_item = {row["mapping_context"]["framework_item"]["id"]: row for row in body}

    closed = by_item[items[0].id]["mapping_context"]["item_coverage"]
    assert closed["closed"] is True
    assert closed["confirmed"] == [{"control_code": "C-COVER", "strength": "partial"}]

    open_item = by_item[items[1].id]["mapping_context"]["item_coverage"]
    assert open_item["closed"] is False and open_item["confirmed"] == []


@pytest.mark.asyncio
async def test_only_full_and_partial_count_as_covered(db_session, client):
    """supporting 不消差距，所以已确认的 supporting 也不能把 closed 置真。"""
    headers = await _auth(client, db_session)
    await _mapping_proposals(db_session, 1, tag="sup")
    item = await db_session.scalar(select(FrameworkItem))
    other = Control(code="C-SUP", title="Supporting", statement="Enables it.")
    db_session.add(other)
    await db_session.flush()
    db_session.add(Mapping(
        control_id=other.id, framework_item_id=item.id,
        strength=MappingStrength.SUPPORTING, rationale="r", quote="q",
    ))
    await db_session.flush()

    body = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    coverage = body[0]["mapping_context"]["item_coverage"]
    assert coverage["closed"] is False, "supporting 不消差距，不能算已覆盖"
    assert coverage["confirmed"] == [{"control_code": "C-SUP", "strength": "supporting"}]


async def _sourced_control(db_session, code: str) -> Control:
    """一条有出处的控制点：文档 → 条款 → ControlSource → 控制点。"""
    from app.clauses.models import Clause
    from app.controls.models import ControlSource, SourceRelation
    from app.ingest.models import DocStatus, DocType, Document

    document = Document(title="Acme 日志管理程序 v1.2", doc_type=DocType.PROCEDURE,
                        file_hash=code.ljust(64, "0"), file_path=f"/{code}.pdf",
                        original_filename=f"{code}.pdf", status=DocStatus.ACTIVE)
    db_session.add(document)
    await db_session.flush()
    clause = Clause(document_id=document.id, number="3.1", heading="Objectives",
                    heading_path="Service Monitoring › Objectives", citation_label="3.1",
                    text="body", order_index=1, level=2)
    control = Control(code=code, title="Log retention", statement="Logs must be retained.")
    db_session.add_all([clause, control])
    await db_session.flush()
    db_session.add(ControlSource(control_id=control.id, clause_id=clause.id,
                                 relation=SourceRelation.DEFINES))
    await db_session.flush()
    return control


@pytest.mark.asyncio
async def test_a_mapping_proposal_carries_the_controls_source_documents(client, db_session):
    """控制点是抽取产物，不是原文。

    判断「这条控制点满不满足某个框架要求」的前提，是先确认它如实反映了原文——
    OQ-5 记着有 28 条已接受的控制点建立在残缺文本上，卡片上看不到出处就发现不了。
    """
    headers = await _auth(client, db_session)
    items = await _framework(db_session, 1, "src")
    control = await _sourced_control(db_session, "C-SRC")
    db_session.add(Proposal(
        kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING, confidence=0.7,
        payload={"control_id": control.id, "framework_item_id": items[0].id,
                 "strength": "partial", "framework_item_quote": "Requirement text 0.",
                 "rationale": "because", "confidence": 0.7},
        citations=[],
    ))
    await db_session.flush()

    body = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    sources = body[0]["mapping_context"]["control"]["sources"]

    assert len(sources) == 1
    assert sources[0]["document_title"] == "Acme 日志管理程序 v1.2"
    assert sources[0]["citation_label"] == "3.1"
    assert sources[0]["heading_path"] == "Service Monitoring › Objectives"
    assert sources[0]["document_id"] and sources[0]["clause_id"]


@pytest.mark.asyncio
async def test_a_control_with_no_recorded_source_reports_an_empty_list(client, db_session):
    headers = await _auth(client, db_session)
    await _mapping_proposals(db_session, 1, tag="nosrc")
    body = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    assert body[0]["mapping_context"]["control"]["sources"] == []


async def _mapping_in(db_session, fw_key: str, strength: str, rationale: str) -> None:
    from app.frameworks.models import Framework

    framework = Framework(key=fw_key, name_zh=fw_key, name_en=fw_key, version="1")
    db_session.add(framework)
    await db_session.flush()
    item = FrameworkItem(framework_id=framework.id, code=f"{fw_key}-1", title="t",
                         description="Requirement text.", level=1, order_index=1)
    control = Control(code=f"C-{fw_key}", title="c", statement="s")
    db_session.add_all([item, control])
    await db_session.flush()
    db_session.add(Proposal(
        kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING, confidence=0.7,
        payload={"control_id": control.id, "framework_item_id": item.id, "strength": strength,
                 "framework_item_quote": "Requirement text.", "rationale": rationale,
                 "confidence": 0.7},
        citations=[]))
    await db_session.flush()


@pytest.mark.asyncio
async def test_filtering_by_framework_narrows_the_queue(client, db_session):
    """CSF 106 个要求项 vs 800-53 的 1014 个，审阅回报差一个量级——
    能只看一个框架，是把 400 多条切成可做的量最有效的一刀。"""
    headers = await _auth(client, db_session)
    await _mapping_in(db_session, "csf", "partial", "covers it")
    await _mapping_in(db_session, "sp800", "partial", "covers it")

    both = (await client.get("/api/proposals?kind=mapping&limit=200", headers=headers)).json()
    only = (await client.get("/api/proposals?kind=mapping&framework=csf&limit=200",
                             headers=headers)).json()

    assert len(both) == 2
    assert len(only) == 1
    assert only[0]["mapping_context"]["framework_item"]["code"] == "csf-1"


@pytest.mark.asyncio
async def test_filtering_by_doubtful_rationale(client, db_session):
    """模型自己写了「没有具体覆盖」却仍标 full/partial 的那批，最可能标错。"""
    headers = await _auth(client, db_session)
    await _mapping_in(db_session, "a", "partial", "The control fully covers this requirement.")
    await _mapping_in(db_session, "b", "partial",
                      "Related, but does not specifically address the scope element.")

    rows = (await client.get(
        "/api/proposals?kind=mapping&doubtful_rationale=true&limit=200", headers=headers)).json()

    assert len(rows) == 1
    assert "does not specifically address" in rows[0]["payload"]["rationale"]
