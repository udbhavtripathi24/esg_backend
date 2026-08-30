"""Audit log (Stage 4).

Append-only by convention: the repository exposes no update or delete methods,
and the API surfaces are read-only. Every mutation writes a row inside the
SAME transaction as the change — no fire-and-forget, no orphans on rollback.

`changes` is JSONB for before/after diffs on updates. For creates/deletes it
carries the full snapshot.
"""
from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, Index
from sqlalchemy import JSON


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_entity", "entity_type", "entity_id"),
        Index("ix_audit_company_time", "company_id", "occurred_at"),
        Index("ix_audit_actor", "actor_user_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: Optional[int] = Field(default=None, foreign_key="organizations.id", index=True)
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)
    actor_user_id: Optional[int] = Field(default=None, foreign_key="user.id")  # null for system events
    action: str  # 'dataset.created', 'file.uploaded', etc.
    entity_type: str
    entity_id: int
    entity_public_id: Optional[str] = None
    request_id: Optional[str] = None
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    changes: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    occurred_at: datetime = Field(default_factory=datetime.utcnow, index=True)
