"""PDF 文本层解析器。

没有文本层的扫描件走不通这条路：得到的是**一份空文档加一条告警**，
不是 OCR 出来的条款。这个产品不支持扫描件，理由与重开条件见
`KNOWN_ISSUES.md` 的 K19（原 OQ-18）。"""

import dataclasses
import re
from collections import Counter
from datetime import date, datetime
from pathlib import Path
from statistics import median

import pdfplumber

from app.parsing.chinese import Item, detect_style, segment
from app.parsing.contract import (
    ClauseNode,
    DocumentMeta,
    ParsedDocument,
    ParseError,
    ParseWarning,
    SourceBlock,
)
from app.parsing.headings import (
    FLOOR,
    acts_as_headings,
    consistency_score,
    continues_the_heading,
    demote_fragment_headings,
)
from app.parsing.numbering import (
    NumberedHeading,
    assemble_tree,
    extract_headings,
    is_page_footer,
    is_toc_line,
    is_version_history_line,
)
from app.parsing.pdf_tables import PdfTable, attach_tables, read_tables
from app.parsing.restructure import adopt_article_titles, nest_chapters
from app.parsing.unstructured import structure_warnings
from app.parsing.vocab import Numbering, vocabulary

TEXT_LAYER_MIN_CHARS = 200
# 比正文大这么多才算「标题字号」。文档C 最小一级小节标题 12.0pt、正文 11.2pt，
# 只差 0.8；而与正文同字号的编号（段落号、列表）差 0，表格里的更小。
DISPLAY_MARGIN_PT = 0.5
# 页边上的页码："3"、"iv"、"Page 3"、"3 of 20"、"3/20"。只在页面顶部或底部一成里认——
# 正文里单独一行的数字（表里的值、换行后的数字）不是页码。
_PAGE_NUMBER = re.compile(
    r"^\s*(?:page\s+)?(?:\d{1,4}|[ivxlcdm]{1,7})(?:\s*(?:of|/)\s*\d{1,4})?\s*$",
    re.IGNORECASE,
)
# 这些行认得出是版面，不属于任何条款：不当标题，也不进条款正文。
# 原文块里它们照旧保留，只是不关联到条款。
_LAYOUT_ROLES = {"table", "running_margin", "page_number"}
_META = re.compile(
    r"^\s*(Version|Owner|Owners|Procedure Owners|Approver|Approved Date|Effective Date)"
    r"\s*:\s*(.*?)\s*$",
    re.IGNORECASE,
)
_DOC_TYPE = re.compile(r"\b(?:IT\s+)?(Policy|Standard|Procedure|Guideline)\b", re.IGNORECASE)
_DATE_FORMATS = (
    "%d/%m/%Y",
    "%Y-%m-%d",
    "%d %B %Y",
    "%d %b %Y",
    "%B %d, %Y",
    "%B %Y",
    "%b %Y",
)


