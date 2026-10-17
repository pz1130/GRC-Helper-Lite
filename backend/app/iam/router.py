from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, status
from sqlalchemy import func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import AppError, Conflict, NotFound, Unauthorized
from app.iam import throttle
from app.iam.audit import record
from app.iam.deps import CurrentUser, require
from app.iam.models import AuditLog, User
from app.iam.permissions import Permission, Role
from app.iam.schemas import (
    AuditLogOut,
    LoginIn,
    LoginOut,
    PasswordChangeIn,
    UserCreateIn,
    UserOut,
    UserUpdateIn,
)
from app.iam.security import (
    create_access_token,
    dummy_verify,
    hash_password,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginOut)
async def login(
    payload: LoginIn, request: Request, session: AsyncSession = Depends(get_session)
) -> LoginOut:
    # 限流在校验之前：被冷却的账号连"密码对不对"都不该问，否则冷却期内仍能
    # 用响应差异继续试。未知账号同样计数——否则"没被限流"本身就泄露了它不存在。
    ip = request.client.host if request.client else None
    await throttle.check(payload.email, ip)

    user = await session.scalar(select(User).where(User.email == payload.email))

    # 统一的失败信息，不区分"用户不存在"与"密码错误"，避免账号枚举。
    # 账号不存在时也必须跑一次等价成本的校验：只统一文案不统一耗时，
    # 攻击者仍能用响应时间把有效邮箱扫出来。
    async def refuse() -> None:
        await throttle.record_failure(payload.email, ip)
        raise Unauthorized("邮箱或密码不正确", code="auth.bad_credentials")

    if user is None:
        dummy_verify(payload.password)
        await refuse()
    elif not verify_password(payload.password, user.password_hash) or not user.is_active or user.expires_at is not None and user.expires_at <= datetime.now(UTC):
        await refuse()

    assert user is not None
    await throttle.clear(payload.email)
    return LoginOut(
        access_token=create_access_token(user.id, user.role, user.token_version),
        user=UserOut.model_validate(user),
    )


@router.get("/me", response_model=UserOut)
async def me(user: CurrentUser) -> UserOut:
    return UserOut.model_validate(user)


@router.post("/password", response_model=LoginOut)
async def change_own_password(
    payload: PasswordChangeIn,
    request: Request,
    user: CurrentUser,
    session: Annotated[AsyncSession, Depends(get_session)],
) -> LoginOut:
    """自己改密码。其他设备上的会话随之失效；这个会话换一张新令牌继续用。"""
    # 与登录共用限流：拿着偷来的会话，不能靠这里无限次猜当前密码。
    ip = request.client.host if request.client else None
    await throttle.check(user.email, ip)
    if not verify_password(payload.current_password, user.password_hash):
        await throttle.record_failure(user.email, ip)
        # 不能用 401：前端见 401 就登出，输错一次当前密码不该有这个后果。
        raise AppError("当前密码不正确", code="auth.current_password_incorrect")
    await throttle.clear(user.email)

    user.password_hash = hash_password(payload.new_password)
    user.token_version += 1
    await session.flush()
    await record(
        session,
        user=user,
        action="user.password_change",
        entity_type="User",
        entity_id=user.id,
        after={"password_changed": True},
        ip=ip,
    )
    await session.commit()
    return LoginOut(
        access_token=create_access_token(user.id, user.role, user.token_version),
        user=UserOut.model_validate(user),
    )


users_router = APIRouter(prefix="/api/users", tags=["users"])


def _snapshot(user: User) -> dict:
    return {
        "email": user.email,
        "name": user.name,
        "role": user.role.value,
        "is_active": user.is_active,
        "expires_at": user.expires_at.isoformat() if user.expires_at else None,
        "engagement_scope_id": user.engagement_scope_id,
    }


# 「最后一名管理员」检查的事务级锁（K6）。值是 "grc_admn" 的 ASCII，与 budget.py 的
# BUDGET_LOCK_KEY 同一种取法，免得和别处的数字键撞上。
ADMIN_LOCK_KEY = 0x6772635F61646D6E


