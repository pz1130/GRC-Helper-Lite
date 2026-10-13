from datetime import UTC, datetime

from app.evidence.models import EvidenceStatus


def display_status(
    status: EvidenceStatus | str,
    valid_until: datetime | None,
    now: datetime | None = None,
) -> str:
    """当场算出证据的有效性状态，**不落库**——派生值一落库就会和事实不同步。"""
    value = status.value if isinstance(status, EvidenceStatus) else status
    if value != EvidenceStatus.COLLECTED.value:
        return value
    if valid_until is None:
        return "valid"
    comparison_time = now or datetime.now(UTC)
    # 库里的时间戳带时区，而调用这个纯函数的夹具可能给不带时区的值。把不带时区的
    # 那个当成另一个的时区来比——否则比较结果取决于 Python 那条「不能混比」的异常。
    if valid_until.tzinfo is None and comparison_time.tzinfo is not None:
        valid_until = valid_until.replace(tzinfo=comparison_time.tzinfo)
    elif comparison_time.tzinfo is None and valid_until.tzinfo is not None:
        comparison_time = comparison_time.replace(tzinfo=valid_until.tzinfo)
    return "valid" if valid_until >= comparison_time else "expired"
