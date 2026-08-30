"""Audit service (Stage 4).

Every mutation writes an audit row inside the SAME transaction as the change.
Contract:
- Callers pass the session they're already using
- We add() the AuditLog and DON'T commit — the caller's commit persists both
- If the caller rolls back, no audit orphan is created
"""
from typing import Any, Optional
from datetime import datetime
from sqlmodel import Session
from app.models.audit_log import AuditLog
from app.models.user import User
from app.core.logging import request_id_ctx


def log_action(
    session: Session,
    actor: Optional[User],
    action: str,
    entity_type: str,
    entity_id: int,
    entity_public_id: Optional[str] = None,
    company_id: Optional[int] = None,
    changes: Optional[dict[str, Any]] = None,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> None:
    """Record an audit entry. Does NOT commit."""
    entry = AuditLog(
        organization_id=(actor.organization_id if actor else None),
        company_id=company_id if company_id is not None else (actor.company_id if actor else None),
        actor_user_id=(actor.id if actor else None),
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        entity_public_id=entity_public_id,
        request_id=request_id_ctx.get(),
        ip_address=ip_address,
        user_agent=user_agent,
        changes=changes,
        occurred_at=datetime.utcnow(),
    )
    session.add(entry)
