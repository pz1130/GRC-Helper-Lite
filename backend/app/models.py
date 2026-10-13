"""模型注册表——所有入口都必须导入它。

SQLAlchemy 只认识**已经 import 过**的模型。跨模块外键（如
documents.uploaded_by → users.id）是**惰性解析**的：configure_mappers()
不会报错，要到 flush 排表依赖顺序时才炸。

实测事故：worker 进程只 import 了 ingest/indexing 两个任务模块，从未
碰到 app.iam.models，于是 users 表不在 metadata 里，**每一份文档的解析
都在 flush 时失败**——而单元测试全绿，因为 conftest 会把所有模型都导进来。

所以：新增任何 models.py 都要在这里加一行，且每个入口（main / worker /
alembic）都导入本模块，而不是各自去 import 自己碰巧需要的那几个。
"""

from app.audit import models as audit_models
from app.clauses import models as clauses_models
from app.conflicts import models as conflict_models
from app.controls import models as controls_models
from app.decision import models as decision_models
from app.environment import models as environment_models
from app.evidence import models as evidence_models
from app.extraction import models as extraction_models
from app.frameworks import models as frameworks_models
from app.iam import models as iam_models
from app.ingest import models as ingest_models
from app.llm import models as llm_models
from app.maturity import models as maturity_models
from app.relations import models as relations_models
from app.review import models as review_models
from app.risk import models as risk_models
from app.search import models as search_models

__all__ = [
    "decision_models",
    "audit_models",
    "clauses_models",
    "conflict_models",
    "controls_models",
    "environment_models",
    "evidence_models",
    "extraction_models",
    "frameworks_models",
    "iam_models",
    "ingest_models",
    "llm_models",
    "maturity_models",
    "relations_models",
    "review_models",
    "risk_models",
    "search_models",
]
