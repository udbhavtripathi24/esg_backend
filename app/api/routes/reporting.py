"""Reporting Readiness API. Real, backed entirely by existing, already-
proven functions and fields -- no new business logic invented here.

Answers exactly what the readiness investigation concluded was honestly
answerable: domain completeness (reused directly from
kpi_aggregation.py), and outstanding review actions for the requested
period (datasets sitting in submitted/under_review, and datasets whose
latest version is rejected/changes_requested without a subsequent
approval). Never claims framework-specific readiness -- no framework
concept exists anywhere in this codebase.
"""
from datetime import date
from typing import Optional
from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db.session import get_session
from app.api.deps import require_permission
from app.core.errors import AppError, NotFoundError
from app.core.tenancy import can_access_company
from app.models.user import User
from app.models.dataset import Dataset, DatasetVersion
from app.services.kpi_aggregation import domain_completeness, MVP_DOMAIN_UPLOAD_TYPE_CODES

router = APIRouter(prefix="/reporting", tags=["reporting"])


def _resolve_target_company_id(session: Session, actor: User, company_id: Optional[int]) -> int:
    if actor.portal_type == "client":
        return actor.company_id
    if company_id is None:
        raise AppError("company_id_required", "company_id is required for this actor", 422, "company_id")
    if not can_access_company(session, actor, company_id):
        raise NotFoundError("Company not found")
    return company_id


class DomainCompletenessRead(BaseModel):
    approved_count: int
    total: int
    percentage: float
    domains: dict[str, bool]


class OutstandingItemRead(BaseModel):
    dataset_public_id: str
    upload_type_code: str
    status: str  # the real DatasetVersion.status value: submitted | under_review | rejected | changes_requested


class ReadinessRead(BaseModel):
    period_start: date
    period_end: date
    completeness: DomainCompletenessRead
    outstanding: list[OutstandingItemRead]


@router.get("/readiness", response_model=ReadinessRead)
def get_reporting_readiness(
    period_start: date = Query(...),
    period_end: date = Query(...),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("report:read")),
):
    target_company_id = _resolve_target_company_id(session, actor, company_id)
    completeness = domain_completeness(session, target_company_id, period_start, period_end)

    outstanding: list[OutstandingItemRead] = []
    datasets = session.exec(
        select(Dataset).where(
            Dataset.company_id == target_company_id,
            Dataset.reporting_period_start == period_start,
            Dataset.reporting_period_end == period_end,
            Dataset.deleted_at.is_(None),
        )
    ).all()
    for ds in datasets:
        versions = session.exec(
            select(DatasetVersion)
            .where(DatasetVersion.dataset_id == ds.id)
            .order_by(DatasetVersion.version_number.desc())
        ).all()
        if not versions:
            continue
        latest = versions[0]
        if latest.status in ("submitted", "under_review", "rejected", "changes_requested"):
            from app.models.upload_type import UploadType
            ut = session.get(UploadType, ds.upload_type_id)
            outstanding.append(OutstandingItemRead(
                dataset_public_id=ds.public_id,
                upload_type_code=ut.code if ut else "unknown",
                status=latest.status,
            ))

    return ReadinessRead(period_start=period_start, period_end=period_end, completeness=completeness, outstanding=outstanding)
