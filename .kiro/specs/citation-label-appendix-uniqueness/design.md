# Citation Label Appendix Uniqueness Bugfix Design

## Overview

一个条款的 `citation_label` 在单个文档内可能并不唯一。当附录（或任意章节）重启编号，使其数字标签与正文条款编号相撞时，`flatten()` 会给两个不同条款写入相同的 `citation_label`（例如正文的 "1" 与附录的 "1"）。审计人员无法从标签区分所引用的是哪一个条款。

修复位于共享的扁平化步骤 `backend/app/parsing/flatten.py`，因此同时覆盖 PDF 与 Word 两条解析路径。修复采用**两趟（two-pass）确定性算法**：第一趟按现状生成所有行；第二趟检测文档级撞号，仅对相撞且标签来自**编号**的行，用其最近的未编号祖先标题加以限定，并保证最终输出无撞号、顺序稳定。非相撞行逐字保持原标签；无撞号文档产出逐字节一致的输出。

## Glossary

- **Bug_Condition (C)**: 撞号条件——文档内两个或多个条款经扁平化后得到相同的 `citation_label`，即存在被多行共享的标签（collision set）。
- **Property (P)**: 期望行为——扁平化后文档内每个条款的 `citation_label` 都唯一；相撞的编号标签用其最近未编号祖先标题限定，使其可区分。
- **Preservation**: 未撞号条款的现有标签（裸编号，或未编号条款的 `heading_path`），以及 `number`、`heading`、`heading_path`、`order_index`、树结构、出处关联等所有其他 Clause 字段必须逐字保持不变。
- **`build_citation_label(number, heading_path)`**: `flatten.py` 中的第一趟默认标签函数——有编号返回裸 `number`，否则返回 `heading_path`。
- **`flatten.walk`**: `flatten.py` 中递归遍历条款树的内部函数，逐节点追加行；已持有 `ancestors`（祖先标题字符串列表）与 `parent_key`（父行在 `rows` 中的下标）。
- **`_disambiguate_labels(rows, ancestor_info)`**: 新增的第二趟后处理函数，只对相撞行就地改写 `citation_label`；无撞号时为 no-op。
- **Nearest un-numbered ancestor（最近未编号祖先）**: 从相撞节点沿祖先链向上、第一个 `number` 为 `None` 的祖先（附录/章节标题通常未编号），其 `heading` 用于限定标签。
- **PATH_SEPARATOR**: 常量 `" › "`，`heading_path` 已用它连接，限定标签复用它以保持视觉一致。
- **order_index**: 行在扁平化输出中的稳定下标（即生成顺序），用作确定性最终兜底的一部分。

## Bug Details

### Bug Condition

缺陷发生在：文档内两个或多个**带编号**条款经 `flatten()` 扁平化后得到相同的数字 `citation_label`（例如正文条款 "1" 与某个重启编号的附录条款 "1"）。`build_citation_label` 直接返回裸 `number`，而 `flatten()` 在逐行写入 `citation_label` 时没有做任何文档级唯一性检查，于是把相同标签写给了不同条款。

**Formal Specification:**
```
FUNCTION isBugCondition(rows)
  INPUT: rows — flatten() 输出的行列表，每行含 citation_label
  OUTPUT: boolean

  labels := [row.citation_label FOR row IN rows]
  RETURN EXISTS label SUCH THAT count(label IN labels) > 1
         // 即存在被多行共享的 citation_label（撞号集非空）
END FUNCTION
```

### Examples

- 正文条款 `number="1"`（`citation_label="1"`）与附录条款 `number="1"`（附录标题 "附录 A" 未编号，`citation_label="1"`）相撞：期望两者可区分，实际都是 "1"。
- 正文 `number="3.4.2"` 在文档内唯一：期望保持 "3.4.2"，这是正确行为（不应被修复触及）。
- 未编号条款 `number=None`、`heading_path="引言"`，文档内唯一：期望保持 "引言"，这是正确行为。
- 边界：两个不同附录 "附录 A"、"附录 B" 下各有条款 "1"——限定后分别为 "附录 A › 1"、"附录 B › 1"，可区分。
- 边界（需最终兜底）：两个附录**标题相同**（都叫 "附录"）下各有条款 "1"，用最近未编号祖先限定后仍为 "附录 › 1" 撞号；或某相撞编号节点**没有任何未编号祖先**，无法用祖先标题限定。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- 数字标签本就唯一的带编号条款，`citation_label` 继续是裸编号（"3.4.2" 仍是 "3.4.2"）。
- 未编号（`number=None`）且 `heading_path` 不相撞的条款，`citation_label` 继续回退为 `heading_path`。
- `number`、`heading`、`heading_path`、`order_index`、`level`、`page_ref`、`kind`、`language`、树结构（`_parent_key`）、出处关联（`_source_keys`）等所有其他字段完全不变——只有相撞行的 `citation_label` 改变。
- 行的数量与顺序不变；输出形状（dict 键集合）不变。

