from pydantic import BaseModel


class PreviousDocumentOut(BaseModel):
    id: int
    title: str
    version: str | None


class ClauseChangeOut(BaseModel):
    clause_id: int
    citation_label: str
    text: str


class MatchedClauseOut(BaseModel):
    old_clause_id: int
    new_clause_id: int
    citation_label: str
    text: str
    old_text: str
    changed: bool


class AffectedControlOut(BaseModel):
    id: int
    code: str
    title: str


class AffectedMappingOut(BaseModel):
    id: int
    control_id: int
    control_code: str
    framework_item_code: str


class AffectedEvidenceOut(BaseModel):
    id: int
    control_id: int
    control_code: str
    title: str


class ImpactOut(BaseModel):
    previous_document: PreviousDocumentOut
    parser_generation_mismatch: bool
    added: list[ClauseChangeOut]
    removed: list[ClauseChangeOut]
    matched: list[MatchedClauseOut]
    affected_controls: list[AffectedControlOut]
    affected_mappings: list[AffectedMappingOut]
    affected_evidence: list[AffectedEvidenceOut]
