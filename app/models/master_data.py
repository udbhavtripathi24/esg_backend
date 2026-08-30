"""Master data models (Stage 4): sites, business units, departments.

All company-scoped with strict tenant isolation. `code` is client-supplied and
unique within a company (a client may have their own site coding scheme).
Business units and departments support hierarchy via optional parent_id — most
orgs are flat but the largest clients (banks, energy majors) have nested BUs.

Design note: kept in ONE file rather than three because they share the same
company-scoped, soft-deletable, hierarchical pattern. Splitting them would
triple the boilerplate without clarifying anything.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import SQLModel, Field, UniqueConstraint, Index
from app.core.public_ids import generate_public_id


def _site_pid() -> str: return generate_public_id("st_")
def _bu_pid() -> str: return generate_public_id("bu_")
def _dept_pid() -> str: return generate_public_id("dept_")


class Site(SQLModel, table=True):
    __tablename__ = "sites"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_site_company_code"),
        Index("ix_site_company_active", "company_id", "is_active"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_site_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    code: str  # client-supplied, unique within company
    name: str
    country: Optional[str] = None
    region: Optional[str] = None
    site_type: Optional[str] = None  # "office" | "plant" | "warehouse" | "other"
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class BusinessUnit(SQLModel, table=True):
    __tablename__ = "business_units"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_bu_company_code"),
        Index("ix_bu_company_parent", "company_id", "parent_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_bu_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    code: str
    name: str
    parent_id: Optional[int] = Field(default=None, foreign_key="business_units.id")
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class Department(SQLModel, table=True):
    __tablename__ = "departments"
    __table_args__ = (
        UniqueConstraint("company_id", "code", name="uq_dept_company_code"),
        Index("ix_dept_company_bu", "company_id", "business_unit_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_dept_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    business_unit_id: Optional[int] = Field(default=None, foreign_key="business_units.id")
    code: str
    name: str
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)