**Scope:**
所有不满足撞号条件（`NOT isBugCondition`）的输入完全不受本次修复影响。具体包括：
- 不存在任何撞号的文档——产出与修复前逐字节一致的输出（后处理为 no-op）。
- 标签本就唯一的单个条款（无论其标签来自编号还是 `heading_path`）。
- 修复只改写属于撞号集、且标签来自编号的行；不碰未参与撞号的任何行。

**Note:** 期望的"正确限定行为"定义在下方 Correctness Properties 的 Property 1；本节聚焦"什么必须不变"。

## Hypothesized Root Cause

根据缺陷描述，根因明确且单一：

1. **缺少文档级唯一性检查**：`flatten()` 在 `walk` 中逐行调用 `build_citation_label(node.number, heading_path)` 并直接写入 `citation_label`，整个流程**没有任何跨行（文档级）的去重/唯一化步骤**。标签仅由单个节点的局部信息（自身编号或自身 `heading_path`）决定。

2. **裸编号在重启编号作用域下非全局唯一**：`build_citation_label` 对带编号节点返回裸 `number`。当附录或任意章节重启编号时，不同作用域会产生相同的数字（"1"、"2"…），而裸编号不携带作用域信息，必然在文档级相撞。

3. **作用域信息未被利用**：`walk` 已持有 `ancestors`（祖先标题）与 `parent_key`，足以定位"最近未编号祖先"用于限定，但第一趟并未使用这些信息去消歧。

修复方向：保留第一趟默认标签，新增第二趟文档级消歧，利用已有的祖先信息限定相撞的编号标签。

## Correctness Properties

Property 1: Bug Condition - 相撞的编号标签被限定为唯一

_For any_ 满足撞号条件（`isBugCondition(rows)` 为 true）的扁平化结果，修复后的 `flatten()` SHALL 使输出中每一行的 `citation_label` 在文档内唯一；对每个属于撞号集且标签来自**编号**的行，SHALL 用其最近未编号祖先标题按 `f"{ancestor_heading}{PATH_SEPARATOR}{number}"` 限定，若限定后仍撞号或不存在未编号祖先则应用确定性最终兜底，最终结果无任何重复标签。

**Validates: Requirements 2.1, 2.2, 2.3**

Property 2: Preservation - 非相撞行与其他字段逐字保持不变

_For any_ 不满足撞号条件（`isBugCondition(rows)` 为 false，即无撞号）的输入，修复后的 `flatten()` SHALL 产出与修复前逐字节一致的结果；且在任何输入下，不属于撞号集的行其 `citation_label` 不变，所有行的 `number`、`heading`、`heading_path`、`order_index`、树结构、出处关联及其他 Clause 字段 SHALL 保持与原始函数完全相同。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4**

## Fix Implementation

### Changes Required

假设根因分析正确，修复全部落在扁平化步骤内，`build_citation_label` 保留作第一趟默认。

**File**: `backend/app/parsing/flatten.py`

**Functions**: `flatten`（在 `walk` 中记录每行祖先信息并在收尾处调用后处理）、新增 `_disambiguate_labels`

**Specific Changes**:

1. **第一趟：在 `walk` 中记录祖先信息**：保持现有行生成逻辑不变（`citation_label` 仍由 `build_citation_label` 给出）。额外维护一个与 `rows` 下标对齐的并行列表 `ancestor_chains`，每项记录该行从直接父到根的祖先 `(number, heading)` 元组序列（顺序：最近祖先在前）。这样第二趟无需再遍历树，直接可查"最近未编号祖先"。
   - 实现方式：`walk` 新增参数携带当前节点的祖先 `(number, heading)` 链；将其追加到 `ancestor_chains`，下标与 `rows` 一致（`key = len(rows)`）。
   - 不改变 `rows` 的字段与顺序。

