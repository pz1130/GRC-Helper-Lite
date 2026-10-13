import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.clauses.models import EMBEDDING_DIM
from app.controls.models import Control
from app.relations.models import ControlEmbedding


async def _control(db_session, code="C-0001") -> Control:
    control = Control(code=code, title="Dual approval", statement="Two approvers required.")
    db_session.add(control)
    await db_session.flush()
    return control


@pytest.mark.asyncio
async def test_embedding_is_optional_until_the_indexer_runs(db_session):
    control = await _control(db_session)
    row = ControlEmbedding(control_id=control.id)
    db_session.add(row)
    await db_session.flush()
    assert row.embedding is None
    assert row.embedding_model is None


@pytest.mark.asyncio
async def test_one_embedding_per_control(db_session):
    control = await _control(db_session)
    db_session.add(ControlEmbedding(control_id=control.id))
    await db_session.flush()
    db_session.add(ControlEmbedding(control_id=control.id))
    with pytest.raises(IntegrityError):
        await db_session.flush()


@pytest.mark.asyncio
async def test_embedding_round_trips_at_the_configured_dimension(db_session):
    control = await _control(db_session)
    vector = [0.01] * EMBEDDING_DIM
    db_session.add(ControlEmbedding(
        control_id=control.id, embedding=vector, embedding_model="embo-01"))
    await db_session.flush()
    db_session.expire_all()
    row = await db_session.scalar(select(ControlEmbedding))
    assert len(row.embedding) == EMBEDDING_DIM
    assert row.embedding_model == "embo-01"


@pytest.mark.asyncio
async def test_deleting_a_control_removes_its_embedding(db_session):
    control = await _control(db_session)
    db_session.add(ControlEmbedding(control_id=control.id))
    await db_session.flush()
    await db_session.delete(control)
    await db_session.flush()
    assert (await db_session.scalars(select(ControlEmbedding))).all() == []
