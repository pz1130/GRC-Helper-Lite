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
  、`extra`（模型给了、OLIR 没有）与 `hit`（两边都有），后两者带模型给的强度与理由。
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


## Segmentation eval (`evals/segmentation/`)

Measures how well import splits a document into clauses.

```
cd backend && export PYTHONPATH=$PWD
python -m evals.segmentation.corpus /tmp/segcorpus          # 9 docs -> .docx + .pdf + .gold.json (needs soffice)
python -m evals.segmentation.score  /tmp/segcorpus evals/results/segmentation-after.json
```

Per-document score = mean of
- **structure F1**: a predicted section counts when its heading matches a gold clause
  and its parent heading matches too;
- **content attribution**: share of gold sentences / table cells found (whitespace-normalised)
  in the text of their own clause.

Overall = mean over the 18 documents (also reported for en / zh).

| run | overall | structure F1 | content | en | zh |
|---|---|---|---|---|---|
| before (`segmentation-before.json`) | 0.461 | 0.431 | 0.490 | 0.664 | 0.298 |
| after (`segmentation-after.json`)   | 0.993 | 0.993 | 0.993 | 0.984 | 1.000 |

## Real-document eval (`evals/real/`)

50 份公开制度/法规原文（来源、许可、URL 见 `real/manifest.csv`；原文件和中间产物在 `real/docs/`、`real/work/`，不入库，可用 `python -m evals.real.collect` 重新下载）。

| 维度 | 份数 |
|---|---|
| 中文 / 英文 | 35 / 15 |
| PDF / DOCX | 31 / 19 |
| 金融监管规章 / 公司制度 / 模板手册 / 法律法规 | 23 / 12 / 11 / 4 |

**标准答案 v2（与解析器无关，2026-10-29）**：
- 来源只有文档文本：`extract.py` 用 pdftotext / soffice 转 txt（不经过项目解析器）；`label.py` 用 MiniMax（CN）每 220 行一段（重叠 15 行）标出标题行、编号、层级，429 指数退避，空回答重试；同文件里的规则启发式（第X章/节/条、PART/CHAPTER/Article、4.1.1 编号）只做交叉核对。
- 独立性保障：`extract/label/gold/review_view` 不 import `app`（`tests/test_real_gold_independence.py` 用 AST 检查）；不读取 main 或分支的解析结果；v1 金标/复核（曾参考解析器分歧）已归档到 `real/gold/v1/`、`real/review/v1/`，v2 缓存全部重建于 `work/v2/`。只有 `score.py` 调解析器。
- **50 份全部人工逐份复核**（`python -m evals.real.review_view <id>` 对照原文看 LLM/规则分歧、编号连续性、层级），每份记录在 `real/review/<id>.json`（reviewed + 说明 + drop/add/set/add_rule/replace）。主要修正：GDPR 补回 LLM 漏掉的一段（Art. 31–37、Ch. IV Sec. 2–4）；UK MLR 重建 PART/CHAPTER 层级；securechems 因 LLM 多次空回答改为全手工标注；若干漏标的 Revision History / 条。
- 约定：编号段落（4.1.1）算条款；条内（一）、1、 列项、表单问题、正文 1. 2. 3. 清单不算；「章>节>条」「Chapter>Section>Article」中条挂在节下。
- 局限：单一 LLM + 单一复核者（机器人，非领域专家）；文本抽取伪影（GDPR 小节标题字距、PDF 折行）会进入金标标题文本；v2 数字与 v1（96.3%）不可直接比较。

评分：`python evals/real/score.py results/real-after-v2.json`，单份 = (结构 F1 + 内容覆盖)/2，层级敏感。BEFORE = main 9076849，同一评分器、同一 v2 金标（`results/real-before-v2.json`）。

| v2 | BEFORE (main) | AFTER |
|---|---|---|
| 总体 | 18.8% | **96.6%**（第四轮前 95.0%，边界轮前 93.7%） |
| 中文 / 英文 | 0.1% / 62.3% | 98.4% / 92.5% |
| PDF / DOCX | 21.3% / 14.8% | 95.3% / 98.7% |
| 金融监管 / 公司制度 / 模板 / 法律 | 0.2% / 7.2% / 71.1% / 16.7% | 98.0% / 99.1% / 91.0% / 96.3% |

最弱：en-cisa-securechems 0.39、en-nist-csf2 0.53、en-sans-breach 0.56、en-eu-gdpr 0.69、en-cisa-vdp 0.78、en-uk-mlr 0.80。边界轮后：en-sans-aup 0.973、en-nist-csf2 0.792、en-eu-gdpr 0.963、en-sans-breach 0.575，其余不变，结果 `results/real-after-v3.json`。第四轮（2026-10-30）：en-uk-mlr 0.916、en-uk-dpcharges 0.974、en-eu-gdpr 0.991、en-cisa-vdp 0.885、en-cisa-securechems 0.863，结果 `results/real-after-v4.json`。v1（旧金标）结果保留在 `results/real-after.json`，仅供参考。

剩余失败见 `KNOWN_ISSUES.md`。