2. **新增 `_disambiguate_labels(rows, ancestor_chains)` 后处理**：在 `flatten` 返回前、`rows` 构建完成后调用。该函数就地改写 `citation_label`，并且**当无撞号时立即返回、不做任何修改（no-op）**。

3. **撞号检测**：对 `rows` 按 `citation_label` 分组，取出现次数 > 1 的标签对应的所有行作为撞号集。若撞号集为空，直接返回（no-op，保证 3.3 逐字节一致）。

4. **限定相撞的编号标签（第二趟核心）**：遍历撞号集中的行，仅处理标签来自编号的行（即 `row["number"]` 非 None）；对这类行用其最近未编号祖先标题限定：
   - 沿该行的 `ancestor_chains[key]` 从最近祖先向上查找第一个 `number is None` 的祖先，取其 `heading`。
   - 新标签 = `f"{ancestor_heading}{PATH_SEPARATOR}{row['number']}"`（复用 `PATH_SEPARATOR = " › "`，与 `heading_path` 一致）。
   - 对撞号集中标签来自 `heading_path` 的未编号行（理论上 `heading_path` 已含完整路径，撞号罕见），本步不改；交由最终兜底保证唯一。

5. **再检查 + 确定性最终兜底**：应用限定后，重新检测是否仍有重复标签（覆盖"两个附录同名标题"或"无未编号祖先"）。对**仍然相撞**的每一行，按以下确定性阶梯逐级升级限定，直到全局唯一：
   - (a) 用完整 `heading_path` 作为限定基（人类可读、信息最全）：对编号行用 `f"{heading_path}{PATH_SEPARATOR}{number}"`，对未编号行用 `heading_path`。
   - (b) 若 (a) 后仍撞号（极端：路径与编号都相同的重复节点），对仍相撞的行**追加 `order_index`**：`f"{label}{PATH_SEPARATOR}#{order_index}"`。`order_index` 等于行在 `rows` 中的唯一下标，保证绝对唯一且顺序稳定，作为最后一道防线。
   - 阶梯按"最人类可读、仍能唯一"的优先级选择：先最近未编号祖先，再完整路径，最后 `order_index`。`order_index` 仅在前两级都无法区分时才出现，兜底不丢失唯一性保证。

6. **确定性与顺序稳定**：后处理只读取每行自身与其祖先链的既有信息，不依赖集合/字典的迭代顺序之外的随机性；按 `rows` 的既有顺序遍历、用 `order_index` 兜底，确保同一输入反复扁平化产出完全相同的标签（满足 2.3）。`rows` 的顺序与其他字段不被改动。

### 兜底策略的理由（Justification）

- **优先最近未编号祖先**：附录/章节标题通常未编号，是人类引用时自然的作用域名（"附录 A 的条款 1"）。这是最人类可读、与需求 2.2 直接对应的限定。
- **退而用完整 `heading_path`**：当最近未编号祖先标题本身重复（两个同名 "附录"）时，完整路径携带更多上下文，仍是人类可读的锚点，且与未编号行既有的 `heading_path` 回退一致。
- **最后用 `order_index`**：`order_index` 是行的唯一、稳定下标，数学上**保证**无论任何退化输入都能得到互不相同的标签；它可读性最低，故仅作最后一道防线。这条阶梯使"输出无撞号"成为可证明的不变式，而非尽力而为。

## Testing Strategy

### Validation Approach

两阶段验证：先在**未修复**代码上用合成条款树复现撞号（产出重复 `citation_label`），再验证修复后所有标签唯一、确定、且非相撞行与其他字段逐字保持不变。解析层不触数据库，测试以**合成 ClauseNode 树**驱动 `flatten()`，断言其返回的行。测试名用中文，提交信息用英文；测试通过 Docker 内 `make test` 运行。

### Exploratory Bug Condition Checking

**Goal**: 在实现修复**之前**，用合成树surface出复现撞号的反例，确认根因（缺少文档级唯一性检查 + 裸编号跨作用域相撞）。若反例未出现则需重新假设根因。

**Test Plan**: 构造正文条款 `number="1"` 与某未编号附录标题下条款 `number="1"` 的合成 `ClauseNode` 树，调用**未修复**的 `flatten()`，断言两行的 `citation_label` 均为 "1"（即重复）。

