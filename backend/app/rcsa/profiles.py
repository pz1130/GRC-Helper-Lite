"""评分档案：默认 5×5 seed 与分级查找。

seed 的标签与阈值只是产品初始建议，不是监管标准，也不是任何机构的风险偏好。它以
**未批准**状态落库：评估时域、各档判断标准和影响维度要由 GRC lead/admin 补全并批准
后才能用于激活活动。机构自己的口径写在部署环境里，不进源码。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.iam.models import User
from app.rcsa.models import ProfileStatus, RcsaScoringProfile, ScoringAlgorithm

DEFAULT_PROFILE_KEY = "default-5x5"

_LIKELIHOOD = ("Rare", "Unlikely", "Possible", "Likely", "Almost certain")
_IMPACT = ("Minor", "Moderate", "Significant", "Major", "Severe")
_BANDS = (
    {"min": 1, "max": 4, "label": "Low"},
    {"min": 5, "max": 9, "label": "Moderate"},
    {"min": 10, "max": 16, "label": "High"},
    {"min": 17, "max": 25, "label": "Critical"},
)


def default_definitions() -> dict[str, Any]:
    return {
        "likelihood": [
            {"level": level, "label": label, "criteria": ""}
            for level, label in enumerate(_LIKELIHOOD, start=1)
        ],
        # 每个影响档位按维度给判断标准；首版按适用维度里最高的那一档定级。
        "impact": [
            {"level": level, "label": label, "criteria": {}}
            for level, label in enumerate(_IMPACT, start=1)
        ],
        "impact_dimensions": [],
        "impact_rule": "max_applicable_dimension",
    }


async def seed_default_profile(session: AsyncSession, *, actor: User) -> RcsaScoringProfile:
    """幂等：已有 default-5x5 v1 就原样返回，不论它是否已被人工校准或批准。"""
    existing = await session.scalar(
        select(RcsaScoringProfile).where(
            RcsaScoringProfile.key == DEFAULT_PROFILE_KEY, RcsaScoringProfile.revision == 1
        )
    )
    if existing is not None:
        return existing
    profile = RcsaScoringProfile(
        key=DEFAULT_PROFILE_KEY,
        revision=1,
        algorithm=ScoringAlgorithm.PRODUCT,
        definitions=default_definitions(),
        bands=default_bands(),
        horizon="",
        status=ProfileStatus.DRAFT,
        created_by=actor.id,
        updated_by=actor.id,
    )
    session.add(profile)
    await session.flush()
    return profile


def band_for(bands: list[dict[str, Any]], score: int) -> str:
    """分值落在哪一档。档位有缺口是档案本身的错误，不能静默归到某一档。"""
    for band in bands:
        if band["min"] <= score <= band["max"]:
            return band["label"]
    raise ValueError(f"score {score} is outside every band")


def profile_problems(profile: RcsaScoringProfile) -> list[str]:
    """批准前必须补全的口径；空列表才能批准。返回的是哪一块不完整，不是句子。"""
    problems: list[str] = []
    definitions = profile.definitions or {}
    if not profile.horizon.strip():
        problems.append("horizon")
    likelihood = definitions.get("likelihood") or []
    if [level.get("level") for level in likelihood] != [1, 2, 3, 4, 5] or not all(
        str(level.get("label", "")).strip() and str(level.get("criteria", "")).strip()
        for level in likelihood
    ):
        problems.append("likelihood")
    dimensions = definitions.get("impact_dimensions") or []
    if not dimensions or not all(isinstance(d, str) and d.strip() for d in dimensions):
        problems.append("impact_dimensions")
    impact = definitions.get("impact") or []
    if [level.get("level") for level in impact] != [1, 2, 3, 4, 5] or not all(
        str(level.get("label", "")).strip()
        and isinstance(level.get("criteria"), dict)
        and all(str(level["criteria"].get(d, "")).strip() for d in dimensions)
        for level in impact
    ):
        problems.append("impact")
    if not _bands_cover_every_score(profile.bands or []):
        problems.append("bands")
    return problems


def _bands_cover_every_score(bands: list[dict[str, Any]]) -> bool:
    ordered = sorted(bands, key=lambda band: band.get("min", 0))
    if not ordered or ordered[0].get("min") != 1 or ordered[-1].get("max") != 25:
        return False
    previous_max = 0
    for band in ordered:
        if not str(band.get("label", "")).strip() or band["min"] > band["max"]:
            return False
        if band["min"] != previous_max + 1:
            return False
        previous_max = band["max"]
    return True


def default_bands() -> list[dict[str, Any]]:
    return [dict(band) for band in _BANDS]
