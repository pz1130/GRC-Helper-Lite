from pathlib import Path

import pytest

from app.parsing.contract import ParseError
from app.parsing.headings import continues_the_heading
from app.parsing.ocr import ocr_pdf
from app.parsing.pdf_parser import TEXT_LAYER_MIN_CHARS, PdfParser, extract_lines


def _write_pdf(path: Path, pages: list[list[str]]) -> Path:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("Helvetica", size=11)
    for lines in pages:
        pdf.add_page()
        for line in lines:
            pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


@pytest.fixture
def sample_pdf(tmp_path: Path) -> Path:
    return _write_pdf(
        tmp_path / "procedure.pdf",
        [
            [
                "IT Procedure - Change Management Procedure",
                "Version: 2.01",
                "Procedure Owners: IT Department",
                "Approver: Chief Risk Officer",
                "Approved Date: 1 December 2022",
                "Effective Date: 1 December 2022",
                "Change Log",
                "1.0 01/06/2021 Start version of the Procedure",
                "2.01 01/12/2022 Spelling errors fixed",
                "Table of Contents",
                "1 Role and Responsibility ........................ 2",
                "4 Change Management Process ..................... 3",
            ],
            [
                "1 Role and Responsibility",
                "The IT Division owns this procedure.",
                "4 Change Management Process",
                "4.1 Normal Change",
                "Normal changes follow the CAB cycle.",
            ],
        ],
    )


def _walk(nodes):
    for node in nodes:
        yield node
        yield from _walk(node.children)


def test_extract_lines_returns_text_and_page_map(sample_pdf: Path):
    lines, page_of = extract_lines(sample_pdf)
    assert any("Change Management Process" in line for line in lines)
    assert set(page_of.values()) == {1, 2}


def test_numbering_traps_are_filtered_end_to_end(sample_pdf: Path):
    parsed = PdfParser().parse(sample_pdf)
    numbers = [node.number for node in _walk(parsed.clauses) if node.kind == "section"]
    assert numbers == ["1", "4", "4.1"]
    assert "1.0" not in numbers and "2.01" not in numbers


def test_hierarchy_comes_from_the_number(sample_pdf: Path):
    parsed = PdfParser().parse(sample_pdf)
    process = next(node for node in parsed.clauses if node.number == "4")
    assert [child.number for child in process.children] == ["4.1"]


def test_pdf_clauses_carry_real_numbers(sample_pdf: Path):
    parsed = PdfParser().parse(sample_pdf)
    assert all(node.number for node in parsed.clauses if node.kind == "section")


def test_page_reference_is_recorded(sample_pdf: Path):
    parsed = PdfParser().parse(sample_pdf)
    assert next(node for node in parsed.clauses if node.number == "1").page_ref == 2


def test_warnings_report_what_was_filtered(sample_pdf: Path):
    parsed = PdfParser().parse(sample_pdf)
    assert any("目录行" in warning or "版本历史行" in warning for warning in parsed.warnings)


def test_cover_metadata_is_extracted(sample_pdf: Path):
    from datetime import date

    meta = PdfParser().parse(sample_pdf).meta
    assert meta.title == "IT Procedure - Change Management Procedure"
    assert meta.version == "2.01"
    assert meta.owner == "IT Department"
    assert meta.approver == "Chief Risk Officer"
    assert meta.approved_date == date(2022, 12, 1)
    assert meta.effective_date == date(2022, 12, 1)
    assert meta.doc_type == "procedure"


def test_scanned_pdf_is_reported_not_silently_empty(tmp_path: Path):
    empty = _write_pdf(tmp_path / "scanned.pdf", [[""]])
    parsed = PdfParser().parse(empty)
    assert parsed.clauses == []
    assert any("文本层" in warning for warning in parsed.warnings)


def test_corrupt_pdf_raises_readable_error(tmp_path: Path):
    broken = tmp_path / "broken.pdf"
    broken.write_bytes(b"%PDF-1.4 truncated")

    with pytest.raises(ParseError) as exc:
        PdfParser().parse(broken)
    assert "PDF" in exc.value.reason


