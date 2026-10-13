"""启动时必须拒绝占位密钥。CHANGE_ME 和代码里的开发默认值都能启动的话，
生产 compose 强制 POSTGRES_PASSWORD 就白写了。"""

import pytest
from pydantic import ValidationError

from app.config import Settings


def test_change_me_is_refused():
    with pytest.raises(ValidationError, match="CHANGE_ME|随机|占位"):
        Settings(
            app_secret_key="CHANGE_ME",
            jwt_secret="a-real-looking-jwt-secret-value-32b",
        )


def test_the_insecure_builtin_default_is_refused():
    with pytest.raises(ValidationError, match="CHANGE_ME|随机|占位|dev-only"):
        Settings(
            app_secret_key="dev-only-insecure-key-please-override",
            jwt_secret="a-real-looking-jwt-secret-value-32b",
        )


def test_a_blank_secret_is_refused():
    with pytest.raises(ValidationError):
        Settings(app_secret_key="   ", jwt_secret="a-real-looking-jwt-secret-value-32b")


def test_a_real_secret_is_accepted():
    settings = Settings(
        app_secret_key="not-a-placeholder-key",
        jwt_secret="not-a-placeholder-jwt",
    )
    assert settings.app_secret_key == "not-a-placeholder-key"
    assert settings.jwt_secret == "not-a-placeholder-jwt"
