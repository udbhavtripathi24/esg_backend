"""Liveness and readiness probes (needed for Cloud Run health checks).

- /health  : liveness — process is up. Never touches the DB.
- /ready    : readiness — dependencies (DB) reachable. Returns 503 if not.
"""
from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlmodel import Session, text
from app.db.session import get_session

router = APIRouter(tags=["health"])


@router.get("/health")
def health():
    return {"status": "ok"}


@router.get("/ready")
def ready(session: Session = Depends(get_session)):
    try:
        session.exec(text("SELECT 1"))
        return {"status": "ready", "database": "ok"}
    except Exception:
        return JSONResponse(status_code=503, content={"status": "not_ready", "database": "unreachable"})
