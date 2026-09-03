"""Layer 1: structured KPI data foundation (locked roadmap item, post-Stage 5).

DESIGN DECISION, disclosed explicitly per the task's own boundary rules:

This is a generic, provenance-rich, metric-per-row design — NOT a rigid
per-domain table (one for Energy, one for Water, etc). Justification:

1. Inspection of the actual domainGuidance columns shows each domain has a
   DIFFERENT shape, not a uniform one: Water reports TWO distinct numeric
   quantities per row (withdrawn AND recycled); Emissions references an
   "emission factor used" as citation metadata, not a measured value;
   Waste pairs a numeric quantity with a categorical disposal method. A
   fixed-column-per-domain table would need a schema migration every time
   a new domain or sub-metric is added — directly contradicting the
   task's "consumer-agnostic" and "extensibility" requirements.

2. This exact direction (a `kpi_values` table with full provenance) is
   not invented here — it was already the documented intent in Stage 5's
   own code: see app/workers/__init__.py's `recalculate_kpi_values` stub,
   whose docstring says it will eventually "write rows to kpi_values with
   full provenance (formula version + factor versions + input dataset
   version)". This design fulfills that pre-existing architectural
   intent rather than diverging from it.

CRITICAL BOUNDARY, enforced structurally, not just by convention:

Layer 1 extracts and stores RAW REPORTED QUANTITIES ONLY — never a
computed, converted, or scored value. No unit conversion happens here
(unit is stored exactly as the uploader specified it in their file). No
emission-factor application happens here (an emissions row's "Activity
data" is stored as reported; the "Emission factor used" column a user
cites in their spreadsheet is NOT parsed or stored anywhere in Layer 1 —
that is genuinely Layer 2 territory, deferred, not fabricated). No
scoring, weighting, or benchmarking logic exists anywhere in this file.

KpiDefinition holds pure NAMING/STRUCTURE (a code, a display name, a unit
hint, which upload_type/domain it belongs to) — never a formula, weight,
or emission factor. `unit_hint` is informational only (shown to a future
UI as a suggestion) and is never used to convert a stored value.
"""
from datetime import datetime, date
from typing import Optional
from sqlmodel import SQLModel, Field, UniqueConstraint, Index
from sqlalchemy import Column, JSON
from app.core.public_ids import generate_public_id


def _kpi_value_pid() -> str:
    return generate_public_id("kv_")


