"""0034 的升级、往返与降级保护，在一个单独的临时库上跑。

不用会话级的测试库：降级会真的删表，而且这里要从旧 head 起步。
"""

import asyncio

import pytest
import pytest_asyncio
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from alembic import command
from tests.conftest import TEST_DATABASE_URL

PREVIOUS_HEAD = "0033"
SERVER_URL = TEST_DATABASE_URL.rsplit("/", 1)[0]
DB_NAME = "grc_test_rcsa_migrations"
DB_URL = f"{SERVER_URL}/{DB_NAME}"


async def _admin(statement: str) -> None:
    engine = create_async_engine(f"{SERVER_URL}/postgres", isolation_level="AUTOCOMMIT")
    async with engine.connect() as conn:
        await conn.exec_driver_sql(statement)
    await engine.dispose()


@pytest_asyncio.fixture
async def alembic_cfg(monkeypatch):
    await _admin(f'DROP DATABASE IF EXISTS "{DB_NAME}"')
    await _admin(f'CREATE DATABASE "{DB_NAME}"')
    # env.py 只认这个变量；不设的话迁移会落到会话共用的测试库上。
    monkeypatch.setenv("ALEMBIC_DATABASE_URL", DB_URL)
    cfg = Config("alembic.ini")
    yield cfg
    await _admin(f'DROP DATABASE IF EXISTS "{DB_NAME}" WITH (FORCE)')


async def _run(fn, cfg, *args) -> None:
    await asyncio.to_thread(fn, cfg, *args)


async def _execute(*statements: str) -> list[list[tuple]]:
    engine = create_async_engine(DB_URL)
    results = []
    async with engine.begin() as conn:
        for statement in statements:
            result = await conn.execute(text(statement))
            results.append([tuple(row) for row in result] if result.returns_rows else [])
    await engine.dispose()
    return results


_SYNTHETIC_BUSINESS_ROWS = (
    (
        "INSERT INTO users (id, email, name, role, password_hash, is_active) "
        "VALUES (1, 'lead@acme.test', 'Lead', 'grc_lead', 'x', true)"
    ),
    "INSERT INTO frameworks (id, key, name_zh, name_en, version) VALUES (1, 'acme-it', 'Acme', 'Acme', '1')",
    (
        "INSERT INTO controls (id, code, title, statement, status) "
        "VALUES (1, 'C-9001', 'Access review', 'Access is reviewed.', 'active')"
    ),
    (
        "INSERT INTO risk_entries (id, title, description, source, source_ref, likelihood, impact, "
        "inherent_score, mitigation, status) "
        "VALUES (1, 'Stale access', '', 'manual', '{}', 3, 4, 12, '', 'open')"
    ),
)

_SNAPSHOT_QUERIES = (
    "SELECT id, email, role FROM users ORDER BY id",
    "SELECT id, key, version FROM frameworks ORDER BY id",
    "SELECT id, code, title, status FROM controls ORDER BY id",
    "SELECT id, title, source, likelihood, impact, inherent_score, status FROM risk_entries ORDER BY id",
)


async def test_upgrade_from_previous_head_keeps_existing_business_rows(alembic_cfg):
    await _run(command.upgrade, alembic_cfg, PREVIOUS_HEAD)
    await _execute(*_SYNTHETIC_BUSINESS_ROWS)
    before = await _execute(*_SNAPSHOT_QUERIES)

    await _run(command.upgrade, alembic_cfg, "head")

    assert await _execute(*_SNAPSHOT_QUERIES) == before
    # 迁移后的库与模型元数据一致（新表、索引、外键都对得上）。
    await _run(command.check, alembic_cfg)


async def test_empty_rcsa_schema_downgrades_and_upgrades_again(alembic_cfg):
    await _run(command.upgrade, alembic_cfg, "head")
    await _run(command.downgrade, alembic_cfg, PREVIOUS_HEAD)

    [tables] = await _execute("SELECT count(*) FROM pg_tables WHERE tablename LIKE 'rcsa_%'")
    assert tables == [(0,)]

    await _run(command.upgrade, alembic_cfg, "head")
    await _run(command.check, alembic_cfg)


async def test_downgrade_refuses_to_drop_tables_holding_rcsa_data(alembic_cfg):
    await _run(command.upgrade, alembic_cfg, "head")
    await _execute(
        _SYNTHETIC_BUSINESS_ROWS[0],
        "INSERT INTO rcsa_scoring_profiles (key, revision, algorithm, definitions, bands, horizon, "
        "status, created_by, updated_by) VALUES ('acme-5x5', 1, 'product', '{}', '[]', '', 'draft', 1, 1)",
    )

    with pytest.raises(RuntimeError, match="rcsa_scoring_profiles"):
        await _run(command.downgrade, alembic_cfg, PREVIOUS_HEAD)

    [rows] = await _execute("SELECT key FROM rcsa_scoring_profiles")
    assert rows == [("acme-5x5",)]


async def test_downgrade_refuses_while_the_register_holds_rcsa_risks(alembic_cfg):
    await _run(command.upgrade, alembic_cfg, "head")
    await _execute(
        "INSERT INTO risk_entries (title, description, source, source_ref, likelihood, impact, "
        "inherent_score, mitigation, status) "
        "VALUES ('Published', '', 'rcsa', '{\"rcsa_item_risk_id\": 7}', 3, 4, 12, '', 'open')"
    )

    # 回退旧约束会让这一行违反 risk_source；必须在动表之前就拒绝。
    with pytest.raises(RuntimeError, match="risk_entries"):
        await _run(command.downgrade, alembic_cfg, PREVIOUS_HEAD)
