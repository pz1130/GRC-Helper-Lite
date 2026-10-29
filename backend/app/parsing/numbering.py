"""编号到条款层级，并过滤已知版面陷阱。

**编号长什么样不在这里定义**，在 `vocab.py`。这里只做三件 vocab 不管的事：
挡掉版面噪声（目录、页脚、版本历史）、把几种读法分别收集起来交给仲裁、
把选中的那一套装成树。

原先这里另有三条写死的正则，和 vocab 各认各的：`C.` 和 `(3)` 只有 docx 认、
`Chapter 4` 只有 PDF 认，于是同一份制度导出成两种格式会切出两棵不同的树。
"""

import re
from dataclasses import dataclass

from app.parsing import vocab
from app.parsing.contract import ClauseNode, ParseWarning
from app.parsing.headings import acts_as_headings, choose_heading_set
from app.parsing.unstructured import append_text
from app.parsing.vocab import Numbering, parse_label

# 词汇表在**调用时**取，不在 import 时绑死——注册一种新的（中文，OQ-17）
# 要能立刻成为一种读法，而不是等进程重启。
# 目录形态之一：引导点
_DOT_LEADER = re.compile(r"\.{4,}")
# 目录形态之二：行尾跟着页码。实测这批文件的目录不带引导点，而是
# "1 Introduction 1" 这种形状——只认引导点会让 84 条目录行全部漏进来。
_TOC_TRAILING_PAGE = re.compile(r"^\d+(?:\.\d+)*\s+.+?\s+\d{1,3}$")
# 「3 Appendix 2」「4.1 Phase 2」：末尾数字是标题自己的编号，不是页码。
_NUMBERED_NOUN_TAIL = re.compile(
    r"(?:appendix|annex|schedule|part|chapter|section|article|phase|level|tier|step|stage|"
    r"table|figure|version|附录|附件|附表|阶段|第)\s*\d{1,3}$",
    re.IGNORECASE,
)
_VERSION_ROW = re.compile(r"^\s*\d+(?:\.\d+)*\.?\s+\d{2}/\d{2}/\d{4}\b")
# 页脚。pdfplumber 会把字距拉开的页码渲染成 "1 | P a g e"
_PAGE_FOOTER = re.compile(
    r"^\s*\d+\s*\|\s*P\s*a\s*g\s*e\b|^\s*Page\s+\d+\s+of\s+\d+\s*$", re.IGNORECASE
)


@dataclass(frozen=True)
class NumberedHeading:
    number: str
    title: str
    line_index: int
    body_start: int | None = None

    @property
    def level(self) -> int:
        return self.number.count(".") + 1

    @property
    def parent_number(self) -> str | None:
        return self.number.rsplit(".", 1)[0] if "." in self.number else None


def is_toc_line(line: str) -> bool:
    """两种目录形态：带引导点的，和行尾跟页码的。"""
    stripped = line.strip()
    if _DOT_LEADER.search(line):
        return True
    return bool(_TOC_TRAILING_PAGE.match(stripped)) and not _NUMBERED_NOUN_TAIL.search(stripped)


def is_page_footer(line: str) -> bool:
    return bool(_PAGE_FOOTER.match(line))


def is_list_item(number: str, trailing_dot: str) -> bool:
    """"1. Open the settings panel" 是操作步骤，不是条款。

    **这条规则什么时候生效，由每份文档自己决定**（见 headings.acts_as_headings）。
    带点的单段编号既可能是标题，也可能是列表项。全局过滤会误删标题。
    """
    return trailing_dot == "." and "." not in number


def is_version_history_line(line: str) -> bool:
    return bool(_VERSION_ROW.match(line))


def _noise(line: str) -> str | None:
    """目录、页脚、版本历史——三类与文风无关的噪声，先挡掉再谈结构。"""
    if is_page_footer(line):
        return "footer"
    if is_toc_line(line):
        return "toc"
    if is_version_history_line(line):
        return "version"
    return None


def _ancestors(number: str) -> list[str]:
    """"4.1.1" → ["4", "4.1"]，由外向内。"""
    parts = number.split(".")
    return [".".join(parts[:cut]) for cut in range(1, len(parts))]


