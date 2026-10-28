# 合并对称关系规范化（merge-duplicates-canonicalization）Bugfix Design

## Overview

`DUPLICATES`（对称）关系必须以规范序 `from_control_id < to_control_id` 存库——否则 A→B 与 B→A 会存成两条互为镜像、唯一约束 `UNIQUE (from_control_id, to_control_id, relation_type)` 无法识别的记录。写入路径 `review/materialize.py._materialize_relation` 已经做了这个规范化（约第 133 行：`if validated.relation_type is RelationType.DUPLICATES and start > end: start, end = end, start`），但合并路径没有。

缺陷出在两处：

1. **执行**：`merge_controls`（`review/materialize.py`）把输家关系的端点重写到赢家 id 时，对 `DUPLICATES` 行不做交换，于是落库的镜像行绕过唯一约束。
2. **计划**：`_plan_relations`（`controls/merge.py`）用未规范化的原始元组构建 `occupied` 集合与转挂后的 `key`，把一次镜像碰撞错判为「转挂（move）」而非「重复丢弃（discard）」。

修复的策略是：抽出**一个**共享小函数 `canonicalize_relation_endpoints`，让写入路径、合并执行路径、合并计划路径都调用它——这正是本代码库反复警示的「两边规则一旦分叉、后果静默」失效模式的对症做法。计划与执行必须同步改动并保持一致：执行时会重算 `plan_digest` 与预览 `expected_digest` 比对，若一边算成 move、另一边算成 discard，指纹发散、合并以 `control.merge_plan_changed` 失败。

修复严守铁律：只有 `review/materialize.py` 写 `control_relations`；保留审计轨迹；除既定 `plan.discards` 外不做任何静默 DELETE。

## Glossary

- **Bug_Condition (C)**：触发缺陷的条件——合并把输家的 `DUPLICATES` 关系转挂到赢家后，原始端点 `rawFrom > rawTo`（即落入非规范序）。
- **Property (P)**：期望行为——`DUPLICATES` 行以 `from_control_id < to_control_id` 落库；镜像碰撞被当作重复丢弃，库中不留镜像行。
- **Preservation**：必须保持不变的行为——非对称关系（如 `DEPENDS_ON`）端点不重排；自环丢弃；精确重复丢弃；无碰撞的 `DUPLICATES` 仍计为 move；写入路径既有规范化不变；合并仍标记输家 `merged`、写 `merged_into_id`、留审计。
- **canonicalize_relation_endpoints**：拟抽取的共享函数，输入 `(from_id, to_id, relation_type)`，对 `DUPLICATES` 返回 `(min, max, type)`，其它类型原样返回。写入、计划、执行三处共用这一份实现。
- **`_materialize_relation`**：`review/materialize.py` 中把单条确认关系落库的函数，已有 `DUPLICATES` 规范化逻辑。
- **`merge_controls`**：`review/materialize.py` 中执行合并的函数，把输家关联转挂到赢家。
- **`_plan_relations`**：`controls/merge.py` 中只读地规划关系转挂/丢弃的函数。
- **occupied**：计划中赢家一侧（不含输家端点）既有关系元组的集合，用于判定转挂后是否碰撞。
- **plan_digest / expected_digest**：合并计划指纹；预览时交前端，执行时重算比对，不一致则拒绝合并。
- **rawFrom / rawTo**：输家关系转挂到赢家 id 后、尚未规范化的原始端点。

## Bug Details

### Bug Condition

缺陷在合并把输家的一条 `DUPLICATES` 关系转挂到赢家、且转挂后的原始端点 `rawFrom > rawTo`（落入非规范序）时显现。此时执行路径不交换端点、直接落库镜像行；计划路径用原始元组比对 `occupied`，因镜像元组与既有规范元组不相等而漏判碰撞，计成 move。

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input of type MergeRelationMove   // 输家一条被转挂到赢家的 DUPLICATES 关系
  OUTPUT: boolean

  rawFrom ← (winner.id IF input.from_control_id = loser.id ELSE input.from_control_id)
  rawTo   ← (winner.id IF input.to_control_id   = loser.id ELSE input.to_control_id)

  RETURN input.relation_type = DUPLICATES
         AND rawFrom ≠ rawTo
         AND rawFrom > rawTo
