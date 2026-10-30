"""推理任务注册表——任务键的唯一出处。

在此之前任务键散落成各模块的私有常量（`EXTRACT_TASK_KEY` 在 extraction/prompts.py、
`MAPPING_TASK_KEY` 在 mapping/prompts.py……），而**给人看的那份清单只写在前端**
（`Providers.tsx` 的 TASK_KEYS）。两边没有任何约束关系，于是漂了：前端注释写
"八个推理任务，加上 embedding"＝9，实际列了 11 个。

两个字段都不是装饰：

- `capability` 决定一个 provider 能不能绑这个任务。**embedding 要的是向量模型**，
  把聊天模型绑上去不会报错，只会让文档解析后向量静默不生成、检索退化成纯关键字。
  批量绑定必须按 capability 分开，否则"一键应用"就是一键打瘫检索。
- `implemented` 记录"设计里有、后端还没实现"的任务。配置页照样会给未实现的任务
  一个绑定位，管理员绑完会以为这个能力已经有了——如实标出来，别假装它能用。
  `maturity_suggestion` 曾经是唯一一个（maturity 模块当时是纯计算，没有 prompts
  也没有消费者），现在补上了：app/maturity/{context,prompts,tasks}.py。
  这个字段目前全是 True——下一个只有设计没有实现的任务进来时，填 False。
"""

from dataclasses import dataclass
from typing import Literal

Capability = Literal["chat", "embedding", "decision"]


@dataclass(frozen=True)
class TaskSpec:
    key: str
    capability: Capability
    implemented: bool
    # False：不参与「一键绑定」。RCSA 的两个通道要单独批准、单独绑定（RCSA → AI 设置），
    # 一键把聊天模型绑上去等于绕过了那份数据外发批准。
    bulk: bool = True


TASK_SPECS: tuple[TaskSpec, ...] = (
    TaskSpec("requirement_decision", "decision", True),
    TaskSpec("control_extract", "chat", True),
    TaskSpec("framework_mapping", "chat", True),
    TaskSpec("relation_inference", "chat", True),
    TaskSpec("conflict_detection", "chat", True),
    TaskSpec("audit_prediction", "chat", True),
    TaskSpec("answer_generation", "chat", True),
    TaskSpec("evidence_suggestion", "chat", True),
    TaskSpec("query_expansion", "chat", True),
    TaskSpec("matrix_mapping", "chat", True),
    TaskSpec("maturity_suggestion", "chat", True),
    TaskSpec("embedding", "embedding", True),
    TaskSpec("rcsa_prefill", "chat", True, bulk=False),
    TaskSpec("rcsa_evidence_relevance", "decision", True, bulk=False),
)


def bulk_assignable_keys(capability: Capability) -> tuple[str, ...]:
    """批量绑定要覆盖的任务：只取该 capability 下**已实现**的。"""
    return tuple(
        spec.key for spec in TASK_SPECS if spec.capability == capability and spec.implemented and spec.bulk
    )
