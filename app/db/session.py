"""Database engine & session.

Postgres-first (decision #10 relies on JSONB + composite FKs). SQLModel on top
of SQLAlchemy 2.x (decision #2/#8). Tests override get_session with their own
engine, so the module-level engine is only used at runtime.
"""
from sqlmodel import SQLModel, Session, create_engine
from app.core.config import settings
from app.core.logging import get_logger

log = get_logger("db")

engine = create_engine(
    settings.DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    # Postgres server encoding is UTF-8; force the client too so em-dashes,
    # non-Latin names, etc. aren't rejected by a locale-derived ASCII default.
    connect_args={"client_encoding": "utf8"},
)


def init_db() -> None:
    """Create tables from metadata. Failure-tolerant: on startup we log and
    continue so the container can boot even if the DB isn't up yet; /ready is
    the authoritative DB health gate (Cloud Run pattern)."""
    import app.models  # noqa: F401
    try:
        SQLModel.metadata.create_all(engine)
    except Exception as exc:  # noqa: BLE001
        log.warning("init_db_skipped", reason=str(exc))


def get_session():
    with Session(engine) as session:
        yield session
