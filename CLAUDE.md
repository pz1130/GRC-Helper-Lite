# GRC Helper

给不同组织的 IT GRC 人员用的制度治理助手：导入制度文件 → 解析成条款树 → AI 抽取控制点 →
映射到合规框架 → 关系/冲突/变更影响 → 审计答复与准备包。

## 验证

```bash
make verify        # 五条一次跑完：pytest / ruff / npm build / i18n-check / make e2e
make test          # 只跑后端，快，写代码时用这个
make i18n-check    # 单跑那条：后端的 code 与 en.json 词条对不对得上
```

`make verify` 的四件事要知道：

- **每条单独报退出码，失败不中断后面的**——一次跑完能看到所有失败，不是第一个。
  绝不要把验证命令管道给 `tail`：报的是 `tail` 的退出码。2026-09-11 就这样同时藏过
  一次 playwright 失败和 ruff 的 126 条，两个都显示为绿。
- **ruff 只报数不判成败**。main 上本来就有 **127** 条既有告警（2026-09-23 实测）（多是 `app/llm/router.py`
  与 `app/ingest/router.py` 的 B008）。只看本次改动的文件有没有新增，别去清基线。
- **`i18n-check` 拦的是测试和构建都拦不住的那一类**：后端加了个 `code=` 却忘了
  补词条。这**不会报错**——前端会回落到后端那句中文，界面照常能用，只是用户看到
  中文。所以它只能靠这条检查发现。
  它不连数据库也不进容器，纯读文件，几百毫秒。
- **e2e 走隔离的 compose 项目**（`make e2e`，跑完 `down -v`），所以慢几分钟。
  不要直接 `npx playwright test`——那跑在开发栈上，每次留下一个 provider 和一个用户。
  中途 Ctrl-C 打断了用 `make e2e-down` 收尾。

## 换一批制度文档时

```bash
make corpus CORPUS_DIR=/abs/path/某机构制度
make corpus CORPUS_DIR=/abs/path CORPUS_ANCHORS="引言,职责分工"   # 换一套文风锚点
make corpus CORPUS_DIR=/abs/path CORPUS_ANCHORS=none             # 还不知道就先跳过
```

**只过解析层，不写任何库**——导入之前先看条款树切不切得开、有没有目录行混进条款、
编号还原对不对。路径必须绝对（docker 的 bind mount 不认相对路径，也不展开 `~`）。

`CORPUS_ANCHORS` 的默认值（`Introduction` / `Roles and Responsibilities`）是
**可配置的示例章节名**，不是解析器的性质。拿它去量不同结构的文档，失败的是「文风不同」
而不是「解析漏了开头」。

## 两条铁律

1. **`controls` / `mappings` / `control_relations` / `policy_conflicts` 只能经
   `backend/app/review/materialize.py` 写入。** AI 任务只产 `Proposal`，人工确认后才落正式表。
   控制点的人工编辑（`update_control`）与合并（`merge_controls`）也在那个模块里，同一把
   `lock_control_writes` 咨询锁下。
2. **对已确认数据的每一次改动都要留审计。** `AuditLog` 按 id 记录实体，所以**不删行**：
   控制点合并是 `status='merged'` + `merged_into_id`，不是 DELETE。

## 语言约定

三类文字，各有各的语言，**按读者分而不是按心情分**：

| 写给谁看 | 语言 | 包括 |
|---|---|---|
| 仓库里面的人 | **中文** | 代码注释、docstring、测试名与测试里的说明 |
| GitHub 上的人 | **英文** | 提交信息、PR 描述 |
| 产品的用户 | **英文** | 界面文案、错误、解析告警、导出物（准备包、答复 Word）——见下 |

**产品只有英文界面**（2026-09-23 决定，`zh.json` 与语言切换已删除）。制度文档
也按只有英文来设计——覆盖扫描判断「规范性条款」只认 shall/must/should，这是
这个决定的前提，不是缺陷。代码里残留的 `name_zh`、答复与导出接口的 `language=zh`
分支是旧数据与旧接口的兼容，界面不再用到。

第三类仍然是硬性的：**后端不产出给人读的句子**。业务错误带一个具体的 code
（`AppError(..., code="control.not_found")`，句子里有变量就再带 `params`），
解析告警带 code 存进 `documents.parse_warning_codes`。前端按 code 去 `en.json`
查词条，由 `make i18n-check` 把关（`make verify` 里有）。后端那句中文只是
**前端还没有这条词条时的兜底**，不是正式文案——用户看到它就是漏了词条。

一个例外：**模型输出校验的理由**（`app/llm/validation.py` 与各 `citations.py`
返回的字符串）写英文句子，不走 code。它的读者先是模型——重试时原样拼进纠错提示词，
code 对模型没有意义——其次才是在确认队列里看到它的审核人，界面上它套在一个有 code
的外壳里（`citation.check_failed`：「Citation check failed: {{reason}}」）。
Pydantic 校验失败一律经 `errors.describe_validation` 压成「字段: 问题」一行，
不要把 `str(ValidationError)` 交给前端。
`code=` 一律写**字面量**，别拼字符串——那条检查是静态扫的，拼出来的它看不见。两边都用 `i18n.exists` 而不是 `defaultValue`——`i18n.ts` 的
`parseMissingKeyHandler` 优先级更高，会把查不到的键显示成最后一段。

## 已知问题与决策记录

**已知问题与待办在根目录的 `KNOWN_ISSUES.md`**：上线前必做的事、文档切割能力的实测缺口、
暂不修的问题及理由。修掉一条就从里面删掉，并在提交说明里写清楚。

没有单独的设计文档。某处为什么这么写，理由写在它旁边的注释里——这个项目的注释
刻意写「为什么」而不只写「做什么」，改代码时理由变了就一起改。

判断一个问题还算不算数的尺子是：**换一批文档仍然成立的才算**（见下一节）。

## 不要把真实机构带进仓库

注释、测试、夹具、提交说明里**不写真实机构名，也不写制度原文**。实测过的外部语料一律
仅在来源公开且可核验时记录评测结果。内部语料即使用代称，也不记录数量、结构、
业务细节或评测统计；只保留通用技术机制。测试里的句子自己编，示例机构用 `Acme`。
`scripts/check_source_hygiene.py`（`make verify` 里有）按**形状**拦截——语料编号、
以 bank 结尾的专名、监管类通名——而不是按名单：把名字写进检测器，检测器自己就成了泄漏源。
它只认形状，单独出现的机构缩写拦不住，所以这条仍然要靠人守。

## 开发库的性质

解析与抽取的验证不能覆盖所有文档结构。**语料迟早
整批换掉**，所以「把开发库里这 N 条数据改对」没有长期价值——要改的是换一批文档仍然成立的
机制（解析器、提示词、配对逻辑、检测）。

## 一个反复出现的坑

切过分支后整个 spec 文件变红，先 `docker compose restart web` 再判断——
Docker 里的 vite 会因文件增删而失效，那通常不是回归。
