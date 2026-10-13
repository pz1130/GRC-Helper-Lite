"""从 XLSX 的第一张表导入审计问题：有界、确定性。"""

from app.matrix.template import Sheet, SheetRejected, read_sheet

QUESTION_HEADERS = frozenset({"question", "question text", "audit question", "问题", "审计问题"})
MAX_QUESTIONS = 200
MAX_QUESTION_CHARS = 10_000
LANGUAGE_HEADERS = frozenset({"language", "language code", "语言", "语言代码"})
LANGUAGES = {
    "en": "en",
    "english": "en",
    "英文": "en",
    "英语": "en",
    "zh": "zh",
    "chinese": "zh",
    "中文": "zh",
    "汉语": "zh",
}


def _header(value: str) -> str:
    return " ".join(value.casefold().replace("_", " ").split())


def _column(headers: list[str], aliases: frozenset[str]) -> int | None:
    matches = [index for index, value in enumerate(headers) if _header(value) in aliases]
    if len(matches) > 1:
        raise SheetRejected("Excel 包含重复的问题或语言列", code="audit.xlsx_duplicate_columns")
    return matches[0] if matches else None


def parse_questions(content: bytes) -> list[tuple[str, str]]:
    sheet: Sheet = read_sheet(content, max_rows=MAX_QUESTIONS + 1)
    question_column = _column(sheet.headers, QUESTION_HEADERS)
    language_column = _column(sheet.headers, LANGUAGE_HEADERS)
    if question_column is None:
        raise SheetRejected("Excel 缺少“审计问题”或“question”列", code="audit.xlsx_question_column_missing")
    if len(sheet.rows) > MAX_QUESTIONS:
        raise SheetRejected(
            f"一次最多导入 {MAX_QUESTIONS} 个问题", code="audit.xlsx_too_many_questions",
            params={"limit": MAX_QUESTIONS},
        )
    rows: list[tuple[str, str]] = []
    # 同一类问题归到一起报行号：错误只能带一个 code，但不能只报第一行。
    blank: list[int] = []
    too_long: list[int] = []
    bad_language: list[int] = []
    for row_number, row in enumerate(sheet.rows, start=2):
        question = row[question_column].strip() if question_column < len(row) else ""
        if not question:
            blank.append(row_number)
            continue
        if len(question) > MAX_QUESTION_CHARS:
            too_long.append(row_number)
            continue
        raw_language = (
            row[language_column].strip().casefold()
            if language_column is not None and language_column < len(row)
            else "en"
        )
        language = LANGUAGES.get(raw_language or "en")
        if language is None:
            bad_language.append(row_number)
            continue
        rows.append((question, language))
    if blank:
        raise SheetRejected(
            f"第 {'、'.join(map(str, blank[:20]))} 行审计问题为空",
            code="audit.xlsx_questions_blank", params={"rows": blank[:20]},
        )
    if too_long:
        raise SheetRejected(
            f"第 {'、'.join(map(str, too_long[:20]))} 行审计问题超过 {MAX_QUESTION_CHARS} 字符",
            code="audit.xlsx_questions_too_long", params={"rows": too_long[:20], "limit": MAX_QUESTION_CHARS},
        )
    if bad_language:
        raise SheetRejected(
            f"第 {'、'.join(map(str, bad_language[:20]))} 行语言无效",
            code="audit.xlsx_languages_invalid", params={"rows": bad_language[:20]},
        )
    if not rows:
        raise SheetRejected("Excel 没有可导入的审计问题", code="audit.xlsx_no_questions")
    return rows
