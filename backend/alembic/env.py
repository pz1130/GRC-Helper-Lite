import asyncio
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.config import get_settings
from app.db import Base


def _url() -> str:
    """迁移目标库。

    ALEMBIC_DATABASE_URL 是给「往返验证」用的逃生口：验收清单要求跑
    upgrade head → downgrade 0011 → upgrade head，而 downgrade 会真的
    DROP TABLE。没有这个覆盖时，唯一的 URL 来自 .env 的 DATABASE_URL，
    也就是开发库——传 APP_DATABASE_URL 是没用的（那只有 eval 脚本自己读），
    于是「隔离往返」会静默地在开发库上删表。已经踩过一次。
    """
    return os.environ.get("ALEMBIC_DATABASE_URL") or get_settings().database_url


# 模型注册集中在 app.models，新增模型只需改那一处
import app.models  # noqa: F401

config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _run_migrations(connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(_url())
    async with engine.connect() as connection:
        await connection.run_sync(_run_migrations)
    await engine.dispose()


def run_migrations_offline() -> None:
    context.configure(
        url=_url(),
        target_metadata=target_metadata,
        literal_binds=True,
    )
    with context.begin_transaction():
        context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_async_migrations())
