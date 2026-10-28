# Implementation Plan

## Overview

本计划按"复现优先"的探索式缺陷修复流程执行，修复全部落在 `backend/app/parsing/flatten.py`：
先写撞号探索测试（修复前 FAIL，证明缺陷），再写保全性测试（修复前 PASS，锁定基线），
然后以两趟确定性算法实现修复（`walk` 记录 `ancestor_chains` + 新增 `_disambiguate_labels`），
最后补充单元/属性测试并通过 Docker 内 `make test` 做 checkpoint 回归。

## Tasks

- [x] 1. 编写撞号复现探索测试（在修复前必须 FAIL）
  - **Property 1: Bug Condition** - 相撞的编号标签被限定为唯一
  - **CRITICAL**: 本测试在未修复代码上 **必须 FAIL**——失败即证明缺陷存在（不同条款共享同一 `citation_label`）
  - **DO NOT attempt to fix the test or the code when it fails** —— 失败是预期的正确结果
  - **NOTE**: 本测试编码了期望行为（所有 `citation_label` 在文档内唯一）；修复后它通过即验证 Fix Checking
  - **GOAL**: 用合成 `ClauseNode` 树 surface 出复现撞号的反例，确认根因（`flatten()` 无文档级唯一性检查 + `build_citation_label` 返回裸编号跨作用域相撞）
  - **Scoped PBT Approach**: 本缺陷为确定性缺陷，用具体合成树复现而非随机生成，保证可复现
  - 新测试文件 `backend/tests/test_parsing_citation_uniqueness.py`，以合成 `ClauseNode` 树驱动 `flatten(nodes, document_id=1)`（解析层不触数据库，无需 DB fixture）
  - 用例 1「正文与附录 1 撞号」：正文顶层条款 `number="1"` + 未编号附录标题 `ClauseNode(heading="附录 A", number=None)` 其下子条款 `number="1"`；断言 `len({r["citation_label"] for r in rows}) == len(rows)`（唯一）——未修复代码下两行 `citation_label` 均为 `"1"`，断言 FAIL
  - 用例 2「无未编号祖先的撞号」：两个顶层编号条款均为 `number="1"`（无任何未编号祖先）；同一唯一性断言在未修复代码下 FAIL
  - 用例 3「两个同名附录各含 1」：两个 `heading="附录"`（均 `number=None`）各含一个子条款 `number="1"`；断言唯一——用于暴露"仅用最近未编号祖先标题限定仍得重复 `附录 › 1`"，证明需要确定性最终兜底
  - 断言内容对齐 design 的 `isBugCondition(rows)`：存在被多行共享的 `citation_label` 即撞号
  - Run test on UNFIXED code
  - **EXPECTED OUTCOME**: 三个用例均 FAIL（证明缺陷存在）
  - 记录反例（如 `flatten` 对正文"1"与附录"1"写入相同 `citation_label="1"`）
  - Mark task complete when test is written, run, and failure is documented
  - _Requirements: 1.1, 1.2, 2.1, 2.2, 2.3_

