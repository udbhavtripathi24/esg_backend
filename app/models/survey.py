"""Surveys and assessments — one engine for both.

A **survey** collects responses (stakeholder feedback, employee well-being,
customer satisfaction). An **assessment** is a survey whose answers are
scored (points per answer, weighted sections, maturity bands), e.g. an ESG
maturity self-assessment or a supplier due-diligence questionnaire.

Design decisions:
- The questions live in ``definition`` (JSON, validated by
  app/surveys/schema.py) with stable ids. Answers reference those ids, so
  wording can be corrected after launch without breaking results. Once a
  survey has responses its *structure* (questions, types, options, logic)
  is locked; scoring can still change and every response is re-scored.
- Respondents are either platform users (invited individually, by
  department, or the whole company) or people without an account (a
  personal link per e-mail address, or one public link). Link tokens are
  random and only their SHA-256 is stored; a personal link is shown once
  and can be reset. The public link is meant to be shared, so it is kept.
- Anonymous surveys never link a submitted response to a person: the
  invitation records *that* someone answered, the response records *what*
  was answered, and the link between them is removed on submission.
- Nothing is hard-deleted once anyone has answered; surveys are archived.
"""
from datetime import datetime
from typing import Any, Optional

from sqlmodel import JSON, Column, Field, Index, SQLModel, UniqueConstraint

from app.core.public_ids import generate_public_id


def _sv_pid() -> str: return generate_public_id("sv_")
def _si_pid() -> str: return generate_public_id("si_")
def _sr_pid() -> str: return generate_public_id("sr_")
def _sf_pid() -> str: return generate_public_id("sf_")


SURVEY_KINDS = ("survey", "assessment")
SURVEY_STATUSES = ("draft", "open", "closed", "archived")
INVITE_CHANNELS = ("user", "email")
INVITE_STATUSES = ("pending", "started", "submitted")
RESPONSE_STATUSES = ("in_progress", "submitted")


class Survey(SQLModel, table=True):
    __tablename__ = "surveys"
    __table_args__ = (Index("ix_survey_company_status", "company_id", "status"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_sv_pid, unique=True, index=True)
    # Owner. A template shared across the Deloitte organisation has no company.
    company_id: Optional[int] = Field(default=None, foreign_key="company.id", index=True)
    organization_id: Optional[int] = Field(default=None, foreign_key="organizations.id")
    is_template: bool = Field(default=False, index=True)
    kind: str = Field(default="survey")
    category: str = Field(default="Custom")
    title: str
    description: Optional[str] = None
    welcome: Optional[str] = None       # shown before the first question
    thank_you: Optional[str] = None     # shown after submission
    definition: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    revision: int = Field(default=1)    # optimistic concurrency for the builder
    status: str = Field(default="draft", index=True)
    opens_at: Optional[datetime] = None
    closes_at: Optional[datetime] = None
    anonymous: bool = Field(default=False)
    open_to_company: bool = Field(default=False)   # any signed-in user of the company may answer
    show_score_to_respondent: bool = Field(default=False)
    public_token: Optional[str] = Field(default=None, index=True)  # the shareable public link, when enabled
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    published_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    archived_at: Optional[datetime] = None


class SurveyInvite(SQLModel, table=True):
    __tablename__ = "survey_invites"
    __table_args__ = (
        UniqueConstraint("survey_id", "user_id", name="uq_survey_invite_user"),
        UniqueConstraint("survey_id", "email", name="uq_survey_invite_email"),
        Index("ix_survey_invite_survey_status", "survey_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_si_pid, unique=True, index=True)
    survey_id: int = Field(foreign_key="surveys.id")
    channel: str  # user | email
    user_id: Optional[int] = Field(default=None, foreign_key="user.id", index=True)
    email: Optional[str] = None
    name: Optional[str] = None
    group_label: Optional[str] = None  # department, supplier, site… used to break results down
    token_hash: Optional[str] = Field(default=None, index=True)  # personal link (email channel)
    status: str = Field(default="pending")
    invited_by: int = Field(foreign_key="user.id")
    invited_at: datetime = Field(default_factory=datetime.utcnow)
    started_at: Optional[datetime] = None
    submitted_at: Optional[datetime] = None
    last_reminded_at: Optional[datetime] = None


class SurveyResponse(SQLModel, table=True):
    __tablename__ = "survey_responses"
    __table_args__ = (Index("ix_survey_response_survey_status", "survey_id", "status"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_sr_pid, unique=True, index=True)
    survey_id: int = Field(foreign_key="surveys.id")
    # Identity — all None for an anonymous survey once submitted (the
    # invitation then only records that its person has answered).
    invite_id: Optional[int] = Field(default=None, foreign_key="survey_invites.id")
    user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    respondent_name: Optional[str] = None
    group_label: Optional[str] = None  # kept for anonymous surveys (breakdowns only above a minimum group size)
    channel: str = Field(default="user")  # user | email | public
    resume_key_hash: Optional[str] = Field(default=None, index=True)  # public-link drafts
    status: str = Field(default="in_progress")
    answers: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    score: Optional[dict[str, Any]] = Field(default=None, sa_column=Column(JSON))
    revision: int = Field(default=1)
    started_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    submitted_at: Optional[datetime] = None


class SurveyFile(SQLModel, table=True):
    """A file attached as the answer to a file-upload question."""
    __tablename__ = "survey_files"
    __table_args__ = (Index("ix_survey_file_response", "response_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_sf_pid, unique=True, index=True)
    survey_id: int = Field(foreign_key="surveys.id")
    response_id: int = Field(foreign_key="survey_responses.id")
    question_id: str
    storage_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_checksum: str
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = None
