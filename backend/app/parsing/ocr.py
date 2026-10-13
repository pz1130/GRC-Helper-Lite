"""OCR fallback for PDFs without a usable text layer.

**这个模块没有接进解析路径，也不要照着现状接。** 决定见 `KNOWN_ISSUES.md`
的 K19（原 OQ-18）：扫描件当前的处理是「空文档 + 一条指向『粘贴纯文本』的警告」，
`PdfParser.parse` 从不调用这里。镜像里也没有 tesseract/poppler，所以真调过来
必然走到下面那条 ParseError。

接入之前必须先解决两件事，缺一件接了也是白接：

1. **下面返回的是词，不是行。** `image_to_data` 是 word-level 的，`data["text"]`
   每项一个词，循环把每个词 append 进 `lines`。而 `extract_headings(lines)` 整套
   判据——编号唯一性、父子比、升序比、MAX_HEADING_CHARS——都建立在「一行 = 一个
   候选标题」上。照现状接上去，拿到的是一串单词，塌树闸门必然判红。
   要接就得先按 block_num/par_num/line_num（或 top 坐标）把词聚回行。
2. **没有判定 OCR 好坏的尺子。** 合成扫描件（把文字画成位图）证明不了任何事——
   真实扫描件有倾斜、印章、装订线、噪点。没有真实扫描件语料就调 OCR，
   调出来的是对合成件的过拟合。

重开条件：第一个真实用户的制度库里出现成批扫描件，或启动模型切分时
（那条路让多模态模型直接吃页面位图，行重建这一步根本不存在，所以要一起做，
不要单独做）。
"""

from pathlib import Path

from app.parsing.contract import ParseError

OCR_CONFIDENCE_THRESHOLD = 0.70
_DPI = 200


def ocr_pdf(path: Path) -> tuple[list[str], float]:
    """把 OCR 结果按**词**返回，外加 0–1 区间的平均置信度。注意不是按行——见模块开头。"""
    try:
        import pytesseract
        from pdf2image import convert_from_path
    except ImportError as exc:
        raise ParseError(
            "该 PDF 没有文本层，需要 OCR，但容器内未安装 tesseract/poppler。"
            "请改用「手动粘贴纯文本」兜底，或在镜像中安装这两个依赖。",
            code="parse_error.ocr_unavailable",
        ) from exc

    lines: list[str] = []
    confidences: list[float] = []
    for image in convert_from_path(str(path), dpi=_DPI):
        data = pytesseract.image_to_data(image, output_type=pytesseract.Output.DICT)
        for value, raw_confidence in zip(data["text"], data["conf"], strict=False):
            if not value.strip():
                continue
            lines.append(value)
            confidence = float(raw_confidence)
            if confidence >= 0:
                confidences.append(confidence / 100.0)

    average = sum(confidences) / len(confidences) if confidences else 0.0
    return lines, average
