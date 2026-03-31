import os
import pytest

os.environ["JWT_SECRET"] = "test-secret-for-jwt-signing"

from auth import hash_password, verify_password, create_access_token, create_refresh_token, decode_token


def test_password_hash_and_verify():
    password = "my-secure-password"
    hashed = hash_password(password)
    assert hashed != password
    assert verify_password(password, hashed)


def test_wrong_password_fails():
    hashed = hash_password("correct-password")
    assert not verify_password("wrong-password", hashed)


def test_create_and_decode_access_token():
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_access_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "access"


def test_create_and_decode_refresh_token():
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_refresh_token(user_id)
    payload = decode_token(token)
    assert payload["sub"] == user_id
    assert payload["type"] == "refresh"


def test_expired_token_fails():
    from datetime import timedelta
    user_id = "550e8400-e29b-41d4-a716-446655440000"
    token = create_access_token(user_id, expires_delta=timedelta(seconds=-1))
    with pytest.raises(Exception):
        decode_token(token)
