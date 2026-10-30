"""与解析器无关的文本抽取：PDF 用 pdftotext，Word 用 LibreOffice 导出纯文本（含自动编号）。

标注与评分都基于这里的行，解析器的输出不参与标准答案的生成。
"""
from __future__ import annotations

import re
import subprocess
import tempfile
from collections import Counter
from pathlib import Path

HERE = Path(__file__).parent
TXT = HERE / "work" / "txt"
_PAGE_NO = re.compile(r"^[\s\-—–·•]*(第\s*\d+\s*页.*|\d+\s*(/|of)\s*\d+|[\-—–\s]*\d{1,3}[\-—–\s]*|page\s*\d+(\s*of\s*\d+)?)[\s\-—–]*$", re.IGNORECASE)


def parse_path(row: dict) -> Path:
    """评测时交给解析器的文件：.doc 先由 LibreOffice 另存为 .docx（应用不收 .doc）。"""
    p = HERE / row["file"]
    if p.suffix == ".doc":
        return HERE / "docs" / "converted" / (p.stem + ".docx")
    return p


def raw_text(row: dict) -> str:
    TXT.mkdir(parents=True, exist_ok=True)
    out = TXT / f"{row['id']}.txt"
    if not out.exists():
        src = parse_path(row)
        if src.suffix == ".pdf":
            # 英文 PDF 用 -layout：否则「4.1」与标题文字会被拆到不同行（SANS 模板即如此）
            args = ["pdftotext", "-layout"] if row["lang"] == "en" else ["pdftotext"]
            subprocess.run([*args, str(src), str(out)], check=True)
        else:
            with tempfile.TemporaryDirectory() as d:
                subprocess.run(["soffice", "--headless", "--convert-to", "txt:Text (encoded):UTF8",
                                "--outdir", d, str(src)], check=True, capture_output=True)
                (Path(d) / (src.stem + ".txt")).rename(out)
    with open(out, encoding="utf-8", errors="replace", newline="") as fh:  # 保留 \r，见 lines()
        return fh.read()


def lines(row: dict) -> list[str]:
    """非空行；去掉页码行和 PDF 每页重复的页眉页脚。"""
    # 个别 PDF（SANS 数据泄露模板）在词间写「\t\r\xa0」：splitlines 会在 \r 处断行，
    # 一词一行后又被「每页重复的短行」过滤吃掉 the/of/data 之类常见词。
    text = re.sub(r"[\r\xa0]", " ", raw_text(row))
    pages = text.split("\f")
    raw = [re.sub(r"\s{2,}", " ", ln.strip().replace("\u3000", " ")) for ln in text.replace("\f", "\n").splitlines()]
    raw = [ln for ln in raw if ln and not _PAGE_NO.match(ln)]
    if row["format"] == "pdf" and len(pages) >= 3:
        per_page = Counter()
        for pg in pages:
            per_page.update({ln.strip() for ln in pg.splitlines() if 0 < len(ln.strip()) < 60})
        repeated = {ln for ln, n in per_page.items() if n >= max(3, len(pages) // 2)}
        raw = [ln for ln in raw if ln not in repeated]
    return raw
