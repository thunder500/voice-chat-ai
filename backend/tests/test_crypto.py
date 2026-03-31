import os
import pytest

os.environ["ENCRYPTION_KEY"] = "a" * 64

from crypto import encrypt_key, decrypt_key


def test_encrypt_decrypt_roundtrip():
    original = "sk-proj-abc123xyz"
    encrypted, iv = encrypt_key(original)
    assert isinstance(encrypted, bytes)
    assert isinstance(iv, bytes)
    assert len(iv) == 12
    decrypted = decrypt_key(encrypted, iv)
    assert decrypted == original


def test_different_plaintexts_produce_different_ciphertexts():
    enc1, iv1 = encrypt_key("key-one")
    enc2, iv2 = encrypt_key("key-two")
    assert enc1 != enc2


def test_same_plaintext_different_iv():
    enc1, iv1 = encrypt_key("same-key")
    enc2, iv2 = encrypt_key("same-key")
    assert iv1 != iv2
    assert enc1 != enc2
