from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ConflictPayload(BaseModel):
    """模型产出、并存进 Proposal.payload 的一条冲突。"""

    clause_a_id: int = Field(gt=0)
    clause_b_id: int = Field(gt=0)
    topic: str = Field(min_length=1, max_length=200)
    difference: str = Field(min_length=10, max_length=2000)
    quote_a: str = Field(min_length=1)
    quote_b: str = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)


class ConflictSideOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    clause_id: int
    document_id: int
    document_title: str
    citation_label: str
    text: str


class ConflictOut(BaseModel):
    id: int
    topic: str
    difference: str
    confidence: float | None
    created_at: datetime
    side_a: ConflictSideOut
    side_b: ConflictSideOut
