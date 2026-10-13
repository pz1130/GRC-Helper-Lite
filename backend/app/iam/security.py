from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, VerifyMismatchError

from app.config import get_settings
from app.errors import Unauthorized
from app.iam.permissions import Role

_hasher = PasswordHasher()
_ALGORITHM = "HS256"


def hash_password(raw: str) -> str:
    return _hasher.hash(raw)


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return _hasher.verify(hashed, raw)
    except (VerifyMismatchError, VerificationError):
        return False


# 账号不存在时用它跑一次等价成本的校验，抹平时序差异。
_DUMMY_HASH = _hasher.hash("timing-equalizer")


def dummy_verify(raw: str) -> None:
    """恒定成本的假校验。必定失败，只为消耗与真校验相同的时间。

    没有它，登录响应耗时会出卖"这个邮箱存不存在"，统一的错误文案就白写了。
    """
    verify_password(raw, _DUMMY_HASH)


@dataclass(frozen=True)
class TokenPayload:
    sub: int
    role: Role
    exp: int
    ver: int = 0


def create_access_token(user_id: int, role: Role, version: int = 0) -> str:
    settings = get_settings()
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    payload = {
        "sub": str(user_id), "role": role.value, "exp": int(expire.timestamp()), "ver": version,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=_ALGORITHM)


def decode_access_token(token: str) -> TokenPayload:
    try:
        raw = jwt.decode(token, get_settings().jwt_secret, algorithms=[_ALGORITHM])
    except jwt.PyJWTError as exc:
        raise Unauthorized("令牌无效或已过期", code="auth.token_invalid") from exc
    # 引入版本之前签发的令牌没有 ver，按 0 算：与从没改过密码的账号一致，升级不踢人。
    return TokenPayload(
        sub=int(raw["sub"]), role=Role(raw["role"]), exp=raw["exp"], ver=int(raw.get("ver", 0)),
    )
