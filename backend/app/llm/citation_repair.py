"""引文修复：把「差一点就逐字」的引文对齐回原文（K15）。

失败批次大多是模型交的引文不是逐字照抄——大小写、漏一个冠词、标点、单个错字。
这里在闸 4 判失败之前，先在原文里找最相近的一段：只有**高度相似且差异不碰
语义要害**（数字、否定、情态动词）时，才把引文换成原文那一段；否则照旧判失败，
交给纠错重试。

刻意保守：
· 引文太短（< MIN_TOKENS 个词）不修——短引文的「相近」没有意义。
· 相似度低于 MIN_RATIO 不修。
· 差异里出现数字、否定词、情态词，一律不修——「shall」改成「may」、「30 天」
  改成「90 天」是意思变了，不是抄错了。
· 只接受「抄错」形态的差异：大小写与词边标点（比较时折叠）、单词内的笔误
  （逐词字符相似度 ≥ TYPO_RATIO）、多/漏冠词 a/an/the。漏掉或加进任何实词都
  不修——「Where practical」≠「Where operationally practical」，限定语丢了是改义。
· 修复后的引文**一定**是原文的子串（取的就是原文的词），闸 4 的逐字检查照常成立。
"""

from dataclasses import dataclass
from difflib import SequenceMatcher

from app.llm.validation import normalize

MIN_TOKENS = 5
MIN_RATIO = 0.90
# 最多允许几处词级差异（替换/插入/删除的词数之和）
MAX_EDITED_TOKENS = 3
TYPO_RATIO = 0.8
_ARTICLES = frozenset({"a", "an", "the"})
_PROTECTED = frozenset({
    "not", "no", "never", "none", "nor", "without", "cannot", "can't", "won't",
    "shall", "must", "should", "may", "might", "will", "can", "could", "would",
    "required", "prohibited", "optional", "all", "any", "only", "except", "unless",
})


@dataclass(frozen=True)
class Repair:
    quote: str
    ratio: float
    edited_tokens: int


def _key(token: str) -> str:
    return token.casefold().strip(".,;:!?()[]\"'")


def _touches_meaning(tokens: list[str]) -> bool:
    return any(
        _key(token) in _PROTECTED or any(ch.isdigit() for ch in token) for token in tokens
    )


def repair_quote(quote: str, source: str) -> Repair | None:
    """引文已逐字命中时返回 None（无需修复）；修不了也返回 None。"""
    norm_quote, norm_source = normalize(quote), normalize(source)
    if not norm_quote or norm_quote in norm_source:
        return None
    q_tokens, s_tokens = norm_quote.split(), norm_source.split()
    if len(q_tokens) < MIN_TOKENS or not s_tokens:
        return None
    q_keys = [_key(t) for t in q_tokens]
    s_keys = [_key(t) for t in s_tokens]

    best: tuple[float, int, int] | None = None
    n = len(q_tokens)
    for length in range(max(1, n - MAX_EDITED_TOKENS), n + MAX_EDITED_TOKENS + 1):
        if length > len(s_tokens):
            break
        for start in range(len(s_tokens) - length + 1):
            window = s_keys[start:start + length]
            # 快速剪枝：首尾至少一个对得上
            if window[0] != q_keys[0] and window[-1] != q_keys[-1]:
                continue
            ratio = SequenceMatcher(None, q_keys, window, autojunk=False).ratio()
            if best is None or ratio > best[0]:
                best = (ratio, start, length)
    if best is None or best[0] < MIN_RATIO:
        return None

    ratio, start, length = best
    window_tokens = s_tokens[start:start + length]
    matcher = SequenceMatcher(None, q_keys, s_keys[start:start + length], autojunk=False)
    edited = 0
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        q_part, s_part = q_tokens[i1:i2], window_tokens[j1:j2]
        if _touches_meaning(q_part + s_part):
            return None
        if tag == "replace":
            if len(q_part) != len(s_part) or not all(
                SequenceMatcher(None, _key(a), _key(b)).ratio() >= TYPO_RATIO
                for a, b in zip(q_part, s_part, strict=True)
            ):
                return None
        elif not all(_key(token) in _ARTICLES for token in q_part + s_part):
            return None
        edited += max(i2 - i1, j2 - j1)
    if edited > MAX_EDITED_TOKENS:
        return None
    repaired = " ".join(window_tokens)
    if repaired not in norm_source:  # 防御：理论上必然成立
        return None
    return Repair(quote=repaired, ratio=round(ratio, 4), edited_tokens=edited)
