"""Notification models (Stage 5).

Two-table design implementing the transactional outbox pattern:

  notification_outbox : written INSIDE the same transaction as the state
                        change that triggered it. If the transaction rolls
                        back, no orphan notification exists.
  notifications       : written by the outbox worker/dispatcher. This is
                        what the UI reads.

Rationale: keeps the trigger reliable (in-transaction) without coupling
notification delivery to the request path. The dispatcher is currently
in-process (see app/services/notification_service.py::dispatch_pending);
same interface will fan out to email later without changing callers.

Four confirmed event types (stakeholder decision):
  data_approved | changes_requested | report_ready | new_assignment
Plus 'data_rejected' — same shape as approved but different action code.
"""
from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, Index
from sqlalchemy import JSON


OUTBOX_STATUSES = ("pending", "dispatched", "failed")
NOTIFICATION_EVENTS = (
    "data_approved",
    "changes_requested",
    "data_rejected",
    "report_ready",
    "new_assignment",
)


class NotificationOutbox(SQLModel, table=True):
    """Written in-transaction with the triggering state change."""
    __tablename__ = "notification_outbox"
    __table_args__ = (
        Index("ix_outbox_status_created", "status", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    event_type: str  # from NOTIFICATION_EVENTS
    # Full context needed to render notifications for all recipients:
    #   { "recipient_user_ids": [...], "entity_type": ..., "entity_id": ...,
    #     "entity_public_id": ..., "title": ..., "body": ... }
    payload: dict[str, Any] = Field(sa_column=Column(JSON))
    status: str = Field(default="pending")
    attempts: int = Field(default=0)
    last_error: Optional[str] = None
    dispatched_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)


class Notification(SQLModel, table=True):
    """User-facing notification rendered by the UI."""
    __tablename__ = "notifications"
    __table_args__ = (
        Index("ix_notif_recipient_unread", "recipient_user_id", "is_read"),
        Index("ix_notif_recipient_created", "recipient_user_id", "created_at"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    recipient_user_id: int = Field(foreign_key="user.id", index=True)
    event_type: str
    entity_type: Optional[str] = None  # 'dataset_version' | 'review' | ...
    entity_id: Optional[int] = None
    entity_public_id: Optional[str] = None
    title: str
    body: Optional[str] = None
    is_read: bool = Field(default=False)
    read_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
