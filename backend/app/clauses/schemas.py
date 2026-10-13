from pydantic import BaseModel, ConfigDict, Field


class ClauseTreeOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    number: str | None
    heading: str
    heading_path: str
    citation_label: str
    text: str
    level: int
    page_ref: int | None
    kind: str
    children: list["ClauseTreeOut"] = Field(default_factory=list)


class ClauseSplitIn(BaseModel):
    at: int


class ClauseMergeIn(BaseModel):
    into_id: int


class ClauseOut(BaseModel):
    """单条条款。抽屉要的是正文，不需要子树。"""

    model_config = ConfigDict(from_attributes=True)

    id: int
    document_id: int
    document_title: str
    number: str | None
    heading: str
    heading_path: str
    citation_label: str
    text: str
    level: int
    page_ref: int | None
