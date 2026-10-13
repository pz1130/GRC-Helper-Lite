from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Path, Query, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.audit import service
from app.audit.export import build_docx
from app.audit.history import similar_history
from app.audit.models import AnswerDraft, AuditEngagement, AuditQuestion, QuestionStatus
from app.audit.preflight import build_preflight
from app.audit.schemas import (
    AnswerFinalizeIn,
    AnswerOut,
    AnswerUpdateIn,
    EngagementCreateIn,
    EngagementOut,
    GenerateAnswerIn,
    HistoryOut,
    PreflightOut,
    QuestionOut,
    QuestionsImportIn,
    SimilarHistoryOut,
)
from app.audit.xlsx import parse_questions
from app.db import get_session
from app.errors import AppError, Conflict, NotFound
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission
from app.llm.providers.base import ProviderError
from app.llm.validation import ValidationFailure
from app.matrix.template import MAX_FILE_BYTES, SheetRejected
from app.worker import enqueue

router = APIRouter(prefix="/api/audit", tags=["audit-assistant"])
Reader = Annotated[User, Depends(require(Permission.READ))]
Author = Annotated[User, Depends(require(Permission.ANSWER_DRAFT))]
Finalizer = Annotated[User, Depends(require(Permission.ANSWER_FINALIZE))]
Session = Annotated[AsyncSession, Depends(get_session)]


@router.get("/engagements", response_model=list[EngagementOut])
async def list_engagements(actor: Reader, session: Session) -> list[AuditEngagement]:
    statement = select(AuditEngagement).order_by(AuditEngagement.id.desc())
    if actor.engagement_scope_id is not None:
        statement = statement.where(AuditEngagement.id == actor.engagement_scope_id)
    return list(await session.scalars(statement))


@router.post("/engagements", response_model=EngagementOut, status_code=status.HTTP_201_CREATED)
async def create_engagement(
    payload: EngagementCreateIn, actor: Author, session: Session
) -> AuditEngagement:
    row = await service.create_engagement(session, payload.model_dump(), actor=actor)
    await session.commit()
    return row


@router.get("/engagements/{engagement_id}/questions", response_model=list[QuestionOut])
async def list_questions(
    engagement_id: Annotated[int, Path(gt=0)], actor: Reader, session: Session
) -> list[AuditQuestion]:
    service.require_engagement_scope(actor, engagement_id)
    if await session.get(AuditEngagement, engagement_id) is None:
        raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    return list(
        await session.scalars(
            select(AuditQuestion)
            .where(AuditQuestion.engagement_id == engagement_id)
            .order_by(AuditQuestion.seq)
        )
    )


@router.post(
    "/engagements/{engagement_id}/questions",
    response_model=list[QuestionOut],
    status_code=status.HTTP_201_CREATED,
)
async def import_questions(
    engagement_id: Annotated[int, Path(gt=0)],
    payload: QuestionsImportIn,
    actor: Author,
    session: Session,
) -> list[AuditQuestion]:
    rows = await service.import_questions(
        session, engagement_id, payload.text, payload.language, actor=actor
    )
    await session.commit()
    return rows


@router.post(
    "/engagements/{engagement_id}/questions/xlsx",
    response_model=list[QuestionOut],
    status_code=status.HTTP_201_CREATED,
)
async def import_questions_xlsx(
    engagement_id: Annotated[int, Path(gt=0)],
    file: Annotated[UploadFile, File()],
    actor: Author,
    session: Session,
) -> list[AuditQuestion]:
    if not (file.filename or "").lower().endswith(".xlsx"):
        raise AppError("仅支持 .xlsx Excel 文件", code="audit.xlsx_only")
    content = await file.read(MAX_FILE_BYTES + 1)
    try:
        questions = parse_questions(content)
    except SheetRejected as exc:
        # read_sheet 与 parse_questions 的拒绝都自带 code；原来一律改写成 import_rejected
        # 加中文 detail，code 丢在这里。
        raise AppError(str(exc), code=exc.code, params=exc.params) from exc
    except ValueError as exc:
        raise AppError(str(exc), code="audit.import_rejected", params={"detail": str(exc)}) from exc
    rows = await service.import_question_rows(
        session, engagement_id, questions, actor=actor, source="xlsx"
    )
    await session.commit()
    return rows


@router.post("/questions/{question_id}/generate")
async def generate_answer(
    question_id: Annotated[int, Path(gt=0)],
    payload: GenerateAnswerIn,
    actor: Author,
    session: Session,
) -> dict[str, int]:
    try:
        proposal = await service.generate_answer(
            session, question_id, payload.language, actor=actor
        )
        await session.commit()
        return {"proposal_id": proposal.id}
    except ValidationFailure as exc:
        await session.commit()
        raise AppError("模型未能生成可核验的原文引用，请重试", code="audit.citation_unverifiable") from exc
    except ProviderError as exc:
        await session.commit()
        raise AppError("AI 服务调用失败，请稍后重试或检查模型配置", code="audit.provider_failed") from exc


