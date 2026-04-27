"""Tests for security helpers: hashing, API key generation, JWT tokens."""
import re
import subprocess
import sys

from app.security import create_admin_token, create_api_key, decode_admin_token, hash_secret, verify_secret

BCRYPT_PATTERN = re.compile(r"^\$2[ab]?\$\d{2}\$.{53}$")


class TestHashing:
    def test_roundtrip(self):
        h = hash_secret("my-password")
        assert verify_secret("my-password", h)

    def test_wrong_password(self):
        h = hash_secret("correct")
        assert not verify_secret("wrong", h)

    def test_hash_is_valid_bcrypt(self):
        h = hash_secret("some-password")
        assert BCRYPT_PATTERN.match(h), f"Hash does not look like a valid bcrypt hash: {h}"

    def test_unique_hashes(self):
        # bcrypt uses a random salt — same password should produce different hashes
        h1 = hash_secret("same-password")
        h2 = hash_secret("same-password")
        assert h1 != h2

    def test_verify_rejects_truncated_hash(self):
        h = hash_secret("password")
        import pytest
        with pytest.raises((ValueError, Exception)):
            verify_secret("password", h[:10])


class TestCli:
    """Verify that the `python -m app.security` CLI produces a hash that verifies correctly."""

    def _run_cli(self, stdin: str) -> str:
        """Run the CLI with the given stdin and return stdout."""
        result = subprocess.run(
            [sys.executable, "-m", "app.security"],
            input=stdin,
            capture_output=True,
            text=True,
        )
        return result.stdout

    def test_explicit_password_hash_verifies(self):
        password = "SuperSecret99"
        output = self._run_cli(password + "\n")
        # Extract the hash from the output line  ADMIN_PASSWORD_HASH=<hash>
        match = re.search(r"ADMIN_PASSWORD_HASH=(\S+)", output)
        assert match, f"Could not find ADMIN_PASSWORD_HASH in output:\n{output}"
        extracted_hash = match.group(1)
        assert BCRYPT_PATTERN.match(extracted_hash), f"Not a valid bcrypt hash: {extracted_hash}"
        assert verify_secret(password, extracted_hash), "Hash from CLI does not verify against the original password"

    def test_generated_password_hash_verifies(self):
        # Empty input → auto-generate path
        output = self._run_cli("\n")
        # Extract generated password
        pwd_match = re.search(r"Generated password: (\S+)", output)
        assert pwd_match, f"Could not find generated password in output:\n{output}"
        generated_password = pwd_match.group(1)
        # Extract hash
        hash_match = re.search(r"ADMIN_PASSWORD_HASH=(\S+)", output)
        assert hash_match, f"Could not find ADMIN_PASSWORD_HASH in output:\n{output}"
        extracted_hash = hash_match.group(1)
        assert BCRYPT_PATTERN.match(extracted_hash), f"Not a valid bcrypt hash: {extracted_hash}"
        assert verify_secret(generated_password, extracted_hash), (
            "Generated password does not verify against its hash"
        )

    def test_short_password_rejected(self):
        result = subprocess.run(
            [sys.executable, "-m", "app.security"],
            input="short\n",
            capture_output=True,
            text=True,
        )
        assert result.returncode != 0
        assert "Error" in result.stderr or "error" in result.stderr


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
