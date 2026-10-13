import os
import subprocess
import sys

import pytest
from sqlalchemy import select

from app import cli
from app.cli import create_admin
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password, verify_password


def test_cli_entrypoint_registers_cross_module_foreign_key_tables():
    env = {**os.environ, "PYTHONPATH": os.getcwd()}
    script = (
        "import app.cli; from app.db import Base; "
        "assert 'audit_engagements' in Base.metadata.tables"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        env=env,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_purge_staging_is_reachable_from_the_command_line(monkeypatch, capsys):
    # `python -c` 里现拼一句是 OQ-16 那个坑的复刻：入口不 import app.models 就炸。
    # 所以清理暂存区也走 app.cli，而不是再开一个裸入口。
    from app.ingest import staging

    async def fake_purge(ctx):
        assert ctx == {}
        return {"removed": 3, "bytes": 9, "referenced": 1, "recent": 0}

    monkeypatch.setattr(staging, "purge_staging", fake_purge)
    monkeypatch.setattr(sys, "argv", ["app.cli", "purge-staging"])

    cli.main()

    assert "'removed': 3" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_create_admin_refuses_a_short_password(db_session):
    with pytest.raises(ValueError, match="至少八位"):
        await create_admin("admin@example.com", "Admin", "short", session=db_session)

    assert await db_session.scalar(select(User).where(User.email == "admin@example.com")) is None


@pytest.mark.asyncio
async def test_create_admin_inserts_user(db_session):
    await create_admin("admin@example.com", "Admin", "pw123456", session=db_session)

    user = await db_session.scalar(select(User).where(User.email == "admin@example.com"))
    assert user is not None
    assert user.role is Role.ADMIN
    assert user.is_active is True
    assert verify_password("pw123456", user.password_hash)


@pytest.mark.asyncio
async def test_create_admin_is_idempotent_and_resets_password(db_session):
    await create_admin("admin@example.com", "Admin", "old-password", session=db_session)
    await create_admin("admin@example.com", "Admin", "new-password", session=db_session)

    users = list(await db_session.scalars(select(User).where(User.email == "admin@example.com")))
    assert len(users) == 1
    assert verify_password("new-password", users[0].password_hash)


@pytest.mark.asyncio
async def test_create_admin_promotes_existing_non_admin(db_session):
    db_session.add(
        User(
            email="someone@example.com",
            name="Someone",
            role=Role.VIEWER,
            password_hash=hash_password("pw"),
        )
    )
    await db_session.flush()

    await create_admin("someone@example.com", "Someone", "pw123456", session=db_session)

    user = await db_session.scalar(select(User).where(User.email == "someone@example.com"))
    assert user.role is Role.ADMIN


@pytest.mark.asyncio
async def test_create_admin_revives_a_locked_out_account(db_session):
    """引导命令是管理员被锁死后的唯一退路，停用和过期都得一并清掉。"""
    from datetime import UTC, datetime, timedelta

    db_session.add(
        User(
            email="locked@example.com",
            name="Locked",
            role=Role.ADMIN,
            password_hash=hash_password("pw"),
            is_active=False,
            expires_at=datetime.now(UTC) - timedelta(days=1),
        )
    )
    await db_session.flush()

    await create_admin("locked@example.com", "Locked", "pw123456", session=db_session)

    user = await db_session.scalar(select(User).where(User.email == "locked@example.com"))
    assert user.is_active is True
    assert user.expires_at is None


def test_rotate_secret_is_reachable_from_the_command_line(monkeypatch, capsys):
    """换主密钥只能在容器里做——它要同时拿着新旧两把密钥，不该经过任何接口。"""
    from app import secrets as secrets_module

    async def fake_rotate(old_key):
        assert old_key == "the-old-key"
        return {"rotated": 3, "skipped": 0}

    monkeypatch.setattr(secrets_module, "rotate_cli", fake_rotate)
    monkeypatch.setattr(sys, "argv", ["app.cli", "rotate-secret", "the-old-key"])

    cli.main()

    assert "'rotated': 3" in capsys.readouterr().out