def test_text_layer_threshold_matches_measured_corpus():
    assert TEXT_LAYER_MIN_CHARS == 200


# ── 扫描件（没有文本层）── OQ-18 ────────────────────────────────────
#
# 扫描件的无文本层路径需要独立验证。
# 合成一份没有文本层的 PDF 就能把它从"未知"变成"已知"：把文字画成位图再存成
# PDF，等价于扫描件的形态（真实扫描件还有噪点、倾斜、印章，那是第二轮的事）。
#
# 钉死的结论：**"OCR 兜底"目前并不存在**。文本层不够时解析器只加一条警告、
# 返回空文档，`ocr_pdf()` 在 app/ 下没有任何调用者，镜像里也没有
# tesseract/poppler。下面三条就是这个事实的可执行版本——谁要接上 OCR，
# 它们会红，那时候改它们才是对的。


def _write_scanned_pdf(path: Path, pages: list[list[str]]) -> Path:
    """把文字画成位图再存成 PDF——产物没有文本层，形态等价于扫描件。"""
    from PIL import Image, ImageDraw

    images = []
    for lines in pages:
        image = Image.new("RGB", (1240, 1754), "white")  # A4 @150dpi
        draw = ImageDraw.Draw(image)
        y = 80
        for line in lines:
            draw.text((80, y), line, fill="black")
            y += 28
        images.append(image)
    images[0].save(str(path), save_all=True, append_images=images[1:])
    return path


@pytest.fixture
def scanned_pdf(tmp_path: Path) -> Path:
    return _write_scanned_pdf(
        tmp_path / "scanned.pdf",
        [
            [
                "IT Procedure - Access Control Procedure",
                "Version: 1.0",
                "1 Roles and Responsibilities",
                "The IT Division owns this procedure and reviews it annually.",
            ],
            [
                "2 Access Provisioning",
                "2.1 All privileged accounts shall be approved by two approvers.",
                "2.2 Access reviews shall be performed quarterly.",
            ],
        ],
    )


def test_scanned_pdf_really_has_no_text_layer(scanned_pdf: Path):
    """先证明这份合成件确实是扫描件的形态，否则下面两条测的是别的东西。"""
    lines, _ = extract_lines(scanned_pdf)
    assert sum(len(line) for line in lines) < TEXT_LAYER_MIN_CHARS


def test_scanned_pdf_yields_an_empty_document_with_a_warning(scanned_pdf: Path):
    """扫描件进来的结果是**空文档 + 一条警告**，不是 OCR 出来的条款。"""
    parsed = PdfParser().parse(scanned_pdf)
    assert parsed.clauses == []
    assert any("文本层" in warning for warning in parsed.warnings)


def test_scan_warning_points_at_the_fallback_that_actually_exists(scanned_pdf: Path):
    """那条警告不能承诺一个不存在的兜底。

    原文写的是"需要 OCR 兜底"——而 OCR 并没有接进解析路径（见本文件上方说明）。
    用户读完会以为系统接下来会 OCR，或者以为装个依赖就好了。
    真正**存在**的兜底是"手动粘贴纯文本"（`POST /api/documents/{id}/plain-text`），
    警告就该指向它。
    """
    warning = next(w for w in PdfParser().parse(scanned_pdf).warnings if "文本层" in w)
    assert "OCR 兜底" not in warning
    assert "粘贴" in warning


def test_scanned_pdf_does_not_set_ocr_used(scanned_pdf: Path):
    """`ocr_used` 恒为 False——`Document.ocr_quality_flag` 由它赋值，

    所以扫描件在库里不会被标成"OCR 质量存疑"，而是看起来像一份正常但空的文档。
    这正是这条路最危险的地方：没有任何下游信号说"这份没解析出来是因为它是扫描件"。
    """
    parsed = PdfParser().parse(scanned_pdf)
    assert parsed.ocr_used is False


