"""Analytics V1 API — thin routing layer. All aggregation logic lives in
app/services/analytics_service.py (which itself reuses
app/services/kpi_aggregation.py); this file only handles HTTP concerns.

Tenancy: identical pattern to app/api/routes/dashboard.py, reusing the
same _resolve_target_company_id-shaped logic (duplicated here as a
private helper rather than imported, since importing a "private"
function across route modules is worse practice than one small,
identical, independently-reviewable copy — the underlying authorization
primitives it calls, accessible_company_ids/can_access_company, are the
actual shared, authoritative logic, not reimplemented here).
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session

from app.db.session import get_session
from app.api.deps import require_permission
from app.core.tenancy import can_access_company
from app.core.errors import AppError, NotFoundError
from app.models.user import User
from app.services import analytics_service

router = APIRouter(prefix="/analytics", tags=["analytics"])


def _resolve_target_company_id(session: Session, actor: User, company_id: Optional[int]) -> int:
    if actor.portal_type == "client":
        return actor.company_id
    if company_id is None:
        raise AppError("company_id_required", "company_id is required for this actor", 422, "company_id")
    if not can_access_company(session, actor, company_id):
        raise NotFoundError("Company not found")
    return company_id


class SiteBreakdownRead(BaseModel):
    site_id: Optional[int]
    value: Optional[float]
    unit: Optional[str]
    excluded_unrecognized_units: list[str]


class UnitBreakdownRead(BaseModel):
    unit: str
    total: float


class KpiSummaryRead(BaseModel):
    kpi_code: str
    total: Optional[float]
    average: Optional[float]
    unit: Optional[str]
    row_count: int
    excluded_unrecognized_units: list[str]
    by_site: list[SiteBreakdownRead]
    by_unit: list[UnitBreakdownRead]


@router.get("/summary", response_model=KpiSummaryRead)
def get_kpi_summary(
    kpi_code: str = Query(...),
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    if kpi_code not in analytics_service.ALL_KPI_CODES:
        raise AppError("invalid_kpi_code", f"Unknown kpi_code. Must be one of {analytics_service.ALL_KPI_CODES}", 422, "kpi_code")
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return analytics_service.kpi_summary(session, target_company_id, kpi_code, period_start, period_end)


class DomainSummaryEntryRead(BaseModel):
    kpi_code: str
    display_name: str
    total: Optional[float]
    unit: Optional[str]
    row_count: int


@router.get("/domains", response_model=list[DomainSummaryEntryRead])
def get_domain_summary(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return analytics_service.domain_summary(session, target_company_id, period_start, period_end)


class TrendPointRead(BaseModel):
    period_start: date
    period_end: date
    value: float
    unit: Optional[str]


@router.get("/trend", response_model=list[TrendPointRead])
def get_historical_trend(
    kpi_code: str = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    if kpi_code not in analytics_service.ALL_KPI_CODES:
        raise AppError("invalid_kpi_code", f"Unknown kpi_code. Must be one of {analytics_service.ALL_KPI_CODES}", 422, "kpi_code")
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return analytics_service.historical_trend(session, target_company_id, kpi_code)


class PeriodComparisonRead(BaseModel):
    kpi_code: str
    period_start: date
    period_end: date
    change_percentage: Optional[float]


@router.get("/period-comparison", response_model=PeriodComparisonRead)
def get_period_comparison(
    kpi_code: str = Query(...),
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    if kpi_code not in analytics_service.ALL_KPI_CODES:
        raise AppError("invalid_kpi_code", f"Unknown kpi_code. Must be one of {analytics_service.ALL_KPI_CODES}", 422, "kpi_code")
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    change = analytics_service.period_over_period_change(session, target_company_id, kpi_code, period_start, period_end)
    return {"kpi_code": kpi_code, "period_start": period_start, "period_end": period_end, "change_percentage": change}


class DomainCompletenessRead(BaseModel):
    approved_count: int
    total: int
    percentage: float
    domains: dict[str, bool]


@router.get("/completeness", response_model=DomainCompletenessRead)
def get_completeness(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return analytics_service.domain_completeness(session, target_company_id, period_start, period_end)


class EnergyTypeBreakdownRead(BaseModel):
    energy_type: str
    value: float
    unit: str
    excluded_unrecognized_units: list[str]


@router.get("/energy-breakdown", response_model=list[EnergyTypeBreakdownRead])
def get_energy_breakdown(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    return analytics_service.energy_type_breakdown(session, target_company_id, period_start, period_end)
