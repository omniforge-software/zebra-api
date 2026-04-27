"""Tests for security helpers: hashing, API key generation, JWT tokens."""
from app.security import create_admin_token, create_api_key, decode_admin_token, hash_secret, verify_secret


class TestHashing:
    def test_roundtrip(self):
        h = hash_secret("my-password")
        assert verify_secret("my-password", h)

    def test_wrong_password(self):
        h = hash_secret("correct")
        assert not verify_secret("wrong", h)


class TestApiKeyGeneration:
    def test_format(self):
        raw_key, prefix = create_api_key()
        assert raw_key.startswith("zebra_")
        assert len(raw_key) > 20
        assert prefix == raw_key[:12]

    def test_unique(self):
        keys = {create_api_key()[0] for _ in range(10)}
        assert len(keys) == 10


class TestJwt:
    def test_roundtrip(self):
        token = create_admin_token("admin")
        assert decode_admin_token(token) == "admin"

    def test_invalid_token(self):
        assert decode_admin_token("garbage.token.here") is None

    def test_tampered_token(self):
        token = create_admin_token("admin")
        assert decode_admin_token(token + "x") is None
