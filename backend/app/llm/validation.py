"""结构化输出校验——spec §6.3 的第 2 道闸。

第 3、4 道闸（clause_id 存在性、原文回验）依赖 Clause 表，属 M4。
这里预留 CitationValidator 插槽，M4 填入真实实现即可，runner 不必改动。
"""

import json
import re
import unicodedata
from typing import Any, Protocol

from jsonschema import Draft202012Validator

_FENCED = re.compile(r"```(?:json)?\s*(.+?)\s*```", re.DOTALL)
# 推理模型（如 minimax-m3）以 <think> 思维链开头。不剥掉的话，下面
# 「第一个 { 到最后一个 }」的兜底会从推理文本里抠出模型思考时举的例子，
# 校验于是报出误导性的字段缺失，掩盖了「预算耗尽在思考中途、JSON 根本
# 没写出来」这个真正的问题。未闭合的 <think> 一路吃到结尾。
_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.DOTALL | re.IGNORECASE)


class ValidationFailure(Exception):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# PDF 抽出来的文本带排版残留，模型抄引文时会不声不响地把它们规范化。这里做同样的
# 折叠：闸 4 仍然是逐字比对——编造或改写过的词照样过不了子串检查——但不再把
# 忠实的引文误拒。
_FOLD = str.maketrans({
    "\u2018": "'", "\u2019": "'", "\u201a": "'", "\u201b": "'", "\u2032": "'",
    "\u201c": '"', "\u201d": '"', "\u201e": '"', "\u201f": '"', "\u2033": '"',
    "\u2010": "-", "\u2011": "-", "\u2012": "-", "\u2013": "-", "\u2014": "-",
    "\u2015": "-", "\u2212": "-",
    "\u2022": " ", "\u2023": " ", "\u25aa": " ", "\u25cf": " ", "\u00b7": " ",
})
_LINE_WRAP = re.compile(r"(?<=\w)-[ \t]*\r?\n[ \t]*(?=\w)")


def normalize(text: str) -> str:
    folded = unicodedata.normalize("NFKC", text).translate(_FOLD)
    return " ".join(_LINE_WRAP.sub("-", folded).split())


def clause_source(clause: Any) -> str:
    """一条条款的**全部文字**：标题在前，正文在后。

    段落首行可能被解析为标题，剩余文字成为正文。引用校验应覆盖两者。
    例如合成句子分成 heading="Teams must" 与 text="retain records." 时，
    只检查正文会误拒完整引文。

    放宽的只是"这条条款的全部文字"，不是"随便什么文字"：闸 4 仍然要求逐字命中。
    """
    heading = (getattr(clause, "heading", "") or "").strip()
    text = (getattr(clause, "text", "") or "").strip()
    return f"{heading}\n{text}".strip()


def extract_json(raw: str) -> dict[str, Any]:
    """容忍模型把 JSON 包在代码块里或前后带解释文字。"""
    raw = _THINK.sub("", raw)
    candidates: list[str] = []

    fenced = _FENCED.search(raw)
    if fenced:
        candidates.append(fenced.group(1))
    candidates.append(raw.strip())

    # 兜底：取第一个 { 到最后一个 } 之间的内容
    start, end = raw.find("{"), raw.rfind("}")
    if start != -1 and end > start:
        candidates.append(raw[start : end + 1])

    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed

    raise ValidationFailure("no valid JSON object was found in the model output")


def validate(payload: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    errors = sorted(Draft202012Validator(schema).iter_errors(payload), key=lambda e: e.path)
    if errors:
        first = errors[0]
        location = "/".join(str(part) for part in first.absolute_path) or "(root)"
        raise ValidationFailure(f"{location}: {first.message}")
    return payload


def correction_prompt(previous: str, reason: str) -> str:
    # 系统提示词是英文的，纠错也用英文——原来这里是中文，模型在一场英文对话里
    # 收到中文纠错。校验理由（schema 与引用校验）同样写英文：读者先是模型，再是
    # 在确认队列里看到它的审核人（2026-09-23 起产品只有英文界面）。
    return (
        "Your previous output did not satisfy the required JSON Schema or citation checks.\n"
        f"Previous output:\n{previous}\n\n"
        f"Problem: {reason}\n\n"
        "Output only the corrected JSON object, with no explanation and no code fences."
    )


class CitationValidator(Protocol):
    """引用校验插槽。返回 None 表示通过，返回字符串表示失败原因。"""

    async def check(self, payload: dict[str, Any]) -> str | None: ...


class NullCitationValidator:
    """M1 的默认实现。M4 会用查库 + 原文回验的实现替换它。"""

    async def check(self, payload: dict[str, Any]) -> str | None:
        return None
