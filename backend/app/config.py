from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_PLACEHOLDER_SECRETS = frozenset(
    {
        "change_me",
        "dev-only-insecure-key-please-override",
        "dev-only-insecure-jwt-please-override",
    }
)


class Settings(BaseSettings):
    """唯一读取环境变量的地方。其他模块一律通过 get_settings() 取值。"""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_version: str = "0.1.0"
    database_url: str = "postgresql+asyncpg://grc:grc@db:5432/grc"
    redis_url: str = "redis://redis:6379/0"

    # Fernet 主密钥，用于加密数据库中的 API key。必须由部署方提供。
    app_secret_key: str = "dev-only-insecure-key-please-override"
    jwt_secret: str = "dev-only-insecure-jwt-please-override"
    jwt_expire_minutes: int = 480

    cors_origins: list[str] = ["http://localhost:5173"]

    # 映射候选预筛改用判断模型（Jev）。默认关闭。打开后**会把本机构控制点的标题与
    # 陈述（经脱敏规则处理）发给判断模型服务（TypeSafe）**——必须先过数据政策审批
    # （KNOWN_ISSUES K17）。还需在「判断模型」设置里选好 Jev 服务；任一条件不满足、
    # 调用失败、超时或预算耗尽，都回退到关键词 + 向量预筛。
    mapping_jev_prefilter: bool = False

    # RCSA 模块的总开关。默认关闭：关闭时 /api/rcsa 下除 status 外一律 404，前端也不显示
    # 入口。与 RCSA 的两个 AI 开关完全独立。生产上要等 PR-5 的发布验收通过再打开。
    rcsa_enabled: bool = False

    @field_validator("app_secret_key", "jwt_secret")
    @classmethod
    def reject_placeholder_secrets(cls, value: str) -> str:
        cleaned = value.strip()
        if not cleaned or cleaned.casefold() in _PLACEHOLDER_SECRETS:
            raise ValueError(
                "必须设置真正的随机密钥，不能用 CHANGE_ME 或代码里的开发占位值。"
            )
        return cleaned


@lru_cache
def get_settings() -> Settings:
    return Settings()