def test_ocr_module_explains_what_is_missing_instead_of_crashing():
    """真去调 ocr_pdf 会拿到一条能照着做的报错，而不是 ImportError 堆栈。

    镜像里没有 tesseract/poppler，所以这是当前**必然**走到的分支。
    """
    import builtins

    real_import = builtins.__import__

    def fake_import(name: str, *args, **kwargs):
        if name in {"pytesseract", "pdf2image"}:
            raise ImportError(name)
        return real_import(name, *args, **kwargs)

    builtins.__import__ = fake_import
    try:
        with pytest.raises(ParseError) as exc:
            ocr_pdf(Path("whatever.pdf"))
    finally:
        builtins.__import__ = real_import
    assert "tesseract" in str(exc.value)


# ── 段落级编号的外部文档 ── OQ-20 ──────────────────────────────────
#
# 文档A 那一类：`2.2` 本身就是一个段落，没有标题行。解析器把编号那
# 一行当标题、剩下的当正文，一句话被劈成两半：
#
#   heading = "Under this standard, teams are required to keep a written technology"
#   text    = "and resilience plan proportionate to their size…"
#
# 下面两条把 2026-09-13 那次改造的效果固定住，免得日后悄悄退回去。


@pytest.fixture
def paragraph_numbered_pdf(tmp_path: Path) -> Path:
    """段落级编号：编号后面直接是正文，没有标题，且会跨行断开。"""
    return _write_pdf(
        tmp_path / "paragraph-numbered.pdf",
        [
            [
                "Operations Manual",
                "Section 4 Technology Risk",
                "2.2 Under this standard, teams are required to keep a written technology",
                "and resilience plan proportionate to their size and to how complex their",
                "operations are.",
                "2.3 The review board expects the owner to revisit the",
                "plan at least annually and after any material incident.",
            ],
        ],
    )


def test_paragraph_number_does_not_leave_half_a_sentence_as_the_heading(
    paragraph_numbered_pdf: Path,
):
    """标题要么是真标题，要么是编号——不能是半句话。

    审核者在确认队列里看到的就是这个 heading，`heading_path` 由它拼成，
    chunk 的上下文前缀也是它。半句话会一路污染到检索和引用可读性。
    """
    parsed = PdfParser().parse(paragraph_numbered_pdf)
    flat: list = []

    def walk(nodes):
        for node in nodes:
            flat.append(node)
            walk(node.children)

    walk(parsed.clauses)
    numbered = [node for node in flat if (node.number or "").strip()]
    assert numbered, "这份文档应当切出带编号的条款"
    for node in numbered:
        assert not continues_the_heading(node.text or ""), (
            f"条款 {node.number} 的正文以小写开头，说明 heading 仍是半句话："
            f"heading={node.heading!r} text={node.text!r}"
        )


def test_sample_house_style_anchors_do_not_fire_on_an_outside_document(
    paragraph_numbered_pdf: Path,
):
    """别拿样本那批文档的文风去量别家机构的文档。

    章节名是部署方的文风约定，不是解析器
    的性质。写死它会对每一份外部文档误报「解析可能不完整」，而它们解析得好好的。
    """
    from app.parsing.validate import check_completeness

    parsed = PdfParser().parse(paragraph_numbered_pdf)
    assert check_completeness(parsed) == []
    assert not any("应有的章节" in warning for warning in parsed.warnings)


def test_known_limit_uppercase_continuation_still_leaves_a_fragment(tmp_path: Path):
    """已知缺口，**刻意不修**：续行以大写词开头时，heading 仍是半句话。

    `continues_the_heading` 用"正文以小写字母开头"代理"这是同一句话的后半截"。
    续行以缩写开头就漏判——而 IT / GRC 制度里这恰恰极常见（IT、API、
    SLA、MFA、VPN…）。下面这条就是实测出来的形状：

        heading = "Teams should ensure that their resilience plans cover"
        text    = "IT assets across every environment and every outside connection."

    修改标题判据前需用获准的多样化语料验证，避免对单一文风过拟合。

    这条测试**描述现状而不是认可现状**。谁要补上这个缺口，它会红：那时候请先
    用 `make corpus CORPUS_DIR=...` 在一批真实文档上量过再改。
    """
    pdf = _write_pdf(
        tmp_path / "uppercase-continuation.pdf",
        [
            [
                "Operations Manual",
                "2.2 Teams should ensure that their resilience plans cover",
                "IT assets across every environment and every outside connection.",
                # 单独一条带编号的会被当成孤儿编号跳过，凑一对才是真实的文档形状
                "2.3 The review board expects the owner to revisit the",
                "plan at least annually and after any material incident.",
            ],
        ],
    )
    flat: list = []

    def walk(nodes):
        for node in nodes:
            flat.append(node)
            walk(node.children)

    walk(PdfParser().parse(pdf).clauses)
    fragment = next(node for node in flat if node.number == "2.2")
    assert fragment.heading.startswith("Teams should ensure"), (
        "如果这条断言不成立，说明缺口被补上了——请更新本测试与 OQ-20"
    )