@router.post("/engagements/{engagement_id}/generate-all")
async def generate_all_answers(
    engagement_id: Annotated[int, Path(gt=0)], actor: Author, session: Session
) -> dict[str, str | int]:
    service.require_engagement_scope(actor, engagement_id)
    engagement = await session.get(AuditEngagement, engagement_id)
    if engagement is None:
        raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    pending_count = await session.scalar(
        select(func.count(AuditQuestion.id)).where(
            AuditQuestion.engagement_id == engagement_id,
            AuditQuestion.status == QuestionStatus.PENDING,
        )
    )
    if not pending_count:
        raise AppError("该审计项目没有待生成的问题", code="audit.no_pending_questions")
    # 双击或两人同时点：两个任务同时开始时都看不到对方的待审提案，会各生成一份（K4）。
    # 按项目固定 job id，同一项目的任务还在排队或在跑时 arq 不再投递，返回空串。
    # 真正投递失败（Redis 不可用）会抛异常，不会走到这里。
    job_id = await enqueue(
        "generate_engagement_answers", engagement_id, actor.id,
        job_id=f"generate-answers:{engagement_id}",
    )
    if not job_id:
        raise Conflict(
            "该审计项目的批量生成正在进行，请等它完成",
            code="audit.batch_already_running",
        )
    await record(
        session,
        user=actor,
        action="audit_answers.batch_enqueue",
        entity_type="AuditEngagement",
        entity_id=engagement_id,
        after={"job_id": job_id, "question_count": pending_count},
    )
    await session.commit()
    return {"job_id": job_id, "question_count": pending_count}


@router.get(
    "/questions/{question_id}/similar-history",
    response_model=list[SimilarHistoryOut],
)
async def get_similar_history(
    question_id: Annotated[int, Path(gt=0)], actor: Reader, session: Session
) -> list:
    question = await service.require_question_scope(session, actor, question_id)
    return await similar_history(
        session,
        question.question_text,
        exclude_question_id=question.id,
        scope_engagement_id=actor.engagement_scope_id,
        limit=3,
    )


@router.get("/engagements/{engagement_id}/preflight", response_model=PreflightOut)
async def get_preflight(
    engagement_id: Annotated[int, Path(gt=0)],
    actor: Reader,
    session: Session,
    language: Annotated[str, Query(pattern=r"^(zh|en)$")] = "en",
) -> dict:
    service.require_engagement_scope(actor, engagement_id)
    engagement = await session.get(AuditEngagement, engagement_id)
    if engagement is None:
        raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    return await build_preflight(session, engagement, language=language)


@router.get("/questions/{question_id}/answer", response_model=AnswerOut | None)
async def get_answer(
    question_id: Annotated[int, Path(gt=0)], actor: Reader, session: Session
) -> AnswerDraft | None:
    await service.require_question_scope(session, actor, question_id)
    return await session.scalar(select(AnswerDraft).where(AnswerDraft.question_id == question_id))


@router.patch("/answers/{answer_id}", response_model=AnswerOut)
async def update_answer(
    answer_id: Annotated[int, Path(gt=0)], payload: AnswerUpdateIn, actor: Author, session: Session
) -> AnswerDraft:
    answer = await session.get(AnswerDraft, answer_id, with_for_update=True, populate_existing=True)
    if answer is None:
        raise NotFound("答复不存在", code="audit.response_not_found")
    row = await service.update_answer(
        session, answer, payload.body, actor=actor, expected_body=payload.expected_body
    )
    await session.commit()
    return row


@router.post("/answers/{answer_id}/finalize", response_model=AnswerOut)
async def finalize_answer(
    answer_id: Annotated[int, Path(gt=0)], payload: AnswerFinalizeIn, actor: Finalizer, session: Session
) -> AnswerDraft:
    # 锁住这一行再比对：同时进来的保存要么排在前面（比对失败），要么排在后面（被定稿拒绝）。
    answer = await session.get(AnswerDraft, answer_id, with_for_update=True, populate_existing=True)
    if answer is None:
        raise NotFound("答复不存在", code="audit.response_not_found")
    row = await service.finalize_answer(session, answer, actor=actor, seen_body=payload.body)
    await session.commit()
    return row


@router.get("/history", response_model=list[HistoryOut])
async def history(actor: Reader, session: Session) -> list[dict]:
    statement = (
        select(AnswerDraft, AuditQuestion.question_text, AuditEngagement.name)
        .join(AuditQuestion, AuditQuestion.id == AnswerDraft.question_id)
        .join(AuditEngagement, AuditEngagement.id == AuditQuestion.engagement_id)
        .where(
            AnswerDraft.finalized_at.is_not(None), AuditQuestion.status == QuestionStatus.FINALIZED
        )
        .order_by(AnswerDraft.finalized_at.desc())
    )
    if actor.engagement_scope_id is not None:
        statement = statement.where(AuditEngagement.id == actor.engagement_scope_id)
    rows = await session.execute(statement)
    return [
        {
            **AnswerOut.model_validate(answer).model_dump(),
            "question_text": question,
            "engagement_name": engagement,
        }
        for answer, question, engagement in rows
    ]


@router.get("/engagements/{engagement_id}/export.docx")
async def export_engagement(
    engagement_id: Annotated[int, Path(gt=0)],
    actor: Reader,
    session: Session,
    language: Annotated[str, Query(pattern=r"^(zh|en)$")] = "en",
) -> StreamingResponse:
    service.require_engagement_scope(actor, engagement_id)
    engagement = await session.get(AuditEngagement, engagement_id)
    if engagement is None:
        raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    content = await build_docx(session, engagement, language=language)
    await record(
        session,
        user=actor,
        action="audit_engagement.export",
        entity_type="AuditEngagement",
        entity_id=engagement.id,
        after={"format": "docx", "language": language},
    )
    await session.commit()
    safe_name = quote(f"{engagement.name}-audit-responses.docx")
    return StreamingResponse(
        iter([content]),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{safe_name}"},
    )
