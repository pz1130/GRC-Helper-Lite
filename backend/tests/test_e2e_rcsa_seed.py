"""e2e 用的 RCSA 合成数据：经确认队列落控制点与映射，评分档案已批准，可重复执行。"""

from sqlalchemy import func, select

from app.controls.models import Control
from app.frameworks.models import Framework, Mapping
from app.iam.models import User
from app.rcsa.models import RcsaScoringProfile
from app.review.models import Proposal, ProposalStatus
from tests.e2e_rcsa_seed import ACCOUNTS, seed


async def test_seed_builds_a_mapped_library_through_the_review_queue(db_session):
    await seed(db_session)

    framework = await db_session.scalar(select(Framework).where(Framework.key == "acme-rcsa"))
    assert framework is not None
    controls = list(await db_session.scalars(select(Control).order_by(Control.code)))
    assert len(controls) == 2
    assert await db_session.scalar(select(func.count()).select_from(Mapping)) == 2
    # 控制点与映射都由人工确认的提案落表，不是直接插的。
    statuses = set(await db_session.scalars(select(Proposal.status)))
    assert statuses == {ProposalStatus.ACCEPTED}
    profile = await db_session.scalar(select(RcsaScoringProfile))
    assert profile.status == "approved"
    emails = set(await db_session.scalars(select(User.email)))
    assert {account["email"] for account in ACCOUNTS} <= emails


async def test_seed_can_run_twice(db_session):
    await seed(db_session)
    await seed(db_session)

    assert await db_session.scalar(select(func.count()).select_from(Control)) == 2
    assert await db_session.scalar(select(func.count()).select_from(Framework)) == 1