def test_dates_and_small_footnotes_do_not_steal_body_numbers(tmp_path):
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for line in ["19 October 2025", "27.12.2025 EN", "1. Owners must approve requests.",
                 "2. Owners must record decisions.", "3. Owners must review access."]:
        pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
    pdf.set_y(245)
    pdf.set_font("Helvetica", size=8)
    pdf.cell(0, 5, "1 Definition of an owner.")
    path = tmp_path / "notes.pdf"
    pdf.output(path)
    parsed = PdfParser().parse(path)
    nodes = list(_walk(parsed.clauses))
    assert [n.number for n in nodes if n.kind == "section"] == ["1", "2", "3"]
    footnote = next(b for b in parsed.source_blocks if "Definition" in b.text)
    assert footnote.locator["role"] == "footnote"
    assert any(footnote.key in n.source_keys for n in nodes)
    assert all("bbox" in b.locator for b in parsed.source_blocks)


def test_standalone_articles_keep_true_titles_and_pages_despite_inline_references(tmp_path):
    path = _write_pdf(tmp_path / "articles.pdf", [
        ["Article 1", "Purpose", "Teams must comply with the requirements in",
         "Article 3 of this document."],
        ["Article 2", "Review", "Teams must review access."],
        ["Article 3", "Evidence", "Teams must retain records."],
    ])
    parsed = PdfParser().parse(path)
    nodes = [n for n in _walk(parsed.clauses) if n.kind == "section"]
    assert [(n.number, n.heading, n.page_ref) for n in nodes] == [
        ("1", "Purpose", 1), ("2", "Review", 2), ("3", "Evidence", 3),
    ]
    assert "Article 3 of this document." in nodes[0].text
    assert not nodes[0].text.startswith("Purpose")
    assert {b.key for b in parsed.source_blocks} == {k for n in nodes for k in n.source_keys}


def _write_sized_pdf(path: Path, pages: list[list[tuple[float, str]]]) -> Path:
    """每行自带字号。正文一律 11pt，标题字号由调用方给。"""
    from fpdf import FPDF

    pdf = FPDF()
    for lines in pages:
        pdf.add_page()
        for size, line in lines:
            pdf.set_font("Helvetica", size=size)
            pdf.cell(0, size * 0.55, line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(path))
    return path


# ── 标题字号 ── 章节号与段落号两套编号 ─────────────────────────────
#
# 文档C / 文档D 的形状：章节标题比正文大一号（文档C 最小的一级只大 0.8pt），
# 正文里另有一套 `N.` 段落号。原先按版面分流后挑「最长的那一套」，
# 于是段落号胜出、章节标题沉进上一段的正文里（文档D 7 个章节全丢），
# 或者章节号与段落号搅在一起塌树（文档C 一条 26 万字）。


def test_larger_font_numbers_are_sections_and_body_numbers_are_their_paragraphs(tmp_path):
    path = _write_sized_pdf(tmp_path / "streams.pdf", [[
        (15, "1. Governance"),
        (11, "1. Teams must retain evidence."),
        (11, "2. Teams must review access."),
        (11, "3. Teams must record decisions."),
        (15, "2. Preparation"),
        (11, "Preparation covers the plans below."),
        (11, "4. Teams must keep a plan."),
        (11, "5. Teams must test the plan."),
    ]])
    parsed = PdfParser().parse(path)
    tree = [(n.number, n.heading, [c.number for c in n.children])
            for n in parsed.clauses if n.kind == "section"]
    # 段落号 1、2 与章节号撞号，于是都带上所属章节
    assert tree == [("1", "Governance", ["1(1)", "1(2)", "1(3)"]),
                    ("2", "Preparation", ["2(4)", "2(5)"])]
    preparation = parsed.clauses[1]
    assert preparation.text == "Preparation covers the plans below."
    assert preparation.children[0].heading == "Teams must keep a plan."
    assert preparation.children[0].level == 2


