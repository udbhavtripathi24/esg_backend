"""Processing job queue (Stage 4).

Postgres-backed durable queue rather than a broker. Rationale in Stage 4 plan
§15: gives us retry, idempotency, observability, and testability without any
infra dependency. Trivially swappable for Pub/Sub later — the worker interface
is what matters.

Polymorphic subject via subject_type + subject_id lets one queue handle multiple
job kinds (checksum verification, metadata extraction, later: template
validation, KPI recalculation).
"""
from datetime import datetime
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, Index
from sqlalchemy import JSON


JOB_STATUSES = ("pending", "running", "completed", "failed", "dead")


class ProcessingJob(SQLModel, table=True):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        # The worker's poll query
        Index("ix_pj_status_scheduled", "status", "scheduled_at"),
        Index("ix_pj_subject", "subject_type", "subject_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    job_type: str  # 'verify_file_checksum' | 'extract_file_metadata' | ...
    subject_type: str  # 'dataset_file' | 'dataset_version' | ...
    subject_id: int
    status: str = Field(default="pending", index=True)
    attempts: int = Field(default=0)
    max_attempts: int = Field(default=3)
    last_error: Optional[str] = None
    payload: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    # Nullable idempotency key: when set, an existing completed job with the same
    # key short-circuits (used to prevent duplicate work on redelivery).
    idempotency_key: Optional[str] = Field(default=None, unique=True, index=True)
    scheduled_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
