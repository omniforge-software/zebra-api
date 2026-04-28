from collections.abc import Generator

from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
_is_sqlite = settings.database_url.startswith("sqlite")
_is_sqlite_memory = _is_sqlite and ":memory:" in settings.database_url

if _is_sqlite_memory:
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
elif _is_sqlite:
    # File-backed SQLite should use the default QueuePool so concurrent
    # requests do not contend on a single shared connection.
    engine = create_engine(
        settings.database_url,
        connect_args={"check_same_thread": False},
    )
else:
    engine = create_engine(settings.database_url, pool_size=5)

if _is_sqlite:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.execute("PRAGMA busy_timeout=5000")
        cursor.execute("PRAGMA synchronous=NORMAL")
        cursor.close()

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app.models import db  # noqa: F401
    import app.database as _self

    Base.metadata.create_all(bind=_self.engine)
    _migrate(_self.engine)


# Columns added after initial release — ALTER TABLE IF NOT EXISTS is not
# supported in all SQLite versions, so we check the existing columns first.
_PRINTER_MIGRATIONS: list[tuple[str, str]] = [
    ("label_length", "VARCHAR(50)"),
    ("media_type",   "VARCHAR(50)"),
    ("media_out",    "BOOLEAN"),
    ("odometer",     "VARCHAR(50)"),
    ("dpi",          "INTEGER"),
]

_API_KEY_MIGRATIONS: list[tuple[str, str]] = [
    ("prefix",       "VARCHAR(16)"),
    ("last_used_at", "DATETIME"),
]


def _migrate(eng) -> None:
    """Apply additive schema changes that create_all won't handle."""
    with eng.connect() as conn:
        existing_printers = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(printers)").fetchall()
        }
        for col, col_type in _PRINTER_MIGRATIONS:
            if col not in existing_printers:
                conn.exec_driver_sql(f"ALTER TABLE printers ADD COLUMN {col} {col_type}")

        existing_keys = {
            row[1]
            for row in conn.exec_driver_sql("PRAGMA table_info(api_keys)").fetchall()
        }
        for col, col_type in _API_KEY_MIGRATIONS:
            if col not in existing_keys:
                conn.exec_driver_sql(f"ALTER TABLE api_keys ADD COLUMN {col} {col_type}")

        conn.commit()
