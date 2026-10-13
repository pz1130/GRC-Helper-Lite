"""检测「结论强于原文」——抽取的控制点把原文语气拉平成 must/shall。

**为什么引用校验查不出来**：闸 4 查的是「引文是否逐字出现在原文中」，而被改动的
情态动词在**生成的 statement 里**，不在引文里。引文可以完全正确，结论却被加强。
这不是闸门失灵，是它本来就不管这件事。

最清楚的一类是原文用「现在这么做」的陈述句（"the review board **meets**
every quarter"）→ 生成变成「必须这么做」的义务句（"**must meet**"）。
原文没承诺的事，控制点替它承诺了。

**为什么只标记不拦截**：SLA 表格用行列表达义务，一个情态词都没有，
渲染成 "must be responded within 10 minutes" 往往合理。做成硬闸门会把
它们全拒掉。所以给审核者一个提示，判断权留给人。
"""

import re

# 结论侧只认强制式：这是被加强之后的样子。
_UPGRADED = re.compile(r"\b(must|shall)\b", re.IGNORECASE)
# 原文侧从宽：只要有任何情态/义务的迹象就不算升格，宁可漏报也不要制造噪声。
_ANY_MODAL = re.compile(
    r"\b(must|shall|should|may|will|required|mandatory|obliged|responsible for)\b",
    re.IGNORECASE,
)


def upgraded_from(statement: str | None, sources: list[str]) -> bool:
    """结论用了 must/shall，而被引的原文里一个情态词都没有。"""
    if not statement or not _UPGRADED.search(statement):
        return False
    return not any(_ANY_MODAL.search(text or "") for text in sources)
