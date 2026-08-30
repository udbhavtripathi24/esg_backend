"""Dataset domain (Stage 4): datasets, versions, files.

Design rules encoded here:
- A dataset is a logical container; its FILES live in versions.
- A version is IMMUTABLE once its status leaves 'draft'. Enforced in the service
  layer, not just documented (see app/services/dataset_service.py).
- current_version_id points at the "active" version for UI convenience; queries
  that need history walk dataset_versions directly.
- Files have a role: 'data' (parsed later) or 'evidence' (never parsed for
  numbers, per approved decision 4).
- soft-delete on datasets; versions are never deleted (audit).
"""
from datetime import datetime, date
from typing import Optional
from sqlmodel import SQLModel, Field, UniqueConstraint, Index, Column
from sqlalchemy import JSON
from app.core.public_ids import generate_public_id


def _ds_pid() -> str: return generate_public_id("ds_")
def _dv_pid() -> str: return generate_public_id("dv_")
def _df_pid() -> str: return generate_public_id("df_")


# Dataset status: coarse-grained state visible in UI.
# Version status carries the fine-grained workflow.
DATASET_STATUSES = ("draft", "in_progress", "submitted", "approved", "rejected", "archived")
VERSION_STATUSES = ("draft", "validated", "submitted", "under_review",
                    "approved", "changes_requested", "rejected")


class Dataset(SQLModel, table=True):
    __tablename__ = "datasets"
    __table_args__ = (
        Index("ix_dataset_company_status", "company_id", "status"),
        Index("ix_dataset_company_period", "company_id", "reporting_period_start"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_ds_pid, unique=True, index=True)

    company_id: int = Field(foreign_key="company.id", index=True)
    site_id: Optional[int] = Field(default=None, foreign_key="sites.id")
    business_unit_id: Optional[int] = Field(default=None, foreign_key="business_units.id")
    department_id: Optional[int] = Field(default=None, foreign_key="departments.id")

    upload_type_id: int = Field(foreign_key="upload_types.id", index=True)

    reporting_period_start: date
    reporting_period_end: date
    reporting_frequency: str = Field(default="quarterly")  # 'monthly'|'quarterly'|'annual'|'custom'

    status: str = Field(default="draft", index=True)
    current_version_id: Optional[int] = Field(default=None, foreign_key="dataset_versions.id")

    notes: Optional[str] = None
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None, index=True)


class DatasetVersion(SQLModel, table=True):
    __tablename__ = "dataset_versions"
    __table_args__ = (
        UniqueConstraint("dataset_id", "version_number", name="uq_dsv_dataset_versionno"),
        Index("ix_dsv_dataset_status", "dataset_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_dv_pid, unique=True, index=True)
    dataset_id: int = Field(foreign_key="datasets.id", index=True)
    version_number: int  # 1, 2, 3...
    status: str = Field(default="draft")
    uploaded_by: int = Field(foreign_key="user.id")
    submitted_at: Optional[datetime] = None
    notes: Optional[str] = None
    # Cached summary of latest review decision so listings don't join.
    # Written by review_service.record_decision inside the same transaction.
    # Shape: {"status": "approved", "reviewer_id": 5, "decided_at": "..."}
    review_decision_summary: Optional[dict] = Field(default=None, sa_column=Column(JSON))
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)


class DatasetFile(SQLModel, table=True):
    __tablename__ = "dataset_files"
    __table_args__ = (
        Index("ix_df_version_role", "dataset_version_id", "role"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_df_pid, unique=True, index=True)
    dataset_version_id: int = Field(foreign_key="dataset_versions.id", index=True)
    role: str  # 'data' | 'evidence'
    storage_key: str  # object storage key, includes company_id prefix
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_checksum: str = Field(index=True)  # indexed for dedup queries later
    uploaded_by: int = Field(foreign_key="user.id")
    uploaded_at: datetime = Field(default_factory=datetime.utcnow)
    deleted_at: Optional[datetime] = Field(default=None)
