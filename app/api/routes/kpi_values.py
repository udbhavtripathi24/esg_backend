"""Layer 1 read API -- KPI values and definitions.

No new RBAC permission invented: reuses dataset:read, since a KPI value is
fundamentally a decomposition of dataset data the actor already has rights
to see. Tenancy filtering mirrors list_datasets() in datasets.py exactly
-- same helpers, same pattern, so this doesn't introduce a second way of
answering "what can this actor see."

HARDENING PASS: every reference that has a real public_id is now exposed
via that public_id, never the raw internal integer -- including site_id,
which was previously exposed raw (matching DatasetRead's own pre-existing
convention, but not the correct standard). company_id remains a raw int
because Company has no public_id anywhere in this codebase (confirmed:
CompanyRead itself exposes raw `id`) -- there is no better identifier
available. DatasetRead's own pre-existing raw-int exposures (site_id,
upload_type_id, created_by) are OUT OF SCOPE for this pass and are left
untouched -- this hardening only tightens Layer 1's own, newly-introduced
API to the correct standard, not pre-existing Upload Center routes.

This is a read-only boundary for FUTURE consumers (Dashboard, Analytics,
Reporting, Benchmarking, Power BI, AI) -- no frontend consumes it yet, per
this task's explicit scope (do not build Dashboard/Analytics now). Kept
deliberately thin: filtering only, no aggregation logic, since aggregation
choices belong to each future consumer, not to this foundational layer.
"""
from typing import Optional
from datetime import date, datetime
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func
from pydantic import BaseModel

from app.db.session import get_session
from app.api.deps import require_permission
from app.core.pagination import Page, paginate_params
from app.core.tenancy import accessible_company_ids
from app.models.user import User
from app.models.kpi import KpiValue, KpiDefinition
from app.models.dataset import Dataset, DatasetVersion, DatasetFile
from app.models.upload_type import UploadType
from app.models.master_data import Site

router = APIRouter(prefix="/kpi-values", tags=["kpi"])
definitions_router = APIRouter(prefix="/kpi-definitions", tags=["kpi"])


class KpiValueRead(BaseModel):
    public_id: str
    dataset_public_id: str
    dataset_version_public_id: str
    source_file_public_id: str
    source_row_number: int
    company_id: int  # no public_id exists for Company anywhere in this codebase
    site_public_id: Optional[str]
    kpi_code: str
    kpi_definition_version: int
    value: float
    unit: str
    attributes: dict
    reporting_period_start: date
    reporting_period_end: date
    created_at: datetime


class KpiDefinitionRead(BaseModel):
    code: str
    display_name: str
    unit_hint: Optional[str]
    upload_type_code: str
    data_type: str
    version: int
    is_active: bool


def _to_read(kv: KpiValue, ds_pid: str, dv_pid: str, df_pid: str, site_pid: Optional[str]) -> KpiValueRead:
    return KpiValueRead(
        public_id=kv.public_id, dataset_public_id=ds_pid,
        dataset_version_public_id=dv_pid, source_file_public_id=df_pid,
        source_row_number=kv.source_row_number, company_id=kv.company_id,
        site_public_id=site_pid, kpi_code=kv.kpi_code,
        kpi_definition_version=kv.kpi_definition_version,
        value=kv.value, unit=kv.unit,
        attributes=kv.attributes, reporting_period_start=kv.reporting_period_start,
        reporting_period_end=kv.reporting_period_end, created_at=kv.created_at,
    )