def test_paragraph_numbers_may_restart_under_each_section(tmp_path):
    """文档C 的写法：三级章节标题逐级小一号，段落号在每一节里从 1 重来。"""
    path = _write_sized_pdf(tmp_path / "tiers.pdf", [[
        (18, "1. Introduction"),
        (11, "1. These guidelines apply to all teams."),
        (11, "2. They apply from 1 January."),
        (18, "2. Guidelines"),
        (14, "2.1. Governance"),
        (12, "2.1.1. Board"),
        (11, "1. The board should approve the strategy."),
        (11, "2. The board should review it yearly."),
        (12, "2.1.2. Strategy"),
        (11, "3. The strategy should be written down."),
    ]])
    sections = [n for n in _walk(PdfParser().parse(path).clauses) if n.kind == "section"]
    # 光秃秃的「1」出现两次，引用指不到确定的一条：整份文档的段落号都带上所属章节
    assert [(n.number, n.level) for n in sections] == [
        ("1", 1), ("1(1)", 2), ("1(2)", 2),
        ("2", 1), ("2.1", 2), ("2.1.1", 3), ("2.1.1(1)", 4), ("2.1.1(2)", 4),
        ("2.1.2", 3), ("2.1.2(3)", 4),
    ]


def test_smaller_font_section_numbers_do_not_become_headings(tmp_path):
    """文档C 附录那张无边框反馈表：单元格里引用的节号比正文小，不能再开一节。"""
    path = _write_sized_pdf(tmp_path / "annex.pdf", [[
        (16, "1. Scope"),
        (11, "1. Teams must retain evidence of every access decision they take,"),
        (11, "including who approved it, when, and for how long it remains valid."),
        (16, "2. Feedback"),
        (11, "The table below lists the comments received during the consultation."),
        (9, "1. Scope One respondent asked for examples. No change."),
        (9, "2. Feedback Another respondent agreed. No change."),
    ]])
    parsed = PdfParser().parse(path)
    feedback = parsed.clauses[-1]
    assert (feedback.number, feedback.heading, feedback.children) == ("2", "Feedback", [])
    assert "One respondent asked" in feedback.text


def test_body_size_subsections_still_hang_under_a_larger_chapter(tmp_path):
    """章用大字号、节用正文字号的文档：只认大字号会把 1.1 这一级整个丢掉。"""
    path = _write_sized_pdf(tmp_path / "chapters.pdf", [[
        (15, "1 Owners"),
        (11, "1.1 Duties"),
        (11, "Owners are accountable."),
        (11, "1.2 Evidence"),
        (11, "Owners must retain records."),
        (15, "2 Review"),
        (11, "2.1 Frequency"),
        (11, "Access is reviewed yearly."),
    ]])
    sections = [n for n in _walk(PdfParser().parse(path).clauses) if n.kind == "section"]
    assert [n.number for n in sections] == ["1", "1.1", "1.2", "2", "2.1"]


def test_a_list_inside_a_paragraph_does_not_split_the_section(tmp_path):
    """段落号在一节里必须逐个递增；正文里的 1. 2. 3. 列表打断递增，整节不拆。"""
    path = _write_sized_pdf(tmp_path / "list.pdf", [[
        (15, "1. Access"),
        (11, "4. Teams must follow these steps:"),
        (11, "1. Open the request."),
        (11, "2. Approve the request."),
        (11, "5. Teams must record the approval."),
        (15, "2. Review"),
        (11, "6. Teams must review access."),
        (11, "7. Teams must record the review."),
    ]])
    parsed = PdfParser().parse(path)
    access, review = parsed.clauses
    assert access.children == []
    assert "Open the request." in access.text
    assert [c.number for c in review.children] == ["6", "7"]


