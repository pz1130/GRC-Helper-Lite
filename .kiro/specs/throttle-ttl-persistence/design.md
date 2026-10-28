# throttle-ttl-persistence Bugfix Design

## Overview

登录失败限流把计数放在 Redis，依赖键的 TTL 让冷却窗口「到点自然过期」。但计数键
的创建（`incr`）与设 TTL（`expire`）是两条独立命令，且 `expire` 只在 `count == 1`
时执行。如果 `incr` 已提交而紧随其后的 `expire` 没有执行成功（瞬时 Redis 错误、
两条命令之间连接被断——都被外层宽泛的 `except Exception` 吞掉），这个键就停在
值 1、没有 TTL 的状态。之后每次失败 `count != 1`，`expire` 再也不会重试，计数只升
不降，窗口永不过期。

修复策略：把「自增 + 确保 TTL」做成**原子、幂等**的单次操作。用一段 Redis Lua
脚本在服务端一次性完成 `INCR`，并在键没有 TTL 时（`PTTL < 0`）补上 `EXPIRE`。
因为脚本原子执行，不存在「incr 成功、expire 丢失」的中间窗口；因为它只在「无 TTL」
时补设、从不覆盖已有 TTL，所以窗口始终锚定在第一次失败（固定窗口，不是滑动窗口）。
`record_failure` 与 `allow` 共用同一段脚本/辅助函数，一处修复覆盖两个调用点。

修复是**最小且定向**的：只替换 `incr`-then-conditional-`expire` 这一处模式，不动
阈值、不动两维度计数、不动 fail-open 取舍、不动 `clear` 的语义。

## Glossary

- **Bug_Condition (C)**: 触发缺陷的条件——计数键已经靠 `incr` 创建/自增，却没有
  TTL（`PTTL` 返回 -1 无过期 或 -2 键不存在时刚被 `incr` 创建但 `expire` 丢失）。
- **Property (P)**: 期望行为——任何一次失败计数记录之后，对应的计数键都必须带有一个
  正的 TTL，窗口能够自行过期。
- **Preservation**: 必须保持不变的既有行为——Redis 不可用时 fail-open、`clear` 只清
  账号键、阈值与两维度计数、固定窗口不被重复命中延长。
- **固定窗口 (fixed window)**: TTL 从「第一次失败」起算，窗口期内后续命中只增加计数、
  **不**重设/延长 TTL；到点整体过期归零。
- **滑动窗口 (sliding window)**: 每次命中都把过期时间往后推。本修复**明确不**采用，
  因为 `clear` 故意不清 IP 键、靠 IP 键窗口自行过期来解锁，滑动窗口会破坏这一依赖。
- **record_failure**: `backend/app/iam/throttle.py` 中按账号 + 按 IP 记录一次登录失败
  计数的函数。
- **allow**: 同文件中的通用固定窗口计数辅助，供登录以外的按用户限频使用。
- **PTTL**: Redis 命令，返回键剩余存活毫秒；-1 表示键存在但无过期，-2 表示键不存在。

## Bug Details

### Bug Condition

缺陷在「计数键被 `incr` 创建或自增后、却没有 TTL」时显形。根因是自增与设 TTL 被拆成
两条命令，且设 TTL 只在首次自增（`count == 1`）时尝试：首次自增后的 `expire` 一旦遇到
瞬时失败（被宽泛 `except` 吞掉），键就永久停在「有计数、无 TTL」，且后续失败不再重试。

**Formal Specification:**
```
FUNCTION isBugCondition(input)
  INPUT: input = { key, redisStateBeforeCall }
  OUTPUT: boolean

  // 调用计数路径（record_failure / allow）之后，
  // 键已存在且携带计数，但没有有效 TTL。
  RETURN keyExistsWithCount(input.key)
         AND pttl(input.key) < 0        // -1 无过期 或 -2 不存在
END FUNCTION
```

说明：现行代码产生该条件的典型路径是——首次失败 `incr` 成功（key=1），随后的
`expire` 因瞬时错误未执行；此后每次失败 `count != 1`，`expire` 永不重试。

### Examples

- **首次失败 expire 丢失**：对空键 `incr` 得 1，紧接着的 `expire` 抛错被吞 →
  键停在 `{value:1, ttl:none}`。期望：键带 15 分钟 TTL；实际：永不过期。