END FUNCTION
```

### Examples

- 输家 `L` 持有规范化的 `DUPLICATES` 行 `(L, X)`（`L < X`）；赢家 `W` 已持有 `(X, W)`（`X < W`）。把 `L` 并入 `W`，该行被转挂为原始元组 `(W, X)`，其规范形 `(X, W)` 已存在。期望：判为重复丢弃、不落库。实际（缺陷）：计划把原始 `(W, X, DUPLICATES)` 比对 `occupied = {(X, W, DUPLICATES)}` 未命中，计为 move，执行落库 `(W, X)`，于是库里同时有 `(X, W)` 与 `(W, X)` 两条镜像行。
- 输家 `L` 持有 `(A, L)`（`A < L`），赢家 `W` 满足 `W < A`，赢家无相关 `DUPLICATES`。转挂后原始元组 `(A, W)`，`A > W`，落入非规范序。期望：规范化为 `(W, A)` 落库，计为 move。实际（缺陷）：以 `(A, W)` 落库，端点未按规范序。
- 非对称例子（对照）：输家 `L` 持有 `DEPENDS_ON (L, Y)`，转挂为 `(W, Y)`。`DEPENDS_ON` 有方向，不参与规范化，`isBugCondition` 返回 false，行为不变。
- 边界：转挂后两端都落到赢家（自环），`rawFrom = rawTo`，`isBugCondition` 返回 false，按既有 `merge.discard.relation_self_loop` 丢弃，不受本修复影响。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- 非对称关系（`DEPENDS_ON` 等）转挂时不重排端点。
- 转挂后成为自环的关系仍以 `merge.discard.relation_self_loop` 丢弃。
- 转挂后原始元组与赢家既有关系精确相同的，仍以 `merge.discard.relation_duplicate` 丢弃。
- 无碰撞的 `DUPLICATES` 转挂仍计为 move。
- 写入路径 `_materialize_relation` 对 `DUPLICATES` 的既有规范化逻辑不变。
- 合并仍把输家标记为 `merged`、写入 `merged_into_id`、记录审计轨迹；除 `plan.discards` 外不删除确认数据。

**Scope:**
所有不满足 `isBugCondition` 的输入都应完全不受本修复影响，包括：
- 非 `DUPLICATES` 的任何关系转挂。
- 转挂后端点本就 `rawFrom < rawTo`（已是规范序）的 `DUPLICATES` 关系。
- 转挂后成为自环的关系。
- 合并路径以外对 `control_relations` 的任何读写（本修复不新增写入点）。

> 期望的「正确行为」本身定义在 Correctness Properties（Property 1）；本节聚焦「什么必须不变」。

## Hypothesized Root Cause

根因清晰、已由代码确认，非推测：合并路径缺少写入路径已有的 `DUPLICATES` 规范化。具体潜在成因分列如下。

1. **执行端点未规范化**：`merge_controls` 的关系转挂循环把 `from_control_id` / `to_control_id` 直接改为 `winner.id`，对 `DUPLICATES` 不做 `start > end` 交换。因此会落库 `(W, X)` 这类非规范序行，并与既有 `(X, W)` 构成唯一约束无法识别的镜像。

2. **计划 occupied 未规范化**：`_plan_relations` 的 `occupied` 由赢家侧关系的原始元组构成；对 `DUPLICATES`，应以规范形纳入集合，否则镜像比对必然漏判。

3. **计划转挂 key 未规范化**：`_plan_relations` 用转挂后的原始 `(new_from, new_to, relation_type)` 作为 `key` 比对 `occupied`；对 `DUPLICATES` 应先规范化再比对，否则镜像碰撞被计成 move。

4. **规则重复实现的漂移风险**：写入路径已有一份内联规范化；若合并路径再各写一份，三处实现极易悄悄分叉。根因之一是「缺少单一实现」，故修复以抽取共享函数收敛。

5. **执行侧碰撞落库**：即便执行端规范化了，若转挂后的规范行恰与赢家既有行相同，仍需在执行时发现并丢弃该行（而非插入），与计划里 `relation_duplicate` 的丢弃判定一致。

## Correctness Properties

Property 1: Bug Condition - 合并对称关系规范化并去镜像

_For any_ input where the bug condition holds（`isBugCondition` 返回 true：一条 `DUPLICATES` 关系转挂到赢家后 `rawFrom > rawTo`），the fixed function SHALL 把端点交换为规范序落库，使 `from_control_id < to_control_id`；且当该关系的规范形与赢家某条既有 `DUPLICATES` 关系相同（镜像碰撞）时，SHALL 在计划中报 `merge.discard.relation_duplicate`、在执行中丢弃该行，使合并后 `control_relations` 中不存在镜像行。

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - 非缺陷转挂行为完全一致

_For any_ input where the bug condition does NOT hold（`isBugCondition` 返回 false：非 `DUPLICATES` 关系、已是规范序的 `DUPLICATES`、自环、或合并路径以外的读写），the fixed function SHALL 产生与原函数完全相同的结果——非对称关系端点不重排、自环仍以 `relation_self_loop` 丢弃、精确重复仍以 `relation_duplicate` 丢弃、无碰撞 `DUPLICATES` 仍计为 move、写入路径既有规范化不变、输家仍标记 `merged` 并留审计。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6**

## Fix Implementation

### Changes Required

根因分析已由代码确认，按此实施。

**新增共享函数**：`canonicalize_relation_endpoints`

**位置**：建议置于 `backend/app/controls/models.py`（与 `RelationType` 同模块，`controls/merge.py` 与 `review/materialize.py` 均可无环导入），或 `backend/app/controls/merge.py` 顶层。最终位置以不引入循环导入为准。

**签名与语义**：
```
FUNCTION canonicalize_relation_endpoints(from_id, to_id, relation_type)
  IF relation_type = DUPLICATES AND from_id > to_id THEN
    RETURN (to_id, from_id)
  END IF
  RETURN (from_id, to_id)
