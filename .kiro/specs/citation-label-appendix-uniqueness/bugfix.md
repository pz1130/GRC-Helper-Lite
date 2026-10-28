# Bugfix Requirements Document

## Introduction

一个条款的 `citation_label` 在单个文档内可能并不唯一。当附录（或任意章节）重启编号，使其数字标签与正文条款编号相撞时，就会出现重复的引用标签。例如正文有条款 "1"，某个附录也从 "1" 重新开始，二者的 `citation_label` 都变成 "1"。审计人员引用"条款 1"时，无法区分指的是正文的"1"还是附录的"1"。

缺陷位于共享的扁平化步骤 `backend/app/parsing/flatten.py` 中：`build_citation_label(number, heading_path)` 直接返回 `number`（如 "1"、"A.1"），而 `flatten()` 在遍历条款树逐行写入 `citation_label` 时没有做任何文档级的唯一性检查。由于修复位于共享步骤，它同时覆盖 PDF 与 Word 两条解析路径；虽然 KNOWN_ISSUES P10 标题提到 Word，但撞号可能由任何重启的编号作用域引起，因此修复必须是通用的（文档级唯一性），而非 Word 专属。

本次修复的目标：扁平化后，文档内每个条款的 `citation_label` 都必须唯一。当两个或多个带编号的条款会共享同一数字标签时，每个相撞的标签用其最近的未编号祖先标题加以限定（qualify），使标签可区分；数字标签本就唯一的条款保持不变。

## Bug Analysis

### Current Behavior (Defect)

附录或任意章节重启编号、与正文或其他作用域的编号相撞时，当前系统产出重复的引用锚点。

1.1 WHEN 文档内两个或多个带编号的条款经扁平化后得到相同的数字 `citation_label`（如正文条款 "1" 与附录条款 "1"）THEN the system 为这些不同条款写入相同的 `citation_label`，使引用锚点在文档内不唯一
1.2 WHEN 附录（或任意章节）重启编号导致其数字标签与正文条款编号重叠 THEN the system 不对相撞标签做任何限定，审计人员无法从 `citation_label` 区分所引用的是哪一个条款

### Expected Behavior (Correct)

扁平化后，文档内每个条款的 `citation_label` 都应唯一且可区分。

2.1 WHEN 文档内两个或多个带编号的条款经扁平化后会得到相同的数字 `citation_label` THEN the system SHALL 用每个相撞条款最近的未编号祖先标题限定其 `citation_label`，使这些标签在文档内彼此可区分
2.2 WHEN 一个相撞条款向上查找其最近的未编号祖先 THEN the system SHALL 选取从该节点向上、第一个没有编号的祖先标题（如附录/章节标题通常未编号）用于限定；若不存在任何未编号祖先，the system SHALL 回退到一个确定性的限定方式（design 阶段确定具体限定格式与回退策略），使标签仍然唯一
2.3 WHEN 同一文档被反复扁平化 THEN the system SHALL 以顺序稳定、确定性的方式产出相同的 `citation_label`（引用标签是审计锚点，稳定性至关重要）

### Unchanged Behavior (Regression Prevention)

修复只影响相撞的标签；其余行为必须逐字保持不变。

3.1 WHEN 一个带编号条款的数字标签在文档内本就唯一 THEN the system SHALL CONTINUE TO 把该裸编号用作 `citation_label`（不得限定不相撞的标签——审计人员依赖 "3.4.2" 继续是 "3.4.2"）
3.2 WHEN 一个条款没有编号（`number` 为 None）且其 `heading_path` 在文档内不相撞 THEN the system SHALL CONTINUE TO 回退使用 `heading_path` 作为 `citation_label`
3.3 WHEN 文档内不存在任何编号撞号 THEN the system SHALL CONTINUE TO 产出与修复前逐字节一致的输出
3.4 WHEN 条款被扁平化 THEN the system SHALL CONTINUE TO 保持 `number`、`heading`、`heading_path`、`order_index`、树结构、出处关联及所有其他 Clause 字段不变——只有 `citation_label` 改变，且只对相撞标签改变