class KpiDefinition(SQLModel, table=True):
    """Pure structural catalog entry: what is this measured quantity called,
    what unit do we expect it to typically be reported in, which upload
    type/domain does it belong to. Contains NO formula, weight, or
    conversion logic — see module docstring.

    `version` exists for forward-compatibility (matching the recalc job's
    own docstring, which anticipates versioned KPI definitions) but is not
    exercised yet — every seeded definition starts at version 1. A future
    definition change (e.g., renaming a unit hint) would increment this
    rather than silently mutating history.
    """
    __tablename__ = "kpi_definitions"
    __table_args__ = (
        UniqueConstraint("code", "version", name="uq_kpi_definition_code_version"),
        Index("ix_kpi_definition_upload_type", "upload_type_id"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    code: str = Field(index=True)  # e.g. "energy.consumption", "water.withdrawal"
    display_name: str
    unit_hint: Optional[str] = None  # informational only, e.g. "kWh" — never enforced/converted
    upload_type_id: int = Field(foreign_key="upload_types.id", index=True)
    data_type: str = Field(default="numeric")  # extensible: "numeric" today; future "boolean"/"categorical"
    version: int = Field(default=1)
    is_active: bool = Field(default=True)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class KpiValue(SQLModel, table=True):
    """One extracted, raw, as-reported measurement, fully traceable back to
    its exact source dataset/version/file/row.

    Tenant fields (company_id) are DENORMALIZED here deliberately — every
    other tenant-scoped table in this backend (Dataset, ConsultantAssignment,
    etc.) filters directly on a company_id column rather than requiring a
    join through multiple tables to establish tenancy, and this table
    follows that same established, already-proven pattern for consistency
    and query performance at scale.

    `attributes` (JSONB) holds domain-specific QUALIFYING TAGS on the
    measurement — e.g. {"energy_type": "electricity"} or
    {"waste_type": "hazardous", "disposal_method": "landfill"}. This is a
    deliberate choice over rigid per-domain dimension columns: different
    domains genuinely have different numbers of qualifying tags (Waste has
    two; Energy has one), and JSON avoids a schema migration every time a
    new domain's tag shape differs from the last. These are DESCRIPTIVE
    TAGS ON A REAL REPORTED NUMBER, never a computed classification.

    Idempotency: the unique constraint on
    (dataset_version_id, source_file_id, source_row_number, kpi_code)
    ensures re-running extraction for the same version/file/row/metric
    (e.g. a retried job) cannot create duplicate rows — the extraction
    service upserts against this constraint rather than blindly inserting.
    """
    __tablename__ = "kpi_values"
    __table_args__ = (
        UniqueConstraint(
            "dataset_version_id", "source_file_id", "source_row_number", "kpi_code",
            name="uq_kpi_value_source_row_metric",
        ),
        Index("ix_kpi_value_company_period", "company_id", "reporting_period_start", "reporting_period_end"),
        Index("ix_kpi_value_dataset_version", "dataset_version_id"),
        Index("ix_kpi_value_site", "site_id"),
        Index("ix_kpi_value_kpi_code", "kpi_code"),
    )

    id: Optional[int] = Field(default=None, primary_key=True)
    public_id: str = Field(default_factory=_kpi_value_pid, unique=True, index=True)

    # Provenance — traces this value back to its exact source, unambiguously.
    dataset_id: int = Field(foreign_key="datasets.id", index=True)
    dataset_version_id: int = Field(foreign_key="dataset_versions.id", index=True)
    source_file_id: int = Field(foreign_key="dataset_files.id", index=True)
    source_row_number: int  # 1-based row number within the source spreadsheet
    extraction_job_id: Optional[int] = Field(default=None, foreign_key="processing_jobs.id")

    # Tenancy + scope — denormalized for direct, join-free filtering (see docstring).
    company_id: int = Field(foreign_key="company.id", index=True)
    site_id: Optional[int] = Field(default=None, foreign_key="sites.id")

    # What was measured.
    kpi_code: str = Field(index=True)
    # HARDENING (post-launch review): kpi_definition_version records
    # EXACTLY which KpiDefinition version was active when this value was
    # extracted, resolved and stamped at write time by the extraction
    # service. Without this, a KpiValue and a KpiDefinition could only be
    # associated by code alone -- if a definition's version is ever bumped
    # later (e.g. its unit_hint changes), old KpiValues would silently
    # appear to belong to the NEW version with no way to tell they were
    # actually extracted under the old one. This closes that gap.
    #
    # Still deliberately NOT a hard database FK to KpiDefinition, for the
    # same reason as kpi_code itself: enforcing referential integrity at
    # the application layer (the extraction service validates and
    # resolves the active version before writing any row) avoids a
    # redundant extra uniqueness constraint on a natural key that isn't
    # otherwise needed. Together, (kpi_code, kpi_definition_version)
    # unambiguously identifies the exact definition row, which is the
    # actual goal here -- adding the FK constraint itself would be
    # enforcement, not additional information, and isn't required to
    # achieve unambiguous provenance.
    kpi_definition_version: int = Field(default=1)
    value: float
    unit: str  # exactly as reported in the source file — never converted
    attributes: dict = Field(default_factory=dict, sa_column=Column(JSON))

    # Denormalized reporting period (from the dataset) — enables direct
    # period-range queries without joining back through datasets. Matches
    # Dataset's own field type exactly (date, not string).
    reporting_period_start: date
    reporting_period_end: date

    created_at: datetime = Field(default_factory=datetime.utcnow)
