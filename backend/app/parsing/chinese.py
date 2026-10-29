"""中文制度的条款切割（OQ-17）：`第X编/章/节/条`，以及 `一、`/`（一）`/`1.` 公文体例。

英文那套（vocab + headings 仲裁）认不出中文编号，原先中文制度要么整篇塌成一条，
要么只有 Word 标题样式那几行成了条款、`第X条` 全部沉在正文里。

两种体例，按文档自己的证据二选一，不混用：

- **条文体**（法规、办法、细则）：出现两条以上连续的 `第X条`。结构是
  编 › 章 › 节 › 条，条号全文连续；条内的 `（一）` `1.` 是列项，留在条的正文里
  ——离开引导句它们没有主语，拆开反而抽取不出完整要求。
- **公文体**（通知、方案、管理要求）：没有 `第X条`，但有两个以上连续的 `一、`。
  结构是 `一、` › `（一）` › `1.`。

**序号必须接得上**才算标题：`第X条` 要等于上一条加一，`（二）` 要等于同一父级下
上一个加一。这样正文里折行到行首的「第三条规定的……」之类引用不会被当成新条款。
层级按本文档**实际出现**的种类排名，不写死：没有「节」的文档，条就是第二级。
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from app.parsing.contract import ClauseNode

_DIGIT = {"〇": 0, "零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4, "五": 5,
          "六": 6, "七": 7, "八": 8, "九": 9}
_UNIT = {"十": 10, "百": 100, "千": 1000}
_NUM = r"[〇零一二两三四五六七八九十百千\d]+"


def cn_to_int(token: str) -> int | None:
    """「十二」→12、「一百零五」→105、「23」→23；认不出返回 None。"""
    if token.isdigit():
        return int(token)
    total = current = 0
    for char in token:
        if char in _DIGIT:
            current = _DIGIT[char]
        elif char in _UNIT:
            total += (current or 1) * _UNIT[char]
            current = 0
        else:
            return None
    return total + current


# 种类按从外到内的顺序排列，层级取本文档实际出现种类中的名次。
ARTICLE_KINDS = ("bian", "zhang", "jie", "tiao")
OUTLINE_KINDS = ("yi", "paren", "dot")

_SPACE = r"[ \t\u3000]"
_PATTERNS = {
    "bian": re.compile(rf"^{_SPACE}*第({_NUM})编{_SPACE}*(.*)$"),
    "zhang": re.compile(rf"^{_SPACE}*第({_NUM})章{_SPACE}*(.*)$"),
    "jie": re.compile(rf"^{_SPACE}*第({_NUM})节{_SPACE}*(.*)$"),
    "tiao": re.compile(rf"^{_SPACE}*第({_NUM})条(?:{_SPACE}+|(?=[\S]))?(.*)$"),
    "yi": re.compile(rf"^{_SPACE}*([一二三四五六七八九十]+)[、，,]{_SPACE}*(\S.*)$"),
    "paren": re.compile(rf"^{_SPACE}*[（(]([一二三四五六七八九十]+)[）)]{_SPACE}*(\S.*)$"),
    "dot": re.compile(rf"^{_SPACE}*(\d{{1,2}})[.．、](?!\d){_SPACE}*(\S.*)$"),
}
_LABEL = {
    "bian": "第{n}编", "zhang": "第{n}章", "jie": "第{n}节", "tiao": "第{n}条",
    "yi": "{n}、", "paren": "（{n}）", "dot": "{n}.",
}
# 标题是短的、不以句号收尾。再长就是一句正文，编号后的整句留作正文。
_TITLE_MAX = 30
# 「第X条」后面紧跟这些字的是引用（「第三条规定的…」「第五条第二款」），不是条款。
_REFERENCE_AFTER = re.compile(r"^(?:规定|所称|所列|所述|第|中|之|至|和|及|或|、|的)")


@dataclass(frozen=True)
class CnLabel:
    kind: str
    ordinal: int
    raw: str
    rest: str


def match_label(line: str, kinds: Iterable[str] = ARTICLE_KINDS + OUTLINE_KINDS) -> CnLabel | None:
    for kind in kinds:
        found = _PATTERNS[kind].match(line)
        if found is None:
            continue
        ordinal = cn_to_int(found.group(1))
        if ordinal is None or ordinal == 0:
            continue
        rest = found.group(2).strip()
        if kind == "tiao" and _REFERENCE_AFTER.match(rest):
            continue
        return CnLabel(kind, ordinal, found.group(1), rest)
    return None


def _sequential(ordinals: list[int]) -> int:
    """最长的「从 1 起逐个加一」的子序列长度，粗估这种编号是不是真结构。"""
    expected, run = 1, 0
    for value in ordinals:
        if value == expected:
            run += 1
            expected += 1
    return run


def detect_style(lines: Iterable[str]) -> str | None:
    """返回 "article"、"outline" 或 None（不是中文编号结构的文档）。"""
    found: dict[str, list[int]] = {}
    for line in lines:
        label = match_label(line)
        if label is not None:
            found.setdefault(label.kind, []).append(label.ordinal)
    if _sequential(found.get("tiao", [])) >= 2:
        return "article"
    if _sequential(found.get("zhang", [])) >= 2 and not found.get("yi"):
        return "article"
    if _sequential(found.get("yi", [])) >= 2:
        return "outline"
    return None


@dataclass
class Item:
    """切割的输入单位：一段（docx）或一行（PDF），或一个已建好的表格节点。"""

    text: str
    source_key: str | None = None
    page_ref: int | None = None
    table: ClauseNode | None = None


def _split_title(kind: str, rest: str) -> tuple[str | None, str]:
    """(标题, 正文)。章节类的整行都是标题；条没有标题；提纲类按句号切。"""
    if kind in ("bian", "zhang", "jie"):
        return (rest or None), ""
    if kind == "tiao":
        return None, rest
    if not rest:
        return None, ""
    # 先按句号切：PDF 折行后一行可能只剩「标题。正文前半句」，整行都不以句号收尾。
    head, sep, tail = rest.partition("。")
    if sep and len(head) <= _TITLE_MAX and not _CLAUSE_PUNCT.search(head):
        return head, tail.strip()
    if not sep and len(rest) <= _TITLE_MAX and not _CLAUSE_PUNCT.search(rest):
        return rest.rstrip("：:"), ""
    return None, rest


# 标题里不会有逗号、分号：带了就是一句正文（或折行的正文前半句）。
_CLAUSE_PUNCT = re.compile(r"[，,；;]")


def segment(items: list[Item], style: str) -> list[ClauseNode]:
    kinds = ARTICLE_KINDS if style == "article" else OUTLINE_KINDS
    present = [kind for kind in kinds
               if any(match_label(item.text, (kind,)) for item in items if item.table is None)]
    rank = {kind: index + 1 for index, kind in enumerate(present)}

    roots: list[ClauseNode] = []
    stack: list[tuple[str, ClauseNode]] = []
    # 上一个序号：条全文连续；其余按父节点作用域重来。
    last: dict[tuple[int, str], int] = {}
    pending_title: ClauseNode | None = None

    def scope(kind: str) -> tuple[int, str]:
        if kind == "tiao":
            return (0, kind)
        parent = next((node for k, node in reversed(stack) if rank[k] < rank[kind]), None)
        return (id(parent) if parent is not None else 0, kind)

    def append_body(text: str, key: str | None, page: int | None) -> None:
        if stack:
            node = stack[-1][1]
            node.text = f"{node.text}\n{text}".strip()
            if key:
                node.source_keys.append(key)
        else:
            from app.parsing.unstructured import append_text
            append_text(roots, text, page_ref=page, source_key=key)

    for item in items:
        if item.table is not None:
            pending_title = None
            if stack:
                parent = stack[-1][1]
                item.table.level = parent.level + 1
                parent.children.append(item.table)
            else:
                roots.append(item.table)
            continue
        text = item.text.strip()
        if not text:
            continue
        label = match_label(text, present)
        accepted = False
        if label is not None:
            key = scope(label.kind)
            previous = last.get(key)
            # 第一条可以不从 1 起（节选、修订稿），之后必须逐个加一。
            accepted = label.ordinal == previous + 1 if previous is not None else (
                label.ordinal == 1 or label.kind in ("tiao", "zhang"))
        if not accepted:
            if pending_title is not None and len(text) <= _TITLE_MAX and not text.endswith("。"):
                # 「第一章」独占一行，章名在下一行。
                pending_title.heading = text
                if item.source_key:
                    pending_title.source_keys.append(item.source_key)
                pending_title = None
                continue
            pending_title = None
            append_body(text, item.source_key, item.page_ref)
            continue

        last[scope(label.kind)] = label.ordinal
        level = rank[label.kind]
        number = _LABEL[label.kind].format(n=label.raw)
        title, body = _split_title(label.kind, label.rest)
        node = ClauseNode(
            heading=title or number, text=body, level=level, number=number,
            page_ref=item.page_ref, source_keys=[item.source_key] if item.source_key else [],
        )
        while stack and rank[stack[-1][0]] >= level:
            stack.pop()
        (stack[-1][1].children if stack else roots).append(node)
        stack.append((label.kind, node))
        # 新的父节点下面，子级序号重新计。
        for child_kind in present[present.index(label.kind) + 1:]:
            if child_kind != "tiao":
                last.pop((id(node), child_kind), None)
        pending_title = node if (title is None and label.kind in ("bian", "zhang", "jie")) else None
    return roots
