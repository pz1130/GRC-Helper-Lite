"""真实语料验收（默认跳过，见 make corpus）。"""

import os
import re
from functools import cache
from pathlib import Path

import pytest

from app.parsing.contract import ClauseNode
from app.parsing.registry import SUPPORTED_EXTENSIONS, get_parser

CORPUS = Path(os.environ.get("CORPUS_DIR", "/samples"))

# 锚点是 **house style** 的东西，不是解析器的：换一家机构就该换一套，
# 拿本仓库这套去量别人的文档，失败的是「文风不同」而不是「解析漏了开头」。
# 换一套：CORPUS_ANCHORS="引言,职责分工"；不知道该填什么就 CORPUS_ANCHORS=none 跳过。
_ANCHORS = os.environ.get("CORPUS_ANCHORS")
ANCHORS = (
    {piece.strip().casefold() for piece in _ANCHORS.split(",") if piece.strip()}
    if _ANCHORS and _ANCHORS != "none"
    else set()
    if _ANCHORS == "none"
    else {
        "introduction",
        "role and responsibility",
        "roles and responsibilities",
        "roles & responsibilities",
    }
)

pytestmark = pytest.mark.skipif(
    not CORPUS.is_dir(), reason="未挂载真实语料目录，跳过（见 make corpus）"
)


def _files() -> list[Path]:
    if not CORPUS.is_dir():
        return []
    return sorted(path for path in CORPUS.iterdir() if path.suffix.lower() in SUPPORTED_EXTENSIONS)


def _walk(nodes: list[ClauseNode]):
    for node in nodes:
        yield node
        yield from _walk(node.children)


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name[:40])
def test_every_real_document_parses_into_clauses(path: Path):
    parsed = get_parser(path).parse(path)
    clauses = list(_walk(parsed.clauses))
    assert clauses, f"{path.name} 一条条款都没解析出来"


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name[:40])
def test_no_ghost_clauses_from_toc_or_version_rows(path: Path):
    parsed = get_parser(path).parse(path)
    for node in _walk(parsed.clauses):
        assert "...." not in node.heading, f"{path.name}: 目录行漏进来了 → {node.heading!r}"
        assert not re.match(r"^\d{2}/\d{2}/\d{4}", node.heading), (
            f"{path.name}: 版本历史行漏进来了 → {node.heading!r}"
        )


@pytest.mark.parametrize("path", _files(), ids=lambda path: path.name[:40])
def test_no_empty_headings(path: Path):
    parsed = get_parser(path).parse(path)
    for node in _walk(parsed.clauses):
        assert node.heading.strip(), f"{path.name}: 出现空标题条款"


def test_docx_tables_are_captured():
    docx_files = [path for path in _files() if path.suffix.lower() == ".docx"]
    if not docx_files:
        pytest.skip("语料里没有 docx")

    parsed = get_parser(docx_files[0]).parse(docx_files[0])
    assert any(node.kind == "table" for node in _walk(parsed.clauses)), "表格一张都没进来"


def test_print_corpus_report(capsys):
    files = _files()
    if not files:
        pytest.skip("语料目录为空")

    with capsys.disabled():
        print(f"\n{'条款':>6} {'表格':>5} {'告警':>5}  文件")
        for path in files:
            parsed = get_parser(path).parse(path)
            nodes = list(_walk(parsed.clauses))
            tables = sum(1 for node in nodes if node.kind == "table")
            print(f"{len(nodes):>6} {tables:>5} {len(parsed.warnings):>5}  {path.name[:52]}")
            for warning in parsed.warnings:
                print(f"{'':>18}⚠️  {warning}")


# ── 结构性断言 ──────────────────────────────────────────────
# 首版的语料测试只断言"不是已知的三类噪声"，从没断言"条款是不是真的条款"，
# 结果 4/6 份文件的树塌了还全绿。下面三条断言的是**结构本身**。


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_top_level_clauses_are_top_level_numbers(path: Path):
    """顶层条款的编号必须是单段的。

    实测事故：噪声顶掉父节点后，1.1 / 1.2 / 2.1 全被打到顶层，
    一份文件冒出 18 个"顶层"条款。
    """
    parsed = get_parser(path).parse(path)
    offenders = [
        n.number
        for n in parsed.clauses
        if n.kind == "section" and n.number and "." in n.number
    ]
    assert not offenders, f"{path.name}: 这些子条款被打到了顶层 → {offenders}"


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_child_number_extends_its_parent(path: Path):
    """树的完整性：子条款编号必须是父条款编号的延长。

    `3.1` 延长 `3`；段落号写成的 `3(1)` 也是（见 pdf_parser.split_numbered_paragraphs）。
    """
    parsed = get_parser(path).parse(path)

    def check(parent: ClauseNode) -> None:
        for child in parent.children:
            if parent.number and child.number:
                assert child.number.startswith((f"{parent.number}.", f"{parent.number}(")), (
                    f"{path.name}: {child.number} 挂在了 {parent.number} 下面"
                )
            check(child)

    for root in parsed.clauses:
        check(root)


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_no_page_footer_or_list_item_became_a_clause(path: Path):
    """页脚与操作步骤不得成为条款（实测各 17 / 41 条）。"""
    import re

    parsed = get_parser(path).parse(path)
    for node in _walk(parsed.clauses):
        assert not re.match(r"^\|?\s*P\s*a\s*g\s*e", node.heading), (
            f"{path.name}: 页脚漏进来了 → {node.heading!r}"
        )


