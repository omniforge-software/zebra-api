"""
Shared test fixtures: in-memory SQLite DB, overridden settings, FastAPI TestClient.
"""
import os

# Set env vars BEFORE any app code imports settings (lru_cache)
os.environ.update({
    "SECRET_KEY": "test-secret-key-that-is-at-least-32-chars-long",
    "ADMIN_USERNAME": "admin",
    "ADMIN_PASSWORD_HASH": "$2b$12$yEHbhuFNX2EC3u8kUkkMCejUlUWu.ohjM6Ukmfr855LhikSzWBUui",  # "testpass"
    "DATABASE_URL": "sqlite:///",
    "SCAN_ON_STARTUP": "false",
    "SCAN_SUBNETS": "192.168.45.0/24",
})

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import Settings, get_settings
from app.database import Base, get_db
from app.models.db import AdminUser, ApiKey, LabelTemplate, Printer
from app.security import hash_secret


# ---------------------------------------------------------------------------
# Settings override — deterministic, no .env file dependency
# ---------------------------------------------------------------------------

_test_settings = Settings(
    secret_key="test-secret-key-that-is-at-least-32-chars-long",
    admin_password_hash="$2b$12$yEHbhuFNX2EC3u8kUkkMCejUlUWu.ohjM6Ukmfr855LhikSzWBUui",
    database_url="sqlite:///",
    scan_on_startup=False,
    scan_subnets="192.168.45.0/24",
)


def _get_test_settings() -> Settings:
    return _test_settings


# ---------------------------------------------------------------------------
# Per-test in-memory DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def db_engine():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)

    @event.listens_for(engine, "connect")
    def _set_pragma(dbapi_conn, _rec):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.close()

    Base.metadata.create_all(bind=engine)
    yield engine
    engine.dispose()


@pytest.fixture()
def db_session(db_engine):
    TestSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    session = TestSession()
    try:
        yield session
    finally:
        session.close()


# ---------------------------------------------------------------------------
# FastAPI TestClient wired to the test DB
# ---------------------------------------------------------------------------

@pytest.fixture()
def client(db_engine, db_session, monkeypatch):
    from fastapi.testclient import TestClient

    # Override settings everywhere
    monkeypatch.setattr("app.config.get_settings", _get_test_settings)
    monkeypatch.setattr("app.database.get_settings", _get_test_settings)
    monkeypatch.setattr("app.services.zpl_render.get_settings", _get_test_settings)
    monkeypatch.setattr("app.services.printer_io.get_settings", _get_test_settings)
    monkeypatch.setattr("app.services.jobs.get_settings", _get_test_settings)

    # Override engine + SessionLocal so init_db() and service code both use the test DB
    TestSession = sessionmaker(bind=db_engine, autocommit=False, autoflush=False)
    monkeypatch.setattr("app.database.engine", db_engine)
    monkeypatch.setattr("app.database.SessionLocal", TestSession)
    monkeypatch.setattr("app.services.jobs.SessionLocal", TestSession)

    from app.main import create_app

    app = create_app()

    # Override the FastAPI get_db dependency
    def _override_get_db():
        s = TestSession()
        try:
            yield s
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db

    with TestClient(app, raise_server_exceptions=False) as tc:
        yield tc


# ---------------------------------------------------------------------------
# Convenience: seed common objects
# ---------------------------------------------------------------------------

@pytest.fixture()
def sample_printer(db_session: Session) -> Printer:
    p = Printer(ip="192.168.45.208", alias="Test ZT411", product_name="ZT411", is_online=True, ports_open=[9100, 515, 80])
    db_session.add(p)
    db_session.commit()
    db_session.refresh(p)
    return p


@pytest.fixture()
def sample_template(db_session: Session) -> LabelTemplate:
    t = LabelTemplate(
        name="test-label",
        description="A test label",
        zpl_body="^XA\n^CF0,35\n^FO30,40^FD{{ title }}^FS\n^FO30,90^FD{{ message }}^FS\n^XZ",
        variables=["message", "title"],
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    return t


@pytest.fixture()
def sample_api_key(db_session: Session) -> tuple[str, ApiKey]:
    """Returns (raw_key, ApiKey ORM object)."""
    raw_key = "zebra_test1234567890abcdefghijklmnop"
    key = ApiKey(name="test-key", key_hash=hash_secret(raw_key), prefix=raw_key[:12], is_active=True)
    db_session.add(key)
    db_session.commit()
    db_session.refresh(key)
    return raw_key, key


@pytest.fixture()
def admin_cookie(client) -> dict[str, str]:
    """Set the admin auth cookie on the client and also return the dict for reference.

    The startup hook seeds the admin user; this just issues a JWT for it.
    """
    from app.security import create_admin_token

    token = create_admin_token("admin")
    client.cookies.set("zebra_admin", token)
    return {"zebra_admin": token}
