# 批量接受跳过已确认冲突（bulk-accept-skip-conflicts）Bugfix Design

## Overview

`bulk_accept`（`backend/app/review/service.py`）在一个 `begin_nested()` 事务里逐条调用 `decide(...)`，且**没有逐条异常处理**。当批内某条合格提案的目标已被确认（映射指向的 `(control, framework_item)` 已有确认映射，或关系指向的控制点对已有同类型确认关系，或并发/同批兄弟提案刚刚占用了该目标），`decide -> materialize` 会抛出 `Conflict`。`Conflict` 是 `AppError`（不是 `IntegrityError`），`decide` 只捕获 `IntegrityError`，于是 `Conflict` 穿透整个批次事务，路由层回滚并返回 409，整批 0 接受——即使其余提案本可接受。

修复思路：在 `bulk_accept` 的循环里，围绕每条 `decide` 调用增加逐条异常处理，仅对**“目标已确认 / 过期冲突 / 底层完整性冲突”**这一类错误计为跳过并继续；其余错误（尤其是**内容非法**的 payload/校验类 `AppError`）仍然外抛，保留“内容非法就整批不落”的既有刻意行为。

关键难点有二，本设计分别给出结论：
1. **skip-vs-abort 边界**：不能照抄 `auto_process` 的 `except (AppError, IntegrityError)`——那会把内容非法也吞成跳过，违反 3.1。本设计采用**精确的 code 允许清单**（`mapping.already_confirmed`、`relation.already_confirmed`、`proposal.stale_conflict`）外加 `IntegrityError` 作为“跳过”边界，其余一律外抛中止整批。
2. **事务结构**：让 `bulk_accept` 像 `auto_process` 一样，把每条处理包进自己的 `begin_nested()` 子事务（savepoint），这样被捕获的那条干净地回滚到它自己的 savepoint，而**更靠前已接受的兄弟提案不被回滚**，会话仍可继续处理下一条。

## Glossary

- **Bug_Condition (C)**：触发缺陷的条件——批内存在至少一条合格提案，其目标在 `decide/materialize` 时已被确认（含同批更靠前的兄弟刚确认、或并发事务已确认）。
- **Property (P)**：缺陷输入下的正确行为——冲突条被计为跳过，其余合格提案被接受且不回滚，`bulk_accept` 不抛 409。
- **Preservation**：非缺陷输入下行为不变，尤其是“内容非法就整批不落”、锁顺序、资格跳过规则、审计完整性、仅由 `materialize.py` 写入正式数据。
- **F / F'**：修复前 / 修复后的 `bulk_accept`。
- **bulk_accept**：`backend/app/review/service.py` 中的批量接受函数，循环内对每条调用 `decide(..., decision=ACCEPT)`。
- **decide**：`service.py` 中的单条裁定函数，自身在 `async with session.begin_nested()` 子事务内 `materialize`，并把 `IntegrityError` 转成 `Conflict(code="proposal.stale_conflict")`。
- **savepoint**：`session.begin_nested()` 建立的嵌套事务点；块内异常只回滚到该 savepoint，不影响外层已提交到外层的工作。
- **已确认类冲突（already-confirmed conflict）**：`materialize` 判重命中后抛出的 `Conflict`——`mapping.already_confirmed`、`relation.already_confirmed`。
- **过期冲突（stale conflict）**：`decide` 捕获底层 `IntegrityError` 后抛出的 `Conflict(code="proposal.stale_conflict")`。
- **内容非法（content-invalid）**：payload/校验类 `AppError`（如 `mapping.payload_invalid`、`relation.payload_invalid`、`control.payload_invalid`、`citation.check_failed`、`decision.payload_required` 等），属于必须中止整批的那一类。

## Bug Details

### Bug Condition

