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
