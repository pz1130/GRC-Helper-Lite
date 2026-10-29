"""两种解析器共用的树级修补：英文条文体的标题、章层级，以及被误升格的正文列表。

都是在已组装好的 ClauseNode 树上做的保守修补——只在证据充分（编号连续、
形态明确）时动手，其余情况原样返回。
"""
from __future__ import annotations

import re

from app.parsing.contract import ClauseNode

_BARE_ARTICLE = re.compile(r"\s*(Article|Section)\s+\d+[A-Za-z]?\s*[.:]?\s*", re.IGNORECASE)
_CHAPTER = re.compile(r"\s*(?:CHAPTER|Chapter)\s+([IVXLC]+|\d+)\s*[.:]?\s*(.*)$")
_NUMBERED_START = re.compile(r"\s*(?:\d|\(|[a-z]\)|[IVXLC]+\.)")
_ROMAN = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100}


def _roman(token: str) -> int:
    if token.isdigit():
        return int(token)
    total = 0
    for here, after in zip(token, token[1:] + " ", strict=True):
        value = _ROMAN[here]
        total += -value if _ROMAN.get(after, 0) > value else value
    return total


def _looks_like_title(line: str) -> bool:
    line = line.strip()
    return (0 < len(line) <= 120 and not line.endswith((".", ";", ":", ","))
            and not _NUMBERED_START.match(line) and not _CHAPTER.match(line))


def adopt_article_titles(nodes: list[ClauseNode]) -> None:
    """独占一行的「Article 3」后面那行短标题，才是这一条的标题。"""
    for node in nodes:
        if node.kind == "section" and _BARE_ARTICLE.fullmatch(node.heading or ""):
            first, _, rest = node.text.partition("\n")
            if _looks_like_title(first):
                node.heading, node.text = first.strip(), rest
        adopt_article_titles(node.children)


_PART = re.compile(r"\s*(?:PART|Part)\s+([IVXLC]+|\d+)\s*[.:]?\s*(.*)$")


def nest_chapters(nodes: list[ClauseNode]) -> list[ClauseNode]:
    """英国 SI 的「PART 1 > CHAPTER 1 > 条」：先按 PART 分组，章号在每个 PART 内重新计数。"""
    parts = _nest_marker(nodes, _PART, "Part", 1)
    if parts is nodes:
        return _nest_marker(nodes, _CHAPTER, "Chapter", 1)
    for part in parts:
        if part.number and part.number.startswith("Part "):
            # 「PART 2 / CHAPTER 1」紧挨着：第一章的标记在 PART 自己的正文里。
            head = ClauseNode(heading="", text=part.text, level=2, kind="unstructured")
            nested = _nest_marker([head, *part.children], _CHAPTER, "Chapter", 2)
            if nested and nested[0] is head:
                part.text = head.text
                nested = nested[1:]
            else:
                part.text = ""
            part.children = nested
    return parts


def _nest_marker(nodes: list[ClauseNode], pattern: re.Pattern, word: str,
                 level: int) -> list[ClauseNode]:
    """正文里独占一行的 CHAPTER I / II …（连续编号、至少两章）提升为一层章节。

    章标题行通常被吞进前一条的正文末尾（或文档开头的无结构段落）。
    """
    found = []  # (node index, line index, number, title)
    for position, node in enumerate(nodes):
        lines = node.text.split("\n")
        for index, line in enumerate(lines):
            match = pattern.fullmatch(line)
            # 「Chapter VIII, a body as referred to…」是正文里的引用，不是章标题。
            if match and match[2] and (match[2][0] in ",;)(" or match[2][0].islower()):
                continue
            if match:
                found.append((position, index, match[1], match[2].strip()))
    # 全大写的 CHAPTER 至少两处时，混合大小写的「Chapter VII.」是折行到行首的正文引用。
    def _caps(item) -> bool:
        return nodes[item[0]].text.split("\n")[item[1]].lstrip().startswith(word.upper())
    if sum(map(_caps, found)) >= 2:
        found = [item for item in found if _caps(item)]
    # 目录残行、附表、说明注释会各自再出现一遍 PART 1…：只取最长的一段 +1 递增序列。
    runs: list[list] = []
    for item in found:
        if runs and _roman(item[2]) == _roman(runs[-1][-1][2]) + 1:
            runs[-1].append(item)
        else:
            runs.append([item])
    # 等长时取跨节点最多的一段：目录残行全挤在开头那个无结构段落里。
    found = max(runs, key=lambda run: (len({item[0] for item in run}), len(run))) if runs else []
    if len(found) < 2:
        return nodes

    by_node: dict[int, list[tuple[int, str, str]]] = {}
    for position, index, number, title in found:
        by_node.setdefault(position, []).append((index, number, title))

    result: list[ClauseNode] = []
    current: ClauseNode | None = None
    for position, node in enumerate(nodes):
        marks = by_node.get(position)
        if not marks:
            (current.children if current else result).append(node)
            continue
        lines = node.text.split("\n")
        cut = marks[0][0]
        node.text = "\n".join(lines[:cut]).strip("\n")
        if node.text or node.kind != "unstructured" or node.children:
            (current.children if current else result).append(node)
        for offset, (index, number, title) in enumerate(marks):
            stop = marks[offset + 1][0] if offset + 1 < len(marks) else len(lines)
            tail = lines[index + 1:stop]
            if not title and tail and _looks_like_title(tail[0]):
                title, tail = tail[0].strip(), tail[1:]
            current = ClauseNode(heading=title or f"{word} {number}", text="\n".join(tail).strip("\n"),
                                 level=level, number=f"{word} {number}", page_ref=node.page_ref)
            result.append(current)
    for chapter in result:
        if chapter.number and chapter.number.startswith(f"{word} "):
            _deepen(chapter.children, level + 1)
    return result


def _deepen(nodes: list[ClauseNode], level: int) -> None:
    for node in nodes:
        node.level = level
        _deepen(node.children, level + 1)


def demote_restarted_lists(nodes: list[ClauseNode]) -> list[ClauseNode]:
    """顶层编号倒退重来（2 之后又是 1. 2. 3.）、且这些「条款」只有一句标题没有正文：
    那是正文里的列表，收回前一条的正文；随后补出来的空壳同号节点并回前一条。
    """
    result: list[ClauseNode] = []
    last: int | None = None
    in_list = False
    for node in nodes:
        number = node.number if node.number and node.number.isdigit() else None
        previous = result[-1] if result else None
        bare = not node.text.strip() and not node.children and len(node.heading) > 40
        if number and bare and previous is not None and last is not None and (
                in_list or int(number) <= last):
            in_list = True
            previous.text = "\n".join(filter(None, [previous.text, f"{number}. {node.heading}"]))
            previous.source_keys.extend(node.source_keys)
            continue
        if (number and previous is not None and previous.number == number
                and node.heading.strip() == number and not node.text.strip()):
            previous.children.extend(node.children)
            continue
        in_list = False
        if number:
            last = int(number)
        result.append(node)
    return result
