"""Shared KPI aggregation helpers — the ONE authoritative implementation
of approved-version selection, unit normalization, and domain
completeness. Extracted from dashboard_service.py so Analytics can reuse
it without creating a second, competing implementation.

This extraction changes NOTHING about Dashboard's behavior — every
function here is copied verbatim from dashboard_service.py, only
relocated and (where genuinely shared, not Dashboard-specific) renamed
without a leading underscore to reflect its now-public, cross-module use.
dashboard_service.py imports from here instead of defining these locally.

VERSION SELECTION / NO DOUBLE-COUNTING (critical correctness rule):
A dataset may have multiple versions over its lifetime (v1 approved, v2
created after changes-requested, etc). Dataset.current_version_id is NOT
reliable for "which version's data is authoritative" — it points at
whatever version is currently active in the workflow, which could be a
brand-new draft sitting on top of a still-valid earlier approval. The
correct authoritative version for a dataset is: the LATEST version (by
version_number) that has ever reached status='approved'. This uses only
fields that already exist (version_number, status) — no new schema.

UNIT HANDLING: KpiValue.unit is stored exactly as reported, never
normalized at extraction time (Layer 1's own, already-locked design).
This module performs ONLY deterministic, universal metric-scale
conversion (kWh<->MWh<->GJ, L<->kL<->ML) — pure arithmetic, not business
methodology. A unit this module does not recognize is excluded from the
sum and flagged, never silently dropped without a trace or silently
treated as compatible. NO conversion table exists here for
waste.generated or emissions.activity_data — this is a genuine,
disclosed gap (waste's real-world units are a plausible future addition
following this exact pattern; emissions' activity data is fundamentally
heterogeneous across scopes and cannot get a single canonical unit at
all).
"""
from datetime import date
from typing import Optional
from sqlmodel import Session, select
from app.models.dataset import Dataset, DatasetVersion
from app.models.kpi import KpiValue

# Deterministic, universal metric-scale conversion ONLY — see module
# docstring. Each KPI's canonical unit matches its seeded KpiDefinition
# unit_hint (scripts_seed_kpi_definitions.py).
UNIT_SCALE_TO_CANONICAL = {
    ("energy.consumption", "kWh"): 1.0,
    ("energy.consumption", "MWh"): 1000.0,
    ("energy.consumption", "GJ"): 277.778,  # SI definition, not a business assumption
    ("energy.consumption", "Wh"): 0.001,
    ("water.withdrawal", "ML"): 1.0,
    ("water.withdrawal", "kL"): 0.001,
    ("water.withdrawal", "L"): 0.000001,
    ("water.withdrawal", "m3"): 0.001,
    ("water.recycled", "ML"): 1.0,
    ("water.recycled", "kL"): 0.001,
    ("water.recycled", "L"): 0.000001,
    ("water.recycled", "m3"): 0.001,
}


def latest_approved_version_ids(session: Session, dataset_ids: list[int]) -> dict[int, int]:
    """For each dataset id, the id of the LATEST version that ever reached
    status='approved'. Returns {dataset_id: version_id}. A dataset with no
    approved version ever is absent from the result — correctly
    contributing nothing."""
    if not dataset_ids:
        return {}
    rows = session.exec(
        select(DatasetVersion.dataset_id, DatasetVersion.id, DatasetVersion.version_number)
        .where(DatasetVersion.dataset_id.in_(dataset_ids), DatasetVersion.status == "approved")
        .order_by(DatasetVersion.dataset_id, DatasetVersion.version_number.desc())
    ).all()
    result: dict[int, int] = {}
    for dataset_id, version_id, _version_number in rows:
        if dataset_id not in result:
            result[dataset_id] = version_id
    return result


