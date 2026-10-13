import os

import pytest
from sqlalchemy import text
from sqlalchemy.engine import make_url

from app.db import engine


def test_module_engine_targets_test_database():
    """`app.db.engine` 在 import 时就建好了；conftest 必须在收集用例之前把它改指到测试库。"""
    assert engine.url == make_url(os.environ["TEST_DATABASE_URL"])


@pytest.mark.asyncio
async def test_pgvector_extension_is_installed(db_session):
    result = await db_session.execute(
        text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
    )
    assert result.scalar() == 1


@pytest.mark.asyncio
async def test_session_can_query(db_session):
    result = await db_session.execute(text("SELECT 42"))
    assert result.scalar() == 42
