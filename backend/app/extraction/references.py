"""仅解析明确指向本文的条款引用，不猜外部文件或隐含术语关系。"""

import re
from collections import defaultdict
from dataclasses import dataclass

from app.clauses.models import Clause

# 不把金额、日期或外部标准的编号当成本文件条款。
_TOKEN = r"\d+(?:\.\d+)*|[零〇一二三四五六七八九十百千]+"
_LOCAL = re.compile(
    rf"本(?:文件|制度|办法|规定|标准|政策)\s*第\s*(?P<zh>{_TOKEN})\s*(?P<unit>[条章节])"
    r"|\b(?:section|clause|article|chapter)\s+(?P<en>\d+(?:\.\d+)*)"
    r"\s+of\s+this\s+(?:document|policy|procedure|standard)\b",
    re.IGNORECASE,
)


def label_key(label: str) -> str:
    value = label.strip().rstrip(".")
    value = re.sub(r"^(?:section|clause|article|chapter)\s+", "", value, flags=re.IGNORECASE)
    return value.casefold()


@dataclass(frozen=True)
class Reference:
    source_clause_id: int
    label: str
    status: str
    target_clause_id: int | None = None


class ReferenceIndex:
    def __init__(self, clauses: list[Clause]) -> None:
        self.labels: dict[tuple[int, str], list[Clause]] = defaultdict(list)
        for clause in clauses:
            self.labels[(clause.document_id, label_key(clause.citation_label))].append(clause)

    def resolve(self, clause: Clause) -> list[Reference]:
        refs: list[Reference] = []
        seen: set[str] = set()
        for match in _LOCAL.finditer(f"{clause.heading or ''}\n{clause.text or ''}"):
            label = match.group("en") or f"第{match.group('zh')}{match.group('unit')}"
            # 阿拉伯数字的中文引用也能对应解析器保存的纯数字标签。
            keys = [label_key(label)]
            if match.group("zh") and match.group("zh").isascii():
                keys.append(match.group("zh"))
            if label in seen:
                continue
            seen.add(label)
            candidates = {c.id: c for key in keys
                          for c in self.labels.get((clause.document_id, key), [])}
            status = "resolved" if len(candidates) == 1 else (
                "missing" if not candidates else "ambiguous"
            )
            refs.append(Reference(clause.id, label, status,
                                  next(iter(candidates)) if len(candidates) == 1 else None))
        return refs
