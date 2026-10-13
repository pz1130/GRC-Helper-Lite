# 框架种子数据

| 文件 | 来源 | 许可 |
|---|---|---|
| `nist-csf-2.0.csv` | NIST Cybersecurity Framework 2.0 | 美国政府作品，公有领域 |
| `nist-800-53-r5.csv` | NIST SP 800-53 Rev.5 + Rev.5 baselines | 美国政府作品，公有领域 |

ISO/IEC 27001:2022 Annex A **不在此目录**：其正文受 ISO 版权保护，不可随仓库分发。
请按 `app/frameworks/template.py` 的列定义自行准备后上传。

CSV 由 `convert_csf.py` / `convert_800_53.py` 从 NIST 官方发布文件生成，
脚本仅用于记录来源与可复现性，**不在运行时联网**。

当前生成输入：

- CSF 2.0 Reference Tool JSON：<https://csrc.nist.gov/extensions/nudp/services/json/csf/elements>
- SP 800-53 Rev.5 OSCAL catalog：<https://github.com/usnistgov/oscal-content/tree/main/nist.gov/SP800-53/rev5/json>
- SP 800-53 LOW / MODERATE / HIGH resolved baseline catalogs：同一 OSCAL 目录下对应的 `*-baseline-resolved-profile_catalog-min.json`

转换器会排除带 `withdraw_reason` / `status=withdrawn` 的条目，将 CSF Subcategory 的正文首句用作标题，并把 OSCAL 参数占位符替换为参数标签或 `organization-defined value`。
