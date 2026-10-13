"""有明确边框的 PDF 表格：保留物理单元格，保守识别带重复表头的跨页延续，不猜测无边框列。"""

import re
from collections import Counter
from dataclasses import dataclass

from pdfplumber.utils import extract_text

from app.parsing.contract import ClauseNode, SourceBlock


@dataclass
class PdfTable:
    page: int
    number: int
    line_indexes: list[int]
    cells: list[SourceBlock]
    page_height: float = 0

    def node(self, level: int) -> ClauseNode:
        # 每个物理单元格只输出一次；坐标明确表达多层表头和跨行/跨列作用范围。
        rows: dict[int, list[str]] = {}
        for cell in self.cells:
            loc = cell.locator
            positions = loc["positions"]
            first, last = positions[0], positions[-1]
            label = f"column {first['column']}"
            if last["column"] != first["column"]:
                label += f"-{last['column']}"
            if last["row"] != first["row"]:
                label += f"; spans rows {first['row']}-{last['row']}"
            rows.setdefault(first["row"], []).append(f"[{label}] {cell.text}")
        text = "\n\n".join(f"Row {row}\n" + "\n".join(cells) for row, cells in rows.items())
        return ClauseNode(
            heading=f"Table {self.number} (page {self.page})", text=text, level=level,
            kind="table", page_ref=self.page,
            source_keys=[*(f"line:{i}" for i in self.line_indexes), *(c.key for c in self.cells)],
        )


def _inside(char: dict, bbox: tuple) -> bool:
    x, y = (char["x0"] + char["x1"]) / 2, (char["top"] + char["bottom"]) / 2
    return bbox[0] <= x < bbox[2] and bbox[1] <= y < bbox[3]


def _letters(text: str) -> Counter:
    return Counter(c for c in text if not c.isspace())


def read_tables(page, extracted: list[dict], *, page_number: int,
                line_offset: int, table_offset: int) -> list[PdfTable]:
    """只有文字能完整且唯一分配到网格时才替代普通行；不确定时保留原有文本。"""
    result: list[PdfTable] = []
    claimed: set[int] = set()
    # 大面积纯填充矩形是底色，不能把文字背后的色块边缘当成单元格边框。
    grid_page = page.filter(lambda obj: not (
        obj["object_type"] == "rect" and obj.get("fill") and not obj.get("stroke")
        and obj["width"] > 3 and obj["height"] > 3
    ))
    for table in grid_page.find_tables():
        boxes = sorted(set(table.cells), key=lambda b: (b[1], b[0]))
        xs = sorted({x for b in boxes for x in (b[0], b[2])})
        ys = sorted({y for b in boxes for y in (b[1], b[3])})
        if len(xs) < 3 or len(ys) < 3:
            continue
        indexes: list[int] = []
        crossing = False
        for index, row in enumerate(extracted):
            chars = [c for c in row["chars"] if c["text"].strip()]
            hits = sum(_inside(c, table.bbox) for c in chars)
            if hits:
                indexes.append(index + line_offset)
                if hits != len(chars):
                    crossing = True
        if not indexes or crossing or claimed.intersection(indexes):
            continue
        number = table_offset + len(result) + 1
        cells: list[SourceBlock] = []
        for box in boxes:
            chars = [c for c in page.chars if _inside(c, box)]
            value = extract_text(chars) if chars else ""
            positions = [{"row": r + 1, "column": c + 1}
                         for r in range(len(ys) - 1) for c in range(len(xs) - 1)
                         if box[1] <= ys[r] < box[3] and box[0] <= xs[c] < box[2]]
            first = positions[0]
            cells.append(SourceBlock(
                key=f"table:{number}:cell:{first['row']}:{first['column']}",
                text=value, kind="table_cell",
                locator={"page": page_number, "table": number, "bbox": list(box),
                         "positions": positions, "role": "table_cell"},
            ))
        original = "".join(extracted[i - line_offset]["text"] for i in indexes)
        if _letters(original) != _letters("".join(c.text for c in cells)):
            continue
        result.append(PdfTable(page_number, number, indexes, cells, page.height))
        claimed.update(indexes)
    return result


