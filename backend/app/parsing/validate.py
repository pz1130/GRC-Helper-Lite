"""house style completeness checks。

默认不强制要求特定章节名，避免把文风差异误报为解析缺失。

知道自己文风的部署方用 `PARSING_EXPECTED_SECTIONS` 配置（逗号分隔），
那时这条检查才有意义——它抓的是"解析漏掉了文档开头"这类真问题。
"""

import os

from app.parsing.contract import ClauseNode, ParsedDocument, ParseWarning

EXPECTED_SECTIONS: tuple[str, ...] = tuple(
    piece.strip()
    for piece in os.environ.get("PARSING_EXPECTED_SECTIONS", "").split(",")
    if piece.strip()
)


def _all_headings(nodes: list[ClauseNode]) -> list[str]:
    headings: list[str] = []
    for node in nodes:
        headings.append(node.heading.strip().casefold())
        headings.extend(_all_headings(node.children))
    return headings


def check_completeness(parsed: ParsedDocument) -> list[str]:
    present = set(_all_headings(parsed.clauses))
    missing = [section for section in EXPECTED_SECTIONS if section.casefold() not in present]
    if not missing:
        return []
    return [
        ParseWarning(
            f"未找到应有的章节：{'、'.join(missing)}——解析可能不完整，建议人工核对",
            code="parse.missing_sections",
            # 传列表而不是拼好的串：顿号是中文排版，拼进参数就会出现在英文句子里。
            params={"sections": list(missing)},
        )
    ]
