"""按顶层子树分组、按字符预算切分的通用算法。"""

from collections.abc import Callable, Sequence


def group_by_top_level[T](
    units: Sequence[T],
    *,
    level_of: Callable[[T], int],
    render_size: Callable[[T], int],
    max_chars: int,
    header_size: int = 0,
) -> list[list[T]]:
    """保持输入顺序。单个超长单元独占一批且不被拆开。"""
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    groups: list[list[T]] = []
    for unit in units:
        if level_of(unit) <= 1 or not groups:
            groups.append([])
        groups[-1].append(unit)

    batches: list[list[T]] = []
    for group in groups:
        current: list[T] = []
        size = header_size
        for unit in group:
            length = render_size(unit)
            if current and size + length > max_chars:
                batches.append(current)
                current, size = [], header_size
            current.append(unit)
            size += length
        if current:
            batches.append(current)
    return batches