def _continues(previous: PdfTable, current: PdfTable, lines: list[str], layout: list[dict]) -> bool:
    if current.page != previous.page + 1 or not previous.page_height or not current.page_height:
        return False
    if max(c.locator["bbox"][3] for c in previous.cells) < previous.page_height * 0.8:
        return False
    if min(c.locator["bbox"][1] for c in current.cells) > current.page_height * 0.15:
        return False

    def header(table):
        cells = [c for c in table.cells if c.locator["positions"][0]["row"] == 1]
        # 多层表头可能跨行，留待人工确认，避免把数据行错当完整表头。
        if any(p["row"] != 1 for c in cells for p in c.locator["positions"]):
            return []
        return [(tuple(p["column"] for p in c.locator["positions"]),
                 " ".join(c.text.split()), c.locator["bbox"][0], c.locator["bbox"][2]) for c in cells]

    left, right = header(previous), header(current)
    if len(left) < 2 or len(left) != len(right):
        return False
    for a, b in zip(left, right, strict=True):
        if a[:2] != b[:2] or not a[1] or abs(a[2] - b[2]) > 3 or abs(a[3] - b[3]) > 3:
            return False
    # 两张表之间有说明、新标题或脚注时，不能擅自跨过去合并。
    for index in range(max(previous.line_indexes) + 1, min(current.line_indexes)):
        if layout[index]["role"] == "running_margin":
            continue
        if lines[index].strip() and not re.fullmatch(r"\s*(?:Page\s+)?[-–]?\s*\d+\s*[-–]?\s*", lines[index], re.IGNORECASE):
            return False
    return True


def attach_tables(nodes: list[ClauseNode], tables: list[PdfTable], *,
                  lines: list[str] | None = None, layout: list[dict] | None = None) -> None:
    """表格继承所在章节；无编号页面继承同页说明文字，原文仍逐行可追溯。"""
    def walk(items):
        for node in items:
            yield node
            yield from walk(node.children)

    original = list(walk(nodes))
    previous: PdfTable | None = None
    previous_node: ClauseNode | None = None
    previous_parent: ClauseNode | None = None
    for table in tables:
        start = min(table.line_indexes)
        preceding = [node for node in original if node.kind == "section" and any(
            key.startswith("line:") and int(key.split(":")[1]) < start for key in node.source_keys
        )]
        parent = preceding[-1] if preceding else next(
            (node for node in original if node.kind == "unstructured" and node.page_ref == table.page),
            None,
        )
        node = table.node(parent.level + 1 if parent else 1)
        compatible_parent = parent is previous_parent or (
            parent is not None and previous_parent is not None
            and parent.kind == previous_parent.kind == "unstructured"
        )
        if (previous is not None and previous_node is not None and compatible_parent
                and lines is not None and layout is not None
                and _continues(previous, table, lines, layout)):
            previous_node.text += f"\n\nContinuation — page {table.page}\n{node.text}"
            previous_node.source_keys.extend(node.source_keys)
            # 后续页的说明仍可随整张表进入抽取；只标为页面上下文，不推断适用范围。
            if parent is not None and parent is not previous_parent:
                previous_node.text += f"\n\nPage {table.page} context (scope not inferred):\n{parent.text}"
                previous_node.source_keys.extend(parent.source_keys)
            origin = previous.cells[0].locator.get("continues_table", previous.number)
            previous_node.heading = f"Table {origin} (pages {previous_node.page_ref}-{table.page})"
            for cell in table.cells:
                cell.locator["continues_table"] = origin
            previous = table
            continue
        node.text = f"Page {table.page}\n{node.text}"
        (parent.children if parent else nodes).append(node)
        previous, previous_node, previous_parent = table, node, parent

    def position(node):
        return min((int(k.split(":")[1]) for k in node.source_keys if k.startswith("line:")),
                   default=float("inf"))

    def sort(items):
        items.sort(key=position)
        for node in items:
            sort(node.children)

    sort(nodes)