def extract_layout(
    path: Path, *, tables: list[PdfTable] | None = None,
) -> tuple[list[str], dict[int, int], list[dict]]:
    """保留逐行版面证据；仅将可靠的版面噪声排除出标题候选。"""
    lines: list[str] = []
    page_of: dict[int, int] = {}
    layout: list[dict] = []
    try:
        with pdfplumber.open(str(path)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                extracted = page.extract_text_lines()
                detected = read_tables(
                    page, extracted, page_number=page_number, line_offset=len(lines),
                    table_offset=len(tables),
                ) if tables is not None else []
                if tables is not None:
                    tables.extend(detected)
                table_lines = {index: table.number for table in detected for index in table.line_indexes}
                sizes = [c["size"] for c in page.chars if c.get("text", "").strip()]
                body_size = median(sizes) if sizes else 0
                toc = any(re.fullmatch(r"(?:table of )?contents", row["text"].strip(),
                                      re.IGNORECASE) for row in extracted)
                for row in extracted:
                    chars = [c for c in row["chars"] if c.get("text", "").strip()]
                    size = median(c["size"] for c in chars) if chars else body_size
                    locator = {
                        "page": page_number, "document_line": len(lines) + 1,
                        "bbox": [row[k] for k in ("x0", "top", "x1", "bottom")],
                        "font_size": round(size, 2),
                        "fonts": sorted({c["fontname"] for c in chars}),
                        "role": "body",
                    }
                    if toc:
                        locator["role"] = "contents"
                    elif body_size and size < body_size * 0.9 and row["top"] > page.height * 0.55:
                        locator["role"] = "footnote"
                    if len(lines) in table_lines:
                        locator["role"] = "table"
                        locator["table"] = table_lines[len(lines)]
                    locator["margin"] = row["top"] < page.height * 0.10 or row["bottom"] > page.height * 0.90
                    if locator["margin"] and locator["role"] in ("body", "footnote") and (
                        _PAGE_NUMBER.match(row["text"]) or is_page_footer(row["text"])
                    ):
                        locator["role"] = "page_number"
                    page_of[len(lines)] = page_number
                    lines.append(row["text"])
                    layout.append(locator)
    except Exception as exc:
        raise ParseError(
            f"无法读取 PDF：{type(exc).__name__}",
            code="parse_error.pdf_unreadable", params={"error": type(exc).__name__},
        ) from exc
    # 目录可跨页且续页没有 Contents；仅接纳连续编号、并在后文重现的标题序列。
    toc_pages = {loc["page"] for loc in layout if loc["role"] == "contents"}
    for page_number in sorted(set(page_of.values())):
        if page_number - 1 not in toc_pages or page_number in toc_pages:
            continue
        prefix: list[int] = []
        for index, loc in enumerate(layout):
            if loc["page"] != page_number:
                continue
            if vocabulary("decimal").match(lines[index]) is None:
                break
            prefix.append(index)
        if len(prefix) < 3:
            continue
        later = {line.strip() for line, loc in zip(lines, layout, strict=True)
                 if loc["page"] > page_number}
        if sum(lines[i].strip() in later for i in prefix) >= len(prefix) * 0.5:
            for index in prefix:
                layout[index]["role"] = "contents"
            toc_pages.add(page_number)
    repeated: dict[str, set[int]] = {}
    for line, loc in zip(lines, layout, strict=True):
        if loc["margin"]:
            key = line.strip()
            repeated.setdefault(key, set()).add(loc["page"])
    for line, loc in zip(lines, layout, strict=True):
        # 小字页脚（「Internal Use Only」）按字号先被归成了脚注；每页原样重复的不是脚注。
        if (loc.pop("margin") and loc["role"] in ("body", "footnote")
                and len(repeated.get(line.strip(), set())) >= 3):
            loc["role"] = "running_margin"
    return lines, page_of, layout


def _body_sizes(lines: list[str], layout: list[dict], limit: int = 3) -> list[float]:
    """按字数排的前几种字号，都可能是正文字号。

    不能直接取第一名：文档C 附录那张 95 页的反馈表（10.4pt）字数比正文（11.2pt）
    还多。拿它当正文，11.2pt 的段落号就全成了「大字号标题」。
    """
    weights: Counter[float] = Counter()
    for line, loc in zip(lines, layout, strict=True):
        if loc["role"] == "body" and line.strip():
            weights[round(loc["font_size"], 1)] += len(line.strip())
    return [size for size, _ in weights.most_common(limit)]


def _title_key(title: str) -> str:
    title = re.sub(r"[.\u2026·]{2,}.*$", "", title)
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]", "", title.lower())[:24]


_ZERO_POINT = re.compile(r"^(\s*)(\d{1,2})\.0(?=\s+\S)")


def _zero_point_chapters(candidates: list[str]) -> list[str]:
    """「1.0 Purpose / 1.1 Background / 2.0 Scope」：`N.0` 是第 N 章本身，不是第 N 章的
    第 0 节。不改写的话会凭空补出 `1`、`2` 占位，真正的章全降成它们的孩子（SANS 模板实测）。
    只在至少两处 `N.0` 且序号不重复时改写，读作 `N.`。"""
    hits = [found[2] for line in candidates if (found := _ZERO_POINT.match(line))]
    if len(hits) < 2 or len(set(hits)) != len(hits):
        return candidates
    return [_ZERO_POINT.sub(r"\1\2.", line) for line in candidates]


def _defer_to_contents(candidates: list[str], lines: list[str], layout: list[dict]) -> list[str]:
    """目录写了「4. Introduction to Online Resources」，正文里先出现的「4. Analyze the
    gaps」就是步骤列表，不是第 4 章。目录只用来**否决**（同号异名，或目录列到了这一级却没有这个号），从不新增标题——
    目录过期时最坏是退回原来的读法。"""
    listed: dict[str, str] = {}
    for line, loc in zip(lines, layout, strict=True):
        if loc["role"] != "contents":
            continue
        label = vocabulary("decimal").match(line.strip())
        if label is not None and (key := _title_key(label.title)):
            listed.setdefault(".".join(str(part) for part in label.parts), key)
    if len(listed) < 3:
        return candidates
    per_depth = Counter(number.count(".") for number in listed)
    # 一两条同级的「目录行」可能是版本历史之类的误判，至少三条才算目录列到了这一级。
    depths = {depth for depth, count in per_depth.items() if count >= 3}
    out = list(candidates)
    for index, line in enumerate(candidates):
        label = vocabulary("decimal").match(line) if line.strip() else None
        if label is None:
            continue
        number = ".".join(str(part) for part in label.parts)
        expected = listed.get(number)
        if expected is None:
            # 目录列到了这一级却没有这个号：正文里的列表号、表格号、地址门牌。
            if number.count(".") in depths:
                out[index] = ""
            continue
        key = _title_key(label.title)
        if key and not (key.startswith(expected[:12]) or expected.startswith(key[:12])):
            out[index] = ""
    return out


def _is_bold(font: str) -> bool:
    return any(word in font.lower() for word in ("bold", "black", "heavy", "semibold"))


