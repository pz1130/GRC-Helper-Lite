import pytest
from sqlalchemy import select, text

from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import dummy_verify, hash_password, verify_password


def test_hash_password_is_not_plaintext():
    hashed = hash_password("s3cret-password")
    assert "s3cret-password" not in hashed
    assert hashed.startswith("$argon2")


def test_verify_password_accepts_correct():
    assert verify_password("s3cret-password", hash_password("s3cret-password"))


def test_verify_password_rejects_wrong():
    assert not verify_password("wrong", hash_password("s3cret-password"))


def test_dummy_verify_never_raises_and_returns_none():
    assert dummy_verify("whatever") is None


def test_dummy_verify_costs_the_same_order_as_a_real_verify():
    """假校验必须真的跑一次 argon2，否则抹不平时序。"""
    import time

    real_hash = hash_password("pw123456")

    def median_ms(fn):
        samples = []
        for _ in range(5):
            start = time.perf_counter()
            fn()
            samples.append((time.perf_counter() - start) * 1000)
        return sorted(samples)[2]

    real = median_ms(lambda: verify_password("wrong", real_hash))
    dummy = median_ms(lambda: dummy_verify("wrong"))
    # 宽松边界：只要求同一数量级，不追求精确相等，避免 CI 抖动导致假红
    assert 0.2 < dummy / real < 5.0, f"real={real:.1f}ms dummy={dummy:.1f}ms"


def test_same_password_hashes_differently():
    assert hash_password("same") != hash_password("same")


@pytest.mark.asyncio
async def test_user_can_be_persisted(db_session):
    user = User(
        email="lead@example.com",
        name="GRC Lead",
        role=Role.GRC_LEAD,
        password_hash=hash_password("pw"),
    )
    db_session.add(user)
    await db_session.flush()

    raw_role = await db_session.scalar(
        text("SELECT role FROM users WHERE email = :email"),
        {"email": "lead@example.com"},
    )
    assert raw_role == "grc_lead"

    db_session.expire(user)
    found = await db_session.scalar(select(User).where(User.email == "lead@example.com"))
    assert found is not None
    assert found.role is Role.GRC_LEAD
    assert found.is_active is True
    assert found.expires_at is None
