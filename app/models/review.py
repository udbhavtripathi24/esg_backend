"""Review models (Stage 5).

Design decisions (approved):
- reviewer_user_id is NULLABLE so pull/queue mode can be added later without
  a migration. Stage 5 always sets it (push mode).
- One review row per assignment. A version can accumulate multiple reviews
  over time (e.g. if a reviewer is reassigned, or in future two-tier mode).
- The 'tier' column is present but always 1 in Stage 5 — reserved for future
  client-approval then Deloitte-assurance flow without schema change.
- Decision recording is transactional (see app/services/review_service.py):
  status change + audit + notification outbox all commit together or none do.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, Index
from app.core.public_ids import generate_public_id


def _rv_pid() -> str: return generate_public_id("rv_")
def _rc_pid() -> str: return generate_public_id("rc_")


REVIEW_STATUSES = ("pending", "approved", "changes_requested", "rejected")
COMMENT_KINDS = ("general", "field", "decision")


class Review(SQLModel, table=True):
    __tablename__ = "reviews"
    __table_args__ = (
        Index("ix_review_version_status", "dataset_version_id", "status"),
        Index("ix_review_reviewer", "reviewer_user_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_rv_pid, unique=True, index=True)
    dataset_version_id: int = Field(foreign_key="dataset_versions.id", index=True)
    # NULLABLE by design: null == "queued, unclaimed" (pull mode, future)
    reviewer_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    tier: int = Field(default=1)  # reserved for two-tier flow
    status: str = Field(default="pending")  # pending → approved/changes_requested/rejected
    assigned_by: int = Field(foreign_key="user.id")
    assigned_at: datetime = Field(default_factory=datetime.utcnow)
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None  # required at decision time by the service
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ReviewComment(SQLModel, table=True):
    """Threaded comments per dataset version.

    parent_comment_id enables real threading (approved decision 3). Root
    comments have parent_comment_id = NULL; replies point at the parent.
    Reply-depth is capped in the API layer at 3 to prevent runaway threads.
    """
    __tablename__ = "review_comments"
    __table_args__ = (
        Index("ix_rc_version", "dataset_version_id"),
        Index("ix_rc_parent", "parent_comment_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_rc_pid, unique=True, index=True)
    dataset_version_id: int = Field(foreign_key="dataset_versions.id", index=True)
    parent_comment_id: Optional[int] = Field(default=None, foreign_key="review_comments.id")
    author_user_id: int = Field(foreign_key="user.id")
    kind: str = Field(default="general")  # general | field | decision
    # For 'field' kind: optional pointer at a row/column ("row=14,col=quantity")
    field_reference: Optional[str] = None
    body: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