- **无 TTL 键上的后续失败**：承上，第二次失败 `incr` 得 2，`count != 1` 故不设
  TTL → 键 `{value:2, ttl:none}`。期望：补回 TTL；实际：仍无 TTL，计数继续爬。
- **IP 键永久锁死**：per-IP 键丢失 TTL 后爬到 50 并停住；`clear` 从不碰 IP 键，
  无其它恢复路径 → 整个 IP 永久被锁。期望：窗口到点自动解锁；实际：永久锁定。
- **账号键长锁**：账号键丢失 TTL 后爬到 5 并停住，只能靠一次成功登录的 `clear`
  解锁，而非窗口自行过期。期望：窗口自行过期；实际：须人工/成功登录干预。
- **边界（正常路径）**：首次失败 `incr` 与 `expire` 都成功 → 键带 TTL，窗口期内
  后续命中只加计数、不延长 TTL，到点归零。此为应保持不变的正确行为。

## Expected Behavior

### Preservation Requirements

**Unchanged Behaviors:**
- Redis 不可用时 fail-open（放行本次尝试），保留现有 `try/except` 与模块文档里写明
  的取舍（对应需求 3.2）。
- `clear` 只删账号键、绝不碰 per-IP 键（对应需求 3.3）。
- 阈值与窗口不变：`MAX_ACCOUNT_FAILURES=5`、`MAX_IP_FAILURES=50`、
  `WINDOW_SECONDS=15*60`；两维度（账号 + IP）计数不变（对应需求 3.4）。
- 固定窗口不被重复命中延长：TTL 锚定在第一次失败，窗口期内后续命中不重设 TTL
  （对应需求 3.1、3.5）。

**Scope:**
所有**不**落在 bug 条件内的输入都应完全不受本修复影响，包括：
- Redis 连接失败路径（`_client` 抛错）——继续放行。
- 已带有效 TTL 的键上的后续命中——只自增、TTL 保持原值不变。
- `check` 读取路径与 `clear` 删除路径——逻辑与键集合不变。
- `allow` 的返回语义（本窗口内第 `limit` 次以内返回 True，之后 False）。

> 注：本次「期望正确行为」的正面定义见下方 Correctness Properties 的 Property 1；
> 本节聚焦于**不得改变**的部分。

## Hypothesized Root Cause

综合缺陷描述与代码，最可能的原因是：

1. **自增与设 TTL 被拆成两条命令（非原子）**：`incr` 与 `expire` 之间存在一个中间
   状态，任何在这中间发生的瞬时错误/连接中断都会留下「有计数、无 TTL」的键。
   - `record_failure`：`count = await redis.incr(key)` 后才 `await redis.expire(...)`。
   - `allow`：同样是 `incr` 后再 `expire`。

2. **设 TTL 的条件过窄（只在 `count == 1`）**：一旦首次 `expire` 丢失，`count` 立刻
   大于 1，代码再无任何路径补设 TTL——缺陷因此不可自愈。

3. **宽泛 `except Exception` 吞掉了 `expire` 失败**：失败既不重试也不显形，使
   「无 TTL」成为静默的持久状态。

4. **恢复路径不对称放大了 IP 维度的影响**：`clear` 故意不清 IP 键（防「用自有账号成功
   登录来抹掉 IP 计数」的绕过），因此 IP 键一旦丢 TTL 便再无恢复路径——永久 IP 级锁死。

修复定向打击前两点（原子化 + 幂等补设 TTL），从而让第 3 点的瞬时失败不再产生持久后果，
也消除第 4 点的永久锁死，而无需改动 `clear` 的安全语义。

## Correctness Properties

Property 1: Bug Condition - 计数后始终带 TTL

_For any_ 命中计数路径（`record_failure` 或 `allow`）的输入，当 bug 条件成立
（`isBugCondition` 返回 true，即键被计数后没有有效 TTL），修复后的函数 SHALL 保证该次
调用结束时对应计数键携带一个正的 TTL（`PTTL > 0`），使其冷却窗口能够自行过期；即便设
TTL 这一步此前曾经丢失，后续任一次计数也 SHALL（重新）补设 TTL。

**Validates: Requirements 2.1, 2.2, 2.3, 2.4**

Property 2: Preservation - 非 bug 输入行为不变

_For any_ bug 条件不成立（`isBugCondition` 返回 false）的输入，修复后的代码 SHALL 产生
与原函数相同的可观察结果，具体保持不变的有：Redis 不可用时 fail-open；`clear` 只清账号
键、不碰 IP 键；阈值（5 / 50 / 15min）与两维度计数；已带有效 TTL 的键在窗口期内后续命中
只自增、TTL 不被延长（固定窗口，而非滑动窗口）。

