"""Company model (Stage 3 — extended to approved ERD core fields).

Core company only. Subscription, frameworks, contact person, and progress% are
DEFERRED to later stages (documented in docs/DATA_MODEL.md) and remain on the
frontend's mock data until then.
"""
from datetime import datetime, date
from typing import Optional, List, TYPE_CHECKING
from sqlmodel import SQLModel, Field, Relationship

if TYPE_CHECKING:
    from app.models.user import User
    from app.models.organization import Organization


class CompanyBase(SQLModel):
    name: str
    industry: Optional[str] = None
    country: Optional[str] = None
    sector: Optional[str] = None
    structure: Optional[str] = None  # "Listed" | "Unlisted"
    plan: str = "Basic"              # "Basic" | "Professional" | "Enterprise"
    status: str = "Pending"          # "Pending" | "Approved" | "Rejected"


class Company(CompanyBase, table=True):
    id: Optional[int] = Field(default=None, primary_key=True)
    organization_id: Optional[int] = Field(default=None, foreign_key="organizations.id", index=True)
    registration_date: Optional[date] = Field(default_factory=date.today)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)  # soft delete
    # Configurable segregation of duties (Stage 4 decision 6): when true, the
    # user who uploaded a dataset version cannot also approve it. Default true;
    # enforced in the review workflow (Stage 5).
    enforce_upload_approval_segregation: bool = Field(default=True)

    organization: Optional["Organization"] = Relationship(back_populates="companies")
    users: List["User"] = Relationship(back_populates="company")


class CompanyCreate(CompanyBase):
    organization_id: Optional[int] = None


class CompanyRead(CompanyBase):
    id: int
    organization_id: Optional[int]
    registration_date: Optional[date]
    created_at: datetime
    updated_at: datetime


class CompanyUpdate(SQLModel):
    name: Optional[str] = None
    industry: Optional[str] = None
    country: Optional[str] = None
    sector: Optional[str] = None
    structure: Optional[str] = None
    plan: Optional[str] = None
    status: Optional[str] = None