_APPENDIX = re.compile(r"\s*(Appendix|Annex)\s+([A-Z]|\d{1,2})\s*[.:\u2014\u2013-]?\s+(\S.{0,90})$")


def _ends_paragraph(candidates: list[str], index: int) -> bool:
    """index 之前最近的非空行是以句末标点收尾的正文。"""
    prev = next((candidates[j].strip() for j in range(index - 1, max(index - 4, -1), -1)
                 if candidates[j].strip()), "")
    return bool(prev) and prev[-1] in ".!?)"


_GLOSSARY = re.compile(r"(?:glossary|definitions?|terms and definitions|acronyms)\b", re.IGNORECASE)


def _glossary_terms(headings: list[NumberedHeading], candidates: list[str], all_bold) -> list[NumberedHeading]:
    """词汇表附录里每个粗体术语（NIST CSF「CSF Category」）各成一条，挂在该附录下。

    术语常比章标题字号小，heading_like 的字号条件接不住。只在标题是
    Glossary/Definitions 的那一章里找：整行粗体、不超过 60 字符、下一非空行不是粗体。
    """
    out: list[NumberedHeading] = []
    for position, heading in enumerate(headings):
        if not _GLOSSARY.match((heading.title or "").strip()):
            continue
        end = headings[position + 1].line_index if position + 1 < len(headings) else len(candidates)
        for index in range(heading.line_index + 1, end):
            text = candidates[index].strip()
            if not text or len(text) > 60 or not all_bold(index) or re.match(r"[\d(•]", text):
                continue
            nxt = next((j for j in range(index + 1, min(index + 4, end)) if candidates[j].strip()), None)
            if nxt is not None and not all_bold(nxt):
                out.append(NumberedHeading(f"{heading.number}.{_UNNUMBERED}{index}", text, index))
    return out


def _refine_by_bold_headings(
    headings: list[NumberedHeading], candidates: list[str], layout: list[dict],
) -> list[NumberedHeading]:
    """标题与正文同字号、只靠粗体区分时（如 NIST CSF 2.0）：
    丢掉不是粗体的「标题」（折行正文以数字开头，如「2.0 website and…」），
    补上同样粗体的「Appendix A.」和首个编号标题之前的无编号前言标题（Abstract、Preface…）。"""
    if len(headings) < 3:
        return headings

    def all_bold(index: int) -> bool:
        fonts = layout[index].get("fonts") or []
        return bool(fonts) and all(_is_bold(f) for f in fonts)

    bold = [h for h in headings if all_bold(h.line_index)]
    top = [h for h in headings if h.level == 1]
    top_bold = [h for h in top if all_bold(h.line_index)]
    if len(top) < 3 or len(top_bold) < 0.8 * len(top):
        return headings
    sizes = {round(layout[h.line_index]["font_size"]) for h in top_bold}
    top_x = min(layout[h.line_index]["bbox"][0] for h in top_bold)
    if len(bold) >= 0.8 * len(headings):
        kept = [h for h in headings
                if any(_is_bold(f) for f in layout[h.line_index].get("fonts") or [])]
    else:
        kept = list(headings)

    def heading_like(index: int) -> bool:
        text = candidates[index].strip()
        if not text or not all_bold(index) or round(layout[index]["font_size"]) not in sizes:
            return False
        if _APPENDIX.match(text):
            return True  # 「Appendix C. Glossary」下面紧跟粗体术语，不看下一行
        nxt = next((j for j in range(index + 1, min(index + 4, len(candidates)))
                    if candidates[j].strip()), None)
        return nxt is not None and not all_bold(nxt)

    taken = {h.line_index for h in kept}
    extra: list[NumberedHeading] = []
    first = min(taken) if taken else len(candidates)
    for index, line in enumerate(candidates):
        if index in taken or not heading_like(index):
            continue
        text = line.strip()
        if (match := _APPENDIX.match(line)) and index > first:
            extra.append(NumberedHeading(f"{match[1]} {match[2]}", match[3].strip(), index))
        elif not (len(text) <= 60 and text[0].isupper() and text[-1] not in ".,;:"
                  and not re.match(r"[\d(]", text)):
            continue
        elif index < first:
            extra.append(NumberedHeading(f"{_UNNUMBERED}{index}", text, index))
        elif layout[index]["bbox"][0] > top_x + 8:
            # 章内缩进的粗体小标题（Sussex「Executive Committee (ExCo)」）：挂到所在章下。
            chapter = max((h for h in top if h.line_index < index), key=lambda h: h.line_index)
            extra.append(NumberedHeading(f"{chapter.number}.{_UNNUMBERED}{index}", text, index))
        elif _ends_paragraph(candidates, index):
            # 不缩进、与正文同字号的整行粗体小标题（SANS 数据泄露模板「Work with Forensic
            # Investigators」）：上一段正文以句末标点收尾才算，挂到最近的编号标题下。
            owner = max((h for h in kept if h.line_index < index), key=lambda h: h.line_index)
            extra.append(NumberedHeading(f"{owner.number}.{_UNNUMBERED}{index}", text, index))
    extra += _glossary_terms(sorted(kept + extra, key=lambda h: h.line_index), candidates, all_bold)
    front = [h for h in extra if h.number.startswith(_UNNUMBERED)]
    if len(front) < 2:
        # 只有一行的是文档标题（SANS 模板封面「Policy」），不是前言章节。
        extra = [h for h in extra if h not in front]
    if not extra and len(kept) == len(headings):
        return headings
    return sorted(kept + extra, key=lambda h: h.line_index)


