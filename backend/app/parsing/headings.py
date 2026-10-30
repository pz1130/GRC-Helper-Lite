"""标题仲裁：同一份文档的几种读法里，挑自洽的那一种。

带点的单段编号既可能是标题也可能是列表项，应按文档内的层级关系判断。
四个维度相乘，防止某一维的高分掩盖另一维的失效。
"""

from collections.abc import Sequence

Label = str | tuple[int, ...]


def _parts(label: Label) -> tuple[int, ...]:
    if isinstance(label, tuple):
        return label
    return tuple(int(piece) for piece in label.split(".") if piece)


def _unique_ratio(parts: list[tuple[int, ...]]) -> float:
    """同一个编号出现多次，多半是正文列表项在反复重来。"""
    return len(set(parts)) / len(parts)


def _parent_ratio(parts: list[tuple[int, ...]]) -> float:
    """子号的父亲必须在它之前出现过。没有子号时这一维不参与评分。"""
    seen: set[tuple[int, ...]] = set()
    total = hit = 0
    for part in parts:
        if len(part) > 1:
            total += 1
            if part[:-1] in seen:
                hit += 1
        seen.add(part)
    return hit / total if total else 1.0


def _ascending_ratio(parts: list[tuple[int, ...]]) -> float:
    """同一层的兄弟应当逐个递增。回退和跳号都算不自洽。"""
    last: dict[tuple[int, ...], int] = {}
    total = hit = 0
    for part in parts:
        parent, index = part[:-1], part[-1]
        previous = last.get(parent)
        if previous is not None:
            total += 1
            if index == previous + 1:
                hit += 1
        last[parent] = index
    return hit / total if total else 1.0


def _starts_at_one(parts: list[tuple[int, ...]]) -> float:
    tops = [part[0] for part in parts if len(part) == 1]
    return 1.0 if not tops or tops[0] == 1 else 0.5


def consistency_score(labels: Sequence[Label]) -> float:
    """这批编号凑成一棵树的程度，0–1。空集是 0。"""
    parts = [_parts(label) for label in labels]
    parts = [part for part in parts if part]
    if not parts:
        return 0.0
    return (
        _unique_ratio(parts)
        * _parent_ratio(parts)
        * _ascending_ratio(parts)
        * _starts_at_one(parts)
    )


# 两种读法的自洽度差在这个范围内，就算打平，改看谁解释得多。
TIE = 0.05
# 低于这个分数的读法一律不采纳——宁可当作"这份文档没有编号结构"，
# 让格式兜底或塌树闸门去处理，也不要建一棵错的树。
FLOOR = 0.5


def choose_heading_set(candidates: Sequence[Sequence[Label]]) -> Sequence[Label]:
    """几种读法里挑一种；都不自洽就返回空。

    打平时选**解释得多**的那个：一条标题的集合天然满分，但它什么也没说明。
    """
    scored = [(consistency_score(candidate), candidate) for candidate in candidates]
    scored = [(score, candidate) for score, candidate in scored if score >= FLOOR]
    if not scored:
        return []
    best = max(score for score, _ in scored)
    contenders = [candidate for score, candidate in scored if score >= best - TIE]
    return max(contenders, key=len)


# ── `N.` 到底是标题还是列表项 ────────────────────────────────
# 原始编号流混有目录、页眉等噪声，自洽度不能单独作为标题判据。
# 有下级编号的单段编号占比提供另一种结构线索；仍属于启发式。
PARENTHOOD_THRESHOLD = 0.30


def parenthood_ratio(labels: Sequence[Label]) -> float:
    """单段编号里，有子号挂上去的占多少。没有单段编号时返回 0。"""
    parts = [_parts(label) for label in labels]
    parts = [part for part in parts if part]
    singles = sorted({part for part in parts if len(part) == 1})
    if not singles:
        return 0.0
    parents = {part[:-1] for part in parts if len(part) > 1}
    return sum(1 for single in singles if single in parents) / len(singles)


def _is_one_ascending_run(labels: Sequence[Label]) -> bool:
    """单段编号只出现一次、并且从 1 开始逐个递增。

    扁平编号的文档（`1. Purpose` / `2. Composition` / `3. Reporting`，没有子节）
    靠"有没有子号"判不出来——它本来就没有子号。区分它和正文列表项的是另一件事：
    正文列表在每一节里各起一遍（1,2,3,1,2,1,2,3…），扁平章节从头到尾只升一次。
    """
    singles = [_parts(label) for label in labels]
    singles = [part[0] for part in singles if len(part) == 1]
    return len(singles) >= 3 and singles == list(range(1, len(singles) + 1))


def acts_as_headings(labels: Sequence[Label]) -> bool:
    """这批单段编号该当成章节标题，还是正文列表项。"""
    return parenthood_ratio(labels) >= PARENTHOOD_THRESHOLD or _is_one_ascending_run(labels)


# ── 标题装着半句话 ────────────────────────────────────────────
# 段落级编号的文档（文档A 的 `2.2` 就是一个段落，没有标题）里，解析器把编号那一行
# 当标题、剩下的当正文，一句话被劈成两半。代价不只是难看：`heading_path` 由它拼成，
# 而 `rebuild_chunks` 把 heading_path 当上下文前缀拼进**每一个 chunk**——半句话的
# 前缀同时污染向量检索和全文检索；确认队列里审核者看到的也是半句话。
#
# 正文的小写起始是前一行属于同一句话的启发式线索。


def continues_the_heading(text: str) -> bool:
    """正文以小写字母开头 ⇒ 上面那行是同一句话的前半截，不是标题。"""
    return (text or "").lstrip()[:1].islower()


def demote_fragment_headings(nodes: list) -> None:
    """就地把"标题其实是半句话"的条款改回去：整段收进正文，标题改用编号。

    只动**有编号**的条款——没有编号就没有别的东西可以当标题，留着半句话
    好过留个空标题。
    """
    for node in nodes:
        demote_fragment_headings(node.children)
        if node.kind != "section" or not (node.number or "").strip():
            continue
        if not continues_the_heading(node.text or ""):
            continue
        if _wrapped_title(node):
            continue
        node.text = f"{node.heading} {node.text}".strip()
        node.heading = node.number


def _wrapped_title(node) -> bool:
    """「Transparent information … for the exercise / of the rights of the data subject / 1. …」：

    折行的长标题。第二行很短、不以句末标点结束、下一行是新段落（编号或大写开头）时，
    把第二行接回标题，而不是把整条降成半句话。
    """
    first, _, rest = (node.text or "").partition("\n")
    first = first.strip()
    following = rest.lstrip()[:1]
    heading = (node.heading or "").strip()
    if not (heading and len(first) <= 60 and len(heading) >= 40 and heading[-1:] not in ".;:,"
            and first[-1:] not in ".;:," and (following.isdigit() or following.isupper())):
        return False
    node.heading, node.text = f"{heading} {first}", rest
    return True