缺陷在批内存在至少一条**合格**（通过 `eligibility()`）提案、而其目标在 `decide/materialize` 执行时已被确认时触发。此时 `decide -> materialize` 抛出 `Conflict`（`mapping.already_confirmed` / `relation.already_confirmed`），或 `decide` 把底层 `IntegrityError` 转成 `Conflict(proposal.stale_conflict)`。由于 `bulk_accept` 的循环没有逐条 `try/except`，该 `Conflict` 穿透外层 `begin_nested()`，路由层回滚整批、返回 409，`accepted` 为 0。

“已被确认”的来源有三种，三种都应跳过而非中止：

- 同一批里更靠前的兄弟提案刚确认了同一目标（如两条 PENDING 映射指向同一 `(control, framework_item)`）；
- 并发事务已确认了该目标，`materialize` 判重命中抛 `Conflict`；
- 判重查询与写入之间产生竞态，落库时数据库唯一约束抛 `IntegrityError`，被 `decide` 转成 `proposal.stale_conflict`。

**Formal Specification:**
```
FUNCTION isBugCondition(batch)
  INPUT: batch of type BulkAcceptRequest   // 一组合法、通过资格检查的提案编号
  OUTPUT: boolean

  RETURN EXISTS p IN batch SUCH THAT
           eligible(p)                      // 通过 eligibility()：PENDING、类型/阈值/OCR/判重均合格
           AND targetAlreadyConfirmed(p)    // decide/materialize 抛 already_confirmed / stale_conflict / IntegrityError
END FUNCTION
```

### 边界论证（skip 的类别 vs abort 的类别）

从代码确认 `Conflict` 是 `AppError` 的子类（`backend/app/errors.py`：`class Conflict(AppError): status_code = 409`），两者并不是“skip/abort”的天然分界线。因此本设计不按异常类型（`Conflict` vs `AppError`）划分，而按**精确 code 允许清单**划分，理由如下：

对 `bulk_accept` 里可达的提案种类（`eligible()` 只放行 `CONTROL_EXTRACT`(非 matrix)、`MAPPING`、`RELATION`），`materialize` 会抛的 `Conflict` 有：

- `mapping.already_confirmed`（`_materialize_mapping` 判重命中）→ **跳过**
- `relation.already_confirmed`（`_materialize_relation` 判重命中）→ **跳过**
- `control.code_conflict`（`materialize` 仅在 `if matrix:` 分支抛）→ **不可达**：`eligible()` 要求 `payload.origin != "matrix"`，matrix 提案根本进不了 `bulk_accept`。

`decide` 层可抛的 `Conflict` 有：

- `proposal.stale_conflict`（捕获 `IntegrityError` 转换而来）→ **跳过**
- `proposal.already_decided`（锁定后发现非 PENDING）→ 实际被 `bulk_accept` 的 `locked_proposal` + `eligibility()` 的 `status == PENDING` 判定提前拦下计为跳过，走不到 `decide`；即便竞态下走到，它语义上也属“已被处理”，跳过安全。

内容非法一律是**普通 `AppError`**（`*.payload_invalid`、`citation.check_failed`、`control.citation_required`、`decision.payload_required` 等），不在允许清单内 → **外抛中止整批**。

结论：采用 code 允许清单 `{mapping.already_confirmed, relation.already_confirmed, proposal.stale_conflict}` 加 `IntegrityError`（防御性兜底，正常路径下 `decide` 已把它转成 `proposal.stale_conflict`）作为“跳过”边界。相比直接 `except Conflict`，允许清单对未来新增的、语义上应中止的 `Conflict`（例如内容冲突类）更稳健，不会误吞。

### Examples