_UNNUMBERED = "\u0000front:"


def _clear_unnumbered(nodes) -> None:
    for node in nodes:
        if node.number and _UNNUMBERED in node.number or node.number == "Sch":
            node.number = None
        elif node.number and node.number.startswith("Sch"):
            parts = node.number.split(".")
            if parts[0] != "Sch":  # 无总标题时 key 为 SchN
                parts = ["Sch", parts[0][3:], *parts[1:]]
            node.number = f"SCHEDULE {parts[1]}" if len(parts) == 2 else parts[-1]
        _clear_unnumbered(node.children)


_SCHEDULE = re.compile(r"\s*SCHEDULE(?:\s+(\d{1,2}))?\s*$")
_SCHEDULE_NOTE = re.compile(r"\s*(?:Regulations?|Articles?|Sections?)\s+\d")


def _schedules(lines: list[str], after: int, paragraphs: list[NumberedHeading]) -> list[NumberedHeading]:
    """英国 SI 正文后的附表：「SCHEDULE N / Regulation 7(1) / 标题」，附表内段落从 1 重新编号。

    内部编号用 Sch / Sch.N / Sch.N.k（或无总标题时 SchN），assemble_tree 之后由
    _clear_unnumbered 改回原文的「SCHEDULE N」与段落号。
    """
    markers = []
    for index in range(after + 1, len(lines)):
        match = _SCHEDULE.match(lines[index])
        if not match:
            continue
        title_at = index + 1
        while title_at < len(lines) and (not lines[title_at].strip() or _SCHEDULE_NOTE.match(lines[title_at])):
            title_at += 1
        if title_at >= len(lines) or len(lines[title_at].strip()) > 120:
            continue
        markers.append((index, match[1] or "1", lines[title_at].strip(), title_at + 1))
    if not markers:
        return []
    out: list[NumberedHeading] = []
    container = next((i for i in range(after + 1, markers[0][0]) if lines[i].strip() == "SCHEDULES"), None)
    if container is not None:
        out.append(NumberedHeading("Sch", "SCHEDULES", container, container + 1))
    for position, (index, number, title, body) in enumerate(markers):
        end = markers[position + 1][0] if position + 1 < len(markers) else len(lines)
        key = f"Sch.{number}" if container is not None else f"Sch{number}"
        out.append(NumberedHeading(key, title, index, body))
        for paragraph in paragraphs:
            if body <= paragraph.line_index < end:
                out.append(NumberedHeading(f"{key}.{paragraph.number}", paragraph.title,
                                           paragraph.line_index, paragraph.body_start))
    return out


def _numbered(line: str) -> Numbering | None:
    if not line.strip() or is_page_footer(line) or is_toc_line(line) or is_version_history_line(line):
        return None
    return vocabulary("decimal").match(line)


# 标题自己折行最多这么多行；再往下还是同字号，就是一段正文而不是标题。
_TITLE_WRAP_LINES = 3


def _leads_a_section(index: int, candidates: list[str], layout: list[dict]) -> bool:
    """标题下面换成了比它小的字：正文，或下一级标题。

    段落号本身也可能比「正文」大——正文字号定错时就是这样（文档C 以附录表格的
    10.4pt 当正文，11.2pt 的段落号全都「更大」）。区别在后面：段落号后面接的是
    同字号的续行，标题后面换了字号。
    不设下限：同一份文档各节正文字号可以不同（文档Q 有的节 11pt、有的 10pt）。
    """
    size = layout[index]["font_size"]
    same = 0
    for line, loc in zip(candidates[index + 1:], layout[index + 1:], strict=True):
        if not line.strip():
            continue
        if abs(loc["font_size"] - size) < DISPLAY_MARGIN_PT:
            same += 1
            if same > _TITLE_WRAP_LINES:
                return False
            continue
        return loc["font_size"] < size
    return True


def _display_at(
    candidates: list[str], layout: list[dict], body_size: float,
) -> list[NumberedHeading]:
    found: list[NumberedHeading] = []
    seen: set[str] = set()
    for index, (line, loc) in enumerate(zip(candidates, layout, strict=True)):
        label = _numbered(line)
        if label is None:
            continue
        number = ".".join(str(part) for part in label.parts)
        size = loc["font_size"]
        larger = (size >= body_size + DISPLAY_MARGIN_PT
                  and _leads_a_section(index, candidates, layout))
        extends = (abs(size - body_size) < DISPLAY_MARGIN_PT and len(label.parts) > 1
                   and number.rsplit(".", 1)[0] in seen)
        if number in seen or not (larger or extends):
            continue
        seen.add(number)
        found.append(NumberedHeading(number, label.title, index))
    return found