@router.get("", response_model=Page[KpiValueRead])
def list_kpi_values(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
    page: int = Query(1, ge=1), page_size: int = Query(50, ge=1, le=200),
    company_id: Optional[int] = None,
    dataset_version_public_id: Optional[str] = None,
    kpi_code: Optional[str] = None,
    site_public_id: Optional[str] = None,
    period_start: Optional[date] = None,
    period_end: Optional[date] = None,
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(KpiValue)

    # Identical tenancy pattern to list_datasets() in datasets.py.
    if actor.portal_type == "client":
        stmt = stmt.where(KpiValue.company_id == actor.company_id)
    else:
        ids = accessible_company_ids(session, actor)
        if ids is None:
            from app.models.company import Company
            stmt = stmt.where(KpiValue.company_id.in_(
                select(Company.id).where(Company.organization_id == actor.organization_id)
            ))
        else:
            if not ids:
                return Page(items=[], total=0, page=page, page_size=page_size)
            stmt = stmt.where(KpiValue.company_id.in_(ids))

    if company_id is not None:
        stmt = stmt.where(KpiValue.company_id == company_id)
    if dataset_version_public_id:
        dv = session.exec(select(DatasetVersion).where(DatasetVersion.public_id == dataset_version_public_id)).first()
        stmt = stmt.where(KpiValue.dataset_version_id == (dv.id if dv else -1))
    if kpi_code:
        stmt = stmt.where(KpiValue.kpi_code == kpi_code)
    if site_public_id:
        s = session.exec(select(Site).where(Site.public_id == site_public_id)).first()
        stmt = stmt.where(KpiValue.site_id == (s.id if s else -1))
    if period_start:
        stmt = stmt.where(KpiValue.reporting_period_start >= period_start)
    if period_end:
        stmt = stmt.where(KpiValue.reporting_period_end <= period_end)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(KpiValue.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = session.exec(stmt).all()

    # Resolve public_ids for the (small) page of results -- not N+1 across
    # the whole table, just the page being returned.
    ds_cache: dict[int, str] = {}
    dv_cache: dict[int, str] = {}
    df_cache: dict[int, str] = {}
    site_cache: dict[int, Optional[str]] = {}
    items = []
    for kv in rows:
        if kv.dataset_id not in ds_cache:
            d = session.get(Dataset, kv.dataset_id)
            ds_cache[kv.dataset_id] = d.public_id if d else "unknown"
        if kv.dataset_version_id not in dv_cache:
            v = session.get(DatasetVersion, kv.dataset_version_id)
            dv_cache[kv.dataset_version_id] = v.public_id if v else "unknown"
        if kv.source_file_id not in df_cache:
            f = session.get(DatasetFile, kv.source_file_id)
            df_cache[kv.source_file_id] = f.public_id if f else "unknown"
        if kv.site_id is not None and kv.site_id not in site_cache:
            s = session.get(Site, kv.site_id)
            site_cache[kv.site_id] = s.public_id if s else None
        items.append(_to_read(
            kv, ds_cache[kv.dataset_id], dv_cache[kv.dataset_version_id],
            df_cache[kv.source_file_id],
            site_cache.get(kv.site_id) if kv.site_id is not None else None,
        ))

    return Page(items=items, total=total, page=page, page_size=page_size)


@definitions_router.get("", response_model=list[KpiDefinitionRead])
def list_kpi_definitions(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
    upload_type_code: Optional[str] = None,
):
    """Structural catalog -- not tenant-scoped (definitions aren't
    company-owned data, same as upload-types)."""
    stmt = select(KpiDefinition).where(KpiDefinition.is_active == True)  # noqa: E712
    if upload_type_code:
        ut = session.exec(select(UploadType).where(UploadType.code == upload_type_code)).first()
        stmt = stmt.where(KpiDefinition.upload_type_id == (ut.id if ut else -1))
    rows = session.exec(stmt).all()

    ut_cache: dict[int, str] = {}
    items = []
    for d in rows:
        if d.upload_type_id not in ut_cache:
            ut = session.get(UploadType, d.upload_type_id)
            ut_cache[d.upload_type_id] = ut.code if ut else "unknown"
        items.append(KpiDefinitionRead(
            code=d.code, display_name=d.display_name, unit_hint=d.unit_hint,
            upload_type_code=ut_cache[d.upload_type_id], data_type=d.data_type,
            version=d.version, is_active=d.is_active,
        ))
    return items
