from pydantic import BaseModel, Field


class GraphNode(BaseModel):
    """图上的一个点。控制点与框架项共用一种形状，前端只认 kind。"""

    key: str
    kind: str  # control | framework_item
    code: str
    title: str
    group: str | None = None
    group_extra: list[str] = Field(default_factory=list)
    functions: list[str] = Field(default_factory=list)
    pending_edges: int = 0
    is_gap: bool = False


class GraphEdge(BaseModel):
    """kind 表类型（着色），status 表状态（虚实），两者正交。"""

    key: str
    source: str
    target: str
    kind: str
    status: str  # confirmed | pending
    confidence: float | None = None
    rationale: str = ""
    proposal_id: int | None = None
    # 一条冲突边底下可能压着多处冲突点（同两份制度在两处打架）。
    conflict_count: int = 0


class GraphGroup(BaseModel):
    key: str
    kind: str  # document | function
    label: str


class GraphStats(BaseModel):
    nodes: int
    edges: int
    # 单独计数：合在一起会让人把提案数量读成事实数量。
    pending_edges: int
    truncated: bool


class GraphOut(BaseModel):
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    groups: list[GraphGroup] = Field(default_factory=list)
    stats: GraphStats
