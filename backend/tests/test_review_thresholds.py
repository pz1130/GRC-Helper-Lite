import pytest


@pytest.mark.parametrize("confidence", [float("nan"), float("inf"), -0.1, 1.1, True])
def test_invalid_confidence_never_bulk_accepts(confidence):
    assert not bulk_acceptable(_proposal(confidence), Thresholds(0.9, 0.6), ocr_flag=False)


def test_inverted_thresholds_fail_closed():
    assert not bulk_acceptable(_proposal(1), Thresholds(0.5, 0.9), ocr_flag=False)


from app.llm.models import AppSetting
from app.review.models import Proposal, ProposalKind
from app.review.thresholds import Thresholds, bulk_acceptable, load

DEFAULTS = Thresholds(auto_accept=0.90, force_manual=0.60)


def _proposal(confidence: float | None) -> Proposal:
    return Proposal(
        kind=ProposalKind.CONTROL_EXTRACT, payload={}, citations=[], confidence=confidence
    )


@pytest.mark.asyncio
async def test_load_reads_the_seeded_settings(db_session):
    """0004 迁移种了 0.90 / 0.60。"""
    thresholds = await load(db_session)
    assert thresholds.auto_accept == pytest.approx(0.90)
    assert thresholds.force_manual == pytest.approx(0.60)


@pytest.mark.asyncio
async def test_load_reflects_admin_changes(db_session):
    setting = await db_session.get(AppSetting, "auto_accept_threshold")
    setting.value = {"value": 0.95}
    await db_session.flush()

    assert (await load(db_session)).auto_accept == pytest.approx(0.95)


def test_high_confidence_can_be_bulk_accepted():
    assert bulk_acceptable(_proposal(0.95), DEFAULTS, ocr_flag=False) is True


def test_confidence_exactly_at_the_threshold_is_acceptable():
    assert bulk_acceptable(_proposal(0.90), DEFAULTS, ocr_flag=False) is True


def test_middle_confidence_is_not_bulk_acceptable():
    assert bulk_acceptable(_proposal(0.75), DEFAULTS, ocr_flag=False) is False


def test_low_confidence_is_not_bulk_acceptable():
    assert bulk_acceptable(_proposal(0.40), DEFAULTS, ocr_flag=False) is False


def test_missing_confidence_is_treated_conservatively():
    assert bulk_acceptable(_proposal(None), DEFAULTS, ocr_flag=False) is False


def test_ocr_suspect_document_forbids_bulk_regardless_of_confidence():
    """spec §9：OCR 存疑的文档，其衍生提案一律逐条确认，无视阈值。"""
    assert bulk_acceptable(_proposal(0.99), DEFAULTS, ocr_flag=True) is False


def test_below_the_force_manual_threshold_is_reported_as_manual():
    """`force_manual` 必须真的影响定级，否则它是个骗人的开关。

    设置页把它显示成「低于此值必须逐条确认」，保存时还校验它必须小于自动接受阈值，
    但代码里它只出现在两处顺序合法性判断里——调它什么都不会变。安全性全靠
    auto_accept 一个阈值撑着（低于它的本来就进不了 AUTO），所以这个开关不会造成
    坏数据，造成的是**错误的安全感**：运维以为自己设了一道人工复核底线。

    本仓库已经处理过同一类问题：provider 级预算原先也是「只存不看的死字段，
    设置页显示成一个开关、实际从不生效」，后来被改成真的生效。同样办。
    """
    from app.review.models import Proposal, ProposalKind, ProposalStatus
    from app.review.service import ReviewTier, assess
    from app.review.thresholds import Thresholds

    limits = Thresholds(auto_accept=0.90, force_manual=0.60, review_sample_rate=0.0)

    def _mapping(confidence: float) -> Proposal:
        return Proposal(
            id=1, kind=ProposalKind.MAPPING, status=ProposalStatus.PENDING,
            confidence=confidence, payload={"rationale": "直接对应"}, citations=[],
        )

    # 0.50 低于 force_manual：必须逐条确认，理由要说清楚是这条线拦的
    low = assess(_mapping(0.50), limits, ocr_flag=False)
    assert low.tier is ReviewTier.MANUAL
    assert "below_force_manual" in low.reasons

    # 0.75 在两条线之间：还是人工，但不是被这条线拦的
    middle = assess(_mapping(0.75), limits, ocr_flag=False)
    assert middle.tier is ReviewTier.DEFERRED
    assert "below_force_manual" not in middle.reasons