- **同批兄弟冲突（1.3 / 2.3）**：两条 PENDING 映射提案都指向 `(C-0001, item#42)`。F：第一条确认后，第二条在 `_materialize_mapping` 抛 `mapping.already_confirmed`，穿透批次事务，第一条也被回滚，整批 0 接受 / 抛错。F'：接受第一条、跳过第二条，`{"accepted": 1, "skipped": 1}`。
- **目标已被并发确认（2.1）**：批外已存在 `(C-0007, item#9)` 的确认映射，批内有一条指向它的合格映射提案。F：抛 `Conflict`、整批回滚。F'：跳过该条，其余照常接受。
- **关系已确认（2.2）**：控制点对 `(5, 8)` 已有 `duplicates` 确认关系，批内一条合格关系提案再次指向它。F：`relation.already_confirmed` 穿透、整批回滚。F'：跳过该条。
- **内容非法（3.1，不变）**：批内一条合格提案在 `materialize` 抛 `mapping.payload_invalid`。F 与 F' 一致：外抛，整批不落库。
- **全部合格无冲突（3.2，不变）**：F 与 F' 一致：全部接受。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors（必须保持不变）：**

- **内容非法整批不落**：任一合格提案抛 payload/校验类 `AppError` 时，整批回滚、整批不落库（3.1）。既有测试 `test_bulk_rolls_back_prior_accepts_on_invalid_content` 守着这条。
- **全部合格无冲突时全部接受**：返回对应 `accepted` 计数（3.2）。
- **不存在计跳过**：`proposal_id` 对应提案不存在 → 跳过（3.3）。
- **不合格计跳过**：OCR 存疑、正文判重、语义存疑、非 PENDING、类型不符、`origin == matrix`、未达阈值 → 跳过（3.4）。
- **锁顺序与咨询锁**：按排序后的 id 顺序取行锁，保留 `lock_control_writes` 事务级咨询锁（3.5）。
- **bulk-ids 校验**：空 / 超过 200 / 非正整数 / 重复 → `review.bulk_ids_invalid` 拒绝整批（3.6）。
- **审计完整 + 写入边界**：审计轨迹完整，正式数据（controls/mappings/control_relations）仅由 `review/materialize.py` 写入（3.7）。

**Scope：**

所有**不满足 `isBugCondition`** 的批次都应与修复前完全一致（`F(batch) = F'(batch)`）。本次改动只新增“对已确认类冲突/过期冲突/`IntegrityError` 的逐条跳过”，不改动资格判定、锁定、审计与写入路径。

> 注：缺陷输入下的正确行为（跳过冲突条、接受其余条）定义在下方 Correctness Properties 的 Property 1。

## Hypothesized Root Cause

从代码确认，根因由两个事实叠加而成：

1. **缺少逐条异常处理**：`bulk_accept` 把整个循环放进单个 `async with session.begin_nested():`，循环体直接 `await decide(...)`，没有 `try/except`。任何从 `decide` 逃出的异常都会穿透外层 `begin_nested()` 并由路由层回滚整批。

2. **`Conflict` 不是 `IntegrityError`**：`decide` 内层只 `except IntegrityError`，把它转成 `Conflict(proposal.stale_conflict)`；而判重命中的 `Conflict`（`mapping.already_confirmed` / `relation.already_confirmed`）本就不是 `IntegrityError`，`decide` 不会捕获它们，直接外抛。

此外还有一个**资格检查的盲区**：`eligibility()/eligible()` 不检查“目标是否已被确认”，因此一条目标已存在的 PENDING 提案能通过资格检查、进入 `decide`，到 `materialize` 才撞上判重。本设计**不**在资格检查里加“目标是否已确认”的预判（那会与同批兄弟的时序、并发事务竞态，无法可靠预判，且重复了 `materialize` 的判重职责），而是把它当作运行期跳过信号处理——与 `auto_process` 的既有做法一致。

对照 `auto_process`（已被证明可用的同类模式）：它把每条 `decide/materialize` 等价逻辑包在 `async with session.begin_nested(): ... except (AppError, IntegrityError): skipped += 1` 里，一条冲突只跳过该条。`bulk_accept` 缺的正是“逐条 savepoint + 逐条捕获”这层结构——但捕获范围必须比 `auto_process` 更窄（只跳过冲突类），以守住 3.1。

### 事务结构确认（savepoint 交互）

