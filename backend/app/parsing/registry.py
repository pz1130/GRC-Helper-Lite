from pathlib import Path

from app.ingest.storage import ALLOWED_EXTENSIONS
from app.parsing.contract import ParseError, Parser

SUPPORTED_EXTENSIONS = ALLOWED_EXTENSIONS


def get_parser(path: Path) -> Parser:
    from app.parsing.docx_parser import DocxParser
    from app.parsing.pdf_parser import PdfParser

    extension = path.suffix.lower()
    if extension == ".docx":
        return DocxParser()
    if extension == ".pdf":
        return PdfParser()
    raise ParseError(
        f"暂不支持的文件类型 {extension}。"
        "Excel 控制矩阵导入属于 M4（见 spec §6.7），目前只支持 .pdf 与 .docx。",
        code="parse_error.unsupported_file_type", params={"extension": extension},
    )
