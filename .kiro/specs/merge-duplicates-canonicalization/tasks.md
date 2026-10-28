# Implementation Plan

- [x] 1. 写缺陷条件探索测试（复现镜像缺陷，修复前先跑）
  - **Property 1: Bug Condition** - 合并对称关系规范化并去镜像
  - **CRITICAL**: 此测试在未修复代码上必须 FAIL —— 失败正是对缺陷存在的佐证
  - **DO NOT attempt to fix the test or the code when it fails** —— 本阶段只复现与记录，不改实现
  - **NOTE**: 此测试编码了期望行为，修复后它应转为 PASS，用来验证 Fix Checking
  - **GOAL**: 制造反例，证明 `_plan_relations`（`controls/merge.py`）与 `merge_controls`（`review/materialize.py`）缺少写入路径已有的 `DUPLICATES` 规范化
  - **Scoped PBT Approach**: 缺陷是确定性的，先把属性收敛到具体可复现的失败场景，再可选地扩展为属性测试
    - 构造输家 `L`、赢家 `W`、第三方控制点 `X`，造出镜像场景：`L` 持 `DUPLICATES (L, X)`（`L < X`），`W` 已持 `DUPLICATES (X, W)`（`X < W`），满足 `isBugCondition`（转挂后 `rawFrom=W > rawTo=X`）
  - 对应 Bug Condition（design「Bug Condition / Formal Specification」）：`relation_type = DUPLICATES AND rawFrom ≠ rawTo AND rawFrom > rawTo`
  - 测试断言应匹配 Expected Behavior Properties（design Property 1）：计划判 `merge.discard.relation_duplicate` 不计 move；执行后落库 `from_control_id < to_control_id` 且无镜像行
  - 用例（在未修复代码上运行）：
    - **镜像碰撞计划误判**：`plan_merge` 对镜像场景——未修复代码会对 `control_relations` 计一次 move（期望为 discard，故此断言 FAIL）
    - **镜像行落库**：`merge_controls` 执行后查询 `control_relations`，未修复代码同时出现 `(X, W)` 与 `(W, X)` 两条镜像行（期望仅一条，故此断言 FAIL）
    - **非规范序落库**：造出转挂后 `rawFrom > rawTo` 但赢家无碰撞的 `DUPLICATES`，未修复代码落库端点未满足 `from < to`（此断言 FAIL）
  - 经 `make test`（Docker 内 pytest）运行；测试名/注释/docstring 用中文
  - **EXPECTED OUTCOME**: 测试 FAILS（正确——证明缺陷存在）
  - 记录反例以理解根因（例如「`plan_merge` 把 `(W, X, DUPLICATES)` 比对 `occupied={(X, W, DUPLICATES)}` 未命中，计为 move」「执行后库中并存 `(X, W)` 与 `(W, X)`」）
  - 测试写好、运行、失败记录完成后，标记本任务完成
  - _Requirements: 1.1, 1.2, 1.3_

