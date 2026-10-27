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


def alembic_heads() -> list[str]:
    """The migration head(s) this code expects."""
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    root = Path(__file__).resolve().parents[2]
    return sorted(ScriptDirectory.from_config(Config(str(root / "alembic.ini"))).get_heads())


def migration_state(conn=None) -> dict:
    """Compare the database's Alembic revision with the code's head.

    Returns {"current": [...], "head": [...], "up_to_date": bool}. Raises if
    the database is unreachable."""
    from alembic.runtime.migration import MigrationContext

    heads = alembic_heads()
    if conn is None:
        with engine.connect() as c:
            current = sorted(MigrationContext.configure(c).get_current_heads())
    else:
        current = sorted(MigrationContext.configure(conn).get_current_heads())
    return {"current": current, "head": heads, "up_to_date": current == heads}


def init_db() -> None:
    """Startup schema check. Failure-tolerant: the container boots even if the
    DB isn't up yet; /ready is the authoritative health gate (Cloud Run).

    Schema changes go through `alembic upgrade head` only. DB_AUTO_CREATE
    (local sandbox) is ignored in production."""
    import app.models  # noqa: F401
    try:
        if settings.DB_AUTO_CREATE and not settings.is_production:
            log.warning("db_auto_create", note="creating tables from models; use Alembic for real databases")
            SQLModel.metadata.create_all(engine)
            return
        state = migration_state()
        if not state["up_to_date"]:
            log.warning("db_migrations_pending", current=state["current"], head=state["head"],
                        fix="run: alembic upgrade head")
    except Exception as exc:  # noqa: BLE001
        log.warning("init_db_skipped", reason=str(exc))


def get_session():
    with Session(engine) as session:
        yield session
