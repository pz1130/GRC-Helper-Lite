"""可逆脱敏管道（spec §6.2）。

占位符格式 `[[PREFIX_N]]`。有两条不变量必须守住，各自对应一个曾经踩过的坑：

1. **一次调用里的编号全局唯一。** 早先 system 与 prompt 各调一次 redact()，
   两边计数器都从 1 开始，合并映射表时 `[[IP_1]]` 直接撞掉，还原出来是另一个
   实体——静默且看起来完全合理。所以对外的主接口是 redact_many()，一次把所有
   文本喂进来共享一套编号。
2. **后续规则不得扫进已经生成的占位符。** 规则是顺序叠加的，而 `PREFIX_N`
   这个形状本身就像系统账号号（`SVC_88`）；一条 `[A-Z]{2,}_\\d+` 的规则会啃进
   `[[IP_1]]` 内部，套出 `[[[[ACCT_1]]]]`，可逆性当场归零。所以文本按
   "是否占位符"切成段，规则只作用在非占位符段上。这是精确做法，不是挑一个
   "但愿没人写得出来的分隔符"去赌。
"""

import re
from collections import Counter
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.llm.models import RedactionRule, RulesetName

# (是否占位符, 片段文本)
_Segment = tuple[bool, str]


@dataclass(frozen=True)
class RedactionBatch:
    """一次脱敏的结果：多段文本共享同一张映射表与同一套编号。"""

    texts: list[str]
    mapping: dict[str, str] = field(default_factory=dict)  # 占位符 → 原文
    hits: dict[str, int] = field(default_factory=dict)     # 前缀 → 不同实体数


@dataclass(frozen=True)
class RedactionResult:
    text: str
    mapping: dict[str, str] = field(default_factory=dict)
    hits: dict[str, int] = field(default_factory=dict)


class _Allocator:
    """跨文本共享的占位符分配器：同一原文永远拿到同一个代号。"""

    def __init__(self) -> None:
        self.mapping: dict[str, str] = {}
        self._reverse: dict[str, str] = {}
        self._counters: Counter[str] = Counter()

    def placeholder_for(self, original: str, prefix: str) -> str:
        existing = self._reverse.get(original)
        if existing is not None:
            return existing
        self._counters[prefix] += 1
        placeholder = f"[[{prefix}_{self._counters[prefix]}]]"
        self._reverse[original] = placeholder
        self.mapping[placeholder] = original
        return placeholder

    @property
    def hits(self) -> dict[str, int]:
        return dict(self._counters)


class RedactionEngine:
    def __init__(self, rules: list[RedactionRule]) -> None:
        self._rules = sorted(
            (r for r in rules if r.enabled), key=lambda r: (r.order_index, r.id or 0)
        )

    def _compile(self, rule: RedactionRule) -> re.Pattern[str] | None:
        if rule.pattern_type == "regex":
            return re.compile(rule.pattern)
        if rule.pattern_type == "dictionary":
            terms = [t.strip() for t in rule.pattern.splitlines() if t.strip()]
            if not terms:
                return None
            # 长词优先，避免"集团"抢在"示例集团"前面命中
            terms.sort(key=len, reverse=True)
            return re.compile("|".join(re.escape(t) for t in terms))
        raise ValueError(f"未知的 pattern_type: {rule.pattern_type}")

    @staticmethod
    def _apply(
        segments: list[_Segment],
        pattern: re.Pattern[str],
        prefix: str,
        alloc: _Allocator,
    ) -> list[_Segment]:
        out: list[_Segment] = []
        for is_placeholder, chunk in segments:
            if is_placeholder:
                out.append((True, chunk))   # 占位符段原样带过，规则扫不进去
                continue

            pos = 0
            for match in pattern.finditer(chunk):
                if match.start() == match.end():
                    continue   # 零宽匹配跳过，否则会插入空占位符
                if match.start() > pos:
                    out.append((False, chunk[pos : match.start()]))
                out.append((True, alloc.placeholder_for(match.group(0), prefix)))
                pos = match.end()
            if pos < len(chunk):
                out.append((False, chunk[pos:]))
        return out

    def redact_many(self, texts: list[str]) -> RedactionBatch:
        """主接口：多段文本共享一套编号与一张映射表。"""
        alloc = _Allocator()
        compiled = [
            (pattern, rule.replacement_prefix)
            for rule in self._rules
            if (pattern := self._compile(rule)) is not None
        ]

        redacted: list[str] = []
        for text in texts:
            segments: list[_Segment] = [(False, text)]
            for pattern, prefix in compiled:
                segments = self._apply(segments, pattern, prefix, alloc)
            redacted.append("".join(chunk for _, chunk in segments))

        return RedactionBatch(texts=redacted, mapping=dict(alloc.mapping), hits=alloc.hits)

    def redact(self, text: str) -> RedactionResult:
        """单段文本的薄包装。多段务必用 redact_many()，否则编号会撞。"""
        batch = self.redact_many([text])
        return RedactionResult(text=batch.texts[0], mapping=batch.mapping, hits=batch.hits)

    def restore(self, text: str, mapping: dict[str, str]) -> str:
        """一次扫完，填回去的原文不再参与后续匹配。

        逐条 `str.replace` 会让「原文本身长得像另一个占位符」的那条被吃第二次，
        还原出第三个东西——静默、且看起来合理。规则是管理员自己写的正则，
        所以这种原文不是纯理论。长键优先，避免前缀互相遮挡。
        """
        if not mapping:
            return text
        pattern = re.compile(
            "|".join(re.escape(key) for key in sorted(mapping, key=len, reverse=True))
        )
        return pattern.sub(lambda match: mapping[match.group(0)], text)


async def load_engine(session: AsyncSession, ruleset: RulesetName) -> RedactionEngine:
    rules = list(
        await session.scalars(
            select(RedactionRule)
            .where(RedactionRule.ruleset == ruleset, RedactionRule.enabled.is_(True))
            .order_by(RedactionRule.order_index)
            .execution_options(populate_existing=True)
        )
    )
    return RedactionEngine(rules)
