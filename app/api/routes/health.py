"""Liveness and readiness probes (needed for Cloud Run health checks).

- /health  : liveness — process is up. Never touches the DB.
- /ready    : readiness — DB reachable and migrated to the code's Alembic
              head. Returns 503 otherwise, so an instance never serves
              traffic against a schema it doesn't match.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, text
from app.db.session import get_session, migration_state

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unreachable"})
    try:
        state = migration_state(session.connection())
    except Exception:  # noqa: BLE001
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "ok",
                                                      "migrations": "unknown"})
    if not state["up_to_date"]:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "ok",
                                                      "migrations": "pending", **state})
    return {"status": "ready", "database": "ok", "migrations": "up_to_date"}