END FUNCTION
```

**改动 1 —— 写入路径收敛到共享函数**

**File**：`backend/app/review/materialize.py`

**Function**：`_materialize_relation`（约第 133 行）

1. 用 `start, end = canonicalize_relation_endpoints(start, end, validated.relation_type)` 替换现有内联的 `if validated.relation_type is RelationType.DUPLICATES and start > end: start, end = end, start`。行为等价，仅收敛为单一实现（满足 3.5：既有规范化不变）。

**改动 2 —— 合并执行路径规范化并处理镜像碰撞**

**File**：`backend/app/review/materialize.py`

**Function**：`merge_controls`（关系转挂循环，约第 704–715 行）

1. 在把 `from_control_id` / `to_control_id` 重写到 `winner.id` 之后，对每行调用 `canonicalize_relation_endpoints(row.from_control_id, row.to_control_id, row.relation_type)`，把结果写回该行端点（`DUPLICATES` 交换，其它不变）。
2. **镜像碰撞处理**：若规范化后的该行与赢家某条既有 `control_relations` 行 `(from, to, relation_type)` 相同，则**删除**（丢弃）该行而非写入——与 `merge_controls` 前段「先删 `plan.discards` 再转挂」驱动删除的方式一致，不引入 `plan.discards` 之外的新删除语义（计划已把该碰撞判为 `relation_duplicate` 丢弃，执行据此落实；满足 2.2、3.6）。实现上需在重写端点前先读出赢家侧既有关系键集合（排除输家端点行），据此判定碰撞。
3. 顺序注意：与既有「先删重复行、`flush`，再转挂」保持一致；规范化与碰撞判定在转挂循环内完成，避免先插入镜像行再触发唯一约束。

**改动 3 —— 合并计划路径规范化 occupied 与转挂 key**

**File**：`backend/app/controls/merge.py`

**Function**：`_plan_relations`（约第 124–166 行）

1. 构建 `occupied` 时，对每条赢家侧关系用 `canonicalize_relation_endpoints` 规范化后再入集合（`DUPLICATES` 以规范形纳入）。
2. 计算转挂后的 `key` 时，先 `new_from, new_to = canonicalize_relation_endpoints(new_from, new_to, row.relation_type)` 再组键，然后比对 `occupied`。这样镜像碰撞命中 `key in occupied`，走 `merge.discard.relation_duplicate` 分支（满足 2.3）；无碰撞仍 `occupied.add(key)` 且 `moved += 1`（满足 3.4）。
3. 自环判定 `new_from == new_to` 维持在规范化之前或之后均等价（规范化不改变相等性），保持 `relation_self_loop` 行为不变（满足 3.2）。

**改动 4 —— 计划与执行一致性**

1. 计划与执行必须同一份规范化与同一套碰撞判定，确保 `plan_digest`（预览）与执行时重算指纹一致：计划把镜像碰撞计为 discard、执行同样丢弃该行，两边的 `moves["control_relations"]` 与 `discards` 一致，`plan_digest` 不发散，不触发 `control.merge_plan_changed`。
2. 三处均调用 `canonicalize_relation_endpoints`，杜绝实现漂移。

## Testing Strategy

### Validation Approach

测试分两阶段：先在**未修复**代码上制造反例、确认缺陷与根因；再验证修复在缺陷输入上正确、在非缺陷输入上行为不变。后端测试经 `make test`（Docker 内 pytest）运行，整体校验用 `make verify`。测试名、注释、docstring 用中文；提交与 PR 用英文。

### Exploratory Bug Condition Checking

**Goal**：在实现修复**之前**制造反例，佐证根因；若反例未如预期出现则需重新假设根因。

**Test Plan**：构造输家 `L`、赢家 `W` 及第三个控制点 `X`，造出镜像场景（`L` 持 `(L, X)`，`W` 持 `(X, W)`），在未修复代码上跑 `plan_merge` 与 `merge_controls`，观察计划把碰撞计成 move、执行落库镜像行。

**Test Cases**：
1. **镜像碰撞计划误判**：`plan_merge` 对镜像场景应（修复后）给 discard，未修复代码会计一次 `control_relations` move（未修复时失败/误判）。
2. **镜像行落库**：`merge_controls` 执行后查询 `control_relations`，未修复代码会出现 `(X, W)` 与 `(W, X)` 两条（未修复时失败）。
3. **非规范序落库**：转挂后 `rawFrom > rawTo` 但赢家无碰撞时，未修复代码落库端点未按 `from < to`（未修复时失败）。
4. **边界 - 自环**：转挂成自环的 `DUPLICATES`，确认仍走 `relation_self_loop`（未修复代码应已正确，用作对照）。

**Expected Counterexamples**：
- 计划把镜像碰撞计为 move 而非 `merge.discard.relation_duplicate`。
- 执行后 `control_relations` 同时存在 `(X, W)` 与 `(W, X)`。
- 可能成因：执行端点未交换、计划 `occupied` 与 `key` 未规范化。

### Fix Checking

**Goal**：验证对所有满足 `isBugCondition` 的输入，修复后的函数产出期望行为。

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  canonical := (min(rawFrom, rawTo), max(rawFrom, rawTo), DUPLICATES)
  // 计划：规范形与赢家既有碰撞 -> 判重丢弃，不计 move
  IF canonical ∈ occupied_canonical THEN
    ASSERT plan reports discard "merge.discard.relation_duplicate"
    ASSERT plan does NOT count a move for input
  END IF
  // 执行：落库规范序，无镜像
  result := merge_controls_fixed(input)
  ASSERT result.from_control_id < result.to_control_id
  ASSERT NOT exists_mirror_row(result)
END FOR
```

