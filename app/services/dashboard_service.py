"""Dashboard V1 — deterministic/structural aggregation methodology.

REFACTOR NOTE (Analytics implementation pass): the shared, cross-feature
aggregation logic (approved-version selection, unit conversion, domain
completeness) has been extracted to app/services/kpi_aggregation.py so
Analytics can reuse it without creating a second, competing
implementation. This file now imports those functions instead of
defining them locally. This is a relocation only — every function
behaves identically to before; no Dashboard behavior has changed.
Dashboard-specific composition (water_recycled_percentage, the "My
Tasks" domain_submission_status, and the two top-level summary
functions) remains here, since Analytics does not need these exact
named shapes.

This file contains NO emission factors, NO ESG scoring, NO benchmarking,
NO framework compliance logic, and NO business methodology beyond what
is explicitly specified in kpi_aggregation.py's module docstring.
"""
from datetime import date
from typing import Optional
from sqlmodel import Session, select
from app.models.dataset import Dataset
from app.models.company import Company
from app.models.consultant_assignment import ConsultantAssignment
from app.models.user import User
from app.services.kpi_aggregation import (
    sum_kpi_for_period, period_over_period_change, domain_completeness,
    latest_approved_version_ids, MVP_DOMAIN_UPLOAD_TYPE_CODES,
)


def water_recycled_percentage(session: Session, company_id: int, period_start: date, period_end: date) -> Optional[float]:
    """(SUM(water.recycled) / SUM(water.withdrawal)) * 100 — a descriptive
    ratio, NOT an ESG score. None ("—") when denominator is zero/unavailable."""
    withdrawal = sum_kpi_for_period(session, company_id, "water.withdrawal", period_start, period_end)
    recycled = sum_kpi_for_period(session, company_id, "water.recycled", period_start, period_end)
    if withdrawal["value"] is None or withdrawal["value"] == 0:
        return None
    if recycled["value"] is None:
        return None
    return (recycled["value"] / withdrawal["value"]) * 100


def domain_submission_status(session: Session, company_id: int, period_start: date, period_end: date) -> list[dict]:
    """For each MVP domain: 'approved' | 'awaiting_approval' | 'not_submitted'
    for this exact period. Backs the real "My Tasks" panel — a derived
    status list, NOT a fabricated task-management entity."""
    from app.models.upload_type import UploadType
    results = []
    for code in MVP_DOMAIN_UPLOAD_TYPE_CODES:
        ut = session.exec(select(UploadType).where(UploadType.code == code)).first()
        if not ut:
            results.append({"upload_type_code": code, "display_name": code, "status": "not_submitted"})
            continue
        datasets = session.exec(
            select(Dataset).where(
                Dataset.company_id == company_id, Dataset.upload_type_id == ut.id,
                Dataset.reporting_period_start == period_start,
                Dataset.reporting_period_end == period_end,
                Dataset.deleted_at.is_(None),
            )
        ).all()
        if not datasets:
            status = "not_submitted"
        else:
            dataset_ids = [d.id for d in datasets]
            version_map = latest_approved_version_ids(session, dataset_ids)
            status = "approved" if version_map else "awaiting_approval"
        results.append({"upload_type_code": code, "display_name": ut.display_name, "status": status})
    return results


def consultant_dashboard_summary(session: Session, actor: User, accessible_company_ids_fn) -> dict:
    """Consultant Dashboard: client counts, plan distribution, registration
    queue, consultant workload. Tenant-scoped via the SAME
    accessible_company_ids() helper already used everywhere else — no
    parallel authorization logic."""
    ids = accessible_company_ids_fn(session, actor)
    stmt = select(Company).where(Company.deleted_at.is_(None))
    if ids is not None:
        if not ids:
            companies = []
        else:
            stmt = stmt.where(Company.id.in_(ids))
            companies = session.exec(stmt).all()
    else:
        stmt = stmt.where(Company.organization_id == actor.organization_id)
        companies = session.exec(stmt).all()

    plan_counts = {"Basic": 0, "Professional": 0, "Enterprise": 0}
    for c in companies:
        if c.plan in plan_counts:
            plan_counts[c.plan] += 1

    company_id_set = {c.id for c in companies}
    assignments = session.exec(
        select(ConsultantAssignment).where(
            ConsultantAssignment.company_id.in_(company_id_set) if company_id_set else False,
            ConsultantAssignment.is_active == True,  # noqa: E712
        )
    ).all() if company_id_set else []

    assigned_by_company: dict[int, list[str]] = {}
    for a in assignments:
        u = session.get(User, a.consultant_user_id)
        if u:
            assigned_by_company.setdefault(a.company_id, []).append(u.name)

    registration_queue = [
        {
            "company_id": c.id,  # Company has no public_id anywhere in this codebase (confirmed)
            "company_name": c.name, "created_at": c.created_at, "plan": c.plan,
            "status": c.status,  # real 3-state enum only: Pending | Approved | Rejected
            "assigned_consultants": assigned_by_company.get(c.id, []),
        }
        for c in sorted(companies, key=lambda x: x.created_at, reverse=True)[:10]
    ]

    workload: dict[int, int] = {}
    for a in assignments:
        workload[a.consultant_user_id] = workload.get(a.consultant_user_id, 0) + 1

    consultant_workload = []
    for user_id, client_count in workload.items():
        u = session.get(User, user_id)
        if u:
            consultant_workload.append({"name": u.name, "role": u.role, "client_count": client_count})

    return {
        "total_clients": len(companies),
        "plan_distribution": plan_counts,
        "registration_queue": registration_queue,
        "consultant_workload": consultant_workload,
    }


def client_dashboard_summary(session: Session, company_id: int, period_start: date, period_end: date) -> dict:
    """Client Dashboard: real KPI aggregates, water-recycled ratio, domain
    completeness, and QoQ, for one exact reporting period. company_id is
    ALWAYS derived from the authenticated actor's own account by the
    caller — never a client-supplied value."""
    energy = sum_kpi_for_period(session, company_id, "energy.consumption", period_start, period_end)
    water = sum_kpi_for_period(session, company_id, "water.withdrawal", period_start, period_end)
    recycled_pct = water_recycled_percentage(session, company_id, period_start, period_end)
    completeness = domain_completeness(session, company_id, period_start, period_end)
    energy_qoq = period_over_period_change(session, company_id, "energy.consumption", period_start, period_end)
    water_qoq = period_over_period_change(session, company_id, "water.withdrawal", period_start, period_end)

    return {
        "period_start": period_start, "period_end": period_end,
        "energy_consumption": energy,
        "water_withdrawal": water,
        "water_recycled_percentage": recycled_pct,
        "domain_completeness": completeness,
        "energy_qoq_percentage": energy_qoq,
        "water_qoq_percentage": water_qoq,
        # Explicitly and honestly deferred — never populated with a
        # computed or fabricated value.
        "carbon_emissions": {"available": False, "reason": "methodology_not_configured"},
    }