def test_a_numbered_list_after_a_colon_is_not_split_into_clauses(tmp_path):
    """「复核应包括：1. …; 2. …;」是列表，不是段落——拆开后每一项都没了主语。"""
    path = _write_sized_pdf(tmp_path / "colon.pdf", [[
        (15, "1. Review"),
        (11, "The access review should cover:"),
        (11, "1. the list of users;"),
        (11, "2. the approvals on file;"),
        (11, "3. the open exceptions."),
        (15, "2. Records"),
        (11, "Teams keep the review results for three years."),
    ]])
    review = PdfParser().parse(path).clauses[0]
    assert review.children == []
    assert "1. the list of users;" in review.text


def test_missing_parent_numbers_are_filled_under_larger_font_headings(tmp_path):
    """与原先的十进制读法一致：缺的父级补一个占位，子节不成为无父的孤儿。"""
    path = _write_sized_pdf(tmp_path / "orphan.pdf", [[
        (16, "1. Scope"),
        (11, "Teams must follow this policy for every system."),
        (16, "2. Access"),
        (13, "2.1.1 Joiners"),
        (11, "New staff receive access on their first day."),
        (13, "2.1.2 Leavers"),
        (11, "Access is removed on the last working day."),
    ]])
    access = PdfParser().parse(path).clauses[1]
    assert [c.number for c in access.children] == ["2.1"]
    assert [c.number for c in access.children[0].children] == ["2.1.1", "2.1.2"]


