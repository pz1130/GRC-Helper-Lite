"""默认评分档案 seed：未批准、可重复执行、不覆盖人工校准。"""

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from app.iam.models import User
from app.iam.permissions import Role
from app.rcsa.models import RcsaScoringProfile
from app.rcsa.profiles import DEFAULT_PROFILE_KEY, band_for, seed_default_profile


async def _admin(db_session) -> User:
    user = User(email="admin@acme.test", name="Admin", role=Role.ADMIN, password_hash="x")
    db_session.add(user)
    await db_session.flush()
    return user


async def _profiles(db_session) -> list[RcsaScoringProfile]:
    return list(
        await db_session.scalars(
            select(RcsaScoringProfile).where(RcsaScoringProfile.key == DEFAULT_PROFILE_KEY)
        )
    )


async def test_seed_creates_an_unapproved_five_by_five_profile(db_session):
    admin = await _admin(db_session)

    await seed_default_profile(db_session, actor=admin)

    [profile] = await _profiles(db_session)
    assert (profile.key, profile.revision, profile.status) == ("default-5x5", 1, "draft")
    assert profile.approved_by is None
    assert [level["label"] for level in profile.definitions["likelihood"]] == [
        "Rare", "Unlikely", "Possible", "Likely", "Almost certain",
    ]
    assert [level["label"] for level in profile.definitions["impact"]] == [
        "Minor", "Moderate", "Significant", "Major", "Severe",
    ]


async def test_running_the_seed_twice_keeps_one_profile(db_session):
    admin = await _admin(db_session)

    first = await seed_default_profile(db_session, actor=admin)
    second = await seed_default_profile(db_session, actor=admin)

    assert second.id == first.id
    assert await db_session.scalar(
        select(func.count()).select_from(RcsaScoringProfile)
    ) == 1


async def test_seed_does_not_overwrite_a_calibrated_and_approved_profile(db_session):
    admin = await _admin(db_session)
    profile = await seed_default_profile(db_session, actor=admin)
    calibrated = {"likelihood": [{"level": 1, "label": "Once in ten years"}], "impact": []}
    profile.definitions = calibrated
    profile.horizon = "12 months"
    profile.status = "approved"
    profile.approved_by = admin.id
    profile.approved_at = datetime(2026, 1, 10, tzinfo=UTC)
    await db_session.flush()

    await seed_default_profile(db_session, actor=admin)

    await db_session.refresh(profile)
    assert profile.definitions == calibrated
    assert profile.status == "approved"
    assert profile.horizon == "12 months"


@pytest.mark.parametrize(
    "score,label",
    [(1, "Low"), (4, "Low"), (5, "Moderate"), (9, "Moderate"), (10, "High"), (16, "High"),
     (17, "Critical"), (25, "Critical")],
)
async def test_default_bands_cover_every_product_score(db_session, score, label):
    admin = await _admin(db_session)
    profile = await seed_default_profile(db_session, actor=admin)

    assert band_for(profile.bands, score) == label


def test_score_outside_every_band_is_an_error():
    with pytest.raises(ValueError):
        band_for([{"min": 1, "max": 4, "label": "Low"}], 5)
