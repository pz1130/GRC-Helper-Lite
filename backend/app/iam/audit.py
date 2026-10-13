"""审计日志写入。

刻意不提供任何删除/清理函数——spec §8.2 要求日志不可删除。
"""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.iam.models import AuditLog, User

_SENSITIVE_KEYS = {"password_hash", "password", "api_key", "jwt_secret", "app_secret_key"}


def scrub(data: dict[str, Any]) -> dict[str, Any]:
    """剔除敏感字段。写日志前必须过它——否则密钥会渗进永久保存的日志里。"""
    return {
        key: value
        for key, value in data.items()
        if key not in _SENSITIVE_KEYS and not key.endswith("_encrypted")
    }


async def record(
    session: AsyncSession,
    *,
    user: User | None,
    action: str,
    entity_type: str,
    entity_id: str | int,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip: str | None = None,
) -> AuditLog:
    entry = AuditLog(
        user_id=user.id if user else None,
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id),
        before=scrub(before) if before else None,
        after=scrub(after) if after else None,
        ip=ip,
    )
    session.add(entry)
    return entry
