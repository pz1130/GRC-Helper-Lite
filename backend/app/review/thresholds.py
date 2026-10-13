"""置信度阈值（spec §7.4 D11）。

M1 把这两个值存进了 AppSetting，M4 是第一个消费者。
"""

import math
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.models import AppSetting
from app.review.models import Proposal

_AUTO_ACCEPT_DEFAULT = 0.90
_FORCE_MANUAL_DEFAULT = 0.60
_REVIEW_SAMPLE_RATE_DEFAULT = 0.10


@dataclass(frozen=True)
class Thresholds:
    auto_accept: float
    force_manual: float
    review_sample_rate: float = _REVIEW_SAMPLE_RATE_DEFAULT


async def _value(session: AsyncSession, key: str, fallback: float) -> float:
    setting = await session.get(AppSetting, key)
    if setting is None:
        return fallback
    try:
        value = setting.value["value"]
        if isinstance(value, bool):
            return fallback
        result = float(value)
        return result if math.isfinite(result) and 0 <= result <= 1 else fallback
    except (KeyError, TypeError, ValueError):
        return fallback


async def load(session: AsyncSession) -> Thresholds:
    return Thresholds(
        auto_accept=await _value(session, "auto_accept_threshold", _AUTO_ACCEPT_DEFAULT),
        force_manual=await _value(session, "force_manual_threshold", _FORCE_MANUAL_DEFAULT),
        review_sample_rate=await _value(
            session, "review_sample_rate", _REVIEW_SAMPLE_RATE_DEFAULT
        ),
    )


def bulk_acceptable(proposal: Proposal, thresholds: Thresholds, *, ocr_flag: bool) -> bool:
    """能否被批量接受。

    OCR 存疑的文档一律禁止批量，无视置信度（spec §9）——OCR 出错时模型看到的
    本来就是乱码，它对乱码的高置信度毫无意义。
    """
    if ocr_flag:
        return False
    if proposal.confidence is None:
        return False  # 没有置信度就保守处理
    confidence = proposal.confidence
    return (
        not isinstance(confidence, bool)
        and math.isfinite(confidence)
        and 0 <= confidence <= 1
        and 0 <= thresholds.force_manual <= thresholds.auto_accept <= 1
        and confidence >= thresholds.auto_accept
        # 不再重复比 force_manual：上面已经要求 force_manual <= auto_accept，
        # 所以 confidence >= auto_accept 蕴含 confidence >= force_manual，那一行恒真。
        # 这条线真正的作用在 service.assess 里（低于它一律 MANUAL）。
    )
