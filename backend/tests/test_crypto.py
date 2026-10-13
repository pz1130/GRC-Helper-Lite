from app.crypto import decrypt, encrypt, mask


def test_encrypt_then_decrypt_roundtrips():
    secret = "sk-ant-api03-abcdefghijklmnop"
    assert decrypt(encrypt(secret)) == secret


def test_ciphertext_does_not_contain_plaintext():
    secret = "sk-ant-api03-abcdefghijklmnop"
    assert secret not in encrypt(secret)


def test_encrypt_is_not_deterministic():
    # Fernet 带随机 IV，同一明文两次加密结果不同
    secret = "sk-ant-api03-abcdefghijklmnop"
    assert encrypt(secret) != encrypt(secret)


def test_mask_keeps_only_tail():
    assert mask("sk-ant-api03-abcdefghijklmnop") == "…mnop"


def test_mask_short_secret_reveals_nothing():
    assert mask("abc") == "…"