- [x] 2. 编写保全性测试（修复前，观察优先，必须 PASS）
  - **Property 2: Preservation** - 非相撞行与其他字段逐字保持不变
  - **IMPORTANT**: 遵循 observation-first 方法——先在 **未修复** 代码上观察输出，再写断言，确认在未修复代码上 PASS 后方可开始修复
  - 在 `backend/tests/test_parsing_citation_uniqueness.py` 中新增保全性测试
  - 用例 1「无撞号文档逐字节一致」：构造无任何撞号的合成树，在修复前捕获 `flatten()` 完整返回（含 `_parent_key`/`_source_keys` 等所有键）作为基线；修复后断言 `flatten_fixed(nodes) == baseline`（后处理为 no-op）。实现上可把基线固化为测试内常量/快照，保证修复后仍逐字节一致
  - 用例 2「唯一编号保持裸标签」：含唯一 `number="3.4.2"` 的条款，断言其 `citation_label == "3.4.2"`（不得被限定）
  - 用例 3「未编号非撞号保持 heading_path」：`number=None` 且 `heading_path` 在文档内唯一（如 `"引言"`），断言 `citation_label == heading_path`
  - 用例 4「其他字段不变」：对无撞号树断言每行 `number`/`heading`/`heading_path`/`order_index`/`level`/`page_ref`/`kind`/`language`/`_parent_key`/`_source_keys` 与基线逐一相等——只有相撞行的 `citation_label` 允许改变
  - 断言对齐 design 的 `NOT isBugCondition(rows)` 范围：不撞号的输入完全不受修复影响
  - Run tests on UNFIXED code
  - **EXPECTED OUTCOME**: 全部 PASS（确立需保全的基线行为）
  - Mark task complete when tests are written, run, and passing on unfixed code
  - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 3. 修复 citation_label 文档级唯一性（附录重启编号撞号）

  - [x] 3.1 实现 ancestor_chains 记录与 _disambiguate_labels 两趟算法
    - 文件：`backend/app/parsing/flatten.py`；保留 `build_citation_label` 作为第一趟默认标签函数（有编号返回裸 `number`，否则 `heading_path`）
    - 第一趟：在 `flatten.walk` 中维护一个与 `rows` 下标对齐的并行列表 `ancestor_chains`，每项记录该行从直接父到根的祖先 `(number, heading)` 元组序列（最近祖先在前）；`walk` 新增参数携带当前节点祖先链，追加时 `key = len(rows)` 与 `rows` 对齐。不改变 `rows` 的字段与顺序
    - 新增 `_disambiguate_labels(rows, ancestor_chains)`：在 `flatten` 返回前、`rows` 构建完成后调用；**无撞号时立即返回、不做任何修改（no-op）**，保证逐字节一致
    - 撞号检测：按 `citation_label` 分组，取出现次数 > 1 的标签对应行为撞号集；空则 no-op 返回
    - 限定相撞的编号标签：仅处理 `row["number"]` 非 None 的撞号行，沿 `ancestor_chains[key]` 从最近祖先向上找第一个 `number is None` 的祖先，新标签 `f"{ancestor_heading}{PATH_SEPARATOR}{row['number']}"`（复用 `PATH_SEPARATOR = " › "`）
    - 确定性最终兜底阶梯（限定后重新检测仍撞号时逐级升级，直到全局唯一）：(a) 用完整 `heading_path` 作为基——编号行 `f"{heading_path}{PATH_SEPARATOR}{number}"`、未编号行 `heading_path`；(b) 仍撞号则追加 `order_index`：`f"{label}{PATH_SEPARATOR}#{order_index}"`，`order_index` 为行唯一下标，保证绝对唯一且顺序稳定
    - 确定性：后处理只读每行自身与祖先链既有信息，按 `rows` 既有顺序遍历、以 `order_index` 兜底；不改动 `rows` 顺序与其他字段
    - _Bug_Condition: isBugCondition(rows) —— 存在被多行共享的 citation_label（from design Bug Condition）_
    - _Expected_Behavior: 每行 citation_label 全局唯一；相撞编号行按 f"{ancestor_heading}{PATH_SEPARATOR}{number}" 限定，必要时沿 heading_path → order_index 兜底（from design Correctness Property 1）_
    - _Preservation: 非相撞行 citation_label 及 number/heading/heading_path/order_index/树结构/出处关联等所有字段不变（from design Preservation Requirements）_
    - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4_

  - [x] 3.2 重新运行撞号探索测试，确认现在 PASS（Fix Checking）
    - **Property 1: Expected Behavior** - 相撞的编号标签被限定为唯一
    - **IMPORTANT**: 重跑任务 1 的 **同一测试**——不要新写测试
    - 任务 1 的测试编码了期望行为；它通过即确认期望行为被满足、缺陷已修复
    - 运行任务 1 的三个撞号用例
    - **EXPECTED OUTCOME**: 全部 PASS（正文"1"与附录"1"可区分；无未编号祖先/同名附录经兜底后唯一；所有 `citation_label` 互不相同）
    - _Requirements: 2.1, 2.2, 2.3_

  - [x] 3.3 重新运行保全性测试，确认仍 PASS（Preservation）
    - **Property 2: Preservation** - 非相撞行与其他字段逐字保持不变
    - **IMPORTANT**: 重跑任务 2 的 **同一组测试**——不要新写测试
    - 运行任务 2 的保全性用例
    - **EXPECTED OUTCOME**: 全部 PASS（无撞号文档逐字节一致；唯一 "3.4.2" 保持裸标签；未编号非撞号保持 heading_path；其他字段无回归）
    - Confirm all tests still pass after fix (no regressions)
    - _Requirements: 3.1, 3.2, 3.3, 3.4_

