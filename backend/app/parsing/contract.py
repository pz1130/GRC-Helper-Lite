"""解析层的对外契约。

parsing 不碰数据库：只接受路径、返回 ParsedDocument。
"""

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Protocol, Self


class ParseWarning(str):
    """一条解析告警：**就是那句中文**，额外挂着 code 和句子里的变量。

    为什么是 `str` 的子类而不是 dataclass：解析层到处把告警当字符串用——
    `"\n".join(warnings)`、`any("文本层" in w for w in warnings)`、
    `warnings.extend(...)`。换成结构体，这些地方连同它们的测试全得跟着动，
    而那些改动和"能不能翻译"毫无关系。这样一来调用方一个字不用改，
    只有**存库**的那一处多读一个 `.code`。

    没带 code 的普通字符串照旧能混在同一个列表里——迁移期就是这样。
    """

    code: str
    params: dict[str, object]

    def __new__(
        cls, text: str, *, code: str, params: dict[str, object] | None = None
    ) -> Self:
        warning = super().__new__(cls, text)
        warning.code = code
        warning.params = params or {}
        return warning


class ParseError(Exception):
    """解析失败。`reason` 是中文兜底；界面按 `code` 查词条（与 ParseWarning 同一套）。"""

    def __init__(self, reason: str, *, code: str, params: dict[str, object] | None = None) -> None:
        self.reason = reason
        self.code = code
        self.params = params or {}
        super().__init__(reason)


@dataclass
class ClauseNode:
    heading: str
    text: str
    level: int
    number: str | None = None
    page_ref: int | None = None
    kind: str = "section"
    children: list["ClauseNode"] = field(default_factory=list)
    source_keys: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class SourceBlock:
    key: str
    text: str
    kind: str
    locator: dict[str, object]


@dataclass(frozen=True)
class DocumentMeta:
    title: str | None
    version: str | None
    owner: str | None
    approver: str | None
    approved_date: date | None
    effective_date: date | None
    doc_type: str | None


@dataclass(frozen=True)
class ParsedDocument:
    meta: DocumentMeta
    clauses: list[ClauseNode]
    warnings: list[str] = field(default_factory=list)
    ocr_used: bool = False
    source_blocks: list[SourceBlock] = field(default_factory=list)
    source_origin: str = "unknown"


class Parser(Protocol):
    def parse(self, path: Path) -> ParsedDocument: ...
