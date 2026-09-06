"""Report snapshot resolution — the single most important correctness
boundary in Report Generation.

Resolves EXACTLY what approved data a report is generated from, at
creation time, using the SAME authoritative-version logic already
proven for Dashboard/Analytics/Admin Console. This module deliberately
contains NO independent approved-version logic of its own — it imports
and calls latest_approved_version_ids() directly. There must be exactly
one implementation of "what counts as approved" in this codebase.

CRITICAL, explicitly mandated by the architecture review and
implementation authorization: this module NEVER reads
Dataset.current_version_id or Report.current_version_id to determine
authoritative data. Both are convenience pointers only. The single
authoritative source is latest_approved_version_ids(), which selects by
(status == 'approved', highest version_number) — a version that is no
longer "current" by pointer but was the last one ever approved is still
correctly selected; a version that IS "current" by pointer but is only a
draft/submitted/rejected/changes_requested attempt is correctly ignored.
"""
import hashlib
import json
from datetime import date
from sqlmodel import Session, select
from app.models.dataset import Dataset
from app.models.kpi import KpiValue
from app.services.kpi_aggregation import latest_approved_version_ids


def resolve_snapshot_inputs(session: Session, company_id: int, period_start: date, period_end: date) -> dict:
    """Resolve the exact, authoritative inputs for a report snapshot.

    Returns {
        "dataset_version_ids": [...],   # the authoritative version per dataset, never current_version_id
        "kpi_value_ids": [...],         # every KpiValue row belonging to those authoritative versions
        "evidence_file_ids": [...],     # every DatasetFile referenced as a source by those KpiValues
    }

    Contains NO business judgment about whether the resolved data is
    "sufficient" for reporting -- that is Reporting Readiness's job, a
    separate, already-built concern. This function only answers "what is
    the exact authoritative approved data for this company/period,"
    nothing more.
    """
    dataset_ids = session.exec(
        select(Dataset.id).where(
            Dataset.company_id == company_id,
            Dataset.reporting_period_start == period_start,
            Dataset.reporting_period_end == period_end,
            Dataset.deleted_at.is_(None),
        )
    ).all()

    # THE authoritative resolution -- never Dataset.current_version_id.
    version_map = latest_approved_version_ids(session, list(dataset_ids))
    authoritative_version_ids = sorted(set(version_map.values()))

    if not authoritative_version_ids:
        return {"dataset_version_ids": [], "kpi_value_ids": [], "evidence_file_ids": []}

    kpi_rows = session.exec(
        select(KpiValue.id, KpiValue.source_file_id).where(
            KpiValue.dataset_version_id.in_(authoritative_version_ids)
        )
    ).all()
    kpi_value_ids = sorted({r[0] for r in kpi_rows})
    evidence_file_ids = sorted({r[1] for r in kpi_rows if r[1] is not None})

    return {
        "dataset_version_ids": authoritative_version_ids,
        "kpi_value_ids": kpi_value_ids,
        "evidence_file_ids": evidence_file_ids,
    }


def compute_content_hash(session: Session, kpi_value_ids: list[int]) -> str:
    """A deterministic hash of the exact referenced KpiValue ids AND their
    current values, computed once at snapshot creation. This is defense
    in depth, not a prevention mechanism: KpiValue rows are append-only
    today (confirmed by direct inspection of kpi_extraction_service.py,
    which never updates or deletes an existing row) -- so this hash
    should never actually change for a given snapshot. If it ever DID
    differ from what's stored, that would mean the append-only invariant
    was violated by some future feature, and this hash makes that
    detectable rather than silently invisible.
    """
    if not kpi_value_ids:
        return hashlib.sha256(b"[]").hexdigest()
    rows = session.exec(
        select(KpiValue.id, KpiValue.value, KpiValue.unit).where(KpiValue.id.in_(kpi_value_ids))
    ).all()
    # Sort by id for determinism -- the hash must not depend on query/row order.
    canonical = sorted([[r[0], r[1], r[2]] for r in rows], key=lambda x: x[0])
    payload = json.dumps(canonical, sort_keys=True).encode()
    return hashlib.sha256(payload).hexdigest()
