"""Analytics V1 — real-data KPI aggregation, breakdowns, trends.

Reuses app/services/kpi_aggregation.py's approved-version-selection and
unit-conversion logic entirely — no second, competing implementation of
either exists anywhere in this file.

BOUNDARY, enforced structurally, not just documented: this file contains
NO ESG scoring, NO emission-factor/CO2e calculation, NO benchmarking, NO
peer comparison, NO Business Unit or Region filtering (neither concept
has any real population path through the actual application UI — see
the readiness investigation), and NO anomaly/outlier judgment (a
distribution is shown; nothing is labeled "anomalous").

AVERAGE DEFINITION, explicitly documented per instruction: "average"
here means the arithmetic mean of every individual approved KpiValue
row's value (after unit conversion, for compatible units only) for the
requested KPI/period — a ROW-LEVEL average, not a per-site average. A
company with many small electricity-meter readings and one large diesel
reading would see the average skew toward the more numerous rows. An
alternative, equally reasonable interpretation ("average of per-site
totals") is NOT what this returns — that would require an additional
grouping step this module deliberately does not perform, to keep the
metric's definition simple and unambiguous.
"""
from datetime import date
from typing import Optional
from sqlmodel import Session, select
from app.models.dataset import Dataset
from app.models.kpi import KpiValue, KpiDefinition
from app.services.kpi_aggregation import (
    latest_approved_version_ids, UNIT_SCALE_TO_CANONICAL,
    period_over_period_change, domain_completeness,
)

ALL_KPI_CODES = ["energy.consumption", "water.withdrawal", "water.recycled", "emissions.activity_data", "waste.generated"]


def _approved_rows_for_period(session: Session, company_id: int, kpi_code: str, period_start: date, period_end: date) -> list[KpiValue]:
    """The real, approved-only KpiValue rows for one company/KPI/period —
    the single query every function in this module builds on."""
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
        return []
    return session.exec(
        select(KpiValue).where(
            KpiValue.dataset_version_id.in_(list(version_map.values())),
            KpiValue.kpi_code == kpi_code,
        )
    ).all()


def kpi_summary(session: Session, company_id: int, kpi_code: str, period_start: date, period_end: date) -> dict:
    """Total, average (row-level, see module docstring), breakdown by
    site, and breakdown by as-reported unit — for one KPI/period.

    by_site values ARE unit-converted (canonical, where convertible) —
    matching Dashboard's own totals so a site breakdown genuinely sums to
    the same total shown elsewhere. by_unit values are NEVER converted —
    deliberately raw, to make unit heterogeneity visible rather than
    hidden (per explicit instruction).
    """
    rows = _approved_rows_for_period(session, company_id, kpi_code, period_start, period_end)
    if not rows:
        return {
            "kpi_code": kpi_code, "total": None, "average": None, "unit": None,
            "row_count": 0, "excluded_unrecognized_units": [],
            "by_site": [], "by_unit": [],
        }

    canonical_unit = "kWh" if kpi_code == "energy.consumption" else ("ML" if kpi_code.startswith("water.") else None)
    total = 0.0
    convertible_values: list[float] = []
    excluded_units: set[str] = set()
    by_site: dict[Optional[int], dict] = {}
    by_unit: dict[str, float] = {}

    for kv in rows:
        by_unit[kv.unit] = by_unit.get(kv.unit, 0.0) + kv.value

        scale = UNIT_SCALE_TO_CANONICAL.get((kpi_code, kv.unit))
        site_key = kv.site_id  # None means "Unassigned" -- never fabricated
        if site_key not in by_site:
            by_site[site_key] = {"site_id": site_key, "value": 0.0, "has_convertible": False, "excluded_unrecognized_units": set()}

        if scale is None:
            excluded_units.add(kv.unit)
            by_site[site_key]["excluded_unrecognized_units"].add(kv.unit)
            continue
        converted = kv.value * scale
        total += converted
        convertible_values.append(converted)
        by_site[site_key]["value"] += converted
        by_site[site_key]["has_convertible"] = True

    average = (sum(convertible_values) / len(convertible_values)) if convertible_values else None

    by_site_list = [
        {
            "site_id": s["site_id"],
            # None (not 0) when this site had ONLY excluded/unrecognized-unit
            # rows -- a genuine "no convertible data" case, distinct from a
            # site that genuinely reported a real value of 0.
            "value": s["value"] if s["has_convertible"] else None,
            "unit": canonical_unit if s["has_convertible"] else None,
            "excluded_unrecognized_units": sorted(s["excluded_unrecognized_units"]),
        }
        for s in by_site.values()
    ]
    by_unit_list = [{"unit": u, "total": v} for u, v in sorted(by_unit.items())]

    return {
        "kpi_code": kpi_code,
        "total": total if convertible_values else None,
        "average": average,
        "unit": canonical_unit if convertible_values else None,
        "row_count": len(rows),
        "excluded_unrecognized_units": sorted(excluded_units),
        "by_site": by_site_list,
        "by_unit": by_unit_list,
    }


