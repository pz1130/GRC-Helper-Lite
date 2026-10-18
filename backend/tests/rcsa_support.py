"""RCSA 测试共用的构造件：合成的 Acme 机构、账号、框架映射和已批准的评分档案。

控制点与映射在这里直接插表——测试要的是「已确认」的状态，确认队列本身另有测试。
"""

from dataclasses import dataclass
from datetime import UTC, datetime

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.controls.models import Control
from app.frameworks.models import Framework, FrameworkItem, Mapping, MappingStrength
from app.iam.models import User
from app.iam.permissions import Role
from app.iam.security import hash_password
from app.rcsa.models import RcsaScoringProfile
from app.rcsa.profiles import seed_default_profile

PASSWORD = "pw123456"


@pytest.fixture
def rcsa_enabled(monkeypatch):
    monkeypatch.setattr(get_settings(), "rcsa_enabled", True)


@dataclass
class Person:
    user: User
    headers: dict[str, str]

    @property
    def id(self) -> int:
        return self.user.id


async def person(client: AsyncClient, db_session: AsyncSession, name: str, role: Role) -> Person:
    user = User(
        email=f"{name}@example.com", name=name.title(), role=role,
        password_hash=hash_password(PASSWORD),
    )
    db_session.add(user)
    await db_session.flush()
    login = await client.post("/api/auth/login", json={"email": user.email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    return Person(user, {"Authorization": f"Bearer {login.json()['access_token']}"})


def with_key(who: Person, key: str) -> dict[str, str]:
    return who.headers | {"Idempotency-Key": key}


@dataclass
class Library:
    framework: Framework
    items: list[FrameworkItem]
    controls: list[Control]


async def library(db_session: AsyncSession, *, owner: User | None = None, controls: int = 3) -> Library:
    """一个框架、两个框架项、若干控制点；第一个控制点同时映射两个框架项。"""
    framework = Framework(key="acme-it", name_zh="Acme IT", name_en="Acme IT", version="1")
    db_session.add(framework)
    await db_session.flush()
    items = [
        FrameworkItem(framework_id=framework.id, code="AC-1", title="Access policy", level=1),
        FrameworkItem(framework_id=framework.id, code="AC-2", title="Account management", level=1),
    ]
    rows = [
        Control(
            code=f"C-{9000 + n}", title=f"Acme control {n}", statement=f"Acme does thing {n}.",
            owner_user_id=owner.id if owner else None,
        )
        for n in range(1, controls + 1)
    ]
    db_session.add_all(items + rows)
    await db_session.flush()
    db_session.add_all(
        [Mapping(control_id=rows[0].id, framework_item_id=items[0].id, strength=MappingStrength.FULL)]
        + [
            Mapping(control_id=control.id, framework_item_id=items[1].id, strength=MappingStrength.PARTIAL)
            for control in rows
        ]
    )
    await db_session.flush()
    return Library(framework, items, rows)


CALIBRATED_DEFINITIONS = {
    "likelihood": [
        {"level": n, "label": label, "criteria": f"Acme likelihood band {n}"}
        for n, label in enumerate(["Rare", "Unlikely", "Possible", "Likely", "Almost certain"], 1)
    ],
    "impact": [
        {"level": n, "label": label, "criteria": {"service": f"Outage tier {n}", "data": f"Data tier {n}"}}
        for n, label in enumerate(["Minor", "Moderate", "Significant", "Major", "Severe"], 1)
    ],
    "impact_dimensions": ["service", "data"],
    "impact_rule": "max_applicable_dimension",
}


async def approved_profile(db_session: AsyncSession, approver: User) -> RcsaScoringProfile:
    profile = await seed_default_profile(db_session, actor=approver)
    profile.definitions = CALIBRATED_DEFINITIONS
    profile.horizon = "12 months"
    profile.status = "approved"
    profile.approved_by = approver.id
    profile.approved_at = datetime(2026, 1, 2, tzinfo=UTC)
    await db_session.flush()
    return profile


CAMPAIGN_BODY = {
    "name": "Acme Q1 RCSA",
    "period_start": "2026-01-01",
    "period_end": "2026-03-31",
    "due_at": "2026-04-30",
    "timezone": "UTC",
}
