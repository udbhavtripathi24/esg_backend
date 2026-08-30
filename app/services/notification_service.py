"""Notification service (Stage 5).

Two functions:
- enqueue_notification: called by other services INSIDE their transaction.
  Writes to notification_outbox only. Does NOT commit.
- dispatch_pending: called by a worker (or the API for tests). Reads
  pending outbox rows, fans out to notifications, marks outbox as dispatched.
  Commits per row so a slow dispatch doesn't block other rows.

Why outbox instead of direct write to notifications:
  If we wrote to `notifications` directly inside the review-decision transaction,
  a rollback would ALSO undo the notifications — which is fine. But we still
  want the same interface for the future email/SMS/webhook fan-out that WILL
  need to happen outside the request transaction. Outbox now, transports later,
  zero refactor.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from app.models.notification import Notification, NotificationOutbox


def enqueue_notification(
    session: Session,
    event_type: str,
    recipient_user_ids: list[int],
    title: str,
    body: Optional[str] = None,
    entity_type: Optional[str] = None,
    entity_id: Optional[int] = None,
    entity_public_id: Optional[str] = None,
) -> NotificationOutbox:
    """Add a pending outbox row. Caller commits."""
    outbox = NotificationOutbox(
        event_type=event_type,
        payload={
            "recipient_user_ids": list(recipient_user_ids),
            "title": title,
            "body": body,
            "entity_type": entity_type,
            "entity_id": entity_id,
            "entity_public_id": entity_public_id,
        },
        status="pending",
    )
    session.add(outbox)
    return outbox


def dispatch_pending(session: Session, batch_size: int = 100) -> dict:
    """Dispatch pending outbox rows to per-recipient notifications.

    Returns {'dispatched': N, 'failed': M}. Commits after each row so a bad
    row doesn't hold up the rest.
    """
    pending = session.exec(
        select(NotificationOutbox)
        .where(NotificationOutbox.status == "pending")
        .order_by(NotificationOutbox.created_at.asc())
        .limit(batch_size)
    ).all()

    dispatched = failed = 0
    for row in pending:
        try:
            recipients = row.payload.get("recipient_user_ids") or []
            for rid in recipients:
                session.add(Notification(
                    recipient_user_id=rid,
                    event_type=row.event_type,
                    entity_type=row.payload.get("entity_type"),
                    entity_id=row.payload.get("entity_id"),
                    entity_public_id=row.payload.get("entity_public_id"),
                    title=row.payload.get("title") or "",
                    body=row.payload.get("body"),
                ))
            row.status = "dispatched"
            row.dispatched_at = datetime.utcnow()
            row.attempts += 1
            session.add(row)
            session.commit()
            dispatched += 1
        except Exception as e:
            session.rollback()
            row.attempts += 1
            row.last_error = str(e)[:500]
            if row.attempts >= 5:
                row.status = "failed"
            session.add(row)
            session.commit()
            failed += 1
    return {"dispatched": dispatched, "failed": failed}
