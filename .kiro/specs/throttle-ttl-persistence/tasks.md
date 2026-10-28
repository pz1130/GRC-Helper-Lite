# Implementation Plan

- [x] 1. 编写 bug 条件探查测试（在修复之前）
  - **Property 1: Bug Condition** - 计数后始终带 TTL
  - **CRITICAL**: 本测试在未修复代码上必须 FAIL —— 失败即证明缺陷存在
  - **DO NOT attempt to fix the test or the code when it fails** —— 本步只负责暴露缺陷
  - **NOTE**: 本测试同时编码了「期望行为」，修复后它转为 PASS 即构成 Fix Checking 的验证
  - **GOAL**: 制造反例，证明「`incr` 成功但 `expire` 丢失」会留下无 TTL 的计数键
  - **测试后端选择**: 现有 `backend/tests/test_login_throttle.py` 的 `_FakeRedis` 的 `expire` 为空操作、不记录 TTL，无法观测 PTTL。本测试改用 Docker 内真实 Redis（`throttle.redis_url`）做 TTL 断言，并用 monkeypatch 注入「首次 expire 抛错」这一拆分失败场景（被外层宽泛 `except` 吞掉）
  - **Scoped PBT Approach**: 本缺陷为确定性缺陷，将属性缩放到具体可复现的失败用例上（见下方 isBugCondition：键被计数后 `PTTL < 0`）
  - 用例 1（首次 expire 丢失后键无 TTL）：注入首次 `expire` 失败后调用 `record_failure`，断言账号键 `PTTL < 0`（未修复命中，暴露缺陷）
  - 用例 2（无 TTL 键上的后续失败仍无 TTL）：承上再记一次失败，断言键 `PTTL` 仍 `< 0`（未修复命中：`count != 1` 不再 expire）
  - 用例 3（IP 键丢 TTL 后无恢复路径）：对 per-IP 键制造无 TTL 状态，断言 `clear` 之后它仍在且仍无 TTL（未修复命中，印证永久锁死）
  - 用例 4（`allow` 同样受影响，边界）：对 `allow` 的键注入 expire 丢失，断言键 `PTTL < 0`（未修复命中，印证两处共用同一缺陷模式）
  - 断言编码期望行为（来自 design 的 Property 1）：计数路径结束后对应键应携带正的 TTL（`PTTL > 0`）
  - 在未修复代码上运行测试
  - **EXPECTED OUTCOME**: 测试 FAIL（正确——证明缺陷存在）
  - 记录反例以理解根因（例如「首次 `incr` 得 1、`expire` 被吞 → 键 `{value:1, ttl:none}`，后续 `count != 1` 永不补设」）
  - 测试写好、运行、失败被记录后，标记本任务完成
  - _Bug_Condition: isBugCondition(input) = keyExistsWithCount(key) AND pttl(key) < 0_
  - _Requirements: 1.1, 1.2, 1.3, 1.4_