def display_headings(
    candidates: list[str], layout: list[dict], body_sizes: list[float],
) -> tuple[list[NumberedHeading], float]:
    """字号比正文大的编号行是章节标题；凑不成一棵树就返回空，交回原来的仲裁。

    字号是同一份文档内部的相对证据，不是文风：章节号与段落号共用 `N.` 形状时
    （文档C、文档D），唯一分得开两者的就是它。原先按版面分流后挑「最长的一套」，
    段落号总比章节号多，于是总是段落号胜出。

    与正文同字号的多级编号，父号已是标题的也收进来——章用大字号、节用正文字号
    的文档，只认大字号会把节这一级整个丢掉。比正文小的一律不收（表格、脚注）。

    哪个字号是正文，先按字数从多到少逐个试，取第一个搭得成树的；再用这些标题底下
    实际的正文字号校正一次。字数第一的不一定是正文：文档C 附录那张 95 页的反馈表
    （10.4pt）比正文（11.2pt）字还多，拿它当正文，表格单元格里的 `4.2.1.` 就成了
    正文字号的下级小节。标题底下接什么字，才是这份文档的正文。

    **不比谁的标题多**——比数量正是原先段落号胜出的原因。
    """
    def coherent(found: list[NumberedHeading]) -> bool:
        # 补出来的父级占位（标题就是编号本身）不算证据：一条表格里的 `1.1` 补个 `1`
        # 就「成树」，会把正文字号的整套章节标题顶掉（NIST CSF 2.0 实测）。
        real = sum(h.title != h.number for h in found)
        return real >= 2 and consistency_score([h.number for h in found]) >= FLOOR

    # 先补父级再评分，与十进制读法的次序一致：缺一级父号是版式常态，不是不自洽。
    for guess in body_sizes:
        found = _with_ancestors(_display_at(candidates, layout, guess))
        if not coherent(found):
            continue
        body_size = _text_size_under(found, candidates, layout) or guess
        refined = _with_ancestors(_display_at(candidates, layout, body_size))
        if body_size != guess and coherent(refined):
            return refined, body_size
        return found, guess
    return [], 0.0


def _with_ancestors(headings: list[NumberedHeading]) -> list[NumberedHeading]:
    """缺的父级补一个占位，与十进制读法（numbering._extract_decimal）的做法一致：
    占位与孩子共用行号、排在孩子前面，正文与出处都是空的，标题就是编号本身。"""
    seen = {h.number for h in headings}
    filled: list[NumberedHeading] = []
    for heading in headings:
        parts = heading.number.split(".")
        for cut in range(1, len(parts)):
            ancestor = ".".join(parts[:cut])
            if ancestor not in seen:
                seen.add(ancestor)
                filled.append(NumberedHeading(ancestor, ancestor, heading.line_index))
        filled.append(heading)
    return filled


def _text_size_under(
    headings: list[NumberedHeading], candidates: list[str], layout: list[dict],
) -> float | None:
    """每一节按自己那段正文里字数最多的字号投一票，取票数最多的。

    一节一票而不是按字数：文档C 附录那一节 25 万字，按字数它一节就压过全部正文。
    也不只看标题下第一行：那常是比正文大的无编号小标题或表头（文档Q 正文
    9.96pt，第一行却常是 11pt 的「Term Definition」）。
    """
    starts = sorted(h.line_index for h in headings)
    votes: Counter[float] = Counter()
    for position, start in enumerate(starts):
        end = starts[position + 1] if position + 1 < len(starts) else len(candidates)
        weights: Counter[float] = Counter()
        for index in range(start + 1, end):
            if candidates[index].strip():
                weights[round(layout[index]["font_size"], 1)] += len(candidates[index].strip())
        if weights:
            votes[weights.most_common(1)[0][0]] += 1
    return votes.most_common(1)[0][0] if votes else None


def _is_a_list(
    starts: list[tuple[int, NumberedHeading]], ends: list[int], own: list[int],
    heading_line: int, body_lines: list[str],
) -> bool:
    """引导句以冒号收尾、或条目以分号收尾：这是一个列表，各项离开引导句就没有主语。

    看的是每一项的**最后一行**：段落的第一行常在逗号处折行（文档D 的第 25 段）。
    """
    before = [index for index in own if index < starts[0][0] and body_lines[index].strip()]
    lead = body_lines[before[-1] if before else heading_line].rstrip()
    if lead.endswith(":"):
        return True
    for (start, _), end in zip(starts, ends, strict=True):
        item = [index for index in own if start <= index < end and body_lines[index].strip()]
        if item and re.search(r";\s*(?:and|or)?$", body_lines[item[-1]].rstrip()):
            return True
    return False


