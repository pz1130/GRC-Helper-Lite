"""RCSA 写命令共用的几步：准入、加锁、版本检查、幂等。

**准入**：模块开关关闭时整个 /api/rcsa（status 除外）404；审计范围账号（外部审计员）
一律 403——包括只读、统计和嵌套详情。部署仍然禁止外部账号，这里不修全站的隔离问题。

**锁顺序** campaign → item → risk/action。活动转换对活动行 FOR UPDATE；子项写入对活动行
FOR SHARE 再锁子项，所以关闭与编辑不会同时成功，而同一活动里不同评估项的作答互不阻塞。

**幂等**：一次性命令带 `Idempotency-Key`。命令键 = actor + 命令 + 目标 + 客户端键，请求
体的哈希一起记进审计事件。**在拿到目标锁之后**再查键：并发的同一请求排队等锁，后到的
那个看到先到者留下的事件，直接重放结果而不是撞上「状态已变」。同键不同请求体是 409。
"""

import hashlib
import json
from typing import Annotated, Any

from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.errors import AppError, Conflict, Forbidden, NotFound
from app.iam.audit import record
from app.iam.deps import require
from app.iam.models import AuditLog, User
from app.iam.permissions import Permission, has_permission


async def module_enabled() -> None:
    if not get_settings().rcsa_enabled:
        raise NotFound("RCSA 模块未启用", code="rcsa.disabled")


def _actor(perm: Permission):
    async def _dep(user: Annotated[User, Depends(require(perm))]) -> User:
        if user.engagement_scope_id is not None:
            raise Forbidden("审计范围账号不能访问 RCSA", code="rcsa.scoped_account")
        return user

    return _dep


Reader = Annotated[User, Depends(_actor(Permission.READ))]
Manager = Annotated[User, Depends(_actor(Permission.RCSA_MANAGE))]
Responder = Annotated[User, Depends(_actor(Permission.RCSA_RESPOND))]
ReviewerUser = Annotated[User, Depends(_actor(Permission.RCSA_REVIEW))]
RiskApprover = Annotated[User, Depends(_actor(Permission.RCSA_RISK_ACCEPT))]
ActionUpdater = Annotated[User, Depends(_actor(Permission.RCSA_ACTION_UPDATE))]
# 配置 AI 通道（绑定服务、登记批准依据、选外发输入）与配置 provider 同一把权限。
AiAdmin = Annotated[User, Depends(_actor(Permission.LLM_CONFIG_WRITE))]


async def idempotency_key(
    key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=200)] = None,
) -> str:
    if not key or not key.strip():
        raise AppError("这个操作需要 Idempotency-Key 请求头", code="rcsa.idempotency_key_required")
    return key.strip()


ClientKey = Annotated[str, Depends(idempotency_key)]


def ensure(user: User, perm: Permission) -> None:
    """对象级检查之前的角色检查：路由入口用的是最低权限，具体动作在这里再要一次。"""
    if not has_permission(user.role, perm):
        raise Forbidden(
            f"当前角色无权执行该操作（需要 {perm.value}）",
            code="auth.permission_required",
            params={"permission": perm.value},
        )


async def lock[Row](
    session: AsyncSession, model: type[Row], row_id: int, *, code: str, share: bool = False
) -> Row:
    """按主键加行锁取行；populate_existing 让会话里的旧对象被锁后读到的值覆盖。"""
    row = await session.scalar(
        select(model)
        .where(model.id == row_id)  # type: ignore[attr-defined]
        .with_for_update(read=share)
        .execution_options(populate_existing=True)
    )
    if row is None:
        raise NotFound("对象不存在", code=code)
    return row


async def fetch[Row](session: AsyncSession, model: type[Row], row_id: int, *, code: str) -> Row:
    row = await session.get(model, row_id)
    if row is None:
        raise NotFound("对象不存在", code=code)
    return row


def check_version(row: Any, expected: int) -> None:
    if row.version != expected:
        raise Conflict(
            "这条记录已被别人改过，请重新加载后再操作",
            code="rcsa.version_conflict",
            params={"expected": expected, "current": row.version},
        )


def bump(row: Any, actor: User) -> None:
    row.version += 1
    row.updated_by = actor.id


def request_hash(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(canonical.encode()).hexdigest()


class Command:
    """一次幂等命令：先 `replay()` 看是否已执行过，执行完 `done()` 写审计事件。"""

    def __init__(
        self, actor: User, name: str, entity_type: str, entity_id: int, client_key: str,
        payload: dict[str, Any],
    ) -> None:
        self.actor = actor
        self.action = f"rcsa.{name}"
        self.entity_type = entity_type
        self.entity_id = entity_id
        self.key = f"{actor.id}:{name}:{entity_type}:{entity_id}:{client_key}"
        self.hash = request_hash(payload)

    async def replay(self, session: AsyncSession) -> int | None:
        """已执行过就返回当时的结果 id；同键不同请求体是冲突。"""
        previous = await session.scalar(
            select(AuditLog).where(
                AuditLog.action.like("rcsa.%"), AuditLog.after["command_key"].astext == self.key
            )
        )
        if previous is None:
            return None
        if previous.after.get("request_hash") != self.hash:
            raise Conflict(
                "这个幂等键已经用于另一个不同的请求",
                code="rcsa.idempotency_key_reused",
            )
        return int(previous.after["result_id"])

    async def done(self, session: AsyncSession, result_id: int, after: dict[str, Any]) -> None:
        await record(
            session,
            user=self.actor,
            action=self.action,
            entity_type=self.entity_type,
            entity_id=self.entity_id,
            after=after | {"command_key": self.key, "request_hash": self.hash, "result_id": result_id},
        )
        await session.flush()


async def audit(
    session: AsyncSession, actor: User, action: str, entity_type: str, entity_id: int,
    after: dict[str, Any], before: dict[str, Any] | None = None,
) -> None:
    """非幂等命令的审计：只放 id、版本、状态、字段名和理由，不放回答或证据正文。"""
    await record(
        session, user=actor, action=f"rcsa.{action}", entity_type=entity_type,
        entity_id=entity_id, before=before, after=after,
    )
