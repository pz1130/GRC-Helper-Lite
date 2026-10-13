"""确认过的映射产出的仍然是**待确认提案**，不是正式控制点（铁律 1）。"""

import hashlib
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.errors import Conflict, NotFound
from app.iam.models import AuditLog, User
from app.matrix.template import MAX_COLUMNS, MAX_ROWS, Sheet, SheetRejected, validate
from app.review.models import Proposal, ProposalKind, ProposalStatus

_SOURCE_KEYS = ("source_sha256", "source_headers", "source_rows")


def validate_mapping_proposal(proposal: Proposal, data: dict[str, Any]) -> dict[str, str]:
    """确认钩子：校验映射结构，以及与原始上传文件之间不可变的绑定。

    整行校验发生在**重新上传**时、在任何导入写入之前。确认的人可以只给
    mapping/notes，也可以把没改过的来源元数据原样回填。"""
    if proposal.kind != ProposalKind.MATRIX_MAPPING:
        raise SheetRejected("不是 Excel 列映射提案", code="matrix.not_mapping_proposal")
    original = proposal.payload
    if not isinstance(original, dict) or not isinstance(data, dict):
        raise SheetRejected("映射提案内容无效", code="matrix.proposal_payload_invalid")
    digest = original.get("source_sha256")
    headers = original.get("source_headers")
    count = original.get("source_rows")
    if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise SheetRejected("映射缺少原始文件摘要，请重新上传", code="matrix.proposal_source_invalid")
    if (not isinstance(headers, list) or not 1 <= len(headers) <= MAX_COLUMNS
            or not all(isinstance(h, str) and h for h in headers)
            or len(set(headers)) != len(headers)):
        raise SheetRejected("映射原始表头无效", code="matrix.proposal_source_invalid")
    if type(count) is not int or not 1 <= count <= MAX_ROWS:
        raise SheetRejected("映射原始行数无效", code="matrix.proposal_source_invalid")
    for key in _SOURCE_KEYS:
        if key in data and data[key] != original[key]:
            raise SheetRejected(
                f"不能修改原始文件绑定 {key}", code="matrix.source_binding_changed", params={"key": key},
            )
    if set(data) - {"mapping", "notes", "confidence", *_SOURCE_KEYS}:
        raise SheetRejected("映射包含不支持的字段", code="matrix.proposal_unknown_fields")
    mapping = data.get("mapping")
    # 用一行占位数据验映射的形状，不必重新读一遍源数据。
    report = validate(Sheet(headers, [["sample"] * len(headers)]), mapping)
    if report:
        # 报告可能有好几条，错误只能带一个 code：给第一条。全部问题看「校验」。
        raise report[0].rejected()
    return dict(mapping)


async def import_rows(
    session: AsyncSession, sheet: Sheet, mapping: dict[str, str], *, actor_id: int,
    mapping_proposal_id: int, source_sha256: str,
) -> dict[str, Any]:
    """内部的确定性转换；鉴权与事务由调用方负责。"""
    report = validate(sheet, mapping)
    if report:
        raise report[0].rejected()
    index = {field: sheet.headers.index(column) for field, column in mapping.items()}
    proposals: list[Proposal] = []
    for ordinal, row in enumerate(sheet.rows, start=1):
        payload = {
            field: row[position].strip() if position < len(row) else ""
            for field, position in index.items()
        }
        payload.update(
            origin="matrix", matrix_mapping_proposal_id=mapping_proposal_id,
            source_sha256=source_sha256, row_number=ordinal, citations=[],
        )
        proposal = Proposal(
            kind=ProposalKind.CONTROL_EXTRACT, status=ProposalStatus.PENDING,
            payload=payload, citations=[], confidence=None, llm_call_id=None,
        )
        session.add(proposal)
        proposals.append(proposal)
    await session.flush()
    result = {"imported": len(proposals), "proposal_ids": [p.id for p in proposals],
              "mapping_proposal_id": mapping_proposal_id}
    session.add(AuditLog(
        user_id=actor_id, action="matrix.import", entity_type="Proposal",
        entity_id=str(mapping_proposal_id), after=result,
    ))
    await session.flush()
    return result


async def import_confirmed(
    session: AsyncSession, sheet: Sheet, content: bytes, *, proposal_id: int,
    actor: User, mapping: dict[str, str] | None = None,
) -> dict[str, Any]:
    proposal = await session.scalar(
        select(Proposal).where(Proposal.id == proposal_id).with_for_update()
        .execution_options(populate_existing=True)
    )
    if proposal is None:
        raise NotFound("列映射提案不存在", code="matrix.proposal_not_found")
    if proposal.kind != ProposalKind.MATRIX_MAPPING or proposal.status not in {
        ProposalStatus.ACCEPTED, ProposalStatus.MODIFIED,
    }:
        raise Conflict("只能导入已确认的 Excel 列映射提案", code="matrix.proposal_not_confirmed")
    data = (proposal.decided_payload if proposal.status == ProposalStatus.MODIFIED
            else proposal.payload)
    confirmed = validate_mapping_proposal(proposal, data)
    if mapping is not None and mapping != confirmed:
        raise Conflict("导入映射必须与已确认的映射完全一致", code="matrix.mapping_mismatch")
    digest = hashlib.sha256(content).hexdigest()
    original = proposal.payload
    if (digest != original["source_sha256"] or sheet.headers != original["source_headers"]
            or len(sheet.rows) != original["source_rows"]):
        raise Conflict("上传文件与列映射提案的原始文件不一致", code="matrix.file_mismatch")
    report = validate(sheet, confirmed)
    if report:
        raise report[0].rejected()
    # 锁住父行把重试串起来，审计记录与它的子提案同一个事务落盘。
    # 原始 payload 与裁定后的 payload 都原样保留，不做改写。
    previous = await session.scalar(select(AuditLog).where(
        AuditLog.action == "matrix.import", AuditLog.entity_type == "Proposal",
        AuditLog.entity_id == str(proposal.id),
    ).order_by(AuditLog.id).limit(1))
    if previous is not None:
        return dict(previous.after)
    return await import_rows(
        session, sheet, confirmed, actor_id=actor.id,
        mapping_proposal_id=proposal.id, source_sha256=digest,
    )