def _join_wrapped_headings(
    headings: list[NumberedHeading], candidates: list[str], layout: list[dict], body_size: float,
) -> None:
    """标题折成两行时，第二行字号字体与标题行相同且大于正文：接回标题。"""
    taken = {heading.line_index for heading in headings}
    for position, heading in enumerate(headings):
        start = heading.line_index
        head = layout[start]
        if not body_size or head["font_size"] < body_size + DISPLAY_MARGIN_PT:
            continue
        index = (heading.body_start or start + 1)
        if index != start + 1:
            continue
        while (index < len(candidates) and index not in taken and candidates[index].strip()
               and abs(layout[index]["font_size"] - head["font_size"]) < 0.5
               and layout[index]["fonts"] == head["fonts"]
               and _numbered(candidates[index]) is None):
            index += 1
        if index == start + 1:
            continue
        extra = " ".join(candidates[i].strip() for i in range(start + 1, index))
        label = _numbered(candidates[start])
        title = heading.title if heading.title and heading.title != heading.number else (
            label.title if label else "")
        headings[position] = dataclasses.replace(
            heading, title=f"{title} {extra}".strip(), body_start=index)


def _has_decimal_paragraphs(nodes: list[ClauseNode]) -> bool:
    """文档已经用多段编号（`4.1.2 Executives must…`）给段落编号。

    这种文档里段落层级已经是 `4.1.2` 这一层，同一份文档不会再叠一套 `N.` 段落号；
    节内的 1. 2. 3. 是正文列表（SANS AUP 4.3.2 的禁止事项，引导句不以冒号结尾）。
    判据：至少两条三段以上编号的条款没有真标题——标题就是编号，或"标题"是半句话。
    """
    count = 0
    for node in nodes:
        if node.kind != "section" or len((node.number or "").split(".")) < 3:
            continue
        heading = (node.heading or "").strip()
        if heading == node.number or continues_the_heading(node.text or ""):
            count += 1
    return count >= 2


def split_numbered_paragraphs(
    nodes: list[ClauseNode], candidates: list[str], layout: list[dict],
    body_lines: list[str], body_size: float, page_of: dict[int, int],
) -> None:
    """章节自己那段正文里的 `N.` 段落号，各自成为这一节的子条款。

    段落号在一节之内必须逐个递增（起点不限：文档D 全文连续编号，文档C 每节从 1
    重来）。正文里夹着的 1. 2. 3. 列表会打断递增——那一节整个不拆，宁可粒度粗，
    也不把一个段落拆成几截。只有一个段落号的节，要在前面已有段落号的前提下接上
    前一节（文档C 的 3.3.5 只有第 24 段）或从 1 重来（文档C 的 3.1）才算；
    全文头一个孤零零的 `1.` 分不清是段落还是列表。

    段落号在全文里不与任何条款号重复时，原样作条款号。每节从 1 重来、或与章节号
    撞号（第 3 章的第 1 段对第 1 章）时，光秃秃的「1」引用指不到确定的一条——这时
    **整份文档**的段落号都写成「所属章节(段落号)」，如 `3(1)`。不写成 `3.1`：那会和真正的
    3.1 小节撞号，也不是原文的说法。
    """
    def walk(items):
        for item in items:
            yield item
            yield from walk(item.children)

    if _has_decimal_paragraphs(list(walk(nodes))):
        return
    last: int | None = None
    made: list[tuple[ClauseNode, ClauseNode]] = []
    for node in list(walk(nodes)):  # 先取定文档顺序，下面会往 children 里插段落
        if node.kind != "section" or not node.source_keys:
            continue
        own = sorted(int(key.split(":")[1]) for key in node.source_keys if key.startswith("line:"))
        heading_line, rest = own[0], own[1:]
        starts: list[tuple[int, NumberedHeading]] = []
        for index in rest:
            label = _numbered(candidates[index])
            if (label is None or not label.trailing_dot or len(label.parts) != 1
                    or abs(layout[index]["font_size"] - body_size) >= DISPLAY_MARGIN_PT):
                continue
            starts.append((index, NumberedHeading(str(label.parts[0]), label.title, index)))
        numbers = [int(heading.number) for _, heading in starts]
        if not numbers or numbers != list(range(numbers[0], numbers[0] + len(numbers))):
            continue
        ends = [index for index, _ in starts[1:]] + [own[-1] + 1]
        if _is_a_list(starts, ends, own, heading_line, body_lines):
            continue
        if len(numbers) == 1 and (last is None or numbers[0] not in (1, last + 1)):
            continue
        last = numbers[-1]
        paragraphs = []
        for (start, heading), end in zip(starts, ends, strict=True):
            keys = [index for index in own if start <= index < end]
            paragraphs.append(ClauseNode(
                heading=heading.title,
                text="\n".join(body_lines[index].strip() for index in keys[1:]),
                level=node.level + 1, number=heading.number, page_ref=page_of.get(start),
                source_keys=[f"line:{index}" for index in keys],
            ))
        kept = [index for index in rest if index < starts[0][0]]
        node.text = "\n".join(body_lines[index].strip() for index in kept)
        node.source_keys = [f"line:{index}" for index in (heading_line, *kept)]
        node.children[:0] = paragraphs
        made.extend((node, paragraph) for paragraph in paragraphs)

    issued = [paragraph.number for _, paragraph in made]
    paragraphs = {id(paragraph) for _, paragraph in made}
    sections = {node.number for node in walk(nodes) if id(node) not in paragraphs and node.number}
    if len(issued) != len(set(issued)) or sections & set(issued):
        for section, paragraph in made:
            if section.number:
                paragraph.number = f"{section.number}({paragraph.number})"


