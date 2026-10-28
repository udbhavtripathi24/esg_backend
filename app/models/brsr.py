"""BRSR questionnaire models.

A company answers the BRSR once per financial year. Answers are stored
one row per question (the question bank in app/brsr/question_bank.py is
the schema for the JSON ``value``). Review happens per section (A, B,
P1..P9): a section is submitted as a unit, approved or sent back as a
unit, and only a fully approved response set can be turned into a BRSR
report.

Design decisions:
- ``brsr_answers`` holds the current value; ``brsr_answer_history`` is
  append-only and receives a row on every change, so "who changed this
  figure, when, and from what" is always answerable for assurance.
- ``revision`` on each answer gives optimistic concurrency: two people
  editing the same question cannot silently overwrite each other — the
  second save gets a 409 with the current value.
- Computed cells (totals, percentages, intensities) are never stored.
  They are derived on read by app/brsr/engine.py, so they can never
  disagree with their inputs.
- Review comments are internal and are never printed in the report
  (the reference filing leaked "Foot Notes: ok" into the published
  document; keeping comments in their own table makes that impossible).
"""
from datetime import datetime
from typing import Any, Optional

from sqlmodel import SQLModel, Field, Column, Index, UniqueConstraint, JSON

from app.core.public_ids import generate_public_id


def _set_pid() -> str: return generate_public_id("brs_")
def _comment_pid() -> str: return generate_public_id("brc_")


# "internally_approved" is used only when the company requires an internal
# (client-side) review before Deloitte's: draft -> submitted ->
# internally_approved -> approved.
SECTION_STATUSES = ("draft", "submitted", "internally_approved", "approved", "changes_requested")
EDITABLE_STATUSES = ("draft", "changes_requested")


class BrsrResponseSet(SQLModel, table=True):
    __tablename__ = "brsr_response_sets"
    __table_args__ = (UniqueConstraint("company_id", "financial_year", name="uq_brsr_set_company_fy"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_set_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    financial_year: str  # "2024-25"
    bank_version: str
    carried_forward_from_id: Optional[int] = Field(default=None, foreign_key="brsr_response_sets.id")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BrsrSection(SQLModel, table=True):
    __tablename__ = "brsr_sections"
    __table_args__ = (UniqueConstraint("response_set_id", "section_code", name="uq_brsr_section"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id", index=True)
    section_code: str
    status: str = Field(default="draft", index=True)
    submission_count: int = Field(default=0)
    submitted_by: Optional[int] = Field(default=None, foreign_key="user.id")
    submitted_at: Optional[datetime] = None
    decided_by: Optional[int] = Field(default=None, foreign_key="user.id")
    decided_at: Optional[datetime] = None
    decision_note: Optional[str] = None
    # Tier 1 (client internal review), when the company requires it
    internal_reviewed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    internal_reviewed_at: Optional[datetime] = None
    internal_review_note: Optional[str] = None
    # Fingerprint of the figures built from the data sheets when Deloitte
    # approved the section. If approved sheet data changes afterwards the
    # section is flagged and the report is held until it is re-approved.
    approved_data_hash: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BrsrAnswer(SQLModel, table=True):
    __tablename__ = "brsr_answers"
    __table_args__ = (
        UniqueConstraint("response_set_id", "question_id", name="uq_brsr_answer"),
        Index("ix_brsr_answer_set_section", "response_set_id", "section_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id")
    section_code: str
    question_id: str
    value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    revision: int = Field(default=1)
    updated_by: int = Field(foreign_key="user.id")
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class BrsrAnswerHistory(SQLModel, table=True):
    __tablename__ = "brsr_answer_history"
    __table_args__ = (Index("ix_brsr_history_set_question", "response_set_id", "question_id"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id")
    question_id: str
    revision: int
    value: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    source: str = Field(default="edit")  # edit | carry_forward
    changed_by: int = Field(foreign_key="user.id")
    changed_at: datetime = Field(default_factory=datetime.utcnow)


class BrsrComment(SQLModel, table=True):
    __tablename__ = "brsr_comments"
    __table_args__ = (Index("ix_brsr_comment_set_section", "response_set_id", "section_code"),)

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_comment_pid, unique=True, index=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id")
    section_code: str
    question_id: Optional[str] = None  # None = section-level comment
    body: str
    kind: str = Field(default="review")  # review | decision
    author_id: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    resolved_at: Optional[datetime] = None
    resolved_by: Optional[int] = Field(default=None, foreign_key="user.id")


# ---------------------------------------------------------------------------
# Per-question workflow (bank v3)
# ---------------------------------------------------------------------------
# The questionnaire is answered by department SPOCs and each answer is
# approved by the department's reviewer before the section can go to
# Deloitte:  open -> submitted -> approved   (or -> returned -> open)
# Any change to an answer puts it back to "open".

QUESTION_STATUSES = ("open", "submitted", "approved", "returned")
DEPARTMENT_ROLES = ("spoc", "reviewer")


class BrsrDepartmentRole(SQLModel, table=True):
    """Who answers (SPOC) and who approves (reviewer) a department's BRSR
    questions at a company. People are platform users; names are never
    imported from engagement files."""
    __tablename__ = "brsr_department_roles"
    __table_args__ = (UniqueConstraint("company_id", "department", "user_id", "role", name="uq_brsr_dept_role"),
                      Index("ix_brsr_dept_role_company", "company_id"))

    id: Optional[int] = Field(default=None, primary_key=True)
    company_id: int = Field(foreign_key="company.id")
    department: str
    user_id: int = Field(foreign_key="user.id", index=True)
    role: str  # spoc | reviewer
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)


class BrsrQuestionStatus(SQLModel, table=True):
    __tablename__ = "brsr_question_status"
    __table_args__ = (UniqueConstraint("response_set_id", "question_id", name="uq_brsr_question_status"),
                      Index("ix_brsr_qstatus_set_section", "response_set_id", "section_code"))

    id: Optional[int] = Field(default=None, primary_key=True)
    response_set_id: int = Field(foreign_key="brsr_response_sets.id")
    section_code: str
    question_id: str
    status: str = Field(default="open")
    answer_revision: Optional[int] = None  # the revision that was submitted / approved
    submitted_by: Optional[int] = Field(default=None, foreign_key="user.id")
    submitted_at: Optional[datetime] = None
    reviewed_by: Optional[int] = Field(default=None, foreign_key="user.id")
    reviewed_at: Optional[datetime] = None
    note: Optional[str] = None
    updated_at: datetime = Field(default_factory=datetime.utcnow)