def sum_kpi_for_period(
    session: Session, company_id: int, kpi_code: str,
    period_start: date, period_end: date,
) -> dict:
    """Sum a single KPI's authoritative approved value for one company and
    one exact reporting period. value is None (not 0) when no
    authoritative data exists — caller must render this honestly."""
    dataset_ids = session.exec(
        select(Dataset.id).where(
            Dataset.company_id == company_id,
            Dataset.reporting_period_start == period_start,
            Dataset.reporting_period_end == period_end,
            Dataset.deleted_at.is_(None),
        )
    ).all()
    version_map = latest_approved_version_ids(session, list(dataset_ids))
    if not version_map:
        return {"value": None, "unit": None, "excluded_unrecognized_units": []}

    rows = session.exec(
        select(KpiValue.value, KpiValue.unit).where(
            KpiValue.dataset_version_id.in_(list(version_map.values())),
            KpiValue.kpi_code == kpi_code,
        )
    ).all()
    if not rows:
        return {"value": None, "unit": None, "excluded_unrecognized_units": []}

    total = 0.0
    counted_any = False
    excluded_units: set[str] = set()
    for value, unit in rows:
        scale = UNIT_SCALE_TO_CANONICAL.get((kpi_code, unit))
        if scale is None:
            excluded_units.add(unit)
            continue
        total += value * scale
        counted_any = True

    if not counted_any:
        return {"value": None, "unit": None, "excluded_unrecognized_units": sorted(excluded_units)}

    canonical_unit = "kWh" if kpi_code == "energy.consumption" else "ML"
    return {"value": total, "unit": canonical_unit, "excluded_unrecognized_units": sorted(excluded_units)}


def period_over_period_change(
    session: Session, company_id: int, kpi_code: str,
    current_period_start: date, current_period_end: date,
) -> Optional[float]:
    """(current - previous) / previous * 100. "Previous" is the most
    recent distinct reporting period ending before current_period_start
    with authoritative approved data for this kpi_code. None when: no
    previous period exists, current has no data, or previous is zero.

    Deliberately named generically (not "quarter-over-quarter") — this
    compares whatever the immediately-prior distinct reporting period
    actually is, with no assumption that reporting periods are calendar
    quarters. Dashboard's own client-facing field names
    (energy_qoq_percentage etc.) are unchanged by this rename — that is
    Dashboard's already-locked API contract, not touched here.
    """
    current = sum_kpi_for_period(session, company_id, kpi_code, current_period_start, current_period_end)
    if current["value"] is None:
        return None

    prior_periods = session.exec(
        select(Dataset.reporting_period_start, Dataset.reporting_period_end)
        .where(
            Dataset.company_id == company_id,
            Dataset.reporting_period_end < current_period_start,
            Dataset.deleted_at.is_(None),
        )
        .distinct()
        .order_by(Dataset.reporting_period_end.desc())
    ).all()

    for prior_start, prior_end in prior_periods:
        prior = sum_kpi_for_period(session, company_id, kpi_code, prior_start, prior_end)
        if prior["value"] is not None and prior["value"] != 0:
            return ((current["value"] - prior["value"]) / prior["value"]) * 100
    return None


MVP_DOMAIN_UPLOAD_TYPE_CODES = ["energy_data", "water_data", "emissions_data", "waste_data"]


def domain_completeness(session: Session, company_id: int, period_start: date, period_end: date) -> dict:
    """For each of the 4 MVP domains, whether this company has an
    authoritative approved submission for this exact period. A DATA
    COMPLETENESS indicator only — never an ESG/compliance/quality score."""
    from app.models.upload_type import UploadType
    domains: dict[str, bool] = {}
    for code in MVP_DOMAIN_UPLOAD_TYPE_CODES:
        ut = session.exec(select(UploadType).where(UploadType.code == code)).first()
        if not ut:
            domains[code] = False
            continue
        dataset_ids = session.exec(
            select(Dataset.id).where(
                Dataset.company_id == company_id, Dataset.upload_type_id == ut.id,
                Dataset.reporting_period_start == period_start,
                Dataset.reporting_period_end == period_end,
                Dataset.deleted_at.is_(None),
            )
        ).all()
        version_map = latest_approved_version_ids(session, list(dataset_ids))
        domains[code] = len(version_map) > 0

    approved_count = sum(1 for v in domains.values() if v)
    total = len(MVP_DOMAIN_UPLOAD_TYPE_CODES)
    return {
        "approved_count": approved_count, "total": total,
        "percentage": round((approved_count / total) * 100, 1),
        "domains": domains,
    }