@pytest.mark.skipif(not ANCHORS, reason="CORPUS_ANCHORS=none：这批文档的 house style 锚点未知")
@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_house_style_anchors_are_present(path: Path):
    """这批文件都以 Introduction 或 Role/Roles and Responsibility 起头。

    锚点缺失意味着解析漏掉了文档开头——正是首版塌树时的症状。
    """
    parsed = get_parser(path).parse(path)
    headings = {n.heading.strip().casefold() for n in _walk(parsed.clauses)}
    assert headings & ANCHORS, f"{path.name}: 一个 house style 锚点都没找到"


# ── 塌树检测 ────────────────────────────────────────────────
# 上面的断言问的都是「已有的条款是不是真条款」，没有一条问「条款是不是太少了」。
# 检查最大块占比与条款密度，提示塌树或过度拆分；阈值是启发式。
# **豁免按原文体量算，不按抽出来的字数算。** 第一版用的是抽出来的字数，那是循环的：
# 塌得越彻底抽出来越少，越容易被「太短不判」放过——实测 文档K 是一份 22k 字的
# 手册，只抽出 2931 字，恰好躲进豁免线以下。判据不能拿被判对象的产物当输入。
# 阈值只有一份定义，在 app/parsing/shape.py——那边是产品**运行时**用的，
# 这边是换语料时批量体检用的。两处各写一套数字迟早会分叉。
from app.parsing.shape import (
    MAX_CLAUSES_PER_1K,
    MAX_HEADING_CHARS,
    MAX_SINGLE_CLAUSE_SHARE,
    MIN_CLAUSES_PER_1K,
    MIN_DISTINCT_NUMBER_RATIO,
)

MIN_RAW_CHARS_TO_JUDGE = 3000
# 松的下限，只抓「大半篇没进来」这种灾难性丢失。实测健康的在 87%–126%
# （超过 100% 是标题路径被重复计入，不必较真），塌掉的是 2% / 9% / 11% / 13%。
MIN_TEXT_COVERAGE = 0.40


@cache
def _parsed(path: Path):
    """一份文件在这一节里被判好几次，解析一次就够——最长的那份有 27 万字。"""
    return get_parser(path).parse(path)


def _raw_text(path: Path) -> str:
    """**独立于解析器**地量一遍原文有多少字。

    拿解析器的产物去判解析器，循环。所以这里自己抽一次。
    """
    if path.suffix.lower() == ".docx":
        from docx import Document as DocxDocument

        document = DocxDocument(path)
        parts = [paragraph.text for paragraph in document.paragraphs]
        for table in document.tables:
            parts += [cell.text for row in table.rows for cell in row.cells]
        return "".join(parts)

    import pdfplumber

    with pdfplumber.open(path) as pdf:
        return "".join(page.extract_text() or "" for page in pdf.pages)


def _shape(path: Path) -> tuple[int, int, int]:
    """返回（条款数, 正文总字数, 最大单条字数）。"""
    nodes = list(_walk(_parsed(path).clauses))
    sizes = [len(node.text or "") for node in nodes]
    return len(nodes), sum(sizes), max(sizes, default=0)


