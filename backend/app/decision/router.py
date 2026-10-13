"""判断角色配置与文档扫描：读取不外发，手动扫描需要文档写权限。"""

from typing import Annotated

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.decision.factory import ADAPTERS
from app.decision.service import ScanResult, read_scan, run_scan
from app.decision.settings import DecisionSettings, read_settings, save_settings
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import User
from app.iam.permissions import Permission

router = APIRouter(prefix="/api")
Session = Annotated[AsyncSession, Depends(get_session)]
Admin = Annotated[User, Depends(require(Permission.LLM_CONFIG_WRITE))]
Reader = Annotated[User, Depends(require(Permission.READ))]
Writer = Annotated[User, Depends(require(Permission.DOCUMENT_WRITE))]


@router.get("/settings/decision-model")
async def get_settings(_: Admin, session: Session) -> dict:
    settings = await read_settings(session)
    return {**settings.model_dump(), "provider_kinds": [k.value for k in ADAPTERS]}


@router.put("/settings/decision-model", response_model=DecisionSettings)
async def put_settings(payload: DecisionSettings, actor: Admin, session: Session) -> DecisionSettings:
    before = await read_settings(session)
    await save_settings(session, payload)
    await record(session, user=actor, action="decision.configure", entity_type="AppSetting", entity_id="decision_model",
                 before=before.model_dump(), after=payload.model_dump())
    await session.commit()
    return payload


@router.get("/documents/{document_id}/requirement-scan", response_model=ScanResult)
async def get_scan(document_id: int, _: Reader, session: Session) -> ScanResult:
    return await read_scan(session, document_id)


@router.post("/documents/{document_id}/requirement-scan", response_model=ScanResult)
async def post_scan(document_id: int, actor: Writer, session: Session) -> ScanResult:
    return await run_scan(session, document_id, actor)
