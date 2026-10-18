"""RCSA 恢复核对用的构造与指纹（scripts/rcsa-restore-check.sh 在两个库上各算一次再比对）。"""

from sqlalchemy import select

from app.rcsa.models import CampaignStatus, RcsaCampaign, RcsaTrigger, TriggerStatus
from tests.rcsa_drill import build, fingerprint


async def test_drill_closes_a_campaign_that_touches_every_rcsa_table(db_session):
    campaign_id = await build(db_session)

    campaign = await db_session.get(RcsaCampaign, campaign_id)
    assert campaign.status == CampaignStatus.CLOSED
    trigger = await db_session.scalar(select(RcsaTrigger))
    assert (trigger.status, trigger.campaign_id) == (TriggerStatus.COMPLETED, campaign_id)
    printed = await fingerprint(db_session)
    empty = [table for table, rows in printed["rows"].items() if rows == 0]
    assert empty == [], f"the drill leaves these RCSA tables empty: {empty}"


async def test_fingerprint_is_stable_and_covers_what_a_restore_must_keep(db_session):
    await build(db_session)

    first, second = await fingerprint(db_session), await fingerprint(db_session)

    assert first == second
    assert {"profiles", "responses", "audit", "triggers", "closed_reports", "db_triggers"} <= first.keys()
    assert len(first["db_triggers"]) == 4
