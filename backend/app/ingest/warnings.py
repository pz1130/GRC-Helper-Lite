"""把解析告警里的 code 摘出来存库。

告警本身是中文句子（`ParseWarning` 是 `str` 的子类），存在
`Document.parse_warnings` 里；这里摘的是它挂着的 code 与变量，存进
`Document.parse_warning_codes`，前端据此翻译。

**没有 code 的告警不进这个列表**：老行、以及还没迁的告警，`parse_warning_codes`
是 NULL，前端就回落到那句中文。空列表会被当成"有 code 但一条都没有"，
和 NULL 不是一回事。
"""

from collections.abc import Sequence

from app.parsing.contract import ParseWarning


def to_codes(warnings: Sequence[str]) -> list[dict[str, object]] | None:
    coded = [
        {"code": warning.code, "params": warning.params}
        for warning in warnings
        if isinstance(warning, ParseWarning)
    ]
    return coded or None