def _judged(path: Path) -> int:
    """够不够大到值得判形状；不够就 skip。返回原文字数。"""
    raw = len(_raw_text(path))
    if raw < MIN_RAW_CHARS_TO_JUDGE:
        pytest.skip(f"{path.name}: 原文不足 {MIN_RAW_CHARS_TO_JUDGE} 字，形状判据不适用")
    return raw


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_no_single_clause_swallows_the_document(path: Path):
    """一条条款装下大半篇正文，等于条款树没切开。

    下游全靠条款定位：引用指向哪一条、变更影响按条款配对、控制点出处回到哪一段。
    一坨装下全文时这些仍然「能跑」，只是全部指向同一个地方。
    """
    _judged(path)
    count, total, biggest = _shape(path)
    share = biggest / total if total else 0
    assert share <= MAX_SINGLE_CLAUSE_SHARE, (
        f"{path.name}: 最大一条装下 {share:.0%} 的正文（共 {count} 条）——条款树没切开"
    )


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_clause_count_is_proportionate_to_the_text(path: Path):
    """条款数与文本量要相称：太少是塌树，太多是把正文行当成了条款。"""
    _judged(path)
    count, total, _ = _shape(path)
    per_k = count / (total / 1000) if total else 0
    assert per_k >= MIN_CLAUSES_PER_1K, (
        f"{path.name}: 每千字只有 {per_k:.2f} 条（共 {count} 条 / {total} 字）——疑似塌树"
    )
    assert per_k <= MAX_CLAUSES_PER_1K, (
        f"{path.name}: 每千字 {per_k:.2f} 条（共 {count} 条 / {total} 字）——疑似把正文行当成了条款"
    )


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_a_document_yields_at_least_one_section_clause(path: Path):
    """全是表格、一条章节都没有，等于没有条款树。

    实测 14 份外部机构的 .docx **全部**如此：它们的段落样式 100% 是 `Normal`，
    视觉上的标题是加粗和字号做出来的，不是套 Heading 样式——而 docx 的标题识别
    只认样式。于是一条章节也找不到，产出的每一条"条款"都是被单独捞出来的表格。

    这条是分类判断不是阈值：占比和密度那两条会放过其中 9 份，因为几十张表格
    看起来"不多不少"。
    """
    _judged(path)
    nodes = list(_walk(_parsed(path).clauses))
    sections = [node for node in nodes if node.kind == "section"]
    assert sections, (
        f"{path.name}: 产出 {len(nodes)} 条，全部是表格，没有任何章节条款"
        f"（解析告警：{_parsed(path).warnings or '无'}）"
    )


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_most_of_the_text_survives_into_clauses(path: Path):
    """抽出来的正文不该比原文少太多——少掉的部分下游永远看不见。"""
    raw = _judged(path)
    _, total, _ = _shape(path)
    coverage = total / raw
    assert coverage >= MIN_TEXT_COVERAGE, (
        f"{path.name}: 只有 {coverage:.0%} 的正文进了条款（{total} / {raw} 字）"
    )


# ── 条款是真条款吗 ──────────────────────────────────────────
# 形状对了不代表内容对。实测 文档M：56 个章节、77 张表、3 层、最大块 11%、
# 0.82 条每千字——形状指标全部健康，**闸门放它过了**。但它的每一个"标题"
# 都是一整段正文：它给**段落**编号（`(a)` `(b)` `(2)`），于是
# "(b)" 后面整段正文被认成「编号 + 标题」，`citation_label` 变成一句 992 字的话，
# 而 `number` 在整篇里重复——56 条带编号的条款只有 19 个不同编号。
#
# 标题长度与编号唯一率是补充线索，不证明解析正确。
@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_a_heading_is_not_a_paragraph(path: Path):
    """标题长成一段正文，说明认标题的判据把正文行收进来了。"""
    _judged(path)
    sections = [node for node in _walk(_parsed(path).clauses) if node.kind == "section"]
    if not sections:
        pytest.skip(f"{path.name}: 没有章节条款，另有断言管它")
    longest = max(sections, key=lambda node: len(node.heading))
    assert len(longest.heading) <= MAX_HEADING_CHARS, (
        f"{path.name}: 最长的标题有 {len(longest.heading)} 字——那是一段正文，不是标题"
        f"（{longest.heading[:60]}…）"
    )


@pytest.mark.parametrize("path", _files(), ids=lambda p: p.name[:40])
def test_clause_numbers_are_mostly_unique_within_a_document(path: Path):
    """编号重复，`citation_label` 就不唯一——审计引用指不到确定的一条。

    段落级编号（CFR 的 `(a)` `(b)`）在每一节里各起一遍，被当成章节编号时
    整篇会有大量同号条款。
    """
    _judged(path)
    numbers = [
        node.number
        for node in _walk(_parsed(path).clauses)
        if node.kind == "section" and (node.number or "").strip()
    ]
    if not numbers:
        pytest.skip(f"{path.name}: 没有带编号的条款")
    ratio = len(set(numbers)) / len(numbers)
    assert ratio >= MIN_DISTINCT_NUMBER_RATIO, (
        f"{path.name}: {len(numbers)} 条带编号的条款只有 {len(set(numbers))} 个不同编号"
        f"（{ratio:.0%}）——编号不唯一，引用指不到确定的一条"
    )