def _extract_decimal(lines: list[str]) -> tuple[list[NumberedHeading], list[str]]:
    warnings: list[str] = []
    seen: dict[str, NumberedHeading] = {}
    dropped = {"toc": 0, "version": 0, "orphan": 0, "footer": 0, "list": 0, "duplicate": 0}

    # 第一遍只做一件事：这份文档里的 `N.` 是章节标题还是正文列表项。
    # 判据是"它们有没有子号挂上去"，只看这一份文档自己（见 headings.py）。
    survey = [
        _number_of(found)
        for line in lines
        if _noise(line) is None and (found := vocab.vocabulary("decimal").match(line))
    ]
    dotted_survey = [
        _number_of(found) for line in lines
        if _noise(line) is None and (found := vocab.vocabulary("decimal").match(line))
        and found.trailing_dot
    ]
    dotted_are_headings = acts_as_headings(survey) or acts_as_headings(dotted_survey)

    ordered: list[NumberedHeading] = []
    synthetic: set[str] = set()

    def remember(heading: NumberedHeading) -> None:
        seen[heading.number] = heading
        ordered.append(heading)

    for index, line in enumerate(lines):
        found = vocab.vocabulary("decimal").match(line)
        if found is None:
            continue
        if (kind := _noise(line)) is not None:
            dropped[kind] += 1
            continue
        number = _number_of(found)
        if not dotted_are_headings and is_list_item(number, "." if found.trailing_dot else ""):
            dropped["list"] += 1
            continue

        heading = NumberedHeading(number=number, title=found.title, line_index=index)
        # 保留**第一次**出现。目录、页脚、列表项都已挡掉，剩下的重复只可能是
        # 正文噪声；而正文噪声总在真条款之后——保留后一次会让文末的噪声顶掉
        # 开头的真条款，并连带把它的子条款打成孤儿，整棵树就塌了。
        if heading.number in seen:
            if heading.number in synthetic:
                # 先前为了接住孤儿补出来的占位，真标题出现了就把名字换上。
                # 位置仍用占位那一处，免得父节点排到子节点后面去。
                position = ordered.index(seen[heading.number])
                real = NumberedHeading(
                    number=heading.number,
                    title=heading.title,
                    line_index=seen[heading.number].line_index,
                )
                ordered[position] = real
                seen[heading.number] = real
                synthetic.discard(heading.number)
            else:
                dropped["duplicate"] += 1
            continue

        # 孤儿不再丢弃，补出缺的父节点。实测丢弃的代价：文档O 丢掉 165 条、
        # 条款数归零，文档B 丢掉 244 条只剩 4 条——丢的不是噪声，是整棵树。
        # 占位用编号本身当标题：没有正文可依据，而空标题下游更难处理。
        for ancestor in _ancestors(heading.number):
            if ancestor not in seen:
                remember(NumberedHeading(number=ancestor, title=ancestor, line_index=index))
                synthetic.add(ancestor)
                dropped["orphan"] += 1
        remember(heading)

    # code 写成**字面量**而不是 f"parse.skipped_{kind}"：`make i18n-check` 是
    # 静态扫的，拼出来的 code 它看不见，词条漏了也就没人发现。
    # orphan 与其余五档**方向相反**：那五档是丢掉的行，它是补出来的父节点。
    # 原先共用"已跳过 N 条X"的模板，于是中文界面把"补出 165 条"说成"跳过 165 条"
    # ——正好相反，而英文词条写的是 Filled in，两边对不上也没人发现。
    labels = {
        "toc": ("已跳过 {n} 条目录行", "parse.skipped_toc"),
        "version": ("已跳过 {n} 条版本历史行", "parse.skipped_version"),
        "orphan": ("已补出 {n} 条缺失的父级编号", "parse.filled_orphan"),
        "footer": ("已跳过 {n} 条页脚行", "parse.skipped_footer"),
        "list": ("已跳过 {n} 条正文列表项", "parse.skipped_list"),
        "duplicate": ("已跳过 {n} 条重复编号", "parse.skipped_duplicate"),
    }
    for kind, count in dropped.items():
        if count:
            template, code = labels[kind]
            warnings.append(
                ParseWarning(template.format(n=count), code=code, params={"count": count})
            )

    # 稳定排序：补出来的父节点与孩子共用行号，插入顺序保证父在前。
    return sorted(ordered, key=lambda heading: heading.line_index), warnings


def _number_of(found: Numbering) -> str:
    """Numbering.parts → 条款号字符串。`label` 可能带尾点，这里要的是不带的。"""
    return ".".join(str(part) for part in found.parts)


