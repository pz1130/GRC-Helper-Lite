# Implementation Plan

- [x] 1. 编写 Bug Condition 复现测试（修复前必须失败）
  - **Property 1: Bug Condition** - 同批兄弟冲突 / 已确认目标导致整批回滚
  - **CRITICAL**: 本测试必须在**未修复**代码上失败——失败即确认缺陷存在；**不要**在失败时去改测试或改生产代码
  - **NOTE**: 本测试编码了期望行为（冲突条跳过、其余接受），修复后它将变为通过以验证修复
  - **GOAL**: 制造反例，证明“批内一条提案目标已确认会让整批回滚 / 抛 409 / accepted=0”
  - **Scoped PBT Approach**: 该缺陷是确定性的，把属性收窄到具体失败用例（同一 `(control, framework_item)` 的两条映射；已确认控制点对的一条关系）以保证可复现
  - 复用现有 fixtures（`backend/tests/test_review_service.py` 的 `_setup`、`_proposal`，`bulk_accept`，`ProposalKind`/`ProposalStatus`）构造 PENDING、合格（高置信度、非 OCR、非判重）的提案
  - 用例 A（同批兄弟映射冲突，1.3）：两条合格 `MAPPING` 提案指向同一 `(control, framework_item)`；按 `sorted(id)` 保证靠前者先处理
  - 用例 B（关系已确认，1.2）：控制点对已有同类型确认关系，批内一条合格 `RELATION` 提案再次指向它（源自 design「关系已确认」示例）
  - 用例 C（目标已被并发确认的映射，1.1）：批外已存在 `(control, framework_item)` 的确认映射，批内一条合格 `MAPPING` 提案指向它
  - 测试断言匹配 design 的 Expected Behavior / Property 1：`bulk_accept` 不抛 409、冲突条计跳过、其余合格条被接受、已接受兄弟不回滚（`{"accepted": N-1, "skipped": ≥1}`）
  - 在**未修复**代码上运行（Docker `make test`）
  - **EXPECTED OUTCOME**: 测试 FAIL（正确——证明缺陷存在：`bulk_accept` 抛 `Conflict`/`AppError` 或整批回滚、`accepted==0`、提案仍 PENDING）
  - 记录反例以理解根因（例如“`bulk_accept` 第二条 `MAPPING` 在 `_materialize_mapping` 抛 `mapping.already_confirmed`，穿透批次事务，第一条一并回滚，整批 0 接受”）
  - 测试写好、运行、失败已记录即可标记完成
  - _Requirements: 1.1, 1.2, 1.3, 1.4, 1.5_

