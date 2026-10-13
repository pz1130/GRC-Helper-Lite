"""无法识别结构的文字仍保留为可引用的块，不伪造条款编号。"""

from app.parsing.contract import ClauseNode, ParseWarning

MAX_BLOCK_CHARS = 4000


def append_text(
    nodes: list[ClauseNode], text: str, *, page_ref: int | None = None,
    source_key: str | None = None,
) -> None:
    if not text.strip():
        return
    if (
        nodes and nodes[-1].kind == "unstructured"
        and nodes[-1].page_ref == page_ref
        and len(nodes[-1].text) + len(text) + 1 <= MAX_BLOCK_CHARS
    ):
        nodes[-1].text += "\n" + text
        if source_key is not None:
            nodes[-1].source_keys.append(source_key)
        return
    # 标签明确是系统生成的位置，不冒充文档自己的章节标题。
    nodes.append(ClauseNode(
        heading=f"Unstructured text {len(nodes) + 1}", text=text,
        level=1, page_ref=page_ref, kind="unstructured",
        source_keys=[source_key] if source_key is not None else [],
    ))


# 第一个章节之前可能有封面、修订记录等前置内容，不应一律告警。
# 前置文字占比过大时仍需提示可能漏认标题；阈值是启发式，应人工复核。
# 表格不算进分子：前置表格占比较高并不意味着正文标题缺失。
MAX_FRONT_MATTER_SHARE = 0.25


def _text_size(nodes: list[ClauseNode]) -> int:
    return sum(len(node.text or "") + _text_size(node.children) for node in nodes)


def structure_warnings(nodes: list[ClauseNode]) -> list[ParseWarning]:
    count = sum(node.kind == "unstructured" for node in nodes)
    if not count:
        return []
    first = next((i for i, node in enumerate(nodes) if node.kind == "section"), None)
    if first is not None and not any(node.kind == "unstructured" for node in nodes[first:]):
        front = sum(len(node.text or "") for node in nodes[:first] if node.kind == "unstructured")
        if front <= _text_size(nodes) * MAX_FRONT_MATTER_SHARE:
            return []
    return [ParseWarning(
        f"已保留 {count} 个结构待确认的文本块，请核对章节归属",
        code="parse.unstructured_text_preserved", params={"count": count},
    )]
