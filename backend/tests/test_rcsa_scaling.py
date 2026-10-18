"""查询条数不随评估项数量增长：报告、待办、活动详情、评估项列表都不能逐项查库（N+1）。

不量耗时——测试机快慢不定，量出来的毫秒没有意义；SQL 条数是确定的，随数据量线性增长
就是 N+1，换到真实规模上必然慢。
"""

from contextlib import contextmanager

import pytest
from sqlalchemy import event

from tests.rcsa_support import (
    COMPLETE_ANSWER,
    acme_world,
    activated,
    draft,
    rcsa_enabled,  # noqa: F401 — fixture
    review,
    save,
    submit,
)

pytestmark = pytest.mark.usefixtures("rcsa_enabled")


@contextmanager
def counted(db_session):
    statements: list[str] = []
    engine = db_session.bind.sync_engine

    def count(conn, cursor, statement, *args):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", count)
    try:
        yield statements
    finally:
        event.remove(engine, "before_cursor_execute", count)


async def _approved_campaign(client, world, controls: int, tag: str) -> dict:
    """每项一条风险情景、复核通过、再挂一条行动——风险与行动那一层也要量到。"""
    campaign, items = await activated(client, world, controls=controls)
    for index, item in enumerate(items):
        response = await save(client, world["assignee"], await draft(client, world["assignee"], item),
                              COMPLETE_ANSWER | {"no_risk_rationale": ""})
        risk = (await client.post(f"/api/rcsa/items/{item['id']}/risks", json={
            "scenario_key": "s", "cause": "c", "event": "e", "impact": "i", "inherent_likelihood": 3,
            "inherent_impact": 3, "inherent_rationale": "r", "residual_likelihood": 2, "residual_impact": 2,
            "residual_rationale": "r", "owner_id": world["assignee"].id,
        }, headers=world["assignee"].headers)).json()
        response = (await client.get(f"/api/rcsa/responses/{response['id']}", headers=world["assignee"].headers)).json()
        response = (await submit(client, world["assignee"], response, key=f"s{tag}{index}")).json()
        await review(client, world["reviewer"], response, "approve", key=f"r{tag}{index}")
        added = await client.post(f"/api/rcsa/item-risks/{risk['id']}/actions", json={
            "title": "a", "owner_id": world["assignee"].id, "verifier_id": world["reviewer"].id,
            "due_at": "2099-01-01", "success_criteria": "done",
        }, headers=world["assignee"].headers)
        assert added.status_code == 201, added.text
    return campaign


async def _cost(client, db_session, world, campaign, path: str, who: str) -> int:
    with counted(db_session) as statements:
        response = await client.get(path.format(id=campaign["id"]), headers=world[who].headers)
        assert response.status_code == 200, response.text
    return len(statements)


@pytest.mark.parametrize("path,who", [
    ("/api/rcsa/campaigns/{id}/report", "lead"),
    ("/api/rcsa/campaigns/{id}/items", "lead"),
    ("/api/rcsa/campaigns/{id}", "lead"),
    ("/api/rcsa/my-work", "assignee"),
    ("/api/rcsa/campaigns", "lead"),
])
async def test_query_count_does_not_grow_with_the_number_of_items(client, db_session, path, who):
    world = await acme_world(client, db_session, controls=8)
    small = await _cost(client, db_session, world, await _approved_campaign(client, world, 2, "a"), path, who)
    large = await _cost(client, db_session, world, await _approved_campaign(client, world, 8, "b"), path, who)

    assert large == small, f"{path}: {small} queries with 2 items, {large} with 8 more"