def _collect(lines: list[str], reading, dedupe: bool = True) -> list[NumberedHeading]:
    """按一种词汇表通读全文，收成一种读法。"""
    found: list[NumberedHeading] = []
    seen: set[str] = set()
    for index, line in enumerate(lines):
        if _noise(line) is not None:
            continue
        label = reading.match(line)
        if label is None:
            continue
        number = _number_of(label)
        if dedupe and number in seen:
            continue
        seen.add(number)
        found.append(
            NumberedHeading(number=number, title=label.title.strip(), line_index=index)
        )
    return found


# 括号编号每一节都重起一遍 `(a)`，去重会把它压成一条。留着重复，交给仲裁
# 去判「每一节各起一遍」是列表项——那是它的活，不是收集阶段的。
_NEVER_DEDUPE = {"paren"}


def _readings(lines: list[str]) -> list[list[NumberedHeading]]:
    """每种词汇表算一种读法，十进制除外——它另有一整套噪声过滤。

    **加一种词汇表就自动多一种读法。** 中文（OQ-17）落地时只要在 `vocab.py`
    加一个实现、往 `VOCABULARIES` 里加一行，这个文件一个字都不用改。
    """
    return [
        _collect(lines, reading, dedupe=reading.kind not in _NEVER_DEDUPE)
        for reading in vocab.VOCABULARIES
        if reading.kind != "decimal"
    ]


def _is_caps_heading(line: str) -> bool:
    text = line.strip()
    if len(text) < 4 or len(text) > 80 or text.endswith("."):
        return False
    letters = [char for char in text if char.isalpha()]
    if len(letters) < 3 or not all(char.isupper() for char in letters):
        return False
    if parse_label(text) is not None:
        return False
    return _noise(text) is None


def _extract_caps(lines: list[str]) -> list[NumberedHeading]:
    found: list[NumberedHeading] = []
    for index, line in enumerate(lines):
        if not _is_caps_heading(line):
            continue
        found.append(
            NumberedHeading(
                number=str(len(found) + 1),
                title=line.strip(),
                line_index=index,
            )
        )
    return found


def extract_headings(lines: list[str]) -> tuple[list[NumberedHeading], list[str]]:
    """几种英文读法里挑自洽的那一棵树。

    `vocab.VOCABULARIES` 里每一种词汇表各算一种读法，都交给 `choose_heading_set`。
    用的是 docx 那条路（`vocab.parse_label`）的同一套词汇表，它认得的形态
    这里一个不少。
    全大写无编号行没有真编号，打分会虚高，只在编号读法全部低于门槛时才用。
    """
    decimal, warnings = _extract_decimal(lines)
    numbered = [candidate for candidate in (decimal, *_readings(lines)) if candidate]
    chosen = choose_heading_set([[heading.number for heading in item] for item in numbered])
    if chosen:
        for candidate in numbered:
            if [heading.number for heading in candidate] == list(chosen):
                return candidate, warnings

    caps = _extract_caps(lines)
    if len(caps) >= 3:
        return caps, [
            *warnings,
            ParseWarning("未识别出编号条款，改用全大写英文标题", code="parse.caps_fallback"),
        ]
    return decimal, warnings


def assemble_tree(
    headings: list[NumberedHeading],
    lines: list[str],
    page_of: dict[int, int] | None = None,
) -> list[ClauseNode]:
    pages = page_of or {}
    nodes: dict[str, ClauseNode] = {}
    roots: list[ClauseNode] = []

    # 标题识别只负责结构；没有标题、或首标题前的文字不能因此消失。
    prefix_end = headings[0].line_index if headings else len(lines)
    for index, line in enumerate(lines[:prefix_end]):
        append_text(roots, line.strip(), page_ref=pages.get(index), source_key=f"line:{index}")

    for position, heading in enumerate(headings):
        end = headings[position + 1].line_index if position + 1 < len(headings) else len(lines)
        body = "\n".join(
            line.strip() for line in lines[(heading.body_start if heading.body_start is not None else heading.line_index + 1) : end] if line.strip()
        )
        node = ClauseNode(
            heading=heading.title,
            text=body,
            level=heading.level,
            number=heading.number,
            page_ref=pages.get(heading.line_index),
            source_keys=[f"line:{index}" for index in range(heading.line_index, end)
                         if lines[index].strip()],
        )
        nodes[heading.number] = node

        parent = nodes.get(heading.parent_number) if heading.parent_number else None
        if parent is None:
            roots.append(node)
        else:
            parent.children.append(node)

    return roots
