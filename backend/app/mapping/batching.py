"""框架项 → 批次；控制点全量渲染。"""

from dataclasses import dataclass
from typing import Any

from app.common.batching import group_by_top_level
from app.errors import AppError
from app.frameworks.coverage import is_requirement

# M4 抽取任务用 12000，但那时提示词里没有几万字符的控制点全集。136 个控制点之后，
# 每批最多 57 个框架项 × 全量控制点的组合空间让模型输出失控（通过率跌到 55%）。
# 减半后每批最多 29 个条目；代价是控制点上下文多重发一轮（批次 40 → 66）。
MAX_BATCH_CHARS = 6000
MAX_CONTROL_CONTEXT_CHARS = 60000


class ControlContextTooLarge(AppError):
    """控制点全集超过映射任务的上下文上限。"""


@dataclass(frozen=True)
class Batch:
    items: list[Any]
    section: str
    mappable_ids: frozenset[int]


def _render_item(item: Any, *, mappable: bool) -> str:
    """容器不带 id 标记：模型无法引用它，也就无法映射到它。

    容器（Function / Category / Family）不是要求项——覆盖度分母里没有它们，
    映过去不消除任何差距。800-53 的 Family 更是连正文都没有，闸 4 必然拒绝，
    而一条不合格就整批作废，一个容器能打掉整批。所以只保留为上下文。
    """
    marker = f"[framework_item_id={item.id}]" if mappable else "##"
    return f"{marker} {item.code} — {item.title}\n{item.description or ''}\n"


def render_items(batch: Batch) -> str:
    lines = [f"# Section: {batch.section}", ""]
    for item in batch.items:
        lines.append(_render_item(item, mappable=item.id in batch.mappable_ids))
    return "\n".join(lines)


def build_batches(items: list[Any], *, max_chars: int = MAX_BATCH_CHARS) -> list[Batch]:
    """按顶层子树分批。容器留在批内作上下文，但不作为映射目标。"""
    with_children = {item.parent_id for item in items if getattr(item, "parent_id", None)}
    requirements = {
        item.id for item in items if is_requirement(item, item.id in with_children)
    }
    groups = group_by_top_level(
        items,
        level_of=lambda item: item.level,
        render_size=lambda item: len(_render_item(item, mappable=item.id in requirements)),
        max_chars=max_chars,
    )
    batches = []
    for group in groups:
        mappable = frozenset(item.id for item in group if item.id in requirements)
        # 整批都是容器时不值得花一次模型调用。
        if group and mappable:
            batches.append(Batch(list(group), group[0].code, mappable))
    return batches


def render_controls(controls: list[Any]) -> str:
    """全量控制点。超限直接失败——静默截断会让差距结论悄悄退化。

    标题排在编号之前：曾经渲染成 `[control_id=9] C-0009 — 标题`，
    模型把紧随其后的 C-0009 当成了 control_id 交上来。
    """
    parts = [
        f"[control_id={control.id}] {control.title} ({control.code})\n"
        f"{control.statement or ''}\n"
        for control in controls
    ]
    text = "\n".join(parts)
    if len(text) > MAX_CONTROL_CONTEXT_CHARS:
        raise ControlContextTooLarge(
            f"控制点全集渲染后 {len(text)} 字符，超过上限 {MAX_CONTROL_CONTEXT_CHARS}"
            f"（当前 {len(controls)} 个控制点）。映射任务需要看到全部控制点才能断言差距，"
            f"不做截断；请引入候选控制点检索后再运行。",
            code="mapping.control_context_too_large",
            params={"chars": len(text), "limit": MAX_CONTROL_CONTEXT_CHARS, "controls": len(controls)},
        )
    return text