**Test Cases**:
1. **正文与附录 "1" 撞号**：合成 正文"1" + 附录（未编号标题）下"1"；未修复代码产出两个 `citation_label="1"`（will fail on fixed code's uniqueness assertion）。
2. **无未编号祖先的撞号**：合成两个顶层编号条款都为 "1"（无任何未编号祖先）；未修复代码产出重复标签。
3. **两个同名附录各含 "1"**：合成 "附录"（未编号）两次、各含条款 "1"；未修复代码产出重复 "1"，且仅用祖先标题限定仍会得到重复 "附录 › 1"（暴露需最终兜底）。

**Expected Counterexamples**:
- 不同条款被写入相同的 `citation_label`。
- 可能根因：`build_citation_label` 返回裸编号 + `flatten()` 无文档级唯一化。

### Fix Checking

**Goal**: 验证对所有满足撞号条件的输入，修复后的 `flatten()` 产出的标签全部唯一且确定。

**Pseudocode:**
```
FOR ALL rows WHERE isBugCondition(rows_before_fix) DO
  fixed := flatten_fixed(nodes)
  labels := [r.citation_label FOR r IN fixed]
  ASSERT len(set(labels)) == len(labels)          // 全局唯一
  ASSERT flatten_fixed(nodes) == flatten_fixed(nodes)  // 确定性/顺序稳定
END FOR
```

### Preservation Checking

**Goal**: 验证对所有不满足撞号条件的输入，修复后的 `flatten()` 与原函数结果完全相同。

**Pseudocode:**
```
FOR ALL nodes WHERE NOT isBugCondition(flatten_original(nodes)) DO
  ASSERT flatten_original(nodes) == flatten_fixed(nodes)
END FOR
```

**Testing Approach**: 推荐对保全性使用基于属性的测试（PBT），因为：
- 它在输入域上自动生成大量用例；
- 它能捕获手写单测易漏的边界；
- 它为"非撞号行为不变"提供更强保证。

**Test Plan**: 先在**未修复**代码上观察无撞号文档、裸编号、未编号 `heading_path` 的输出，再写断言验证修复后这些行为逐字节不变。

**Test Cases**:
1. **无撞号文档逐字节一致**：观察无撞号树在未修复代码上的输出，断言修复后 `flatten_fixed == flatten_original`（后处理为 no-op）。
2. **唯一编号保持裸标签**：含唯一 "3.4.2" 的树，断言修复后仍为 "3.4.2"。
3. **未编号非撞号保持 heading_path**：`number=None` 且 `heading_path` 唯一，断言修复后仍为该 `heading_path`。
4. **其他字段不变**：断言修复前后 `number`/`heading`/`heading_path`/`order_index`/`level`/`page_ref`/`kind`/`language`/`_parent_key`/`_source_keys` 全部相同。

### Unit Tests

- 正文 "1" 与未编号附录下 "1" 撞号 → 修复后分别为 "1" 与 "附录 A › 1"（或相应未编号祖先标题限定），两者唯一。
- 无未编号祖先的撞号 → 走最终兜底（完整 `heading_path` 或 `order_index`），断言唯一。
- 两个同名附录各含 "1" → 祖先标题限定后仍撞，断言最终兜底使其唯一。
- 无撞号文档 → 后处理 no-op，输出与未修复一致。
- 边界：出现次数 > 2 的撞号（三个 "1"）均被唯一化。

### Property-Based Tests

- 对任意合成 `ClauseNode` 树，断言 `flatten_fixed()` 返回的所有 `citation_label` 互不相同（唯一性不变式）。
- 对任意无撞号合成树，断言 `flatten_fixed() == flatten_original()`（保全性）。
- 对任意合成树，断言两次 `flatten_fixed()` 结果完全一致（确定性/顺序稳定）。

### Integration Tests

- 全链路：合成含撞号的树经 `flatten()` 后，断言写入的每条 Clause `citation_label` 在文档内唯一，`order_index` 与树结构（`parent_id`）不变。
- 作用域切换：同一文档含多个重启编号的附录，断言各作用域下标签均可区分且稳定。
- 确定性：对同一合成文档反复扁平化，断言产出的 `citation_label` 逐次一致。
