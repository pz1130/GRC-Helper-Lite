from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    func,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, TimestampMixin
from app.iam.permissions import Role


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[Role] = mapped_column(
        SAEnum(
            Role,
            name="user_role",
            native_enum=False,
            length=32,
            values_callable=lambda roles: [r.value for r in roles],
        ),
        nullable=False,
    )
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # 改密码时加一。令牌里带着签发时的版本，对不上就作废——JWT 本身撤销不了，
    # 不这样做的话重设密码后，旧令牌（包括被偷走的）照样能用到 8 小时过期。
    token_version: Mapped[int] = mapped_column(Integer, default=0, server_default="0", nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 外部审计员账号：限时 + 限某次审计范围（spec §8.1）
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    engagement_scope_id: Mapped[int | None] = mapped_column(
        ForeignKey("audit_engagements.id", ondelete="SET NULL"), nullable=True
    )


class AuditLog(Base):
    """操作审计日志。只增不删（spec §8.2）。"""

    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_entity", "entity_type", "entity_id"),
        Index("ix_audit_log_at", "at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    action: Mapped[str] = mapped_column(String(100), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(64), nullable=False)
    before: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    after: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    ip: Mapped[str | None] = mapped_column(String(64), nullable=True)
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
