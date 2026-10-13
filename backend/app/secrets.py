"""主密钥轮换。

`APP_SECRET_KEY` 加密着库里所有 provider 的 API key。它泄露或需要定期更换时，
换掉环境变量还不够——库里的密文是用旧密钥加的，换完全部解不开。

轮换：用旧密钥解、用当前密钥加，原地换掉密文。**明文只在内存里存在一瞬**，
不写日志、不落盘。

解不开的行会被跳过而不是报错——那通常意味着它已经是新密钥加的（轮换中断后重跑），
或者旧密钥给错了。两种情况都不该把库改坏，所以只数不动。
"""

from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.crypto import encrypt, fernet_for
from app.llm.models import LLMProviderConfig


async def rotate_secret_key(session: AsyncSession, *, old_key: str) -> dict[str, int]:
    """把所有 provider 密钥从 old_key 重新加密到当前的 APP_SECRET_KEY。"""
    if not old_key.strip():
        raise ValueError("必须给出旧的 APP_SECRET_KEY")

    old = fernet_for(old_key)
    rotated = skipped = 0
    for config in await session.scalars(select(LLMProviderConfig)):
        try:
            plaintext = old.decrypt(config.api_key_encrypted.encode()).decode()
        except Exception:  # noqa: BLE001 —— 解不开就跳过，见模块文档
            skipped += 1
            continue
        config.api_key_encrypted = encrypt(plaintext)
        rotated += 1
    await session.flush()
    return {"rotated": rotated, "skipped": skipped}


async def rotate_cli(old_key: str) -> dict[str, Any]:
    from app.db import session_factory

    async with session_factory() as session:
        result = await rotate_secret_key(session, old_key=old_key)
        await session.commit()
        return result