async def _would_orphan_admin(
    session: AsyncSession, target: User, data: dict
) -> bool:
    """这次改动会不会让系统失去最后一名启用的管理员。

    覆盖两种走法：把自己降级/停用，以及把别人降级/停用——真正要守的
    不变量是"系统里始终至少有一名启用的 admin"，不只是"别动自己"。
    """
    now = datetime.now(UTC)

    def usable(expires_at: datetime | None) -> bool:
        return expires_at is None or expires_at > now

    # 「可用」= 启用且没过有效期：只看启用状态时，最后一名管理员给自己设个已经过去
    # 的到期日，系统里就没有能登录的管理员了。
    if not (target.role is Role.ADMIN and target.is_active and usable(target.expires_at)):
        return False

    stays_admin = data.get("role", target.role) is Role.ADMIN
    stays_active = data.get("is_active", target.is_active)
    stays_unexpired = usable(data.get("expires_at", target.expires_at))
    if stays_admin and stays_active and stays_unexpired:
        return False

    # 只在「这次会少一名可用管理员」时才锁：两名管理员同时互相降级，不锁的话两边
    # 都数到「还有另一个」，都通过。锁持有到事务提交，后到的那个数到的是提交后的人数。
    await session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": ADMIN_LOCK_KEY})
    others = await session.scalar(
        select(func.count())
        .select_from(User)
        .where(
            User.role == Role.ADMIN,
            User.is_active.is_(True),
            (User.expires_at.is_(None)) | (User.expires_at > now),
            User.id != target.id,
        )
    )
    return not others


@users_router.get("", response_model=list[UserOut])
async def list_users(
    _: User = Depends(require(Permission.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> list[User]:
    return list(await session.scalars(select(User).order_by(User.id)))


@users_router.post("", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(
    payload: UserCreateIn,
    request: Request,
    actor: User = Depends(require(Permission.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> User:
    if payload.engagement_scope_id is not None:
        from app.audit.models import AuditEngagement

        if await session.get(AuditEngagement, payload.engagement_scope_id) is None:
            raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    user = User(
        email=payload.email,
        name=payload.name,
        role=payload.role,
        password_hash=hash_password(payload.password),
        expires_at=payload.expires_at,
        engagement_scope_id=payload.engagement_scope_id,
    )
    session.add(user)
    try:
        await session.flush()
    except IntegrityError as exc:
        # 不回滚的话 session 停在失效态，这个请求里后续任何一次用它都会炸。
        await session.rollback()
        raise Conflict("该邮箱已存在", code="user.email_exists") from exc

    await record(
        session,
        user=actor,
        action="user.create",
        entity_type="User",
        entity_id=user.id,
        after=_snapshot(user),
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    return user


@users_router.patch("/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    payload: UserUpdateIn,
    request: Request,
    actor: User = Depends(require(Permission.USER_MANAGE)),
    session: AsyncSession = Depends(get_session),
) -> User:
    user = await session.get(User, user_id)
    if user is None:
        raise NotFound("用户不存在", code="user.not_found")

    before = _snapshot(user)
    data = payload.model_dump(exclude_unset=True)
    if data.get("engagement_scope_id") is not None:
        from app.audit.models import AuditEngagement

        if await session.get(AuditEngagement, data["engagement_scope_id"]) is None:
            raise NotFound("审计项目不存在", code="audit.engagement_not_found")
    if await _would_orphan_admin(session, user, data):
        raise Conflict("系统必须保留至少一名启用的管理员，无法执行该改动", code="user.last_active_admin")
    password_reset = "password" in data
    if password_reset:
        user.password_hash = hash_password(data.pop("password"))
        # 已经发出去的令牌（包括可能被偷走的）随之作废。
        user.token_version += 1
    for field, value in data.items():
        setattr(user, field, value)
    await session.flush()

    after = _snapshot(user)
    # 快照里没有密码（也不该有），只改密码时 before 与 after 一模一样，
    # 审计里就看不出发生过什么。记一个标记，不记哈希。
    if password_reset:
        after["password_reset"] = True
    await record(
        session,
        user=actor,
        action="user.update",
        entity_type="User",
        entity_id=user.id,
        before=before,
        after=after,
        ip=request.client.host if request.client else None,
    )
    await session.commit()
    if password_reset:
        # 忘了密码的人往往已经把自己试到冷却；不清的话新密码还要再等一个窗口。
        # 只清账号计数，IP 计数的理由见 throttle.clear。
        await throttle.clear(user.email)
    return user


audit_router = APIRouter(prefix="/api/audit-log", tags=["audit-log"])


@audit_router.get("", response_model=list[AuditLogOut])
async def list_audit_log(
    entity_type: str | None = None,
    user_id: int | None = None,
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    _: User = Depends(require(Permission.AUDIT_LOG_READ)),
    session: AsyncSession = Depends(get_session),
) -> list[AuditLog]:
    stmt = select(AuditLog).order_by(AuditLog.id.desc()).limit(min(limit, 500)).offset(offset)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if user_id:
        stmt = stmt.where(AuditLog.user_id == user_id)
    return list(await session.scalars(stmt))
