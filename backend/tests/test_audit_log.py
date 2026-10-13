import pytest
from sqlalchemy import select

from app.iam.audit import record, scrub
from app.iam.models import AuditLog, User
from app.iam.permissions import Role
from app.iam.security import hash_password


def test_scrub_removes_password_hash():
    assert scrub({"email": "a@b.c", "password_hash": "$argon2..."}) == {"email": "a@b.c"}


def test_scrub_removes_any_encrypted_field():
    cleaned = scrub({"name": "prod", "api_key_encrypted": "gAAAA", "token_encrypted": "x"})
    assert cleaned == {"name": "prod"}


def test_scrub_leaves_ordinary_fields():
    assert scrub({"name": "prod", "model": "claude-opus-5"}) == {
        "name": "prod",
        "model": "claude-opus-5",
    }


@pytest.mark.asyncio
async def test_record_persists_entry(db_session):
    user = User(
        email="admin@example.com",
        name="Admin",
        role=Role.ADMIN,
        password_hash=hash_password("pw"),
    )
    db_session.add(user)
    await db_session.flush()

    await record(
        db_session,
        user=user,
        action="user.create",
        entity_type="User",
        entity_id="7",
        after={"email": "new@example.com", "password_hash": "$argon2-should-be-scrubbed"},
        ip="10.0.0.1",
    )
    await db_session.flush()

    entry = await db_session.scalar(select(AuditLog))
    assert entry is not None
    assert entry.user_id == user.id
    assert entry.action == "user.create"
    assert entry.entity_id == "7"
    assert entry.after == {"email": "new@example.com"}   # 已被 scrub
    assert entry.ip == "10.0.0.1"


@pytest.mark.asyncio
async def test_audit_log_model_has_no_delete_helper():
    """spec §8.2：AuditLog 不可删除。审计模块不得暴露任何删除函数。"""
    import app.iam.audit as audit_module

    exported = [name for name in dir(audit_module) if not name.startswith("_")]
    assert not any("delete" in name.lower() or "purge" in name.lower() for name in exported)
