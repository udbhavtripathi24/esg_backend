"""ConsultantAssignment model (Stage 3).

Links a Deloitte/organization user to a client company. A consultant's access
to a company comes ONLY from an active row here (decision #5). Never placed in
the JWT as authoritative (decision #6).

Removal semantics: soft removal via is_active=False (kept for audit), not hard
delete. A unique constraint prevents duplicate ACTIVE assignments; a partial
unique index enforces this at the DB level.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, UniqueConstraint, Index


class ConsultantAssignment(SQLModel, table=True):
    __tablename__ = "consultant_assignments"
    __table_args__ = (
        # Prevent duplicate assignment rows for the same pair. (Active-only
        # dedupe is additionally guarded in the service layer; a full partial
        # unique index is added in the migration.)
        UniqueConstraint("company_id", "consultant_user_id", name="uq_company_consultant"),
        Index("ix_consultant_assignment_company", "company_id"),
        Index("ix_consultant_assignment_user", "consultant_user_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id")
    consultant_user_id: int = Field(foreign_key="user.id")
    role_on_account: Optional[str] = None  # e.g. "Lead", "Support"
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class ConsultantAssignmentCreate(SQLModel):
    company_id: int
    consultant_user_id: int
    role_on_account: Optional[str] = None


class ConsultantAssignmentRead(SQLModel):
    id: int
    company_id: int
    consultant_user_id: int
    role_on_account: Optional[str]
    is_active: bool
    created_at: datetime


class ConsultantAssignmentUpdate(SQLModel):
    role_on_account: Optional[str] = None
    is_active: Optional[bool] = None
