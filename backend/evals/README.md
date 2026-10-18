# 评估集（K15 / K16 / G4）

固定、可复现，全部用仓库自带的公开数据：

| 套件 | 数据 | 指标 | 需要模型 |
|---|---|---|---|
| `prefilter` | NIST CSF 2.0 × OLIR 中出现的 191 个 800-53 基础控制项（陈述截 250 字符），716 个 OLIR 正例 | 预筛候选召回 recall@10/20/40（关键词、向量、融合） | 关键词一路不需要；`--vectors` 需要 embedding |
| `mapping` | 同上，走应用的 `run_mapping` 全流程 | 批次校验通过率、对 OLIR 的精确率/召回/F1、引文修复次数、token | 是 |
| `extraction` | 用 800-53 基础控制项原文拼成的 Word 制度（4 个控制族 × 4 条），走 `run_parse` + `run_extraction` | 批次校验通过率、引文逐字率、引文修复次数、token | 是 |

```bash
cd backend
python -m evals.harness prefilter                      # 离线，零成本
GRC_EVAL_LLM_API_KEY=... python -m evals.harness prefilter --vectors
GRC_EVAL_LLM_API_KEY=... python -m evals.harness mapping --label after
GRC_EVAL_LLM_API_KEY=... python -m evals.harness extraction --label after [--retries 3]
```

- key 只从环境变量读，不写进库以外的任何地方；评估库里的 provider key 照常用 `APP_SECRET_KEY` 加密。
- 每次运行新建独立库 `grc_eval_<suite>_<label>`（默认连 `localhost:55432`，用 `GRC_EVAL_DB_ADMIN_URL` 改）。
- 映射套件可选 `--top-k N`（候选数）、`--items-per-batch N`（每批框架项数），只在评估进程里替换，不改产品默认值。
- 结果写进 `evals/results/<suite>-<label>.json`；映射报告里 `mapping` 计全部提案，`mapping_closing`
  只计 full/partial——覆盖度只认这两档，supporting 不关缺口。映射套件另写一份逐对明细
  `mapping-<label>-pairs.json`：`missed`（OLIR 有、模型没给，`in_candidates` 区分模型见过没选与预筛没给）
  与 `extra`（模型给了、OLIR 没有，带强度与理由）。
- **人工抽看（K16）**：`python -m evals.review_sheet evals/results/mapping-<label>-pairs.json out.xlsx`
  按固定种子抽样，生成带下拉判断列的审阅表；汇总页按判断自动计数。
- **改动前后对比**：在旧代码的工作树里跑同一个 harness，
  `cd <旧代码>/backend && PYTHONPATH=<本仓库>/backend python -m evals.harness mapping --label before`。

**Jev（判断模型）实验**：`python -m evals.jev prefilter|verify`，key 读 `JEV_API_KEY`
（回退 `TYPESAFE_API_KEY`）；端到端用 `python -m evals.harness mapping --candidates jev`
（复用 `evals/.cache/jev-scores.json`）。只发公开 NIST 文本。

**局限：**
- OLIR 只收了一部分真实关系，也收了一些牵强的关联，绝对数字偏悲观，适合看方向（同 K17）。
- 抽取语料是 NIST 原文拼成的，不是真实机构制度；关键要求召回需要人工黄金集
  （`tests/test_extraction_quality.py`），这里不评估。
- 模型输出有随机性：单次运行的差异在几个百分点以内时不能当作改进。
