from urllib.parse import urlparse

import pytest

from app.config import get_settings
from app.worker import WorkerSettings, ping


@pytest.mark.asyncio
async def test_ping_task_returns_pong():
    assert await ping({}) == "pong"


def test_worker_settings_registers_ping():
    assert ping in WorkerSettings.functions


def test_worker_registers_control_embedding_backfill():
    names = {getattr(item, "coroutine", item).__name__ for item in WorkerSettings.functions}
    assert "embed_controls" in names


def test_worker_sweeps_the_staging_volume_on_a_schedule():
    # /tmp 只进不出的唯一出口（OQ-4）：没人排这个队，它就只能靠人手工清。
    names = {job.coroutine.__name__ for job in WorkerSettings.cron_jobs}
    assert "purge_staging" in names


def test_worker_settings_points_at_configured_redis():
    parsed = urlparse(get_settings().redis_url)
    assert WorkerSettings.redis_settings.host == parsed.hostname
    assert WorkerSettings.redis_settings.port == parsed.port


def test_job_timeout_leaves_room_for_ocr_and_extraction():
    """一份大 PDF 走 OCR + 抽取要几分钟，超时不能设得比这还短。"""
    assert WorkerSettings.job_timeout >= 600


def test_worker_retries_failed_jobs():
    """spec §9：任务幂等 + 自动重试。"""
    assert WorkerSettings.max_tries > 1
