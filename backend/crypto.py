import os
from cryptography.hazmat.primitives.ciphers.aead import AESGCM


def _get_encryption_key() -> bytes:
    hex_key = os.environ.get("ENCRYPTION_KEY", "")
    if not hex_key or len(hex_key) != 64:
        raise ValueError(
            "ENCRYPTION_KEY must be a 64-character hex string (32 bytes). "
            "Generate one with: python -c \"import secrets; print(secrets.token_hex(32))\""
        )
    return bytes.fromhex(hex_key)


def encrypt_key(plaintext: str) -> tuple[bytes, bytes]:
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    iv = os.urandom(12)
    ciphertext = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    return ciphertext, iv


def decrypt_key(ciphertext: bytes, iv: bytes) -> str:
    key = _get_encryption_key()
    aesgcm = AESGCM(key)
    plaintext = aesgcm.decrypt(iv, ciphertext, None)
    return plaintext.decode("utf-8")
