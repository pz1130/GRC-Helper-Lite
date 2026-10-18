"""真实事务下的并发：同时提交、同时关闭、同时发布，结果唯一，审计与业务同成败。

普通测试跑在一个最后回滚的事务里，看不到两个连接之间的锁。这里每个请求一个独立
会话、各自提交，在单独的临时库上跑。
"""

import asyncio
from datetime import date

import pytest_asyncio
from alembic.config import Config
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from alembic import command
from app.errors import AppError
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.rcsa import campaigns, closing, responses, risks
from app.rcsa.models import RcsaResponse
from app.rcsa.schemas import (
    AnswerV1,
    CampaignCreateIn,
    CloseIn,
    ItemsAddIn,
    PublishIn,
    ReviewIn,
    RiskIn,
)
from app.risk.models import RiskEntry
from tests.conftest import TEST_DATABASE_URL
from tests.rcsa_support import COMPLETE_ANSWER, approved_profile, library

SERVER_URL = TEST_DATABASE_URL.rsplit("/", 1)[0]
DB_NAME = "grc_test_rcsa_concurrency"
DB_URL = f"{SERVER_URL}/{DB_NAME}"


async def _admin(statement: str) -> None:
    engine = create_async_engine(f"{SERVER_URL}/postgres", isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(statement)
    await engine.dispose()


@pytest_asyncio.fixture
async def sessions(monkeypatch):
    await _admin(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
    await _admin(f'CREATE DATABASE "{DB_NAME}"')
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", DB_URL)
    await asyncio.to_thread(command.upgrade, Config("alembic.ini"), "head")
    engine = create_async_engine(DB_URL, pool_size=10)
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()
    await _admin(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


async def _seed(factory, *, risk: bool = False, approve: bool = False) -> dict:
    """一个已激活的活动、一个评估项、一份已保存的草稿；按需加风险并复核通过。"""
    async with factory() as session:
        people = {}
        for name, role in (("lead", Role.GRC_LEAD), ("reviewer", Role.GRC_LEAD), ("assignee", Role.CONTRIBUTOR)):
            people[name] = User(email=f"{name}@example.com", name=name, role=role, password_hash="x")
        session.add_all(people.values())
        await session.flush()
        lib = await library(session, owner=people["assignee"], controls=1)
        profile = await approved_profile(session, people["lead"])
        campaign = await campaigns.create_campaign(session, CampaignCreateIn(
            name="Acme Q1", framework_id=lib.framework.id, profile_id=profile.id,
            period_start=date(2026, 1, 1), period_end=date(2026, 3, 31), due_at=date(2026, 4, 30),
        ), people["lead"])
        [item] = await campaigns.add_items(session, campaign.id, ItemsAddIn(
            control_ids=[lib.controls[0].id], reviewer_id=people["reviewer"].id), people["lead"])
        await campaigns.activate(session, campaign.id, campaign.version, people["lead"], "seed-activate")
        await session.refresh(item)
        response = await responses.open_draft(session, item.id, item.version, people["assignee"])
        answer = COMPLETE_ANSWER | ({"no_risk_rationale": ""} if risk else {})
        response = await responses.save_answer(session, response.id, AnswerV1.model_validate(answer),
                                               response.version, people["assignee"])
        risk_row = None
        if risk:
            risk_row = await risks.add_risk(session, item.id, RiskIn(
                scenario_key="s1", cause="c", event="e", impact="i", inherent_likelihood=4, inherent_impact=3,
                inherent_rationale="r", residual_likelihood=2, residual_impact=3, residual_rationale="r",
                owner_id=people["assignee"].id), people["assignee"])
        if approve:
            await session.refresh(response)
            response = await responses.submit(session, response.id, response.version, people["assignee"], "seed")
            response = await responses.review(session, response.id, ReviewIn(
                decision="approve", expected_version=response.version), people["reviewer"], "seed")
        await session.commit()
        return {"people": people, "campaign_id": campaign.id, "item_id": item.id,
                "response_id": response.id, "risk_id": risk_row.id if risk_row else None}


async def _race(factory, *calls):
    """每个调用一个独立会话，同时开始，各自提交；返回结果或异常。"""
    async def run(call):
        async with factory() as session:
            result = await call(session)
            await session.commit()
            return result

    return await asyncio.gather(*(run(call) for call in calls), return_exceptions=True)


async def _count(factory, statement) -> int:
    async with factory() as session:
        return int(await session.scalar(statement) or 0)


def _audits(action: str):
    return select(func.count()).select_from(AuditLog).where(AuditLog.action == action)


async def _person(factory, user: User) -> User:
    async with factory() as session:
        return await session.get(User, user.id)


async def test_two_simultaneous_submits_produce_one_submission(sessions):
    seeded = await _seed(sessions)
    assignee = await _person(sessions, seeded["people"]["assignee"])

    def submit(key):
        async def call(session):
            response = await session.get(RcsaResponse, seeded["response_id"])
            return await responses.submit(session, response.id, response.version, assignee, key)
        return call

    results = await _race(sessions, submit("a"), submit("b"))

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], AppError), results
    assert errors[0].code in {"rcsa.response_not_draft", "rcsa.version_conflict"}
    assert await _count(sessions, _audits("rcsa.submit")) == 1


async def test_the_same_submit_sent_twice_at_once_replays(sessions):
    seeded = await _seed(sessions)
    assignee = await _person(sessions, seeded["people"]["assignee"])

    async with sessions() as session:
        version = (await session.get(RcsaResponse, seeded["response_id"])).version

    async def call(session):
        return await responses.submit(session, seeded["response_id"], version, assignee, "same")

    results = await _race(sessions, call, call)

    assert not [r for r in results if isinstance(r, Exception)], results
    assert await _count(sessions, _audits("rcsa.submit")) == 1


async def test_two_simultaneous_closes_close_once(sessions):
    seeded = await _seed(sessions, approve=True)
    lead = await _person(sessions, seeded["people"]["lead"])
    async with sessions() as session:
        campaign = await closing.begin_review(session, seeded["campaign_id"], 2, lead)
        await session.commit()
        version = campaign.version

    def close(key):
        async def call(session):
            return await closing.close(session, seeded["campaign_id"], CloseIn(expected_version=version), lead, key)
        return call

    results = await _race(sessions, close("a"), close("b"))

    errors = [r for r in results if isinstance(r, Exception)]
    assert len(errors) == 1 and isinstance(errors[0], AppError), results
    assert await _count(sessions, _audits("rcsa.close")) == 1


async def test_two_simultaneous_publishes_create_one_register_risk(sessions):
    seeded = await _seed(sessions, risk=True, approve=True)
    lead = await _person(sessions, seeded["people"]["lead"])

    def publish(key):
        async def call(session):
            from app.rcsa.models import RcsaItemRisk

            risk = await session.get(RcsaItemRisk, seeded["risk_id"])
            return await risks.publish(session, risk.id, PublishIn(mode="create", expected_version=risk.version),
                                       lead, key)
        return call

    results = await _race(sessions, publish("a"), publish("b"))

    assert all(not isinstance(r, Exception) or isinstance(r, AppError) for r in results), results
    assert await _count(sessions, select(func.count()).select_from(RiskEntry)) == 1
    assert await _count(sessions, _audits("rcsa.publish_risk")) == 1