def test_numbered_paragraph_stream_survives_alternating_page_margins(tmp_path):
    from fpdf import FPDF

    pdf = FPDF()
    for page in range(2):
        pdf.add_page()
        pdf.set_font("Helvetica", size=15)
        pdf.cell(0, 8, f"{page + 1}. Topic", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        for number in range(page * 3 + 1, page * 3 + 4):
            pdf.set_x(15 if page else 25)
            pdf.cell(0, 6, f"{number}. Teams must retain evidence.", new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "mirrored.pdf"
    pdf.output(path)
    parsed = PdfParser().parse(path)
    assert [[c.number for c in n.children] for n in parsed.clauses] == [
        ["1(1)", "1(2)", "1(3)"], ["2(4)", "2(5)", "2(6)"],
    ]


def test_same_size_numbered_paragraphs_without_sections_stay_flat(tmp_path):
    """没有大字号标题时，段落号自己就是条款——不因为这次改动变样。"""
    from fpdf import FPDF

    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", size=11)
    for number in range(1, 7):
        pdf.cell(0, 6, f"{number}. Teams must retain evidence.", new_x="LMARGIN", new_y="NEXT")
    path = tmp_path / "flat.pdf"
    pdf.output(path)
    parsed = PdfParser().parse(path)
    assert [n.number for n in parsed.clauses if n.kind == "section"] == [str(i) for i in range(1, 7)]


def test_contents_continuation_does_not_capture_later_body_anchors(tmp_path):
    path = _write_pdf(tmp_path / "contents.pdf", [
        ["Contents", "Overview"],
        ["1 Owners", "1.1 Duties", "1.2 Evidence", "Purpose and scope", "This policy applies to all teams."],
        ["1 Owners", "Owners are accountable.", "1.1 Duties", "Teams must review access.",
         "1.2 Evidence", "Teams must retain records."],
    ])
    parsed = PdfParser().parse(path)
    numbered = [n for n in _walk(parsed.clauses) if n.kind == "section"]
    assert [(n.number, n.page_ref) for n in numbered] == [("1", 3), ("1.1", 3), ("1.2", 3)]
    intro = next(b for b in parsed.source_blocks if b.text == "Purpose and scope")
    assert intro.locator["role"] == "body"


# ── 页眉与页码 ── 认出来了就不能再进正文 ───────────────────────────
#
# 页眉（每页重复的机构名、文档标题）与页码早已认得出——它们不会被当成标题——
# 却照样留在条款正文里：跨页的一句话中间夹着「3 / 机构名 / 文档标题」。
# 抽取要逐字引用原文，跨页混入页眉会导致引文不匹配。


def _write_paged_pdf(path: Path, pages: list[list[str]]) -> Path:
    """合成版式：每页顶部两行页眉、底部一行小字页脚与居中页码。"""
    from fpdf import FPDF

    pdf = FPDF()
    for number, lines in enumerate(pages, start=1):
        pdf.add_page()
        pdf.set_auto_page_break(False)
        pdf.set_font("Helvetica", size=9)
        pdf.set_xy(10, 8)
        pdf.cell(0, 4, "Acme Corporation", new_x="LMARGIN", new_y="NEXT")
        pdf.cell(0, 4, "Access Management Procedure", new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=11)
        pdf.set_xy(10, 30)
        for line in lines:
            pdf.cell(0, 6, line, new_x="LMARGIN", new_y="NEXT")
        pdf.set_font("Helvetica", size=8)
        pdf.set_xy(10, 280)
        pdf.cell(0, 4, "Internal Use Only")
        pdf.set_font("Helvetica", size=9)
        pdf.set_xy(0, 285)
        pdf.cell(210, 4, str(number), align="C")
    pdf.output(str(path))
    return path


def test_running_headers_and_page_numbers_stay_out_of_clause_text(tmp_path):
    path = _write_paged_pdf(tmp_path / "paged.pdf", [
        ["1 Scope", "This procedure covers every system that Acme runs."],
        ["2 Access", "Owners must review access every quarter and record the"],
        ["outcome in the access register.", "3 Records", "Records are kept for three years."],
    ])
    parsed = PdfParser().parse(path)
    sections = [n for n in _walk(parsed.clauses) if n.kind == "section"]
    assert [n.number for n in sections] == ["1", "2", "3"]
    access = sections[1]
    assert access.text == (
        "Owners must review access every quarter and record the\n"
        "outcome in the access register."
    )
    for node in sections:
        assert "Acme Corporation" not in node.text
        assert "Internal Use Only" not in node.text  # 小字页脚：每页重复，不是脚注
        assert not any(line.strip().isdigit() for line in node.text.splitlines())
    # 原文视图里它们还在：只是不属于任何条款
    texts = [block.text.strip() for block in parsed.source_blocks]
    assert texts.count("Acme Corporation") == 3
    assert "2" in texts


def test_a_number_line_inside_the_body_is_not_taken_for_a_page_number(tmp_path):
    """页码只在页边上。正文里单独一行的数字（表里的值、编号续行）照旧是正文。"""
    path = _write_paged_pdf(tmp_path / "values.pdf", [
        ["1 Retention", "Logs are kept for this many days:", "90", "after which they are purged."],
        ["2 Review", "Owners review the logs monthly."],
        ["3 Records", "Records are kept for three years."],
    ])
    retention = PdfParser().parse(path).clauses[0]
    assert "\n90\n" in retention.text


def test_a_paragraph_number_that_clashes_with_a_chapter_number_is_qualified(tmp_path):
    """只有第 3 章有段落号：段落号之间不重复，可「1」与第 1 章撞号。"""
    path = _write_sized_pdf(tmp_path / "clash.pdf", [[
        (15, "1. Scope"),
        (11, "This procedure covers every cloud service."),
        (15, "2. Owners"),
        (11, "Service owners run the checks."),
        (15, "3. Monitoring"),
        (11, "1. Monitoring must be independent of the provider."),
        (11, "2. Checks must follow the service agreement."),
    ]])
    monitoring = PdfParser().parse(path).clauses[2]
    assert [c.number for c in monitoring.children] == ["3(1)", "3(2)"]


def test_paragraph_numbers_that_are_unique_in_the_document_stay_bare(tmp_path):
    """全文连续、又不与任何章节号撞号的段落号本身就唯一，不必加前缀。"""
    path = _write_sized_pdf(tmp_path / "unique.pdf", [[
        (15, "1. Governance"),
        (11, "10. Teams must retain evidence."),
        (11, "11. Teams must review access."),
        (15, "2. Preparation"),
        (11, "12. Teams must keep a plan."),
    ]])
    parsed = PdfParser().parse(path)
    assert [[c.number for c in n.children] for n in parsed.clauses] == [["10", "11"], ["12"]]
