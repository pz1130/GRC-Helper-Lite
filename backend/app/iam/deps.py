from collections.abc import Callable, Coroutine
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import get_session
from app.errors import Forbidden, Unauthorized
from app.iam.models import User
from app.iam.permissions import Permission, has_permission
from app.iam.security import decode_access_token


async def current_user(
    authorization: Annotated[str | None, Header()] = None,
    session: AsyncSession = Depends(get_session),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("缺少访问令牌", code="auth.token_missing")

    payload = decode_access_token(authorization.split(" ", 1)[1])
    user = await session.get(User, payload.sub)
    if user is None or not user.is_active:
        raise Unauthorized("账号不存在或已停用", code="auth.account_unknown_or_disabled")
    if user.expires_at is not None and user.expires_at <= datetime.now(UTC):
        raise Unauthorized("账号已过有效期", code="auth.account_expired")
    if payload.ver != user.token_version:
        raise Unauthorized("密码已更改，请重新登录", code="auth.session_revoked")
    return user


CurrentUser = Annotated[User, Depends(current_user)]


def require(perm: Permission) -> Callable[..., Coroutine[Any, Any, User]]:
    """用法：`user: User = Depends(require(Permission.DOCUMENT_WRITE))`"""

    async def _dep(user: CurrentUser) -> User:
        if not has_permission(user.role, perm):
            raise Forbidden(
                f"当前角色无权执行该操作（需要 {perm.value}）",
                code="auth.permission_required",
                params={"permission": perm.value},
            )
        return user

    return _dep
