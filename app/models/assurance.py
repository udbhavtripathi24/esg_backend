"""Independent assurance of a company's BRSR.

How the platform models an assurance engagement (ISAE 3000 (Revised) /
ICAI SSAE 3000, as used for SEBI's BRSR Core):

- An **engagement** covers one company's BRSR for one financial year, at a
  stated level (limited or reasonable), by a named provider.
- The **team** (``assurance_members``) is named: a signing partner, managers,
  practitioners and optionally an engagement quality reviewer (EQR). Every
  member declares independence, and the platform refuses anyone who
  prepared, submitted or approved the data, or who advises the client.
- Each section in scope has a **workpaper** (``assurance_items``): a
  practitioner records the procedures performed and the conclusion; a
  different senior member reviews it (four eyes).
- **Information requests** ask the client for evidence; the client answers
  with text and files; the assurer accepts or sends it back.
- **Findings** record errors or weaknesses, the client's management
  response, and how each was resolved (corrected, or left uncorrected and
  reported).
- The partner **signs** the opinion. The signature records a fingerprint
  of the BRSR answers; any later change makes the assurance visibly stale.
  The statement and the sign-off register are written to storage at
  signing and never regenerated, so what was signed is what is downloaded.
"""
from datetime import date, datetime
from typing import Any, Optional

from sqlalchemy import text
from sqlmodel import SQLModel, Field, Column, JSON, Index, UniqueConstraint

from app.core.public_ids import generate_public_id


def _eng_pid() -> str: return generate_public_id("asr_")
def _req_pid() -> str: return generate_public_id("air_")
def _find_pid() -> str: return generate_public_id("afd_")


# "superseded": a signed engagement replaced by a re-assurance after the BRSR changed
ENGAGEMENT_STATUSES = ("planning", "fieldwork", "signed", "withdrawn", "superseded")
LEVELS = ("limited", "reasonable")
MEMBER_ROLES = ("partner", "manager", "practitioner", "eqr")
OPINIONS = ("unmodified", "qualified", "adverse", "disclaimer")


class AssuranceEngagement(SQLModel, table=True):
    __tablename__ = "assurance_engagements"
    # At most one live engagement per BRSR, enforced by the database so two
    # simultaneous creates cannot both succeed.
    __table_args__ = (Index("uq_assurance_active_per_set", "response_set_id", unique=True,
                            postgresql_where=text("status IN ('planning', 'fieldwork', 'signed')"),
                            sqlite_where=text("status IN ('planning', 'fieldwork', 'signed')")),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_eng_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id", index=True)
    financial_year: str
    level: str = Field(default="limited")
    standard: str = Field(default="ISAE 3000 (Revised) / ICAI SSAE 3000")
    provider_name: str
    scope: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    status: str = Field(default="planning", index=True)
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    fieldwork_started_at: Optional[datetime] = None
    eqr_cleared_by: Optional[int] = Field(default=None, foreign_key="user.id")
    eqr_cleared_at: Optional[datetime] = None
    eqr_note: Optional[str] = None
    signed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    signed_at: Optional[datetime] = None
    opinion: Optional[str] = None
    opinion_basis: Optional[str] = None
    statement_text: Optional[str] = None
    signed_content_hash: Optional[str] = None
    statement_storage_key: Optional[str] = None
    statement_sha256: Optional[str] = None
    register_storage_key: Optional[str] = None
    register_sha256: Optional[str] = None
    # The sign-off register exactly as signed (rows of service.register_rows)
    signed_register: Optional[list[dict[str, Any]]] = Field(default=None, sa_column=Column(JSON, nullable=True))
    withdrawn_by: Optional[int] = Field(default=None, foreign_key="user.id")
    withdrawn_at: Optional[datetime] = None
    withdrawn_reason: Optional[str] = None
    superseded_by_id: Optional[int] = Field(default=None, foreign_key="assurance_engagements.id")


class AssuranceMember(SQLModel, table=True):
    __tablename__ = "assurance_members"
    __table_args__ = (UniqueConstraint("engagement_id", "user_id", name="uq_assurance_member"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="assurance_engagements.id", index=True)
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str
    added_by: int = Field(foreign_key="user.id")
    added_at: datetime = Field(default_factory=datetime.utcnow)
    independence_declared_at: Optional[datetime] = None
    removed_at: Optional[datetime] = None
    removed_by: Optional[int] = Field(default=None, foreign_key="user.id")


class AssuranceItem(SQLModel, table=True):
    """Workpaper for one BRSR section in scope."""
    __tablename__ = "assurance_items"
    __table_args__ = (UniqueConstraint("engagement_id", "section_code", name="uq_assurance_item"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    engagement_id: int = Field(foreign_key="assurance_engagements.id", index=True)
    section_code: str
    status: str = Field(default="open")  # open | prepared | reviewed
    procedures: Optional[str] = None
    conclusion: Optional[str] = None  # no_exceptions | exceptions
    prepared_by: Optional[int] = Field(default=None, foreign_key="user.id")
    prepared_at: Optional[datetime] = None
    reviewed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    reviewed_at: Optional[datetime] = None
    review_note: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class AssuranceRequest(SQLModel, table=True):
    """Information request (evidence asked of the client)."""
    __tablename__ = "assurance_requests"
    __table_args__ = (Index("ix_assurance_request_eng_status", "engagement_id", "status"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_req_pid, unique=True, index=True)
    engagement_id: int = Field(foreign_key="assurance_engagements.id")
    section_code: str
    question_id: Optional[str] = None
    title: str
    detail: Optional[str] = None
    due_date: Optional[date] = None
    status: str = Field(default="open")  # open | responded | accepted
    requested_by: int = Field(foreign_key="user.id")
    requested_at: datetime = Field(default_factory=datetime.utcnow)
    response_text: Optional[str] = None
    responded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    responded_at: Optional[datetime] = None
    closed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    closed_at: Optional[datetime] = None
    # Every send-back is kept: [{"by": user_id, "at": iso, "note": str}]
    history: list[dict[str, Any]] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class AssuranceFinding(SQLModel, table=True):
    __tablename__ = "assurance_findings"
    __table_args__ = (Index("ix_assurance_finding_eng_status", "engagement_id", "status"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_find_pid, unique=True, index=True)
    engagement_id: int = Field(foreign_key="assurance_engagements.id")
    section_code: str
    question_id: Optional[str] = None
    severity: str  # observation | misstatement | control_deficiency
    description: str
    status: str = Field(default="open")  # open | responded | corrected | uncorrected
    raised_by: int = Field(foreign_key="user.id")
    raised_at: datetime = Field(default_factory=datetime.utcnow)
    management_response: Optional[str] = None
    responded_by: Optional[int] = Field(default=None, foreign_key="user.id")
    responded_at: Optional[datetime] = None
    resolved_by: Optional[int] = Field(default=None, foreign_key="user.id")
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
