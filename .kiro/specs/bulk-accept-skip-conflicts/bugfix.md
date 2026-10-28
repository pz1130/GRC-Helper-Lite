# Bugfix Requirements Document

## Introduction

批量接受（`bulk_accept`，`backend/app/review/service.py`）在一批提案里只要有**一条**提案的目标已被确认，就会让整批回滚、返回 409 且接受数为 0，而不是接受其余可接受的提案、只跳过那一条冲突的提案。

“目标已被确认”指映射提案指向的 `(control, framework_item)` 组合、或关系提案指向的控制点对已经存在确认记录——可能是同一批里更靠前的兄弟提案刚刚确认的，也可能是并发事务确认的。

根因是 `bulk_accept` 在单个 `begin_nested()` 事务里逐条调用 `decide(...)`，且**没有逐条异常处理**。资格检查 `eligibility()/eligible()` 不检查目标是否已被确认，因此一条目标已存在的 PENDING 提案能通过资格检查，随后 `decide -> materialize` 抛出 `Conflict`（`mapping.already_confirmed` 或 `relation.already_confirmed`）。`Conflict` 是 `AppError` 而非 `IntegrityError`，`decide` 只捕获 `IntegrityError`，于是 `Conflict` 穿透 `bulk_accept` 的嵌套事务，路由层回滚并重新抛出，整批失败。

同模块的 `auto_process` 做法正确：它把每条的 `decide/materialize` 包在 `try/except (AppError, IntegrityError): skipped += 1` 里，一条冲突只跳过该条、其余照常处理。

本次修复让 `bulk_accept` 对“目标已确认/冲突”这一类错误按跳过处理并继续，同时**保留**“内容非法则整批不落”的既有刻意行为。

## Bug Analysis

### Current Behavior (Defect)

当前批量接受里，只要有一条提案的目标已被确认，整批就会失败。

1.1 WHEN 一次批量接受里至少有一条映射提案指向的 `(control, framework_item)` 组合已被确认 THEN `bulk_accept` 让整个嵌套事务回滚、整批不落库
1.2 WHEN 一次批量接受里至少有一条关系提案指向的控制点对已有同类型的确认关系 THEN `bulk_accept` 让整个嵌套事务回滚、整批不落库
1.3 WHEN 同一批里有两条 PENDING 映射提案指向同一 `(control, framework_item)`（第一条被接受并确认该映射，第二条随后在 materialize 抛出 `mapping.already_confirmed`）THEN 第二条的 `Conflict` 穿透批次事务，第一条的接受一并回滚，整批不落库
1.4 WHEN 某条提案在 `decide/materialize` 抛出已确认类冲突（`mapping.already_confirmed`、`relation.already_confirmed`）THEN 该错误穿透 `bulk_accept` 的 `begin_nested()`，路由层返回 409 且 accepted 为 0
1.5 WHEN 本应被接受的其余合格提案与一条冲突提案处于同一批 THEN 这些本可接受的提案因整批回滚而全部未被接受

### Expected Behavior (Correct)

目标已确认的单条提案应计为跳过，其余合格提案继续接受。

2.1 WHEN 一次批量接受里某条映射提案指向的 `(control, framework_item)` 组合已被确认 THEN `bulk_accept` SHALL 将该条计为跳过并继续处理其余提案
2.2 WHEN 一次批量接受里某条关系提案指向的控制点对已有同类型的确认关系 THEN `bulk_accept` SHALL 将该条计为跳过并继续处理其余提案
2.3 WHEN 同一批里有两条 PENDING 映射提案指向同一 `(control, framework_item)` THEN `bulk_accept` SHALL 接受第一条、把第二条计为跳过，而不回滚第一条
2.4 WHEN 某条提案在 `decide/materialize` 抛出已确认类冲突或过期冲突（`mapping.already_confirmed`、`relation.already_confirmed`、`proposal.stale_conflict`，或底层 `IntegrityError`）THEN `bulk_accept` SHALL 仅跳过该条、不中断整批，并保证已接受提案的落库不被这条冲突回滚
2.5 WHEN 一批提案处理结束 THEN `bulk_accept` SHALL 返回真实的 accepted 与 skipped 计数，反映实际接受的提案与被跳过（含冲突、不合格、不存在）的提案
2.6 WHEN 本应被接受的其余合格提案与一条冲突提案处于同一批 THEN `bulk_accept` SHALL 接受这些合格提案，其接受结果不因兄弟提案的冲突而回滚

