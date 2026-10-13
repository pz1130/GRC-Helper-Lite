"""编号词汇表：认出一行开头的编号，**不判断它是不是标题**。

同一个 `1.` 在一份文档里是章节标题（文档A 的 `1. Introduction`），在另一份里是
操作步骤（`1. Open the settings panel`）。那个判断依赖整篇文档的自洽性，属于
`headings.py` 的仲裁；这里只回答"这行开头有没有编号、编号是什么"。

实测 36 份文档，英文就用到三种词汇：

    1.1. Background            decimal   样本语料、文档A、文档K
    (a) The rules set forth    paren     文档M，226 行
    C.  Reporting              letter    文档P 的 A.–D.
    Chapter 4 Controls         article   Article / Section / Chapter 三个关键词

**这里是唯一一处定义"行首编号长什么样"。** PDF、docx、「粘贴纯文本」三条路
都从这里取，不要在别处再写一条正则——`numbering.py` 原先那样做过，结果是
同一份制度导出成 .docx 和 .pdf 切出来的条款树不一样（见 test_parsing_vocab.py
末尾三条）。

中文（`第三章` / `一、` / `（一）`）是 OQ-17，本期不实现——但 `Vocabulary`
这个协议就是给它留的位置：加一个实现、往 `VOCABULARIES` 里加一行即可，
调用方一个字不用改。
"""

import re
from dataclasses import dataclass
from typing import Protocol

# 单段编号超过这个值就不是编号，是年份或金额。没有哪份文档有第 2024 节。
# 这是**形状**判断，不是文风判断——换哪家机构都成立。
MAX_SINGLE_PART = 999
_DATE_PREFIX = re.compile(
    r"^\s*(?:\d{1,2}[./-]\d{1,2}[./-]\d{4}\b|"
    r"\d{1,2}\s+(?:January|February|March|April|May|June|July|August|September|"
    r"October|November|December|Jan|Feb|Mar|Apr|Jun|Jul|Aug|Sep|Oct|Nov|Dec)"
    r"\s+\d{4}\b)", re.IGNORECASE,
)


@dataclass(frozen=True)
class Numbering:
    label: str
    parts: tuple[int, ...]
    kind: str
    title: str
    trailing_dot: bool


class Vocabulary(Protocol):
    kind: str

    def match(self, line: str) -> Numbering | None: ...


@dataclass(frozen=True)
class _Decimal:
    kind: str = "decimal"
    _re: re.Pattern = re.compile(r"^\s*(\d+(?:\.\d+)*)(\.?)[ \t]+(\S.*?)\s*$")

    def match(self, line: str) -> Numbering | None:
        found = self._re.match(line)
        if found is None:
            return None
        if _DATE_PREFIX.match(line):
            return None
        parts = tuple(int(piece) for piece in found.group(1).split("."))
        if len(parts) == 1 and parts[0] > MAX_SINGLE_PART:
            return None
        return Numbering(
            label=found.group(1) + found.group(2),
            parts=parts,
            kind=self.kind,
            title=found.group(3),
            trailing_dot=found.group(2) == ".",
        )


@dataclass(frozen=True)
class _Paren:
    kind: str = "paren"
    _re: re.Pattern = re.compile(r"^\s*\((\d{1,3}|[a-zA-Z])\)[ \t]+(\S.*?)\s*$")

    def match(self, line: str) -> Numbering | None:
        found = self._re.match(line)
        if found is None:
            return None
        token = found.group(1)
        part = int(token) if token.isdigit() else ord(token.lower()) - ord("a") + 1
        return Numbering(
            label=f"({token})",
            parts=(part,),
            kind=self.kind,
            title=found.group(2),
            trailing_dot=False,
        )


@dataclass(frozen=True)
class _Letter:
    kind: str = "letter"
    _re: re.Pattern = re.compile(r"^\s*([A-Z])\.[ \t]+(\S.*?)\s*$")

    def match(self, line: str) -> Numbering | None:
        found = self._re.match(line)
        if found is None:
            return None
        return Numbering(
            label=f"{found.group(1)}.",
            parts=(ord(found.group(1)) - ord("A") + 1,),
            kind=self.kind,
            title=found.group(2),
            trailing_dot=True,
        )


@dataclass(frozen=True)
class _Article:
    """`Article 7` / `Section 2` / `Chapter 4`——编号写在关键词后面。

    分隔符既可能是空白，也可能是 `.` `:` 破折号：`Chapter 4 — Controls`。
    """

    kind: str = "article"
    _re: re.Pattern = re.compile(
        r"^\s*(?:Article|Section|Chapter)\s+(\d+(?:\.\d+)*)(?:(?:\s*[.:—–-]\s*|\s+)(\S.*?))?\s*$",
        re.IGNORECASE,
    )

    def match(self, line: str) -> Numbering | None:
        found = self._re.match(line)
        if found is None:
            return None
        parts = tuple(int(piece) for piece in found.group(1).split("."))
        if len(parts) == 1 and parts[0] > MAX_SINGLE_PART:
            return None
        return Numbering(
            label=found.group(1),
            parts=parts,
            kind=self.kind,
            title=found.group(2) or line.strip(),
            trailing_dot=False,
        )


# 顺序即优先级。四种形态互不重叠，所以顺序目前不影响结果；
# 加中文词汇表时也不会——它的起始字符与这四种都不同。
VOCABULARIES: tuple[Vocabulary, ...] = (_Decimal(), _Paren(), _Letter(), _Article())


def vocabulary(kind: str) -> Vocabulary:
    """按 kind 取一种词汇表。

    调用方只想要某一种读法时用它——`numbering.extract_headings` 就是这样
    把"十进制"、"关键词"、"括号"三种读法**分开**收集再交给仲裁的。
    `parse_label` 那种"先匹配上的算"在那里不适用：仲裁要的是几套并列的候选，
    不是一个赢家。
    """
    for candidate in VOCABULARIES:
        if candidate.kind == kind:
            return candidate
    raise KeyError(f"没有这种词汇表：{kind}")


def parse_label(line: str) -> Numbering | None:
    """认出行首编号；认不出返回 None。不做任何"是不是标题"的判断。"""
    for candidate in VOCABULARIES:
        found = candidate.match(line)
        if found is not None:
            return found
    return None
