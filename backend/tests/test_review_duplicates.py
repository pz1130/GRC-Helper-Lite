"""正文逐字相同的控制点：在确认队列里摊开，由审核者当场选（OQ-8）。

`materialize` 一直在替审核者判重——按归一化标题相同就并进已有控制点，并且
丢掉新提案的 statement 只留引用。问题不在那把钥匙，在于它做得悄无声息：
statement 一字不差、标题被抽取器起成两个样子的两条，会各建一个控制点。
"""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from app.clauses.models import Clause
from app.controls.models import Control, ControlSource
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.ingest.models import DocStatus, DocType, Document
from app.review.materialize import normalise_statement
from app.review.models import Proposal, ProposalKind

STATEMENT = "Business continuity requirements must be documented before a change."


@pytest.fixture(autouse=True)
def embed_queue(monkeypatch):
    monkeypatch.setattr("app.review.router.enqueue", AsyncMock(return_value="embed-job"))


async def _lead(db_session, email: str = "lead@example.com") -> User:
    user = User(
        email=email, name=email, role=Role.GRC_LEAD, password_hash=hash_password("pw123456")
    )
    db_session.add(user)
    await db_session.flush()
    return user


async def _auth(client, email: str = "lead@example.com") -> dict[str, str]:
    resp = await client.post("/api/auth/login", json={"email": email, "password": "pw123456"})
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


async def _control(db_session, code: str, statement: str) -> Control:
    control = Control(code=code, title=f"Existing {code}", statement=statement)
    db_session.add(control)
    await db_session.flush()
    return control


async def _proposal(db_session, *, statement: str, title: str, confidence: float = 0.95):
    document = Document(
        title="P",
        doc_type=DocType.PROCEDURE,
        file_hash=uuid4().hex * 2,
        file_path="/x.pdf",
        original_filename="x.pdf",
        status=DocStatus.ACTIVE,
    )
    db_session.add(document)
    await db_session.flush()
    clause = Clause(
        document_id=document.id,
        number="4.1",
        heading="H",
        heading_path="D › H",
        citation_label="4.1",
        text=statement,
        order_index=0,
        level=1,
    )
    db_session.add(clause)
    await db_session.flush()
    quote = statement.split(" must ")[0]
    proposal = Proposal(
        kind=ProposalKind.CONTROL_EXTRACT,
        payload={
            "title": title,
            "statement": statement,
            "citations": [{"clause_id": clause.id, "quote": quote}],
        },
        citations=[{"clause_id": clause.id, "quote": quote}],
        confidence=confidence,
        document_id=document.id,
    )
    db_session.add(proposal)
    await db_session.flush()
    return proposal


async def test_the_queue_says_which_control_already_says_this(client, db_session):
    # 大小写与多余空白不该骗过判重——两条真的是同一句话。
    existing = await _control(db_session, "C-0019", f"  {STATEMENT.upper()}  ")
    await _proposal(db_session, statement=STATEMENT, title="Documented BC requirements")
    await _lead(db_session)

    resp = await client.get("/api/proposals", headers=await _auth(client))

    card = resp.json()[0]
    assert card["duplicate_of"] == {
        "id": existing.id, "code": "C-0019", "title": "Existing C-0019"
    }


async def test_a_flagged_proposal_cannot_be_swept_up_by_bulk_accept(client, db_session):
    # 置信度足够高，今天它是可以被批量接受的——重复正是从这条路悄悄进来的。
    await _control(db_session, "C-0019", STATEMENT)
    await _proposal(db_session, statement=STATEMENT, title="Documented BC requirements")
    await _lead(db_session)

    resp = await client.get("/api/proposals", headers=await _auth(client))

    assert resp.json()[0]["bulk_acceptable"] is False


async def test_bulk_accept_refuses_it_on_the_server_too(client, db_session):
    # 前端只是藏掉勾选框；真正的拦截必须在后端，否则直接打接口照样进得来。
    await _control(db_session, "C-0019", STATEMENT)
    proposal = await _proposal(db_session, statement=STATEMENT, title="Documented BC")
    await _lead(db_session)
    headers = await _auth(client)

    resp = await client.post(
        "/api/proposals/bulk-accept", json={"ids": [proposal.id]}, headers=headers
    )

    assert resp.json() == {"accepted": 0, "skipped": 1}
    assert await db_session.scalar(select(func.count()).select_from(Control)) == 1


async def test_a_new_statement_is_left_alone(client, db_session):
    await _control(db_session, "C-0019", STATEMENT)
    await _proposal(db_session, statement="Something else entirely must happen.", title="Other")
    await _lead(db_session)

    card = (await client.get("/api/proposals", headers=await _auth(client))).json()[0]

    assert card["duplicate_of"] is None
    assert card["bulk_acceptable"] is True


async def test_merging_hangs_the_citations_on_the_existing_control(client, db_session):
    existing = await _control(db_session, "C-0019", STATEMENT)
    proposal = await _proposal(db_session, statement=STATEMENT, title="Documented BC")
    await _lead(db_session)
    headers = await _auth(client)

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide",
        json={"decision": "accept", "merge_into_control_id": existing.id},
        headers=headers,
    )

    assert resp.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(Control)) == 1
    sources = list(
        await db_session.scalars(
            select(ControlSource).where(ControlSource.control_id == existing.id)
        )
    )
    assert len(sources) == 1


async def test_not_merging_still_creates_a_second_control(client, db_session):
    # 默认行为一个字不改：不选合并就是另建，没有任何东西在背后改判。
    await _control(db_session, "C-0019", STATEMENT)
    proposal = await _proposal(db_session, statement=STATEMENT, title="Documented BC")
    await _lead(db_session)

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide",
        json={"decision": "accept"},
        headers=await _auth(client),
    )

    assert resp.status_code == 200
    assert await db_session.scalar(select(func.count()).select_from(Control)) == 2


async def test_merging_into_a_control_that_says_something_else_is_refused(client, db_session):
    # 队列可能是几分钟前渲染的；那条控制点在这期间被人编辑过就不能再并。
    existing = await _control(db_session, "C-0019", "A completely different requirement.")
    proposal = await _proposal(db_session, statement=STATEMENT, title="Documented BC")
    await _lead(db_session)

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide",
        json={"decision": "accept", "merge_into_control_id": existing.id},
        headers=await _auth(client),
    )

    assert resp.status_code == 400
    # 整个事务被回滚，所以这里不查计数——共享 session 的测试里那个数字说明不了什么。
    assert "无法并入" in resp.text


async def test_a_rejection_cannot_carry_a_merge_target(client, db_session):
    existing = await _control(db_session, "C-0019", STATEMENT)
    proposal = await _proposal(db_session, statement=STATEMENT, title="Documented BC")
    await _lead(db_session)

    resp = await client.post(
        f"/api/proposals/{proposal.id}/decide",
        json={"decision": "reject", "reason": "no", "merge_into_control_id": existing.id},
        headers=await _auth(client),
    )

    assert resp.status_code == 422


async def test_postgres_normalises_statements_the_same_way_python_does(db_session):
    """判重的过滤在 SQL 里做、比对在 Python 里做，两边的规则必须一致。

    不一致的后果是静默的：队列该标的没标，看起来一切正常。
    """
    from app.review.materialize import sql_normalised_statement

    messy = "  Records   ARE\tkept\nfor  seven years.  "
    control = await _control(db_session, "C-0777", messy)

    rendered = await db_session.scalar(
        select(sql_normalised_statement()).where(Control.id == control.id)
    )

    assert rendered == normalise_statement(messy)
