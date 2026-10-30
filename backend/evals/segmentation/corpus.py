"""切割评估语料：程序生成的合成制度文档，带逐条标准答案。

每份文档先描述成一棵「标准条款树」（编号、标题、正文句子），再渲染成 .docx；
同一份 .docx 用 LibreOffice 转出 .pdf，于是两种格式共用一份答案。
全部内容是虚构的通用 GRC 文本，不含任何机构语料。
"""

from __future__ import annotations

import itertools
import json
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path

from docx import Document
from docx.enum.text import WD_BREAK
from docx.shared import Pt

CN_DIGITS = "零一二三四五六七八九"


def cn_numeral(n: int) -> str:
    if n < 10:
        return CN_DIGITS[n]
    if n < 20:
        return "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    if n < 100:
        return CN_DIGITS[n // 10] + "十" + (CN_DIGITS[n % 10] if n % 10 else "")
    raise ValueError(n)


@dataclass
class GoldClause:
    number: str | None
    heading: str
    sentences: list[str] = field(default_factory=list)
    children: list[GoldClause] = field(default_factory=list)
    # 渲染方式：style=Word 标题样式级别；bold/size 为手工格式；inline=标题与正文同段
    style_level: int | None = None
    bold: bool = False
    size: float | None = None
    inline: bool = False
    table: list[list[str]] | None = None
    # 正文里但不应成为条款的编号行（列表项），渲染在正文句子之后
    list_items: list[str] = field(default_factory=list)


@dataclass
class GoldDoc:
    name: str
    lang: str
    clauses: list[GoldClause]
    title: str
    body_size: float = 11.0


_counter = itertools.count(1)
ORD = ["", "One", "Two", "Three", "Four", "Five"]

EN_TOPICS = ["access review", "backup restoration", "vendor onboarding", "incident triage",
             "change approval", "password rotation", "log retention", "patch deployment",
             "risk acceptance", "data classification", "encryption key custody",
             "privileged session", "business continuity test", "asset inventory",
             "security awareness training", "firewall rule review"]
ZH_TOPICS = ["访问权限复核", "备份恢复演练", "供应商准入", "事件分级", "变更审批", "口令更换",
             "日志留存", "补丁部署", "风险接受", "数据分级", "密钥保管", "特权会话", "业务连续性测试",
             "资产清点", "安全意识培训", "防火墙策略复核"]


def en_sentence() -> str:
    n = next(_counter)
    topic = EN_TOPICS[n % len(EN_TOPICS)]
    return (f"The {topic} owner shall complete control activity number {n} "
            f"and retain evidence for {n % 7 + 2} years.")


def zh_sentence() -> str:
    n = next(_counter)
    topic = ZH_TOPICS[n % len(ZH_TOPICS)]
    return f"{topic}责任部门应当完成第{n}号控制活动，相关记录至少保存{n % 7 + 2}年。"


def _sents(lang: str, k: int) -> list[str]:
    make = en_sentence if lang == "en" else zh_sentence
    return [make() for _ in range(k)]


# ── 文档定义 ────────────────────────────────────────────────────────────────

def doc_en_styled() -> GoldDoc:
    topics = ["Purpose", "Scope", "Roles and Responsibilities", "Access Control",
              "Monitoring and Review", "Exceptions"]
    clauses = []
    for i, t in enumerate(topics, 1):
        c = GoldClause(str(i), t, _sents("en", 2), style_level=1)
        for j in range(1, 3 if i % 2 else 4):
            sub = GoldClause(f"{i}.{j}", f"{t} Requirement {ORD[j]}", _sents("en", 2), style_level=2)
            c.children.append(sub)
        if i == 3:
            c.table = [["Role", "Responsibility"], ["CISO", "Approves the policy"],
                       ["IT Operations", "Implements technical controls"]]
        clauses.append(c)
    return GoldDoc("en_styled", "en", clauses, "Information Security Policy")


def doc_en_manual() -> GoldDoc:
    """无样式，章节号 `N.` 加粗大字号，正文里有 `1.` 操作步骤列表（P3/P4 形态）。"""
    topics = ["Governance", "Risk Assessment", "Asset Management", "Operations Security",
              "Supplier Relationships", "Compliance", "Improvement"]
    clauses = []
    for i, t in enumerate(topics, 1):
        c = GoldClause(str(i), t, _sents("en", 2), bold=True, size=14)
        if i in (2, 4):
            c.list_items = [f"{k}. Record step {k} of the {t.lower()} procedure in the register"
                            for k in range(1, 4)]
        for j in range(1, 3):
            c.children.append(GoldClause(f"{i}.{j}", f"{t} Control {ORD[j]}", _sents("en", 2),
                                         bold=True, size=12))
        clauses.append(c)
    return GoldDoc("en_manual", "en", clauses, "Risk Management Standard")


def doc_en_article() -> GoldDoc:
    """`CHAPTER I` + `Article N` 独占一行、标题在下一行（P2 形态）。"""
    chapters = [("General Provisions", 3), ("Risk Governance", 4), ("Outsourcing", 3),
                ("Final Provisions", 2)]
    clauses = []
    art = 0
    roman = ["I", "II", "III", "IV", "V"]
    for ci, (title, n) in enumerate(chapters):
        ch = GoldClause(f"CHAPTER {roman[ci]}", title, [], bold=True, size=14)
        for _ in range(n):
            art += 1
            ch.children.append(GoldClause(f"Article {art}", f"Provision on {EN_TOPICS[art % len(EN_TOPICS)]}",
                                          _sents("en", 2), bold=True, size=12))
        clauses.append(ch)
    return GoldDoc("en_article", "en", clauses, "Regulation on ICT Risk")


def doc_en_wrapped() -> GoldDoc:
    """标题很长，在 PDF 中会折成两行（P9 形态）。"""
    long = [("Management of Information Security Incidents and Improvements Including "
            "Communication of Security Events"),
            ("Information Security Aspects of Business Continuity Management and "
            "Redundancy of Information Processing Facilities"),
            ("Compliance with Legal and Contractual Requirements Including Privacy and "
            "Protection of Personally Identifiable Information")]
    clauses = []
    for i, t in enumerate(long, 1):
        c = GoldClause(str(i), t, _sents("en", 3), style_level=1)
        c.children.append(GoldClause(f"{i}.1", "Responsibilities and procedures",
                                     _sents("en", 2), style_level=2))
        clauses.append(c)
    return GoldDoc("en_wrapped", "en", clauses, "Operations Standard")


def _zh_articles(lang_sents=2, items=False, start=1, count=3):
    out = []
    for k in range(start, start + count):
        c = GoldClause(f"第{cn_numeral(k)}条", f"第{cn_numeral(k)}条", _sents("zh", lang_sents),
                       inline=True)
        if items and k % 2 == 0:
            c.list_items = [f"（{cn_numeral(m)}）{s}" for m, s in
                            enumerate(_sents("zh", 2), 1)]
        out.append(c)
    return out


def doc_zh_regulation() -> GoldDoc:
    """第X章 + 第X条（条号全文连续，条内（一）（二）列项）。"""
    chapters = [("总则", 3), ("组织架构与职责", 4), ("风险识别与评估", 4),
                ("监督检查", 3), ("附则", 2)]
    clauses, art = [], 1
    for ci, (title, n) in enumerate(chapters, 1):
        ch = GoldClause(f"第{cn_numeral(ci)}章", title, [], bold=True, size=14)
        ch.children = _zh_articles(items=True, start=art, count=n)
        art += n
        clauses.append(ch)
    return GoldDoc("zh_regulation", "zh", clauses, "信息科技风险管理办法")


def doc_zh_sections() -> GoldDoc:
    """第X章 / 第X节 / 第X条 三级。"""
    clauses, art = [], 1
    for ci, (title, secs) in enumerate([("总则", []), ("访问控制", ["账号管理", "权限复核"]),
                                        ("运行管理", ["变更管理", "备份管理", "日志管理"]),
                                        ("附则", [])], 1):
        ch = GoldClause(f"第{cn_numeral(ci)}章", title, [], style_level=1)
        if not secs:
            ch.children = _zh_articles(start=art, count=2)
            art += 2
        for si, st in enumerate(secs, 1):
            sec = GoldClause(f"第{cn_numeral(si)}节", st, [], style_level=2)
            sec.children = _zh_articles(start=art, count=2)
            art += 2
            ch.children.append(sec)
        clauses.append(ch)
    return GoldDoc("zh_sections", "zh", clauses, "信息系统运行管理规定")


def doc_zh_policy() -> GoldDoc:
    """一、/（一）/ 1. 三级（党政机关公文常见体例），正文不加粗同字号。"""
    tops = ["总体要求", "职责分工", "工作内容", "保障措施"]
    clauses = []
    for i, t in enumerate(tops, 1):
        c = GoldClause(f"{cn_numeral(i)}、", t, _sents("zh", 1), bold=True)
        for j in range(1, 3):
            sub = GoldClause(f"（{cn_numeral(j)}）", f"{t}第{cn_numeral(j)}项", _sents("zh", 1),
                             bold=True)
            if i == 3:
                for k in range(1, 3):
                    sub.children.append(GoldClause(f"{k}.", f"具体事项{i}{j}{k}",
                                                   _sents("zh", 1), inline=True))
            c.children.append(sub)
        clauses.append(c)
    return GoldDoc("zh_policy", "zh", clauses, "关于加强网络安全管理工作的通知")


def doc_zh_styled_table() -> GoldDoc:
    """Word 标题样式 + 1/1.1 编号 + 表格。"""
    tops = ["目的", "适用范围", "职责", "管理要求", "检查与考核"]
    clauses = []
    for i, t in enumerate(tops, 1):
        c = GoldClause(str(i), t, _sents("zh", 2), style_level=1)
        if i == 4:
            for j in range(1, 4):
                c.children.append(GoldClause(f"{i}.{j}", f"{t}之{cn_numeral(j)}",
                                             _sents("zh", 2), style_level=2))
        if i == 3:
            c.table = [["角色", "职责"], ["信息科技部", "负责制度执行"], ["风险管理部", "负责监督检查"]]
        clauses.append(c)
    return GoldDoc("zh_styled_table", "zh", clauses, "数据安全管理细则")


def doc_zh_plain_articles() -> GoldDoc:
    """只有「第X条」、没有章，且正文中出现以「第X条」开头的引用句。"""
    arts = _zh_articles(start=1, count=8)
    arts[5].sentences.append("第三条规定的复核结果应当在十个工作日内报送风险管理部门。")
    return GoldDoc("zh_plain_articles", "zh", arts, "外包风险管理指引")


DOCS = [doc_en_styled, doc_en_manual, doc_en_article, doc_en_wrapped, doc_zh_regulation,
        doc_zh_sections, doc_zh_policy, doc_zh_styled_table, doc_zh_plain_articles]


# ── 渲染 ───────────────────────────────────────────────────────────────────

def _run(paragraph, text, *, bold=False, size=None, lang="en"):
    run = paragraph.add_run(text)
    run.bold = bold or None
    run.font.size = Pt(size) if size else None
    return run


def render_docx(doc: GoldDoc, path: Path) -> None:
    document = Document()
    normal = document.styles["Normal"]
    normal.font.size = Pt(doc.body_size)
    if doc.lang == "zh":
        normal.font.name = "Noto Serif CJK SC"
    title = document.add_paragraph()
    _run(title, doc.title, bold=True, size=18)

    def emit(c: GoldClause):
        label = c.number or ""
        sep = "" if label.endswith(("、", "）")) or not label else " "
        if c.inline:
            text = f"{label}{sep if not label.startswith('第') else '　'}" + \
                   (c.sentences[0] if c.sentences else "")
            if c.number and c.number.endswith(".") and c.heading:
                text = f"{label}{c.heading}。" + (c.sentences[0] if c.sentences else "")
            p = document.add_paragraph()
            _run(p, text)
            rest = c.sentences[1:]
        else:
            head = c.heading if not label else (
                f"{label}{sep}{c.heading}" if not label.upper().startswith(("CHAPTER", "ARTICLE"))
                else None)
            if head is None:
                p = document.add_paragraph()
                _run(p, label, bold=c.bold, size=c.size)
                p.runs[0].add_break(WD_BREAK.LINE) if False else None
                p2 = document.add_paragraph()
                _run(p2, c.heading, bold=c.bold, size=c.size)
            elif c.style_level:
                document.add_heading(head, level=c.style_level)
            else:
                p = document.add_paragraph()
                _run(p, head, bold=c.bold, size=c.size)
            rest = c.sentences
        for s in rest:
            _run(document.add_paragraph(), s)
        for item in c.list_items:
            _run(document.add_paragraph(), item)
        if c.table:
            t = document.add_table(rows=len(c.table), cols=len(c.table[0]))
            t.style = "Table Grid"
            for r, row in enumerate(c.table):
                for col, val in enumerate(row):
                    t.cell(r, col).text = val
        for ch in c.children:
            emit(ch)

    for c in doc.clauses:
        emit(c)
    document.save(str(path))


def gold_json(doc: GoldDoc) -> dict:
    def walk(c: GoldClause):
        sentences = list(c.sentences)
        if c.inline and c.number and c.number.endswith(".") and c.heading:
            pass
        items = list(c.list_items)
        cells = [v for row in (c.table or []) for v in row]
        return {"number": c.number, "heading": c.heading, "sentences": sentences + items,
                "table_cells": cells, "children": [walk(x) for x in c.children]}
    return {"name": doc.name, "lang": doc.lang, "clauses": [walk(c) for c in doc.clauses]}


def build(out: Path, pdf: bool = True) -> list[Path]:
    global _counter
    _counter = itertools.count(1)
    out.mkdir(parents=True, exist_ok=True)
    files = []
    for make in DOCS:
        doc = make()
        docx_path = out / f"{doc.name}.docx"
        render_docx(doc, docx_path)
        (out / f"{doc.name}.gold.json").write_text(
            json.dumps(gold_json(doc), ensure_ascii=False, indent=1), encoding="utf-8")
        files.append(docx_path)
    if pdf and shutil.which("soffice"):
        subprocess.run(["soffice", "--headless", "--convert-to", "pdf", "--outdir", str(out),
                        *map(str, files)], check=True, capture_output=True, timeout=600)
        files += sorted(out.glob("*.pdf"))
    return files


if __name__ == "__main__":
    import sys
    for f in build(Path(sys.argv[1] if len(sys.argv) > 1 else "evals/segmentation/corpus")):
        print(f)
    _ = asdict