- `decide` 自身在 `async with session.begin_nested()` 内执行 `materialize`。当 `materialize` 抛 `Conflict`，SQLAlchemy 会回滚到**该内层 savepoint**，撤销这条提案的部分写入，然后异常继续上抛。
- 若 `bulk_accept` 为每条处理再包一层自己的 savepoint（`async with session.begin_nested()` 围住 `decide` 调用），当 `decide` 抛出被捕获时，外层这层 savepoint 回滚，**只撤销本条**；更靠前已完成的兄弟提案位于各自已退出的 savepoint 中，保持不变；会话状态干净，可继续下一条。
- 这正是 `auto_process` 依赖的机制：每条 `begin_nested()` + `except` → 单条回滚、整体继续。因此推荐 `bulk_accept` **镜像 `auto_process` 的结构**：每条一个 savepoint，`except` 仅对允许清单计跳过。

## Correctness Properties

Property 1: Bug Condition - 冲突条被跳过、其余被接受且不回滚

_For any_ 满足 `isBugCondition` 的批次（存在合格提案其目标已被确认），修复后的 `bulk_accept` SHALL 把每条“目标已确认 / 过期冲突 / 底层 `IntegrityError`”（code 属于 `{mapping.already_confirmed, relation.already_confirmed, proposal.stale_conflict}` 或 `IntegrityError`）计为跳过并继续，接受其余所有合格且无冲突的提案，这些被接受提案的落库 SHALL NOT 因兄弟提案的冲突而回滚，且 `bulk_accept` SHALL NOT 抛出 409；返回的 `accepted` 等于批内“合格且目标未被确认”的条数，`skipped` 至少包含所有“合格但目标已被确认”的条数。

**Validates: Requirements 2.1, 2.2, 2.3, 2.4, 2.5, 2.6**

Property 2: Preservation - 非缺陷输入行为不变（含内容非法整批不落）

_For any_ 不满足 `isBugCondition` 的批次，修复后的 `bulk_accept` SHALL 产生与修复前完全相同的结果（`F(batch) = F'(batch)`）。特别地：任一合格提案抛内容非法类 `AppError`（payload/校验类，code 不在允许清单内）时 SHALL 让整批回滚、整批不落库；全部合格无冲突时 SHALL 接受全部；不存在 / 不合格的提案 SHALL 计为跳过；排序 id 锁顺序与 `lock_control_writes` 咨询锁 SHALL 保持；非法 ids 列表 SHALL 以 `review.bulk_ids_invalid` 拒绝整批；审计轨迹 SHALL 完整且正式数据仅由 `materialize.py` 写入。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7**

## Fix Implementation

### Changes Required

假设根因分析正确，改动集中在一处函数。

**File**: `backend/app/review/service.py`

**Function**: `bulk_accept`

**Specific Changes**:

1. **定义跳过 code 允许清单**：在模块内（或函数内）声明可跳过的冲突 code 常量集合：
   - `_BULK_SKIP_CONFLICT_CODES = {"mapping.already_confirmed", "relation.already_confirmed", "proposal.stale_conflict"}`
   - 配中文注释说明：这些是“目标已确认 / 过期冲突”类，按跳过处理；其余 `AppError`（内容非法）必须外抛以保留整批不落。

2. **为每条处理包裹独立 savepoint**：镜像 `auto_process`，把单条的“锁定 + 资格判定 + decide”放进 `async with session.begin_nested():`，使被捕获的失败只回滚该条。外层仍保留一个 `begin_nested()` + `lock_control_writes` 以固定锁顺序、承接整批回滚语义。

3. **精确捕获并计跳过**：围绕 `decide` 调用捕获异常，仅当满足以下之一时计 `skipped += 1` 并 `continue`：
   - 异常是 `AppError` 且 `exc.code in _BULK_SKIP_CONFLICT_CODES`；
   - 异常是 `IntegrityError`（防御性兜底）。
   其余异常（含内容非法的 `AppError`）**重新抛出**，由外层回滚整批——保留 3.1。