- [x] 2. 编写保持性属性测试（在修复之前）
  - **Property 2: Preservation** - 非 bug 输入行为不变
  - **IMPORTANT**: 遵循 observation-first 方法论——先在**未修复**代码上观察真实行为，再把它固化成测试
  - **测试后端选择**: 固定窗口 TTL 相关断言用 Docker 内真实 Redis（`throttle.redis_url`）；fail-open / `clear` / 阈值 / 两维度计数等可继续沿用 `_FakeRedis`（必要时扩展其以跟踪 TTL/PTTL 并支持 `eval`，否则对 TTL 相关项一律走真实 Redis）
  - 观察并固化（来自 design 的 Preservation Requirements）：
    - 固定窗口不被延长：观察带有效 TTL 的键在后续命中后 TTL 不增大（允许自然递减），写属性测试断言「后续命中不重设 TTL」
    - Redis 不可用时 fail-open：观察 `_client` 抛错时放行，断言放行语义不变
    - `clear` 只清账号键：观察成功登录只删账号键、保留 IP 键，断言不变
    - 阈值与两维度不变：观察 `MAX_ACCOUNT_FAILURES=5` / `MAX_IP_FAILURES=50` / `WINDOW_SECONDS=15min` 与账号 + IP 两键计数，断言不变
    - `allow` 返回语义：窗口内计数序列 `[True, True, True, False, False]` 不变
  - 用基于属性的测试覆盖「for all 非 bug 输入」：随机生成命中序列断言 TTL 不被延长、计数单调递增；随机生成账号/IP/窗口组合断言阈值与两维度行为与基线一致
  - 在未修复代码上运行测试
  - **EXPECTED OUTCOME**: 测试 PASS（确立需要保持的基线行为）
  - 测试写好、运行、在未修复代码上通过后，标记本任务完成
  - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 3. 修复「计数键可能无 TTL」缺陷

  - [x] 3.1 实现 Lua 脚本与 `_incr_with_ttl` 辅助并改造两个调用点
    - 在 `backend/app/iam/throttle.py` 定义 Lua 脚本常量（附中文注释）：`INCR` 计数键，`PTTL < 0`（-1 无过期 / -2 不存在）时才 `EXPIRE`，返回计数值——服务端原子执行，一次关掉「incr/expire 拆分」与「只在 count==1 设 TTL」两个缺口
    - 新增 `async def _incr_with_ttl(redis, key, window_seconds) -> int`，内部用 `redis.eval(...)`（或 `register_script` 可调用对象）执行脚本并返回计数；中文 docstring 说明脚本仅在键缺 TTL 时补设、永不覆盖已有 TTL（固定窗口，非滑动窗口）
    - 改造 `record_failure`：把 `count = await redis.incr(key)` + `if count == 1: await redis.expire(...)` 替换为 `await _incr_with_ttl(redis, key, WINDOW_SECONDS)`；两维度键集合 `_keys(email, ip)` 与循环结构不变
    - 改造 `allow`：把同样的 `incr`-then-conditional-`expire` 替换为 `count = await _incr_with_ttl(redis, key, window_seconds)`，`return int(count) <= limit` 判定不变
    - 不改动：外层 `try/except`（fail-open）、`_client` / `_close` / `check` / `clear` 逻辑、阈值常量、键命名、两维度计数
    - 提交信息用英文（Chinese tests/comments, English commits）
    - _Bug_Condition: isBugCondition(input) = keyExistsWithCount(key) AND pttl(key) < 0_
    - _Expected_Behavior: expectedBehavior(result) —— 计数路径结束后对应键 PTTL > 0（来自 design Property 1）_
    - _Preservation: Preservation Requirements from design（fail-open / clear 只清账号键 / 阈值 / 两维度 / 固定窗口）_
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.2 验证 bug 条件探查测试现在通过（Fix Checking）
    - **Property 1: Expected Behavior** - 计数后始终带 TTL
    - **IMPORTANT**: 重跑任务 1 的**同一批**测试——不要新写测试
    - 任务 1 的测试编码了期望行为；它通过即确认 bug 条件下的键都带正的 TTL
    - 运行任务 1 的 bug 条件探查测试
    - **EXPECTED OUTCOME**: 测试 PASS（确认缺陷已修复，窗口能自行过期）
    - _Requirements: 2.1, 2.2, 2.3, 2.4_

  - [x] 3.3 验证保持性测试仍然通过（Preservation）
    - **Property 2: Preservation** - 非 bug 输入行为不变
    - **IMPORTANT**: 重跑任务 2 的**同一批**测试——不要新写测试
    - 运行任务 2 的保持性属性测试
    - **EXPECTED OUTCOME**: 测试 PASS（确认无回归：fail-open / clear 只清账号键 / 阈值与两维度 / 固定窗口不被延长 / `allow` 返回语义均不变）
    - _Requirements: 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 4. 补充单元 / 集成测试
  - 单元（`record_failure`）：首次失败后键带 TTL；注入首次 expire 丢失后，后续失败能补回 TTL
  - 单元（`allow`）：窗口内计数 `[True, True, True, False, False]` 不变；注入 expire 丢失后键仍带 TTL
  - 单元（边界）：对已无 TTL 的键再次命中能补 TTL；对已有 TTL 的键命中不延长 TTL
  - 集成（`/api/auth/login` 全流程）：注入首次 expire 丢失后，账号在窗口到点后能自行解锁（修复前会永久锁）
  - 集成（per-IP 维度）：IP 键在窗口到点后自行解锁，且 `clear` 期间从不被触碰
  - 集成：成功登录只清账号计数、不动 IP 计数的既有行为保持不变
  - 测试用中文命名/注释，经 `make test` 在带 Redis 的 Docker 环境运行
  - _Requirements: 2.1, 2.2, 2.3, 2.4, 3.1, 3.2, 3.3, 3.4, 3.5_

- [x] 5. Checkpoint - 确保所有测试通过
  - 运行 `make test`（必要时 `make verify`），确认全部通过
  - 若出现问题，向用户确认后再继续
