"""Report Generation foundation.

Reuses the exact proven patterns from Dataset/DatasetVersion (immutable
versioning), Layer 1 (append-only KpiValue references, never duplicated),
and ProcessingJob (async generation, idempotency).

CRITICAL, explicitly required by the architecture review: Report.
current_version_id is a CONVENIENCE POINTER ONLY, exactly like the
already-known-unreliable Dataset.current_version_id. It must NEVER be
used to determine which version is authoritative for generation,
approval, or publication. The authoritative version for any purpose is
always resolved by explicit status lookup (see
app/services/report_snapshot_service.py), never by this pointer field.

Snapshot design: references (IDs), never duplicated data. KpiValue
already permanently carries kpi_definition_version — the snapshot does
NOT separately store KPI definition versions, since that would be
redundant, driftable state duplicating what each referenced KpiValue row
already guarantees permanently.

Snapshot integrity depends on a standing invariant, confirmed true today
by direct inspection of kpi_extraction_service.py: KpiValue rows are
never mutated or hard-deleted once written. If this invariant is ever
violated by a future feature, snapshot reproducibility breaks silently.
The content_hash field (see ReportSnapshot) exists specifically to make
such drift detectable after the fact, as defense in depth.
"""
from datetime import datetime, date
from typing import Optional, Any
from sqlmodel import SQLModel, Field, Column, Index, UniqueConstraint
from sqlalchemy import JSON
from app.core.public_ids import generate_public_id


def _report_pid() -> str: return generate_public_id("rp_")
def _report_version_pid() -> str: return generate_public_id("rpv_")
def _artifact_pid() -> str: return generate_public_id("rpa_")
def _template_pid() -> str: return generate_public_id("rpt_")


REPORT_VERSION_STATUSES = (
    "draft", "generating", "generated", "under_review",
    "changes_requested", "approved", "published", "failed",
)


class Report(SQLModel, table=True):
    """Logical report container — the enduring identity across
    regenerations. Mirrors Dataset's own role relative to DatasetVersion."""
    __tablename__ = "reports"
    __table_args__ = (
        Index("ix_report_company_type_period", "company_id", "report_type", "reporting_period_start"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_report_pid, unique=True, index=True)
    company_id: int = Field(foreign_key="company.id", index=True)
    report_type: str = Field(default="esg_management_report")
    reporting_period_start: date  # matches Dataset's own field type exactly, for direct comparability
    reporting_period_end: date
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    # CONVENIENCE POINTER ONLY -- see module docstring. Never authoritative.
    current_version_id: Optional[int] = Field(default=None, foreign_key="report_versions.id")
    status: str = Field(default="draft")


class ReportVersion(SQLModel, table=True):
    """One immutable generation attempt. New regeneration = new row, never
    a mutation of an existing one -- identical discipline to DatasetVersion."""
    __tablename__ = "report_versions"
    __table_args__ = (
        UniqueConstraint("report_id", "version_number", name="uq_report_version_number"),
        Index("ix_report_version_report_status", "report_id", "status"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_report_version_pid, unique=True, index=True)
    report_id: int = Field(foreign_key="reports.id", index=True)
    version_number: int
    status: str = Field(default="draft", index=True)
    template_id: int = Field(foreign_key="report_templates.id")
    generator_version: str = Field(default="v1")
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    generation_started_at: Optional[datetime] = None
    generation_completed_at: Optional[datetime] = None
    reviewed_at: Optional[datetime] = None
    approved_at: Optional[datetime] = None
    published_at: Optional[datetime] = None
    reviewer_user_id: Optional[int] = Field(default=None, foreign_key="user.id")
    failure_reason: Optional[str] = None


class ReportSnapshot(SQLModel, table=True):
    """The frozen, immutable manifest of exactly what data a ReportVersion
    was generated from. One-to-one with ReportVersion. Never modified
    after creation -- a correction always means a new ReportVersion (and
    therefore a new ReportSnapshot), never an edit to this row.

    Stores REFERENCES (ids), not duplicated KPI data -- see module
    docstring. content_hash is a deterministic hash of the referenced
    KpiValue ids+values, computed once at creation, enabling detection
    (not prevention) of any future violation of the append-only
    KpiValue invariant this design depends on.
    """
    __tablename__ = "report_snapshots"
    __table_args__ = (
        UniqueConstraint("report_version_id", name="uq_report_snapshot_version"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    report_version_id: int = Field(foreign_key="report_versions.id", index=True)
    dataset_version_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    kpi_value_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    evidence_file_ids: list[int] = Field(default_factory=list, sa_column=Column(JSON))
    configuration: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON))
    content_hash: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReportArtifact(SQLModel, table=True):
    """A generated file for a ReportVersion. Deliberately ONE-TO-MANY
    (no unique constraint on report_version_id alone) -- PDF/XLSX are
    explicitly deferred, not blocked, and will exist later; modeling
    one-to-one now would guarantee a migration the moment they ship."""
    __tablename__ = "report_artifacts"
    __table_args__ = (
        UniqueConstraint("report_version_id", "format", name="uq_report_artifact_version_format"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_artifact_pid, unique=True, index=True)
    report_version_id: int = Field(foreign_key="report_versions.id", index=True)
    format: str = Field(default="docx")
    storage_key: str
    original_filename: str
    mime_type: str
    size_bytes: int
    sha256_checksum: str
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ReportTemplate(SQLModel, table=True):
    """Versioned template. Once used by any ReportVersion, immutable --
    a template change is always a new version, never an edit."""
    __tablename__ = "report_templates"
    __table_args__ = (
        UniqueConstraint("report_type", "version", name="uq_report_template_type_version"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_template_pid, unique=True, index=True)
    report_type: str
    version: int
    name: str
    is_active: bool = Field(default=True)
    created_by: int = Field(foreign_key="user.id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
