"""Evidence files — supporting documents behind reported figures.

An evidence file is uploaded by a preparer against a BRSR question or
section (a payroll extract behind the headcount, electricity bills behind
energy), or by the client in answer to an assurance information request.

Design decisions:
- Stored through the object-storage layer under a company-scoped key; the
  SHA-256 of the content is recorded at upload, so the assurance pack can
  prove the file is the one that was reviewed.
- Never hard-deleted. A preparer may withdraw their own file while the
  section is still editable (``deleted_at``); the row and the stored object
  stay for the audit trail. Files that assurance has relied on (attached to
  an accepted request, or inside a signed engagement) cannot be withdrawn.
"""
from datetime import datetime
from typing import Optional

from sqlmodel import SQLModel, Field, Index

from app.core.public_ids import generate_public_id


def _ev_pid() -> str: return generate_public_id("ev_")


class EvidenceFile(SQLModel, table=True):
    __tablename__ = "evidence_files"
    __table_args__ = (Index("ix_evidence_set_section", "response_set_id", "section_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_ev_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id")
    section_code: str
    question_id: Optional[str] = None
    # Set when uploaded in answer to an assurance information request
    assurance_request_id: Optional[int] = Field(default=None, foreign_key="assurance_requests.id", index=True)
    description: Optional[str] = None
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_checksum: str
    storage_key: str = Field(unique=True)
    uploaded_by: int = Field(foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None
    deleted_by: Optional[int] = Field(default=None, foreign_key="user.id")
