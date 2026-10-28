"""单元测试：共享函数 `canonicalize_relation_endpoints` 的端点规范化语义。

对应 bugfix「merge-duplicates-canonicalization」任务 4 的单元测试部分
（design「Unit Tests」）。写入、计划、执行三处共用这一份实现，故它必须对
`DUPLICATES`（对称）把端点收敛为 `from_id < to_id`、对所有其它（有方向）关系
无论端点大小都原样返回。

覆盖：
- `DUPLICATES` 乱序（from > to）交换为 (min, max)
- `DUPLICATES` 已是规范序（from < to）不动
- `DUPLICATES` from == to 不动（自环，规范化不改变相等性）
- 非 `DUPLICATES`（含 DEPENDS_ON / IMPLEMENTS / REFINES / CONFLICTS_WITH）任何序都不动
"""

import pytest

from app.controls.models import RelationType, canonicalize_relation_endpoints

# 所有有方向、不参与规范化的关系类型（即除 DUPLICATES 外的全部枚举值）。
_非对称类型 = [kind for kind in RelationType if kind is not RelationType.DUPLICATES]


def test_DUPLICATES乱序时交换端点为规范序():
    """from > to 的 DUPLICATES 应交换成 (min, max)。"""
    assert canonicalize_relation_endpoints(9, 2, RelationType.DUPLICATES) == (2, 9)


def test_DUPLICATES已是规范序时端点不动():
    """from < to 的 DUPLICATES 已是规范序，原样返回。"""
    assert canonicalize_relation_endpoints(2, 9, RelationType.DUPLICATES) == (2, 9)


def test_DUPLICATES两端相等时端点不动():
    """from == to（自环）规范化不改变相等性，原样返回。"""
    assert canonicalize_relation_endpoints(5, 5, RelationType.DUPLICATES) == (5, 5)


@pytest.mark.parametrize("kind", _非对称类型, ids=[k.value for k in _非对称类型])
def test_非DUPLICATES关系乱序时也不重排端点(kind):
    """有方向的关系即便 from > to 也不交换 —— 方向有语义，不能规范化掉。"""
    assert canonicalize_relation_endpoints(9, 2, kind) == (9, 2)


@pytest.mark.parametrize("kind", _非对称类型, ids=[k.value for k in _非对称类型])
def test_非DUPLICATES关系正序时端点不动(kind):
    """有方向的关系 from < to 同样原样返回。"""
    assert canonicalize_relation_endpoints(2, 9, kind) == (2, 9)