### Unchanged Behavior (Regression Prevention)

内容非法仍须整批不落；既有的锁顺序、跳过规则与审计必须保持不变。

3.1 WHEN 某条提案因内容非法在 `decide/validate_decision/materialize` 抛出 payload/校验类 `AppError`（如 `mapping.payload_invalid`、`relation.payload_invalid`、`decision.payload_required` 等）THEN `bulk_accept` SHALL CONTINUE TO 让整批回滚、整批不落库（保留“内容非法就整批不落”的既有刻意行为）
3.2 WHEN 批内所有提案均合格且无冲突 THEN `bulk_accept` SHALL CONTINUE TO 接受全部提案并返回对应的 accepted 计数
3.3 WHEN 某个提案编号对应的提案不存在 THEN `bulk_accept` SHALL CONTINUE TO 将其计为跳过
3.4 WHEN 某条提案未通过资格检查（OCR 存疑、正文判重、语义存疑、非 PENDING、类型不符、origin 为 matrix、未达阈值等）THEN `bulk_accept` SHALL CONTINUE TO 将其计为跳过
3.5 WHEN 批量接受获取行锁 THEN `bulk_accept` SHALL CONTINUE TO 按排序后的 id 顺序加锁，并保留事务级咨询锁（`lock_control_writes`），以避免交叠批次间死锁
3.6 WHEN 提案编号列表非法（空、超过 200 个、含非正整数、含重复）THEN `bulk_accept` SHALL CONTINUE TO 以 `review.bulk_ids_invalid` 拒绝整批
3.7 WHEN 任一提案被接受或跳过 THEN 系统 SHALL CONTINUE TO 保持审计轨迹完整，且仅由 `review/materialize.py` 写入 controls/mappings/control_relations

### Deriving the Bug Condition

**Bug Condition Function** — 识别触发该缺陷的输入：

```pascal
FUNCTION isBugCondition(batch)
  INPUT: batch of type BulkAcceptRequest  // 一组合法的、通过资格检查的提案
  OUTPUT: boolean

  // 批内存在至少一条合格提案，其目标在 decide/materialize 时已被确认
  // （含同批更靠前的兄弟提案刚确认、或并发事务已确认的情况）
  RETURN EXISTS p IN batch SUCH THAT
           eligible(p) AND targetAlreadyConfirmed(p)
END FUNCTION
```

**Property Specification** — 定义缺陷输入下的正确行为：

```pascal
// Property: Fix Checking —— 冲突条被跳过，其余被接受
FOR ALL batch WHERE isBugCondition(batch) DO
  result ← bulk_accept'(batch)
  ASSERT no_batch_rollback(result)
  ASSERT result.accepted = count(p IN batch WHERE eligible(p) AND NOT targetAlreadyConfirmed(p))
  ASSERT result.skipped  ≥ count(p IN batch WHERE eligible(p) AND targetAlreadyConfirmed(p))
  ASSERT NOT raised_409(result)
END FOR
```

- **F**：修复前的 `bulk_accept`——一条已确认目标的提案会让整批回滚、返回 409。
- **F'**：修复后的 `bulk_accept`——把已确认类冲突/过期冲突/`IntegrityError` 计为跳过并继续，内容非法仍整批不落。

**Preservation Goal** — 对非缺陷输入保持行为不变：

```pascal
// Property: Preservation Checking
FOR ALL batch WHERE NOT isBugCondition(batch) DO
  ASSERT F(batch) = F'(batch)
END FOR

// 特别地，内容非法的整批中止行为必须保持：
FOR ALL batch WHERE EXISTS p IN batch SUCH THAT eligible(p) AND contentInvalid(p) DO
  ASSERT bulk_accept'(batch) aborts_whole_batch
END FOR
```
