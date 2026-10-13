"""字段级加解密。

数据库里存的 AI provider API key 必须加密（spec §8.2）。主密钥来自环境变量
APP_SECRET_KEY，不落库、不进日志。
"""

import base64
import hashlib
from functools import lru_cache

from cryptography.fernet import Fernet

from app.config import get_settings


def fernet_for(key: str) -> Fernet:
    """按给定主密钥造一个 Fernet。轮换时要同时拿着新旧两把（见 app/secrets.py）。"""
    # 允许运维填任意长度的随机串：派生成 Fernet 要求的 32 字节 urlsafe base64 key
    digest = hashlib.sha256(key.encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


@lru_cache
def _fernet() -> Fernet:
    return fernet_for(get_settings().app_secret_key)


def encrypt(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode()).decode()


def decrypt(ciphertext: str) -> str:
    return _fernet().decrypt(ciphertext.encode()).decode()


def mask(secret: str) -> str:
    """展示用掩码。API 响应中只能出现它，绝不返回明文。"""
    if len(secret) <= 8:
        return "…"
    return "…" + secret[-4:]