- [x] 4. 补充单元测试与基于属性的测试
  - 在 `backend/tests/test_parsing_citation_uniqueness.py` 补充单元用例，覆盖 design 的 Unit Tests：
    - 正文 "1" 与未编号附录（如 "附录 A"）下 "1" → 修复后分别为 `"1"` 与 `"附录 A › 1"`，断言两者唯一
    - 无未编号祖先的撞号 → 走兜底（完整 `heading_path` 或 `#{order_index}`），断言唯一
    - 两个同名附录各含 "1" → 祖先标题限定后仍撞，断言最终兜底使其唯一
    - 出现次数 > 2 的撞号（三个 "1"）→ 全部被唯一化
  - 基于属性的测试（PBT）——注：`hypothesis` 当前不在 `backend/pyproject.toml` 的 `dev` 依赖中，需先将其加入 `[project.optional-dependencies].dev` 并在 Docker 镜像内安装；若选择不引入 hypothesis，则以确定性生成器（遍历结构参数组合）编写等价的穷举式 PBT。实现以下不变式：
    - 唯一性：对任意合成 `ClauseNode` 树，`flatten_fixed(nodes)` 返回的所有 `citation_label` 互不相同
    - 确定性/顺序稳定：对任意合成树，两次 `flatten_fixed(nodes)` 结果完全相等
    - 保全性：对任意 **无撞号** 合成树，`flatten_fixed(nodes) == flatten_original(nodes)`
    - order_index 兜底：构造路径与编号均相同的退化重复节点，断言最终标签仍唯一且含 `#{order_index}`
  - 可选集成测试（如有价值）：经 `persist()` 全链路写入后，断言同一文档内每条 Clause 的 `citation_label` 唯一，且 `order_index` 与树结构（`parent_id`）不变
  - _Requirements: 2.1, 2.2, 2.3, 3.1, 3.2, 3.3, 3.4_

- [x] 5. Checkpoint - 确保全部测试通过
  - 在 Docker 内运行 `make test` 跑完整后端测试套件；解析为后端独立逻辑，无需前端
  - 另可运行 `make verify` 做回归把关（lint + 测试）
  - 确认新增/已有测试全绿；如有问题向用户确认
  - 提交信息用英文，测试名与注释用中文

## Notes

- 任务顺序遵循"复现优先"：任务 1（探索测试，修复前 FAIL）→ 任务 2（保全性测试，修复前 PASS）→ 任务 3（实现 + 复测）→ 任务 4（补充测试）→ 任务 5（checkpoint）。
- Property 1 = Bug Condition / Expected Behavior（撞号编号标签被限定为唯一）；Property 2 = Preservation（非相撞行与其他字段逐字不变）。
- 解析层不触数据库：`flatten()` 单测以合成 `ClauseNode` 树驱动，仅集成测试经 `persist()` 需要 DB fixture。
- `hypothesis` 尚未在 `backend/pyproject.toml` 的 `dev` 依赖中；若采用 PBT，需先加入并在镜像内安装，否则以确定性穷举生成器实现等价不变式。
- 测试名与注释用中文，提交信息用英文；测试统一通过 Docker 内 `make test` 运行。