def domain_summary(session: Session, company_id: int, period_start: date, period_end: date) -> list[dict]:
    """Totals for all 5 real KPI codes at once — the "breakdown by
    domain/KPI" view. Uses actual KpiDefinition display names, never
    invented labels."""
    results = []
    definitions = {d.code: d for d in session.exec(select(KpiDefinition).where(KpiDefinition.is_active == True)).all()}  # noqa: E712
    for code in ALL_KPI_CODES:
        summary = kpi_summary(session, company_id, code, period_start, period_end)
        defn = definitions.get(code)
        results.append({
            "kpi_code": code,
            "display_name": defn.display_name if defn else code,
            "total": summary["total"], "unit": summary["unit"], "row_count": summary["row_count"],
        })
    return results


def historical_trend(session: Session, company_id: int, kpi_code: str) -> list[dict]:
    """Every real reporting period this company has EVER had authoritative
    approved data for this KPI, in chronological order. No interpolation,
    no invented points — a period with no approved data simply isn't in
    the list, rather than appearing as a fabricated zero."""
    periods = session.exec(
        select(Dataset.reporting_period_start, Dataset.reporting_period_end)
        .where(Dataset.company_id == company_id, Dataset.deleted_at.is_(None))
        .distinct()
        .order_by(Dataset.reporting_period_start)
    ).all()

    trend = []
    for period_start, period_end in periods:
        s = kpi_summary(session, company_id, kpi_code, period_start, period_end)
        if s["total"] is not None:
            trend.append({
                "period_start": period_start, "period_end": period_end,
                "value": s["total"], "unit": s["unit"],
            })
    return trend


def energy_type_breakdown(session: Session, company_id: int, period_start: date, period_end: date) -> list[dict]:
    """Real energy.consumption breakdown by attributes.energy_type. Only
    categories genuinely present in the returned data appear — never a
    fixed, invented category list (no 'renewables'/'fuel oil'/'other'
    unless a real row actually reports one)."""
    rows = _approved_rows_for_period(session, company_id, "energy.consumption", period_start, period_end)
    by_type: dict[str, dict] = {}
    for kv in rows:
        energy_type = kv.attributes.get("energy_type", "unspecified") if kv.attributes else "unspecified"
        scale = UNIT_SCALE_TO_CANONICAL.get(("energy.consumption", kv.unit))
        if energy_type not in by_type:
            by_type[energy_type] = {"energy_type": energy_type, "value": 0.0, "excluded_unrecognized_units": set()}
        if scale is None:
            by_type[energy_type]["excluded_unrecognized_units"].add(kv.unit)
            continue
        by_type[energy_type]["value"] += kv.value * scale

    return [
        {
            "energy_type": t["energy_type"], "value": t["value"], "unit": "kWh",
            "excluded_unrecognized_units": sorted(t["excluded_unrecognized_units"]),
        }
        for t in by_type.values()
    ]


# Re-exported for the API layer, so route code never needs a second
# import path for the same shared logic.
__all__ = [
    "kpi_summary", "domain_summary", "historical_trend", "energy_type_breakdown",
    "period_over_period_change", "domain_completeness", "ALL_KPI_CODES",
]
