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
