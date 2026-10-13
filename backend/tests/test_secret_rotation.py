"""主密钥轮换。

`APP_SECRET_KEY` 是 Fernet 主密钥，加密着库里所有 provider 的 API key。
原先既没有轮换机制、也没有说明——密钥泄露了只能换掉它，然后**所有 provider 的
密钥一起失效**，得一个个重新到设置页里填。

轮换要能做到：用旧密钥解、用新密钥加，库里的密文原地换掉，明文一刻都不落盘。
"""

import pytest

from app.crypto import decrypt, encrypt, fernet_for
from app.llm.models import LLMProviderConfig, ProviderKind
from app.secrets import rotate_secret_key

OLD = "old-master-key-for-the-test"


async def _provider(db_session, name: str, secret: str, key: str) -> LLMProviderConfig:
    config = LLMProviderConfig(
        name=name, kind=ProviderKind.OPENAI, model="gpt-4o",
        api_key_encrypted=fernet_for(key).encrypt(secret.encode()).decode(),
    )
    db_session.add(config)
    await db_session.flush()
    return config


async def test_every_stored_key_is_re_encrypted(db_session):
    await _provider(db_session, "one", "sk-first", OLD)
    await _provider(db_session, "two", "sk-second", OLD)

    result = await rotate_secret_key(db_session, old_key=OLD)

    assert result == {"rotated": 2, "skipped": 0}
    for config in await db_session.scalars(
        __import__("sqlalchemy").select(LLMProviderConfig).order_by(LLMProviderConfig.id)
    ):
        # 现在能用**当前**主密钥解开了
        assert decrypt(config.api_key_encrypted).startswith("sk-")


async def test_a_row_already_on_the_new_key_is_left_alone(db_session):
    """轮换中断后重跑必须安全——已经换过的不能再用旧密钥去解。"""
    config = LLMProviderConfig(
        name="already", kind=ProviderKind.OPENAI, model="gpt-4o",
        api_key_encrypted=encrypt("sk-current"),
    )
    db_session.add(config)
    await db_session.flush()

    result = await rotate_secret_key(db_session, old_key=OLD)

    assert result == {"rotated": 0, "skipped": 1}
    assert decrypt(config.api_key_encrypted) == "sk-current"


async def test_a_wrong_old_key_changes_nothing(db_session):
    config = await _provider(db_session, "one", "sk-first", OLD)
    before = config.api_key_encrypted

    result = await rotate_secret_key(db_session, old_key="not-the-old-key")

    assert result == {"rotated": 0, "skipped": 1}
    assert config.api_key_encrypted == before


async def test_an_empty_old_key_is_refused(db_session):
    with pytest.raises(ValueError):
        await rotate_secret_key(db_session, old_key="   ")