### Preservation Checking

**Goal**：验证对所有不满足 `isBugCondition` 的输入，修复后的函数与原函数结果相同。

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT merge_controls_original(input) = merge_controls_fixed(input)
END FOR
```

**Testing Approach**：保持性检查推荐用基于属性的测试，因为它能自动覆盖输入域内大量用例、捕捉手写单测易漏的边界、并对「非缺陷输入行为不变」给出较强保证。

**Test Plan**：先在未修复代码上观察非缺陷输入（非对称关系、已规范序 `DUPLICATES`、自环、精确重复）的行为，再写属性测试固化这些行为，确保修复后不变。

**Test Cases**：
1. **非对称关系不重排**：观察 `DEPENDS_ON` 转挂端点顺序不变，写测试固化（3.1）。
2. **自环仍丢弃**：观察转挂成自环仍走 `relation_self_loop`，固化（3.2）。
3. **精确重复仍丢弃**：观察转挂后原始元组与赢家既有精确相同时走 `relation_duplicate`，固化（3.3）。
4. **无碰撞仍 move**：观察无碰撞 `DUPLICATES` 转挂仍计 move 且规范序落库，固化（3.4）。
5. **写入路径规范化不变**：`_materialize_relation` 对 `DUPLICATES` 仍规范化落库，固化（3.5）。
6. **审计与标记不变**：合并后输家 `status = merged`、`merged_into_id` 已写、审计记录存在（3.6）。

### Unit Tests

- `canonicalize_relation_endpoints`：`DUPLICATES` 乱序交换、已序不动、非 `DUPLICATES` 任何序都不动。
- `_plan_relations`：镜像碰撞判 `relation_duplicate`；无碰撞 `DUPLICATES` 计 move；自环判 `relation_self_loop`；非对称关系不重排。
- `merge_controls`：镜像碰撞执行后无镜像行且端点规范序；无碰撞转挂端点规范序；非对称关系端点不变。
- 计划/执行一致性：同一场景下 `plan_digest` 在预览与执行重算时一致，不触发 `control.merge_plan_changed`。

### Property-Based Tests

- 随机生成含 `DUPLICATES` 与非对称关系的关系集合与赢/输家配置，断言合并后 `control_relations` 中所有 `DUPLICATES` 行满足 `from < to` 且无镜像对。
- 随机生成非缺陷输入，断言修复前后行为一致（保持性）。
- 随机场景下断言计划的 `moves`/`discards` 与执行实际落实一致（指纹不发散）。

### Integration Tests

- 端到端合并流程：造出镜像场景，预览取指纹，带指纹执行合并，断言成功且库中无镜像 `DUPLICATES` 行、审计记录完整。
- 跨场景切换：同一合并中混合自环、精确重复、镜像碰撞、无碰撞 `DUPLICATES`、非对称关系，断言各自归类正确、唯一约束不触发。
- 指纹一致性：预览与执行之间数据不变时合并成功；若执行重算与预览分类不同则以 `control.merge_plan_changed` 失败——验证计划与执行同步改动后不再发散。