- [x] 2. 写保持性属性测试（修复前先写，观察优先）
  - **Property 2: Preservation** - 非缺陷转挂行为完全一致
  - **IMPORTANT**: 遵循观察优先（observation-first）方法——先在未修复代码上观察非缺陷输入的真实行为，再写属性测试固化
  - 覆盖 `isBugCondition` 返回 false 的输入：非对称关系、已是规范序的 `DUPLICATES`、自环、精确重复、写入路径、审计/标记
  - 观察并固化（来自 design「Preservation Requirements」）：
    - **非对称关系不重排**：观察 `DEPENDS_ON (L, Y)` 转挂为 `(W, Y)`，端点顺序不变 —— 固化（3.1）
    - **自环仍丢弃**：观察转挂成自环（两端都落到赢家）仍走 `merge.discard.relation_self_loop` —— 固化（3.2）
    - **精确重复仍丢弃**：观察转挂后原始元组与赢家既有精确相同时走 `merge.discard.relation_duplicate` —— 固化（3.3）
    - **无碰撞仍 move**：观察无碰撞 `DUPLICATES` 转挂仍计 move，且落库 `from < to` —— 固化（3.4）
    - **写入路径规范化不变**：观察 `_materialize_relation` 对 `DUPLICATES` 仍规范化落库 —— 固化（3.5）
    - **审计与标记不变**：观察合并后输家 `status = merged`、`merged_into_id` 已写、审计记录存在 —— 固化（3.6）
  - 推荐用基于属性的测试：随机生成非缺陷输入（非对称关系、已规范序 `DUPLICATES`、自环、精确重复及赢/输家配置），断言修复前后行为一致，自动覆盖输入域、捕捉手写单测易漏的边界
  - 经 `make test`（Docker 内 pytest）运行；测试名/注释/docstring 用中文
  - **EXPECTED OUTCOME**: 测试 PASS（确认这是需要保持的基线行为）
  - 测试写好、运行、在未修复代码上通过后，标记本任务完成
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 3. 修复：合并路径对称关系规范化并去镜像

  - [x] 3.1 新增共享函数 `canonicalize_relation_endpoints`
    - 置于不引入循环导入的位置（建议 `backend/app/controls/models.py`，与 `RelationType` 同模块；或 `backend/app/controls/merge.py` 顶层），供写入、计划、执行三处共用
    - 语义：`IF relation_type = DUPLICATES AND from_id > to_id THEN RETURN (to_id, from_id) ELSE RETURN (from_id, to_id)`
    - _Bug_Condition: isBugCondition(input) —— DUPLICATES 且转挂后 rawFrom > rawTo（design「Bug Condition」）_
    - _Expected_Behavior: canonicalize_relation_endpoints 返回规范序 (min, max, type)，非 DUPLICATES 原样返回（design「Fix Implementation / 新增共享函数」）_
    - _Requirements: 3.5_

  - [x] 3.2 改动 1 —— 写入路径收敛到共享函数（behavior-preserving）
    - File：`backend/app/review/materialize.py`，Function：`_materialize_relation`（约第 133 行）
    - 用 `start, end = canonicalize_relation_endpoints(start, end, validated.relation_type)` 替换现有内联的 `if validated.relation_type is RelationType.DUPLICATES and start > end: start, end = end, start`
    - 行为等价，仅收敛为单一实现，消除规则重复实现的漂移风险
    - _Bug_Condition: isBugCondition(input) from design_
    - _Preservation: 写入路径既有规范化逻辑不变（design「Preservation Requirements」）_
    - _Requirements: 3.5_

  - [x] 3.3 改动 2 —— 合并执行路径规范化并处理镜像碰撞
    - File：`backend/app/review/materialize.py`，Function：`merge_controls`（关系转挂循环，约第 704–715 行）
    - 在重写端点前，先读出赢家侧既有 `control_relations` 关系键集合（排除输家端点行），用于镜像碰撞判定
    - 把 `from_control_id` / `to_control_id` 重写到 `winner.id` 后，对每行调用 `canonicalize_relation_endpoints(row.from_control_id, row.to_control_id, row.relation_type)` 并写回端点（`DUPLICATES` 交换，其它不变）
    - **镜像碰撞处理**：若规范化后该行与赢家某条既有行 `(from, to, relation_type)` 相同，则删除（丢弃）该行而非写入 —— 与前段「先删 `plan.discards`、`flush`、再转挂」的删除方式一致，不引入 `plan.discards` 之外的新删除语义
    - 顺序注意：规范化与碰撞判定在转挂循环内完成，避免先插入镜像行再触发唯一约束
    - 保留输家 `status = merged`、`merged_into_id`、审计轨迹不变
    - _Bug_Condition: isBugCondition(input) from design_
    - _Expected_Behavior: 落库 from_control_id < to_control_id；镜像碰撞丢弃不落库，合并后无镜像行（design Property 1）_
    - _Preservation: 自环/精确重复丢弃不变；审计与 merged 标记不变（design「Preservation Requirements」）_
    - _Requirements: 2.1, 2.2, 3.2, 3.3, 3.6_

  - [x] 3.4 改动 3 —— 合并计划路径规范化 occupied 与转挂 key
    - File：`backend/app/controls/merge.py`，Function：`_plan_relations`（约第 124–166 行）
    - 构建 `occupied` 时，对每条赢家侧关系先用 `canonicalize_relation_endpoints` 规范化后再入集合（`DUPLICATES` 以规范形纳入）
    - 计算转挂后 `key` 时，先 `new_from, new_to = canonicalize_relation_endpoints(new_from, new_to, row.relation_type)` 再组键，然后比对 `occupied`：镜像碰撞命中 `key in occupied` 走 `merge.discard.relation_duplicate`（2.3）；无碰撞仍 `occupied.add(key)` 且 `moved += 1`（3.4）
    - 自环判定 `new_from == new_to` 维持不变（规范化不改变相等性），保持 `relation_self_loop` 行为（3.2）
    - _Bug_Condition: isBugCondition(input) from design_
    - _Expected_Behavior: 镜像碰撞在计划中报 merge.discard.relation_duplicate 而非计 move（design Property 1）_
    - _Preservation: 无碰撞仍计 move；自环仍 relation_self_loop（design「Preservation Requirements」）_
    - _Requirements: 2.3, 3.2, 3.4_

  - [x] 3.5 改动 4 —— 计划与执行一致性（plan_digest 不发散）
    - 确保计划与执行使用同一份 `canonicalize_relation_endpoints` 与同一套碰撞判定：计划把镜像碰撞计为 discard、执行同样丢弃该行，两边 `moves["control_relations"]` 与 `discards` 一致
    - 结果：预览 `plan_digest` 与执行时重算指纹一致，不触发 `control.merge_plan_changed`
    - 三处（写入/计划/执行）均调用共享函数，杜绝实现漂移
    - _Bug_Condition: isBugCondition(input) from design_
    - _Expected_Behavior: plan_digest 与 expected_digest 一致，合并不以 control.merge_plan_changed 失败（design「改动 4 —— 计划与执行一致性」）_
    - _Requirements: 2.2, 2.3_

  - [x] 3.6 验证缺陷条件探索测试现在通过（Fix Checking）
    - **Property 1: Expected Behavior** - 合并对称关系规范化并去镜像
    - **IMPORTANT**: 重新运行任务 1 的同一组测试 —— 不要新写测试
    - 任务 1 的测试编码了期望行为；它通过即确认 Fix Checking：对所有满足 `isBugCondition` 的输入，计划判 `relation_duplicate` 不计 move、执行落库 `from < to` 且无镜像行
    - 经 `make test`（Docker 内 pytest）运行
    - **EXPECTED OUTCOME**: 测试 PASS（确认缺陷已修复）
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.7 验证保持性测试仍然通过（Preservation Checking）
    - **Property 2: Preservation** - 非缺陷转挂行为完全一致
    - **IMPORTANT**: 重新运行任务 2 的同一组测试 —— 不要新写测试
    - 对所有不满足 `isBugCondition` 的输入断言修复前后行为一致（无回归）
    - 经 `make test`（Docker 内 pytest）运行
    - **EXPECTED OUTCOME**: 测试 PASS（确认无回归）
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 4. 补充单元测试、集成测试与指纹一致性测试
  - 单元测试（`make test`，中文测试名/注释）：
    - `canonicalize_relation_endpoints`：`DUPLICATES` 乱序交换、已序不动、非 `DUPLICATES` 任何序都不动
    - `_plan_relations`：镜像碰撞判 `relation_duplicate`；无碰撞 `DUPLICATES` 计 move；自环判 `relation_self_loop`；非对称关系不重排
    - `merge_controls`：镜像碰撞执行后无镜像行且端点规范序；无碰撞转挂端点规范序；非对称关系端点不变
  - 属性测试：随机生成含 `DUPLICATES` 与非对称关系的关系集合与赢/输家配置，断言合并后所有 `DUPLICATES` 行满足 `from < to` 且无镜像对；随机非缺陷输入断言修复前后行为一致
  - 集成测试（端到端）：造出镜像场景，预览取指纹，带指纹执行合并，断言成功、库中无镜像 `DUPLICATES` 行、审计记录完整；跨场景混合自环/精确重复/镜像碰撞/无碰撞 `DUPLICATES`/非对称关系，断言各自归类正确、唯一约束不触发
  - 计划/执行指纹一致性：同一场景下 `plan_digest` 在预览与执行重算时一致，不触发 `control.merge_plan_changed`
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_

- [x] 5. Checkpoint - 确保所有测试通过
  - 运行 `make test`（Docker 内 pytest），整体校验运行 `make verify`
  - 确认 Fix Checking、Preservation Checking、单元/集成/指纹一致性测试全部通过
  - 提交与 PR 用英文；若有疑问先询问用户
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4, 3.5, 3.6_