**Validates: Requirements 3.1, 3.2, 3.3, 3.4, 3.5**

## Fix Implementation

### Changes Required

假定上述根因分析成立。

**File**: `backend/app/iam/throttle.py`

**共享脚本/辅助**：新增一段 Lua 脚本与一个调用它的辅助函数，`record_failure` 与
`allow` 都改为调用它。

**Lua 脚本（服务端原子执行）**：
```
-- KEYS[1] = 计数键; ARGV[1] = 窗口秒数
local n = redis.call('INCR', KEYS[1])
if redis.call('PTTL', KEYS[1]) < 0 then   -- -1 无过期, -2 不存在
    redis.call('EXPIRE', KEYS[1], ARGV[1])
end
return n
```

**Specific Changes**:
1. **定义脚本常量**：在模块内定义上面的 Lua 文本（附中文注释），作为「自增 + 幂等
   确保 TTL」的单一来源。

2. **原子化自增 + 确保 TTL 的辅助**：新增 `async def _incr_with_ttl(redis, key,
   window_seconds) -> int`，内部用 `redis.eval(_INCR_WITH_TTL_LUA, 1, key,
   window_seconds)`（或 `register_script` 生成的可调用对象）执行脚本并返回计数值。
   用中文 docstring 说明：脚本仅在键缺 TTL 时补设，永不覆盖已有 TTL（固定窗口）。

3. **改造 `record_failure`**：把
   ```
   count = await redis.incr(key)
   if count == 1:
       await redis.expire(key, WINDOW_SECONDS)
   ```
   替换为 `await _incr_with_ttl(redis, key, WINDOW_SECONDS)`。两维度键集合
   `_keys(email, ip)` 与循环结构不变。

4. **改造 `allow`**：把同样的 `incr`-then-conditional-`expire` 替换为
   `count = await _incr_with_ttl(redis, key, window_seconds)`，`return int(count) <= limit`
   的判定不变。

5. **不改动的部分**：外层 `try/except`（fail-open）、`_client` / `_close` / `check` /
   `clear` 的逻辑、阈值常量、键命名、两维度计数，全部保持原样。

### 方案权衡（为什么选 Lua）

- **Lua 脚本（首选）**：`INCR` 与条件 `EXPIRE` 在服务端原子执行，单次往返，一次关掉
  「incr/expire 拆分」与「只在 count==1 才设 TTL」两个缺口；幂等——任何一次调用都能
  在键缺 TTL 时补回，且从不覆盖已有 TTL（天然保持固定窗口）。
- **pipeline / MULTI 的 `INCR` + 条件 `EXPIRE`**：MULTI 能让两命令原子提交，但
  「是否需要 EXPIRE」依赖 `INCR`/`PTTL` 的结果，纯 MULTI 无法在事务内分支（需
  `WATCH`/重试或两次往返），比 Lua 复杂且多一跳。
- **`SET key 0 EX w NX` + `INCR`**：`NX` 保证只有首次才设 TTL 与初值，但首次路径仍是
  两条命令；且语义改写较大，不如 Lua 直接。
- **在 Python 侧判断 `PTTL < 0` 再补 `EXPIRE`**：能覆盖「后续命中补设」，但**首次失败
  那一刻仍是 incr→(读 pttl)→expire 的拆分窗口**，没真正关掉根因，仅把概率降低。

因此选 Lua 为主方案：原子、单次往返、同时闭合两个半缺口，并天然满足固定窗口约束。

## Testing Strategy

### Validation Approach

两阶段：先在**未修复**代码上制造反例、让缺陷现形并确认根因；再验证修复对 bug 输入产生正确
行为，且对非 bug 输入完全保持既有行为。测试用中文命名，经 `make test` 在带 Redis 的
Docker 环境中运行。现有 `test_login_throttle.py` 的 `_FakeRedis` 只实现了 incr/expire/
delete 且 `expire` 为空操作、不记录 TTL；要验证 TTL 行为，需要一个能观测 PTTL 并支持
Lua（或等价分支）的后端——优先用 Docker 内的真实 Redis（`throttle.redis_url`），对
「expire 丢失」这类注入场景再辅以 monkeypatch。

### Exploratory Bug Condition Checking

**Goal**: 在实现修复**之前**，制造反例让缺陷现形，确认/推翻根因分析。若推翻，需重新假设。

