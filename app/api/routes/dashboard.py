"""Dashboard V1 API — thin routing layer. All aggregation logic lives in
app/services/dashboard_service.py; this file only handles HTTP concerns
(auth, tenancy enforcement, request/response shaping).

Tenancy: consultant dashboard reuses company:read + the existing
accessible_company_ids() helper (same as companies.py) — no parallel
authorization logic. Client dashboard reuses dataset:read (same
permission as kpi-values/kpi-validation) and NEVER accepts a
client-supplied company_id — a client actor's company_id is always
their own, taken from the JWT-derived actor record.
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.db.session import get_session
from app.api.deps import require_permission
from app.core.tenancy import accessible_company_ids, can_access_company
from app.core.errors import AppError, NotFoundError
from app.models.user import User
from app.services import dashboard_service

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class RegistrationQueueEntry(BaseModel):
    company_id: int
    company_name: str
    created_at: str
    plan: str
    status: str
    assigned_consultants: list[str]


class ConsultantWorkloadEntry(BaseModel):
    name: str
    role: str
    client_count: int


class ConsultantDashboardRead(BaseModel):
    total_clients: int
    plan_distribution: dict[str, int]
    registration_queue: list[RegistrationQueueEntry]
    consultant_workload: list[ConsultantWorkloadEntry]


@router.get("/consultant", response_model=ConsultantDashboardRead)
def get_consultant_dashboard(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("company:read")),
):
    result = dashboard_service.consultant_dashboard_summary(session, actor, accessible_company_ids)
    result["registration_queue"] = [
        {**r, "created_at": r["created_at"].isoformat()} for r in result["registration_queue"]
    ]
    return result


class KpiAggregateRead(BaseModel):
    value: Optional[float]
    unit: Optional[str]
    excluded_unrecognized_units: list[str]


class DomainCompletenessRead(BaseModel):
    approved_count: int
    total: int
    percentage: float
    domains: dict[str, bool]


class CarbonEmissionsRead(BaseModel):
    available: bool
    reason: Optional[str] = None


class ClientDashboardRead(BaseModel):
    period_start: date
    period_end: date
    energy_consumption: KpiAggregateRead
    water_withdrawal: KpiAggregateRead
    water_recycled_percentage: Optional[float]
    domain_completeness: DomainCompletenessRead
    energy_qoq_percentage: Optional[float]
    water_qoq_percentage: Optional[float]
    carbon_emissions: CarbonEmissionsRead


def _resolve_target_company_id(session: Session, actor: User, company_id: Optional[int]) -> int:
    if actor.portal_type == "client":
        return actor.company_id
    if company_id is None:
        raise AppError("company_id_required", "company_id is required for this actor", 422, "company_id")
    if not can_access_company(session, actor, company_id):
        raise NotFoundError("Company not found")
    return company_id


@router.get("/client", response_model=ClientDashboardRead)
def get_client_dashboard(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return dashboard_service.client_dashboard_summary(session, target_company_id, period_start, period_end)


class DomainSubmissionStatusRead(BaseModel):
    upload_type_code: str
    display_name: str
    status: str  # 'approved' | 'awaiting_approval' | 'not_submitted'


@router.get("/client/tasks", response_model=list[DomainSubmissionStatusRead])
def get_client_dashboard_tasks(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return dashboard_service.domain_submission_status(session, target_company_id, period_start, period_end)