4. **保持既有跳过与校验不变**：`require_actor`、bulk-ids 校验（`review.bulk_ids_invalid`）、`sorted(proposal_ids)` 锁顺序、`locked_proposal` 为 `None` 跳过、`eligibility()` 不合格跳过，均原样保留。

5. **返回真实计数**：继续返回 `{"accepted": accepted, "skipped": skipped}`，其中 `skipped` 涵盖不存在、不合格、以及新增的冲突跳过（2.5）。

**结构示意（伪代码，非最终代码）：**
```
_BULK_SKIP_CONFLICT_CODES = {
    "mapping.already_confirmed",
    "relation.already_confirmed",
    "proposal.stale_conflict",
}

async with session.begin_nested():              # 外层：固定锁顺序 + 整批回滚语义
    await lock_control_writes(session)
    for proposal_id in sorted(proposal_ids):
        proposal = await locked_proposal(session, proposal_id)
        if proposal is None:
            skipped += 1; continue
        acceptable, _ = await eligibility(session, proposal, limits)
        if not acceptable:
            skipped += 1; continue
        try:
            async with session.begin_nested():   # 每条一个 savepoint
                await decide(session, proposal_id, actor=actor, decision=Decision.ACCEPT)
            accepted += 1
        except IntegrityError:
            skipped += 1
        except AppError as exc:
            if exc.code in _BULK_SKIP_CONFLICT_CODES:
                skipped += 1
            else:
                raise                            # 内容非法 → 外抛，整批回滚
```

> 注意：`decide` 内部已自带 `begin_nested()`。`bulk_accept` 再包一层外部 savepoint，使捕获到异常时回滚边界落在 `bulk_accept` 这一层、恰好只撤销当前这条，兄弟提案不受影响。实现时需确认嵌套 savepoint 下 `decide` 的 `IntegrityError -> Conflict` 转换仍生效（正常路径下冲突会以 `proposal.stale_conflict` 到达 `except AppError`）。

## Testing Strategy

### Validation Approach

两阶段：先在**未修复**代码上复现缺陷（整批回滚 / 抛 409 / 0 接受），确认根因；再在**修复后**验证冲突条被跳过、其余被接受，并用保留测试守住“内容非法整批不落”等既有行为。测试用中文命名，经 Docker 中 `make test` 运行。

### Exploratory Bug Condition Checking

**Goal**：在实现修复前，先写出能在未修复代码上失败的测试，复现“同批兄弟冲突导致整批回滚”，确认或推翻根因。若推翻则需重新假设。

**Test Plan**：构造两条 PENDING、均合格（高置信度、非 OCR、非判重）的映射提案，指向同一 `(control, framework_item)`。在未修复代码上调用 `bulk_accept`，断言它抛错 / 整批回滚、accepted 为 0、两条仍为 PENDING。再对关系提案构造已确认关系的情形复现 `relation.already_confirmed`。

**Test Cases**：
1. **同批兄弟映射冲突**：两条指向同一 `(control, framework_item)` 的合格映射提案（未修复代码上会整批失败）。
2. **关系已确认**：控制点对已有同类型确认关系，批内一条合格关系提案指向它（未修复代码上会整批失败）。
3. **目标已被并发确认的映射**：批外已有确认映射，批内一条合格映射提案指向它（未修复代码上会整批失败）。

**Expected Counterexamples**：
- `bulk_accept` 抛 `Conflict`（`mapping.already_confirmed` / `relation.already_confirmed`）或整批回滚、accepted 为 0。
- 可能原因：循环缺逐条异常处理；`Conflict` 非 `IntegrityError` 不被 `decide` 捕获。

### Fix Checking

**Goal**：验证对所有满足 bug condition 的输入，修复后的 `bulk_accept` 产生正确行为（冲突条跳过、其余接受、不回滚、不抛 409）。

