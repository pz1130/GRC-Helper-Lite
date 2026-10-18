"""评估指标：纯函数，确定性，有单元测试（tests/test_eval_metrics.py）。"""

from collections.abc import Iterable


def base_control(code: str) -> str:
    """AC-2.1 / AC-2(1) → AC-2：OLIR 只列基础控制项。"""
    return code.split("(")[0].split(".")[0]


def prf(predicted: Iterable[tuple[str, str]], expected: Iterable[tuple[str, str]]) -> dict[str, float | int]:
    pred, gold = set(predicted), set(expected)
    tp = len(pred & gold)
    precision = tp / len(pred) if pred else 0.0
    recall = tp / len(gold) if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"tp": tp, "predicted": len(pred), "expected": len(gold),
            "precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}


def candidate_recall(candidates: dict[str, list[str]], expected: dict[str, set[str]], k: int) -> dict[str, float | int]:
    """每个框架项的前 k 个候选里，OLIR 正例占全部正例的比例（预筛的召回上限）。"""
    hit = total = 0
    for item, positives in expected.items():
        top = set(candidates.get(item, [])[:k])
        hit += len(top & positives)
        total += len(positives)
    return {"k": k, "hit": hit, "positives": total, "recall": round(hit / total, 4) if total else 0.0}


def rate(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def pair_details(
    predicted: dict[tuple[str, str], dict[str, str]],
    gold: set[tuple[str, str]],
    candidates: dict[str, set[str]],
) -> dict[str, list[dict[str, object]]]:
    """逐对明细，给人工抽看用（K16）。

    missed：OLIR 有、模型没给；`in_candidates` 区分「模型见过没选」与「预筛没给它看」。
    extra：模型给了、OLIR 没有——只算 OLIR 覆盖到的子类，与 prf 的计分口径一致。
    hit：两边都有，带模型给的强度——OLIR 不分强弱，标成「支撑」的命中不关缺口。
    """
    scored = {csf for csf, _ in gold}
    missed = [
        {"csf": csf, "control": control, "in_candidates": control in candidates.get(csf, set())}
        for csf, control in sorted(gold - predicted.keys())
    ]
    extra = [
        {"csf": csf, "control": control, **predicted[(csf, control)]}
        for csf, control in sorted(predicted.keys() - gold)
        if csf in scored
    ]
    hit = [{"csf": csf, "control": control, **predicted[(csf, control)]} for csf, control in sorted(predicted.keys() & gold)]
    return {"missed": missed, "extra": extra, "hit": hit}
