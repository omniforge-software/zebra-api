import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
from jose import JWTError, jwt

from app.config import get_settings


def hash_secret(secret: str) -> str:
    return bcrypt.hashpw(secret.encode(), bcrypt.gensalt()).decode()


def verify_secret(secret: str, secret_hash: str) -> bool:
    return bcrypt.checkpw(secret.encode(), secret_hash.encode())


def create_api_key() -> tuple[str, str]:
    raw_key = f"zebra_{secrets.token_urlsafe(32)}"
    return raw_key, raw_key[:12]


def create_admin_token(username: str) -> str:
    settings = get_settings()
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=settings.admin_jwt_minutes)
    return jwt.encode({"sub": username, "exp": expires_at}, settings.secret_key, algorithm="HS256")


def decode_admin_token(token: str) -> str | None:
    settings = get_settings()
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=["HS256"])
    except JWTError:
        return None
    username = payload.get("sub")
    return username if isinstance(username, str) else None


if __name__ == "__main__":
    import sys

    print("Zebra API — admin password setup")
    print("  Press Enter to auto-generate a secure password, or type one now.")

    # getpass reads /dev/tty directly and blocks in non-interactive contexts (tests, pipes).
    # Fall back to plain stdin.readline() when stdin is not a TTY.
    try:
        if sys.stdin.isatty():
            import getpass
            password = getpass.getpass("Password (leave blank to generate): ").strip()
        else:
            print("Password (leave blank to generate): ", end="", flush=True)
            password = sys.stdin.readline().strip()
    except (KeyboardInterrupt, EOFError):
        sys.exit(0)

    if not password:
        password = secrets.token_urlsafe(16)
        print(f"Generated password: {password}")
    elif len(password) < 8:
        print("Error: password must be at least 8 characters.", file=sys.stderr)
        sys.exit(1)

    hashed = hash_secret(password)
    print(f"\nAdd this line to your .env file:")
    print(f"ADMIN_PASSWORD_HASH={hashed}")
