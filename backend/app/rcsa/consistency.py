"""人工关联的负面信号与自评结论交叉检查；不推断未登记事件或自动修改评分。"""

from datetime import date

from app.rcsa.schemas import AnswerV1


def contradictions(answer: AnswerV1, start: date, end: date, *, has_risks: bool) -> list[int]:
    # 设计有效不等于运行有效；只有运行有效或明确声称没有风险才需解释矛盾。
    controlled = answer.operating.rating == "effective" or (not has_risks and bool(answer.no_risk_rationale))
    if not controlled:
        return []
    return [
        index for index, signal in enumerate(answer.signals)
        if signal.started_on <= end and (signal.ended_on is None or signal.ended_on >= start)
    ]