def extract_lines(path: Path) -> tuple[list[str], dict[int, int]]:
    lines, page_of, _ = extract_layout(path)
    return lines, page_of


def _empty_meta() -> DocumentMeta:
    return DocumentMeta(
        title=None,
        version=None,
        owner=None,
        approver=None,
        approved_date=None,
        effective_date=None,
        doc_type=None,
    )


def _parse_date(raw: str) -> date | None:
    for date_format in _DATE_FORMATS:
        try:
            # 只取日期部分，时区无关
            return datetime.strptime(raw.strip(), date_format).date()
        except ValueError:
            continue
    return None


def _cover_meta(lines: list[str]) -> DocumentMeta:
    fields: dict[str, str] = {}
    doc_type: str | None = None
    for line in lines[:60]:
        if match := _META.match(line):
            key = match.group(1).casefold()
            normalized = "owner" if key in {"owner", "owners", "procedure owners"} else key
            value = match.group(2).strip()
            if value:
                fields.setdefault(normalized, value)
        if doc_type is None and (match := _DOC_TYPE.search(line)):
            doc_type = match.group(1).lower()

    title = next((line.strip() for line in lines if line.strip()), None)
    return DocumentMeta(
        title=title,
        version=fields.get("version"),
        owner=fields.get("owner"),
        approver=fields.get("approver"),
        approved_date=_parse_date(fields.get("approved date", "")),
        effective_date=_parse_date(fields.get("effective date", "")),
        doc_type=doc_type,
    )


