"""分块（纯函数，不碰数据库）。

按字符数而不是 token 数切，避免把分块逻辑绑死在某一家 provider 上。
英文约 4 字符/token，1200 字符约 300 token，低于主流 embedding 模型上限。
"""

import re
from dataclasses import dataclass

MAX_CHARS = 1200
OVERLAP_CHARS = 150

_PARAGRAPH = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True)
class Chunk:
    text: str
    index: int


def with_context(heading_path: str, body: str) -> str:
    """给待嵌入文本加上标题路径前缀。"""
    body = body.strip()
    return f"{heading_path}\n\n{body}" if body else heading_path


def _pieces(text: str, max_chars: int) -> list[str]:
    """先按段落切，超长的段落再按句子切，仍超长的硬切在空白处。"""
    out: list[str] = []
    for paragraph in _PARAGRAPH.split(text):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            out.append(paragraph)
            continue

        buffer = ""
        for sentence in _SENTENCE_END.split(paragraph):
            candidate = f"{buffer} {sentence}".strip() if buffer else sentence
            if len(candidate) <= max_chars:
                buffer = candidate
                continue
            if buffer:
                out.append(buffer)
            while len(sentence) > max_chars:
                cut = sentence.rfind(" ", 0, max_chars)
                cut = cut if cut > 0 else max_chars
                out.append(sentence[:cut].strip())
                sentence = sentence[cut:].strip()
            buffer = sentence
        if buffer:
            out.append(buffer)
    return out


def split(
    text: str, *, max_chars: int = MAX_CHARS, overlap: int = OVERLAP_CHARS
) -> list[Chunk]:
    """按段落、句子、空白优先级切分文本，并在块之间保留尾部重叠。"""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("overlap must be non-negative and smaller than max_chars")

    pieces = _pieces(text, max_chars)
    if not pieces:
        return []

    chunks: list[Chunk] = []
    carry = ""
    pending = list(pieces)

    def tail_of(value: str) -> str:
        tail = value[-overlap:] if overlap else ""
        if tail and " " in tail:
            tail = tail[tail.index(" ") + 1 :]
        return tail

    def take_fit(value: str) -> tuple[str, str]:
        """切出一个不断词的前缀，以及没用掉的后缀。"""
        if len(value) <= max_chars:
            return value, ""
        cut = value.rfind(" ", 0, max_chars + 1)
        cut = cut if cut > 0 else max_chars
        return value[:cut].strip(), value[cut:].strip()

    while pending:
        piece = pending.pop(0)
        candidate = f"{carry} {piece}".strip() if carry else piece
        if len(candidate) <= max_chars:
            carry = candidate
            continue

        if carry:
            chunks.append(Chunk(text=carry, index=len(chunks)))
            prefix = tail_of(carry)
            pending.insert(0, f"{prefix} {piece}".strip() if prefix else piece)
            carry = ""
            continue

        # 一个自然段或句子本身就可能在加了重叠之后超限。再按空白切一次，
        # 保证重叠不会把某一块撑过配置的上限。
        part, remainder = take_fit(piece)
        chunks.append(Chunk(text=part, index=len(chunks)))
        if remainder:
            prefix = tail_of(part)
            pending.insert(0, f"{prefix} {remainder}".strip() if prefix else remainder)

    if carry:
        chunks.append(Chunk(text=carry, index=len(chunks)))
    return chunks
