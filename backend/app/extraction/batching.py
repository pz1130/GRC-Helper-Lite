"""确定性的分批：条款既不切开也不丢弃。"""

from dataclasses import dataclass, field

from app.clauses.models import Clause
from app.extraction.references import Reference, ReferenceIndex

MAX_BATCH_CHARS = 12000
MAX_REFERENCE_CHARS = 6000
# 每批最多几条**有正文**的目标条款。只按字数塞满的话，一批有几条取决于文档怎么分章：
# 每章一条的文档一批一条，每章七八条的一批七八条。两阶段抽取要为每条目标各交一份
# 审查结论，G4 实测（MiniMax M3，2026-09-27）七八条一批时输出三四万 token 后丢掉
# JSON 格式，两份文档 14 条关键要求一条没抽到；一条一批的两份召回 13/14。
# 上限 2 后同两份复测：关键要求召回 0/14 → 13/14，批次校验通过率约 45% → 约 80%。
# 取 2 而不是 1：一条一批也可靠，但调用数翻倍；2 已经把失败拉回到与一条一批同一水平。
MAX_BATCH_TARGETS = 2


@dataclass(frozen=True)
class Batch:
    clauses: list[Clause]
    section: str
    context: list[Clause] = field(default_factory=list)
    references: list[Reference] = field(default_factory=list)

    @property
    def source_ids(self) -> list[int]:
        return [c.id for c in [*self.context, *self.clauses]]


def render(batch: Batch) -> str:
    lines = [f"# Section: {batch.section}", ""]

    def append_clause(clause: Clause) -> None:
        lines.append(f"[clause_id={clause.id}] {clause.citation_label} — {clause.heading}")
        parent_id = getattr(clause, "parent_id", None)
        if parent_id is not None:
            lines.append(f"[parent_clause_id={parent_id}]")
        if clause.text:
            lines.append(clause.text)
        lines.append("")

    if batch.context:
        lines.append("# Supporting context (ancestors and explicit internal references; do not extract on its own)")
    for clause in batch.context:
        append_clause(clause)
    if batch.references:
        lines.append("# Internal reference resolution (unresolved references require human review)")
        for ref in batch.references:
            lines.append(
                f"[from_clause_id={ref.source_clause_id}] {ref.label}: {ref.status}"
                + (f" [to_clause_id={ref.target_clause_id}]" if ref.target_clause_id else "")
            )
    lines.append("# Target clauses")
    for clause in batch.clauses:
        append_clause(clause)
    return "\n".join(lines)


def build_batches(
    clauses: list[Clause], *, max_chars: int = MAX_BATCH_CHARS,
    max_targets: int = MAX_BATCH_TARGETS,
) -> list[Batch]:
    """目标条款恰好出现一次；父级与明确的内部引用作为可引用上下文重带。

    大小按含上下文的完整提示词计算。一个目标加其上下文就超限时单独成批，
    不截断原文或悄悄丢弃限定条件。
    """
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if max_targets <= 0:
        raise ValueError("max_targets must be positive")

    def substantive(items: list[Clause]) -> int:
        # 纯标题条款没有可抽的义务，只交一条很短的审查结论，不占名额。
        return sum(1 for item in items if (item.text or "").strip())
    by_id = {clause.id: clause for clause in clauses}
    reference_index = ReferenceIndex(clauses)

    def with_context(targets: list[Clause], section: str) -> Batch:
        target_ids = {c.id for c in targets}
        context_ids: set[int] = set()
        for target in targets:
            seen = {target.id}
            parent_id = getattr(target, "parent_id", None)
            while parent_id in by_id and parent_id not in seen:
                seen.add(parent_id)
                parent = by_id[parent_id]
                if parent.document_id != target.document_id:
                    break
                if parent_id not in target_ids:
                    context_ids.add(parent_id)
                parent_id = getattr(parent, "parent_id", None)
        # 广度优先跟随明确引用，包含引用条款的父级限定；遇到循环只读一次。
        included = target_ids | context_ids
        queue = [c for c in clauses if c.id in included]
        visited: set[int] = set()
        references: list[Reference] = []
        remaining = MAX_REFERENCE_CHARS
        for source in queue:
            if source.id in visited:
                continue
            visited.add(source.id)
            for ref in reference_index.resolve(source):
                if ref.status != "resolved" or ref.target_clause_id in included:
                    references.append(ref)
                    continue
                dependency = by_id[ref.target_clause_id]
                additions = {dependency.id}
                parent_id = getattr(dependency, "parent_id", None)
                while parent_id in by_id and parent_id not in additions:
                    parent = by_id[parent_id]
                    if parent.document_id != source.document_id:
                        break
                    additions.add(parent_id)
                    parent_id = getattr(parent, "parent_id", None)
                extra = [c for c in clauses if c.id in additions - included]
                cost = len(render(Batch(extra, "")))
                if cost > remaining:
                    references.append(Reference(source.id, ref.label, "context_limit", dependency.id))
                    continue
                remaining -= cost
                included.update(additions)
                context_ids.update(additions - target_ids)
                queue.extend(extra)
                references.append(ref)
        return Batch(targets, section, [c for c in clauses if c.id in context_ids], references)

    groups: list[list[Clause]] = []
    sections: dict[int, str] = {}
    for clause in clauses:
        if clause.level <= 1 or not groups:
            groups.append([])
            sections[len(groups) - 1] = clause.heading or ""
        groups[-1].append(clause)
    batches: list[Batch] = []
    for position, group in enumerate(groups):
        section = sections[position]
        chunk: list[Clause] = []
        for clause in group:
            candidate = with_context([*chunk, clause], section)
            if chunk and (
                len(render(candidate)) > max_chars
                or substantive([*chunk, clause]) > max_targets
            ):
                batches.append(with_context(chunk, section))
                chunk = []
            chunk.append(clause)
        if chunk:
            batches.append(with_context(chunk, section))
    return batches