class PdfParser:
    def parse(self, path: Path) -> ParsedDocument:
        tables: list[PdfTable] = []
        lines, page_of, layout = extract_layout(path, tables=tables)
        blocks = [SourceBlock(
            key=f"line:{index}", text=line, kind="line",
            locator=layout[index],
        ) for index, line in enumerate(lines) if line.strip()]
        blocks.extend(cell for table in tables for cell in table.cells)
        warnings: list[str] = []  # 混着 ParseWarning，见 contract.ParseWarning

        if not any(line.strip() for line in lines):
            # 不要写"需要 OCR 兜底"——OCR 并没有接进这条路径（app/parsing/ocr.py
            # 的 ocr_pdf 零调用者），那句话承诺了一个不存在的东西。指向真正存在
            # 的兜底：手动粘贴纯文本（POST /api/documents/{id}/plain-text）。
            warnings.append(
                ParseWarning(
                    "该 PDF 没有可用的文本层（疑似扫描件），本系统不支持扫描件。"
                    "请改用带文本层的 PDF/DOCX，或在文档页用「粘贴纯文本」录入正文。",
                    code="parse.scanned_no_text_layer",
                )
            )
            return ParsedDocument(meta=_empty_meta(), clauses=[], warnings=warnings,
                                  source_blocks=blocks, source_origin="pdf_text")

        if sum(len(line) for line in lines) < TEXT_LAYER_MIN_CHARS:
            warnings.append(ParseWarning(
                "该 PDF 的可读取文字较少，已保留全部已读取文字；请核对是否还有扫描内容",
                code="parse.sparse_text_layer",
            ))

        candidates = [line if loc["role"] == "body" else ""
                      for line, loc in zip(lines, layout, strict=True)]
        cn_style = detect_style(candidates)
        if cn_style is not None:
            # 中文条文体/公文体：字号与英文编号读法都不适用，见 chinese.py。
            body_lines = [line if loc["role"] not in _LAYOUT_ROLES else ""
                          for line, loc in zip(lines, layout, strict=True)]
            clauses = segment([Item(line, source_key=f"line:{index}",
                                    page_ref=page_of.get(index))
                               for index, line in enumerate(body_lines)], cn_style)
            attach_tables(clauses, tables, lines=lines, layout=layout)
            warnings.extend(structure_warnings(clauses))
            demote_fragment_headings(clauses)
            return ParsedDocument(meta=_cover_meta(lines), clauses=clauses, warnings=warnings,
                                  source_blocks=blocks, source_origin="pdf_text")
        candidates = _defer_to_contents(candidates, lines, layout)
        candidates = _zero_point_chapters(candidates)
        displayed, body_size = display_headings(candidates, layout, _body_sizes(lines, layout))
        headings, filter_warnings = extract_headings(candidates)
        # 分开考察不同字号/缩进的连续正文编号，避免章节号和段落号互相污染。
        streams: dict[tuple, list[NumberedHeading]] = {}
        for index, (line, loc) in enumerate(zip(candidates, layout, strict=True)):
            label = vocabulary("decimal").match(line)
            if label and label.trailing_dot and len(label.parts) == 1:
                # 对开页的正文边距可能交替变化，同时考察仅按字号聚合的读法。
                for indent in (None, round(loc["bbox"][0] / 5)):
                    key = (round(loc["font_size"]), indent)
                    streams.setdefault(key, []).append(NumberedHeading(
                        str(label.parts[0]), label.title, index,
                    ))
        runs = [stream for stream in streams.values()
                if acts_as_headings([h.number for h in stream])]
        if runs and len(longest := max(runs, key=len)) > len(headings):
            headings = longest
            filter_warnings = []
        if displayed:
            # 字号分得开时不再挑「最长的一套」：那一套恰恰是段落号。
            headings = displayed
            filter_warnings = []
        # 独占一行的 Article/Section/Chapter 是强结构证据，正文引用不得抢占编号。
        explicit: dict[str, list[NumberedHeading]] = {}
        for index, line in enumerate(candidates):
            match = re.fullmatch(r"\s*(Article|Section|Chapter)\s+(\d+)\s*", line, re.IGNORECASE)
            if not match:
                continue
            title = line.strip()
            body_start = index + 1
            if index + 1 < len(lines) and page_of[index + 1] == page_of[index]:
                following = candidates[index + 1].strip()
                if following and len(following) <= 160 and not re.match(r"[\d(]", following):
                    title = following
                    body_start += 1
            explicit.setdefault(match[1].lower(), []).append(NumberedHeading(
                match[2], title, index, body_start,
            ))
        # 英国 SI 体例：标题独占一行，下一行才是「19.—(1) …」或「2. These …」。
        for index, line in enumerate(candidates[:-1]):
            title = line.strip()
            if (not title or len(title) > 120 or title[-1] in ".,;:" or not title[0].isupper()
                    or re.match(r"(?:PART|CHAPTER|SCHEDULE)\b", title)):
                continue
            nxt = re.match(r"\s*(\d{1,3})\.(?:—|–|\s+[A-Z(“\"])", lines[index + 1])
            # 目录否决可能已抹掉「1.—(1)」这一行（编号同、标题不同），这里读原行。
            if nxt and layout[index + 1]["role"] == "body" and page_of.get(index + 1) == page_of.get(index):
                explicit.setdefault("regulation", []).append(NumberedHeading(
                    nxt[1], title, index, index + 1,
                ))
        if regulations := explicit.pop("regulation", None):
            # 封面残页、附表会各自从 1 重新编号：只留最长的一段大致连续递增的序列。
            runs_: list[list[NumberedHeading]] = []
            for heading in regulations:
                if runs_ and 0 < int(heading.number) - int(runs_[-1][-1].number) <= 3:
                    runs_[-1].append(heading)
                else:
                    runs_.append([heading])
            best = max(runs_, key=len)
        else:
            best = []
        coherent = [stream for stream in explicit.values()
                    if acts_as_headings([h.number for h in stream])]
        if len(best) >= 5:
            # 分段时已保证递增、间隔不超过 3（漏掉的是 19A 这类插入条），不再要求严格 +1。
            coherent.append(best + _schedules(lines, best[-1].line_index, regulations or []))
        if coherent:
            headings = max(coherent, key=len)
            displayed = []
        if not coherent:
            headings = _refine_by_bold_headings(headings, candidates, layout)
        warnings.extend(filter_warnings)
        toc_count = sum(loc["role"] == "contents" for loc in layout)
        if toc_count:
            warnings.append(ParseWarning(
                f"已跳过 {toc_count} 条目录行", code="parse.skipped_toc",
                params={"count": toc_count},
            ))
        if not headings:
            warnings.append(
                ParseWarning(
                    "未识别出任何编号条款，该 PDF 可能不是标准 house style",
                    code="parse.no_numbered_clauses",
                )
            )

        _join_wrapped_headings(headings, candidates, layout, body_size)
        body_lines = [line if loc["role"] not in _LAYOUT_ROLES else ""
                      for line, loc in zip(lines, layout, strict=True)]
        clauses = assemble_tree(headings, body_lines, page_of=page_of)
        _clear_unnumbered(clauses)
        if displayed:
            split_numbered_paragraphs(clauses, candidates, layout, body_lines, body_size, page_of)
        adopt_article_titles(clauses)
        clauses = nest_chapters(clauses)
        attach_tables(clauses, tables, lines=lines, layout=layout)
        warnings.extend(structure_warnings(clauses))
        # 段落级编号的文档里，"标题"其实是段落的前半句——整段收回正文。
        demote_fragment_headings(clauses)
        return ParsedDocument(
            meta=_cover_meta(lines),
            clauses=clauses,
            warnings=warnings,
            source_blocks=blocks, source_origin="pdf_text",
        )