**Test Plan**: 模拟「`incr` 成功但 `expire` 丢失」的拆分失败，然后断言键最终**应当**带
TTL。在未修复代码上运行以观察失败并理解根因。制造手段二选一：(a) 用真实 Redis 预先用
`incr`/不设 expire 造出一个无 TTL 的键，再走 `record_failure` 的后续失败路径；(b)
monkeypatch 让第一次 `expire` 抛错（被外层 `except` 吞），再继续记录失败。

**Test Cases**:
1. **首次 expire 丢失后键无 TTL**：注入首次 `expire` 失败，断言键 `PTTL < 0`（未修复
   会命中，暴露缺陷）。
2. **无 TTL 键上的后续失败仍无 TTL**：承上再记一次失败，断言键 `PTTL` 仍 `< 0`（未修复
   会命中，因为 `count != 1` 不再 expire）。
3. **IP 键丢 TTL 后无恢复路径**：对 per-IP 键制造无 TTL 状态，断言 `clear` 之后它仍在
   且仍无 TTL（未修复会命中，印证永久锁死）。
4. **allow 同样受影响**（边界）：对 `allow` 的键注入 expire 丢失，断言键无 TTL（未修复
   会命中，印证两处共用同一缺陷模式）。

**Expected Counterexamples**:
- 计数键被 `incr` 创建/自增后 `PTTL < 0`。
- 可能原因：incr/expire 拆分、只在 `count == 1` 设 TTL、`expire` 失败被吞。

### Fix Checking

**Goal**: 验证对所有 bug 条件成立的输入，修复后的函数都产生期望行为（键带正的 TTL）。

**Pseudocode:**
```
FOR ALL input WHERE isBugCondition(input) DO
  result := record_failure_fixed(input)   // 或 allow_fixed(input)
  ASSERT pttl(input.key) > 0               // expectedBehavior(result)
END FOR
```

### Preservation Checking

**Goal**: 验证对所有 bug 条件不成立的输入，修复后的函数与原函数结果一致。

**Pseudocode:**
```
FOR ALL input WHERE NOT isBugCondition(input) DO
  ASSERT observable(record_failure_original(input)) = observable(record_failure_fixed(input))
END FOR
```

**Testing Approach**: 保持性检查推荐用基于属性的测试，因为它能在输入域内自动生成大量用例、
覆盖手写单测易漏的边界，并对「非 bug 输入行为不变」给出更强保证。

**Test Plan**: 先在**未修复**代码上观察非 bug 输入的行为（固定窗口 TTL 不延长、fail-open、
`clear` 只清账号键、阈值），再写测试把这些行为固化，确保修复后仍然成立。

**Test Cases**:
1. **固定窗口不被延长**：观察带有效 TTL 的键在后续命中后 TTL 不增大（允许自然递减），
   写测试在修复后断言「后续命中不重设 TTL」。
2. **Redis 不可用时放行**：观察 `_client` 抛错时放行，写测试断言修复后仍 fail-open。
3. **clear 只清账号键**：观察成功登录只删账号键、保留 IP 键，写测试断言修复后不变。
4. **阈值与两维度不变**：观察 5 / 50 / 15min 与账号 + IP 两键计数，写测试断言修复后不变。

### Unit Tests

- `record_failure`：首次失败后键带 TTL；注入首次 expire 丢失后，后续失败能补回 TTL。
- `allow`：窗口内计数 `[True,True,True,False,False]` 不变；注入 expire 丢失后键仍带 TTL。
- 边界：对已无 TTL 的键再次命中能补 TTL；对已有 TTL 的键命中不延长 TTL。

### Property-Based Tests

- 随机生成一串失败次数与「expire 丢失」注入点，断言任一序列结束后计数键始终 `PTTL > 0`
  （Property 1）。
- 随机生成对「已带 TTL 的键」的命中序列，断言 TTL 不被延长、计数单调递增（固定窗口保持）。
- 随机生成账号/IP/窗口组合，断言阈值与两维度计数行为与基线一致（Property 2）。

### Integration Tests

- 走 `/api/auth/login` 全流程：注入首次 expire 丢失后，账号在 15 分钟窗口到点后能自行解锁
  （修复前会永久锁）。
- per-IP 维度：IP 键在窗口到点后自行解锁，且 `clear` 期间从不被触碰。
- 成功登录只清账号计数、不动 IP 计数的既有集成行为保持不变。
