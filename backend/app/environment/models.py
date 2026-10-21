from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy import Enum as SAEnum
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base


class TechAssetCategory(StrEnum):
    CLOUD = "cloud"
    PAM = "pam"
    SIEM = "siem"
    EDR = "edr"
    IAM = "iam"
    DLP = "dlp"
    BACKUP = "backup"
    NETWORK = "network"
    DATABASE = "database"
    TICKETING = "ticketing"
    OTHER = "other"


class TechAssetEnvironment(StrEnum):
    PROD = "prod"
    DR = "dr"
    DEV = "dev"
    ALL = "all"


class TechAssetStatus(StrEnum):
    ACTIVE = "active"
    PLANNED = "planned"
    RETIRING = "retiring"


class HowEnforced(StrEnum):
    AUTOMATED = "automated"
    SEMI_AUTOMATED = "semi_automated"
    MANUAL = "manual"


class ImplementationStatus(StrEnum):
    IMPLEMENTED = "implemented"
    PARTIAL = "partial"
    PLANNED = "planned"
    NOT_APPLICABLE = "not_applicable"


# 这些别名只是让对外的模型词汇好找，既不另建枚举类型，也不另加数据库约束。
AssetCategory = TechAssetCategory
AssetEnvironment = TechAssetEnvironment
AssetStatus = TechAssetStatus
ImplementationHowEnforced = HowEnforced


def _enum(enum_cls: type[StrEnum], name: str) -> SAEnum:
    return SAEnum(
        enum_cls,
        name=name,
        native_enum=False,
        length=32,
        values_callable=lambda members: [member.value for member in members],
        create_constraint=True,
    )


class TechAsset(Base):
    __tablename__ = "tech_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True, nullable=False)
    category: Mapped[TechAssetCategory] = mapped_column(
        _enum(TechAssetCategory, "tech_asset_category"), nullable=False
    )
    vendor: Mapped[str] = mapped_column(String(200), nullable=False, default="")
    environment: Mapped[TechAssetEnvironment] = mapped_column(
        _enum(TechAssetEnvironment, "tech_asset_environment"), nullable=False
    )
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    scope_note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    status: Mapped[TechAssetStatus] = mapped_column(
        _enum(TechAssetStatus, "tech_asset_status"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class Implementation(Base):
    __tablename__ = "implementations"
    __table_args__ = (
        UniqueConstraint("id", "control_id", name="uq_implementation_id_control"),
        UniqueConstraint("control_id", "tech_asset_id", name="uq_implementation_control_asset"),
        CheckConstraint(
            "status <> 'not_applicable' OR "
            "(na_justification IS NOT NULL AND btrim(na_justification) <> '')",
            name="ck_implementation_na_justification",
        ),
        # 普通唯一约束里，PostgreSQL 把每个 NULL 都当成互不相同的值。
        Index(
            "uq_implementation_control_without_asset",
            "control_id",
            unique=True,
            postgresql_where="tech_asset_id IS NULL",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    control_id: Mapped[int] = mapped_column(
        ForeignKey("controls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tech_asset_id: Mapped[int | None] = mapped_column(
        ForeignKey("tech_assets.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    description: Mapped[str] = mapped_column(Text, nullable=False, default="")
    how_enforced: Mapped[HowEnforced] = mapped_column(
        _enum(HowEnforced, "implementation_how_enforced"), nullable=False
    )
    status: Mapped[ImplementationStatus] = mapped_column(
        _enum(ImplementationStatus, "implementation_status"), nullable=False
    )
    na_justification: Mapped[str | None] = mapped_column(Text, nullable=True)
    owner_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )
    last_verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