- [x] 2. 编写 Preservation 测试（实现修复前，须在未修复代码上通过）
  - **Property 2: Preservation** - 非缺陷输入行为不变（含内容非法整批不落）
  - **IMPORTANT**: 遵循 observation-first 方法——先在**未修复**代码上观察并记录各非缺陷输入的实际行为，再写测试把该行为钉住，确认在未修复代码上通过
  - 内容非法整批不落（3.1）：引用现有 `test_bulk_rolls_back_prior_accepts_on_invalid_content`（`backend/tests/test_review_service.py`），**不要盲目重复**；确认它仍钉住“一条合格提案内容非法 → `bulk_accept` 抛 `AppError`、整批回滚、accepted=0、已处理兄弟不落库”
  - 全部合格无冲突全部接受（3.2）：观察并钉住“批内全部合格无冲突 → 全部接受、`accepted == 批大小`”
  - 不存在计跳过（3.3）：钉住“`proposal_id` 对应提案不存在 → 跳过”（参照现有 document_id=None 的跳过用例模式）
  - 不合格计跳过（3.4）：钉住“OCR 存疑 / 正文判重 / 未达阈值 / 非 PENDING / 类型不符 / `origin == matrix` → 跳过”（复用现有 `test_bulk_accept_skips_the_ones_below_the_threshold`、`test_bulk_accept_refuses_everything_from_an_ocr_suspect_document` 的意图）
  - 锁顺序与咨询锁（3.5）：钉住“按 `sorted(proposal_ids)` 加行锁、`lock_control_writes` 仍被调用”（可对会话打桩/监视 `lock_control_writes`）
  - bulk-ids 校验（3.6）：钉住“空 / 超过 200 / 非正整数 / 重复 ids → 以 `review.bulk_ids_invalid` 拒绝整批”
  - 事务完整性的当前基线（observation-first 说明）：完整的“兄弟 A 落库保留、B 仍 PENDING、无异常”只能在**修复后**断言；本步仅钉住未修复代码下必须保持的那些行为（上述 3.1–3.6），完整断言留到任务 4
  - 在**未修复**代码上运行（Docker `make test`）
  - **EXPECTED OUTCOME**: 这些测试全部 PASS（确认待保留的基线行为）
  - 测试写好、运行、在未修复代码上通过即可标记完成
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 3. 修复 bulk_accept：对已确认类冲突逐条跳过并继续

  - [x] 3.1 实现修复（code 允许清单 + 逐条 savepoint + 精确捕获）
    - **File**: `backend/app/review/service.py`，**Function**: `bulk_accept`
    - 声明可跳过冲突 code 允许清单常量 `_BULK_SKIP_CONFLICT_CODES = {"mapping.already_confirmed", "relation.already_confirmed", "proposal.stale_conflict"}`，并加中文注释说明其为“目标已确认 / 过期冲突”类
    - 镜像 `auto_process` 的事务结构：外层保留 `begin_nested()` + `lock_control_writes` 固定锁顺序与整批回滚语义；为每条处理（`decide` 调用）再包一层独立 `begin_nested()` savepoint，使被捕获的失败只回滚该条、不影响靠前已接受的兄弟
    - 围绕 `decide` 精确捕获：`IntegrityError`（防御性兜底）→ 计跳过并 `continue`；`AppError` 且 `exc.code in _BULK_SKIP_CONFLICT_CODES` → 计跳过并 `continue`；其余 `AppError`（内容非法 payload/校验类）→ `raise` 外抛，由外层整批回滚（守住 3.1）
    - 保持 `require_actor`、bulk-ids 校验（`review.bulk_ids_invalid`）、`sorted(proposal_ids)` 锁顺序、`locked_proposal is None` 跳过、`eligibility()` 不合格跳过原样不变
    - 返回真实计数 `{"accepted": accepted, "skipped": skipped}`，`skipped` 涵盖不存在、不合格与新增的冲突跳过
    - 正式数据仍仅经 `review/materialize.py` 写入，审计轨迹完整（3.7）
    - _Bug_Condition: isBugCondition(batch) = EXISTS p: eligible(p) AND targetAlreadyConfirmed(p)（来自 design）_
    - _Expected_Behavior: Property 1 —— 冲突条跳过、其余接受且不回滚、不抛 409、返回真实计数（来自 design）_
    - _Preservation: Preservation Requirements —— 内容非法整批不落 + 锁顺序/咨询锁/资格跳过/审计/写入边界不变（来自 design）_
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.5, 3.7_

  - [x] 3.2 验证 Bug Condition 复现测试现在通过（Fix Checking）
    - **Property 1: Expected Behavior** - 冲突条被跳过、其余被接受且不回滚
    - **IMPORTANT**: 重新运行任务 1 的**同一组**测试——**不要**另写新测试；该测试已编码期望行为，通过即确认期望行为被满足
    - 运行任务 1 的三个用例（同批兄弟映射、关系已确认、并发已确认映射）于 Docker `make test`
    - **EXPECTED OUTCOME**: 测试 PASS（确认缺陷已修复：不抛 409、冲突条跳过、其余接受、已接受兄弟保留）
    - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6_

  - [x] 3.3 验证 Preservation 测试仍然通过（无回归）
    - **Property 2: Preservation** - 非缺陷输入行为不变（含内容非法整批不落）
    - **IMPORTANT**: 重新运行任务 2 的**同一组**测试——**不要**另写新测试
    - 运行任务 2 的保留测试（含引用的 `test_bulk_rolls_back_prior_accepts_on_invalid_content`）于 Docker `make test`
    - **EXPECTED OUTCOME**: 测试 PASS（确认无回归：内容非法仍整批不落，锁顺序/咨询锁/资格跳过/bulk-ids 校验/审计均不变）
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_

- [x] 4. 补充单元 / 集成测试（事务完整性属性 + 路由端点）
  - 事务完整性属性测试（核心，2.6）：构造 `[A, B]`，A 合格且目标未确认、B 合格但与 A 指向同一目标（A 确认后 B 冲突）；按 `sorted(id)` 保证 A 先处理。断言 `{"accepted": 1, "skipped": 1}`；commit/refresh 后 A 对应 mapping/relation 确实落库且 `A.status == ACCEPTED`；`B.status` 仍为 `PENDING`（未被接受也未被错误裁定）；`bulk_accept` 未抛异常
  - 单元：`bulk_accept` 对 `mapping.already_confirmed`、`relation.already_confirmed`、`proposal.stale_conflict` 分别计跳过并继续（2.1, 2.2, 2.4）
  - 单元：允许清单常量 `_BULK_SKIP_CONFLICT_CODES` 恰好包含这三个 code（防止将来误扩大跳过边界）
  - 属性化（可选增强）：随机生成“若干合格无冲突 + 若干合格已确认目标”的批次，断言 `accepted` 等于无冲突合格数、`skipped` 覆盖冲突条、不抛 409；随机排列 ids 断言结果与顺序无关（2.5, 2.6, 3.5）
  - 集成（路由层，2.3/2.4/2.5）：经 `/api/proposals` 批量接受端点，同批兄弟冲突场景返回 **200** 且 `{"accepted": 1, "skipped": 1}`（而非 409）；审计轨迹对已接受条完整记录
  - 集成（路由层，3.1）：内容非法场景经端点仍整批失败，保留既有错误响应
  - 测试用中文命名/注释，经 Docker `make test` 运行
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.5, 3.7_

- [x] 5. Checkpoint - 确认全部测试通过
  - 在 Docker 中运行 `make test`（必要时 `make verify`），确认任务 1–4 的全部测试通过、无回归
  - 若出现问题，向用户确认后再处理
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 2.5, 2.6, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6, 3.7_