**Pseudocode:**
```
FOR ALL batch WHERE isBugCondition(batch) DO
  result := bulk_accept_fixed(batch)
  ASSERT NOT raised_409(result)
  ASSERT no_batch_rollback(result)
  ASSERT result.accepted = count(p IN batch WHERE eligible(p) AND NOT targetAlreadyConfirmed(p))
  ASSERT result.skipped  >= count(p IN batch WHERE eligible(p) AND targetAlreadyConfirmed(p))
END FOR
```

### Preservation Checking

**Goal**：验证对所有不满足 bug condition 的输入，修复后与修复前结果一致。

**Pseudocode:**
```
FOR ALL batch WHERE NOT isBugCondition(batch) DO
  ASSERT bulk_accept_original(batch) = bulk_accept_fixed(batch)
END FOR
// 特别地：内容非法必须整批中止
FOR ALL batch WHERE EXISTS p IN batch SUCH THAT eligible(p) AND contentInvalid(p) DO
  ASSERT bulk_accept_fixed(batch) aborts_whole_batch
END FOR
```

**Testing Approach**：属性化测试适合保留性检查，因为它能在输入域上自动生成大量用例、覆盖手写用例遗漏的边界、并对“非冲突输入行为不变”给出较强保证。先在未修复代码上观察非冲突输入的行为，再写测试把该行为钉住。

**Test Plan**：先观察未修复代码在“内容非法 / 全部合格 / 不存在 / 不合格”各情形下的行为，再写测试断言修复后保持一致。

**Test Cases**：
1. **内容非法整批不落**：观察并钉住“一条合格提案内容非法 → 整批回滚、accepted 0、已处理兄弟不落库”（复用既有 `test_bulk_rolls_back_prior_accepts_on_invalid_content` 的意图）。
2. **全部合格无冲突全部接受**：钉住“批内全部合格无冲突 → 全部接受、accepted = 批大小”。
3. **不存在 / 不合格计跳过**：钉住“不存在编号、OCR 存疑 / 判重 / 未达阈值 / 非 PENDING / matrix origin → 跳过”。
4. **锁顺序与咨询锁**：钉住“按排序 id 加锁、`lock_control_writes` 仍被调用”。

### 事务完整性属性测试（核心）

**Goal**：验证“一条后置提案冲突时，已接受的兄弟不被回滚”。

- **用例**：构造 `[A, B]`，A 合格且目标未确认、B 合格但目标已被确认（或与 A 指向同一目标，使 A 确认后 B 冲突）。按 `sorted(id)` 保证 A 先处理。
- **断言**：`{"accepted": 1, "skipped": 1}`；提交 / 刷新后 A 对应的 mapping/relation 确实落库、A.status == ACCEPTED；B.status 仍为 PENDING（未被接受、也未被错误裁定）；`bulk_accept` 未抛异常。

### Unit Tests

- `bulk_accept` 对 `mapping.already_confirmed`、`relation.already_confirmed`、`proposal.stale_conflict` 分别计跳过并继续。
- `bulk_accept` 对内容非法 `AppError`（如 `mapping.payload_invalid`）外抛、整批回滚。
- 边界：不存在编号、不合格提案、空 / 超限 / 重复 / 非正整数 ids（`review.bulk_ids_invalid`）。
- 允许清单常量包含且仅包含三个预期 code。

### Property-Based Tests

- 随机生成含“若干合格无冲突 + 若干合格已确认目标”的批次，断言 `accepted` 等于无冲突合格数、`skipped` 覆盖冲突条，且不抛 409。
- 随机生成含至少一条内容非法提案的批次，断言整批回滚、无任何落库。
- 随机排列 ids，断言结果与排序无关（锁顺序固定、计数稳定）。

### Integration Tests

- 经路由层 `/api/proposals` 的批量接受端点：同批兄弟冲突场景返回 200 且 `{"accepted": 1, "skipped": 1}`（而非 409）。
- 并发/已确认目标场景下，端点返回真实计数，审计轨迹对已接受条完整记录。
- 内容非法场景经路由层仍整批失败（保留既有响应）。
