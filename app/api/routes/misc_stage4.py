"""Smaller Stage 4 route groups kept together for compactness:
upload-types, processing-jobs, audit-logs, integrations.
"""
from datetime import datetime
from typing import Optional, Any
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select, func
from pydantic import BaseModel
from app.db.session import get_session
from app.api.deps import require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params
from app.core.tenancy import accessible_company_ids, can_access_company
from app.models.user import User
from app.models.upload_type import UploadType
from app.models.processing_job import ProcessingJob
from app.models.audit_log import AuditLog
from app.models.integration import Integration
from app.services.audit import log_action


# ---------- Upload types (read-only for most; admin can manage) ----------

upload_types_router = APIRouter(prefix="/upload-types", tags=["upload-types"])


class UploadTypeRead(BaseModel):
    code: str
    display_name: str
    purpose: Optional[str]
    allowed_mime_types: list[str]
    max_file_size_bytes: int
    processing_mode: str
    is_active: bool


class UploadTypeCreate(BaseModel):
    code: str
    display_name: str
    purpose: Optional[str] = None
    allowed_mime_types: list[str]
    max_file_size_bytes: int = 25 * 1024 * 1024
    processing_mode: str = "async"


class UploadTypeUpdate(BaseModel):
    display_name: Optional[str] = None
    purpose: Optional[str] = None
    allowed_mime_types: Optional[list[str]] = None
    max_file_size_bytes: Optional[int] = None
    is_active: Optional[bool] = None


@upload_types_router.get("", response_model=list[UploadTypeRead])
def list_upload_types(session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("upload_type:read"))):
    return session.exec(select(UploadType).where(UploadType.is_active == True)
                        .order_by(UploadType.code)).all()


@upload_types_router.get("/{code}", response_model=UploadTypeRead)
def get_upload_type(code: str, session: Session = Depends(get_session),
                    actor: User = Depends(require_permission("upload_type:read"))):
    ut = session.exec(select(UploadType).where(UploadType.code == code)).first()
    if not ut:
        raise NotFoundError("Upload type not found")
    return ut


@upload_types_router.post("", response_model=UploadTypeRead, status_code=201)
def create_upload_type(body: UploadTypeCreate, request: Request,
                       session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("upload_type:manage"))):
    if session.exec(select(UploadType).where(UploadType.code == body.code)).first():
        raise AppError("duplicate_code", "Upload type already exists", 422, "code")
    ut = UploadType(**body.model_dump())
    session.add(ut); session.flush()
    log_action(session, actor, "upload_type.created", "upload_type", ut.id,
               changes=body.model_dump(),
               ip_address=request.client.host if request.client else None)
    session.commit(); session.refresh(ut)
    return ut


@upload_types_router.patch("/{code}", response_model=UploadTypeRead)
def update_upload_type(code: str, body: UploadTypeUpdate, request: Request,
                       session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("upload_type:manage"))):
    ut = session.exec(select(UploadType).where(UploadType.code == code)).first()
    if not ut:
        raise NotFoundError("Upload type not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(ut, k, v)
    ut.updated_at = datetime.utcnow()
    session.add(ut); session.flush()
    log_action(session, actor, "upload_type.updated", "upload_type", ut.id,
               changes=changes,
               ip_address=request.client.host if request.client else None)
    session.commit(); session.refresh(ut)
    return ut


# ---------- Jobs (read-only observability) ----------

jobs_router = APIRouter(prefix="/jobs", tags=["jobs"])


class JobRead(BaseModel):
    id: int
    job_type: str
    subject_type: str
    subject_id: int
    status: str
    attempts: int
    max_attempts: int
    last_error: Optional[str]
    scheduled_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]


@jobs_router.get("", response_model=Page[JobRead])
def list_jobs(session: Session = Depends(get_session),
              actor: User = Depends(require_permission("audit:read")),
              page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
              status: Optional[str] = None,
              job_type: Optional[str] = None):
    page, page_size = paginate_params(page, page_size)
    stmt = select(ProcessingJob)
    if status:
        stmt = stmt.where(ProcessingJob.status == status)
    if job_type:
        stmt = stmt.where(ProcessingJob.job_type == job_type)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(ProcessingJob.scheduled_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return Page(items=session.exec(stmt).all(), total=total, page=page, page_size=page_size)


@jobs_router.get("/{job_id}", response_model=JobRead)
def get_job(job_id: int, session: Session = Depends(get_session),
            actor: User = Depends(require_permission("audit:read"))):
    j = session.get(ProcessingJob, job_id)
    if not j:
        raise NotFoundError("Job not found")
    return j


# ---------- Audit logs (read-only) ----------

audit_router = APIRouter(prefix="/audit-logs", tags=["audit"])


class AuditRead(BaseModel):
    id: int
    company_id: Optional[int]
    actor_user_id: Optional[int]
    action: str
    entity_type: str
    entity_id: int
    entity_public_id: Optional[str]
    request_id: Optional[str]
    changes: Optional[dict[str, Any]]
    occurred_at: datetime
    actor_name: Optional[str] = None
    company_name: Optional[str] = None


@audit_router.get("", response_model=Page[AuditRead])
def list_audit(session: Session = Depends(get_session),
               actor: User = Depends(require_permission("audit:read")),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               entity_type: Optional[str] = None,
               entity_id: Optional[int] = None,
               company_id: Optional[int] = None,
               action: Optional[str] = Query(None, max_length=80, description="an action, a prefix ending in '.', or a prefix followed by '*'"),
               actor_user_id: Optional[int] = None,
               date_from: Optional[datetime] = None,
               date_to: Optional[datetime] = None):
    page, page_size = paginate_params(page, page_size)
    stmt = select(AuditLog)
    # Tenant scoping: client users see only their company's logs
    if actor.portal_type == "client":
        stmt = stmt.where(AuditLog.company_id == actor.company_id)
    else:
        ids = accessible_company_ids(session, actor)
        if ids is None:
            from app.models.company import Company
            stmt = stmt.where(AuditLog.company_id.in_(
                select(Company.id).where(Company.organization_id == actor.organization_id)
            ))
        elif ids:
            stmt = stmt.where(AuditLog.company_id.in_(ids))
        else:
            return Page(items=[], total=0, page=page, page_size=page_size)
    if entity_type:
        stmt = stmt.where(AuditLog.entity_type == entity_type)
    if entity_id is not None:
        stmt = stmt.where(AuditLog.entity_id == entity_id)
    if company_id is not None:
        stmt = stmt.where(AuditLog.company_id == company_id)
    if action:
        prefix = action[:-1] if action.endswith("*") else action if action.endswith(".") else None
        stmt = stmt.where(AuditLog.action.startswith(prefix, autoescape=True) if prefix
                          else AuditLog.action == action)
    if actor_user_id is not None:
        stmt = stmt.where(AuditLog.actor_user_id == actor_user_id)
    if date_from is not None:
        stmt = stmt.where(AuditLog.occurred_at >= date_from.replace(tzinfo=None))
    if date_to is not None:
        stmt = stmt.where(AuditLog.occurred_at <= date_to.replace(tzinfo=None))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(AuditLog.occurred_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = session.exec(stmt).all()
    from app.models.company import Company
    uids = {r.actor_user_id for r in rows if r.actor_user_id}
    cids = {r.company_id for r in rows if r.company_id}
    unames = {u.id: u.name for u in session.exec(select(User).where(User.id.in_(uids)))} if uids else {}
    cnames = {c.id: c.name for c in session.exec(select(Company).where(Company.id.in_(cids)))} if cids else {}
    items = [AuditRead(**r.model_dump(), actor_name=unames.get(r.actor_user_id),
                       company_name=cnames.get(r.company_id)) for r in rows]
    return Page(items=items, total=total, page=page, page_size=page_size)


# ---------- Integrations (stub CRUD, no live sync) ----------

integrations_router = APIRouter(prefix="/integrations", tags=["integrations"])


INTEGRATION_STATUSES = ("configured", "disabled", "error")
# What the record may hold. Never credentials: when a live connector arrives,
# secrets go to Secret Manager, not this table.
CONFIG_TEXT = {"domain": 60, "system": 120, "frequency": 40, "owner": 120, "notes": 1000, "note": 1000,
               "endpoint": 300}


def _clean_config(cfg: Optional[dict[str, Any]]) -> Optional[dict[str, Any]]:
    if cfg is None:
        return None
    out: dict[str, Any] = {}
    for k, v in cfg.items():
        if k in CONFIG_TEXT:
            s = str(v or "").strip()[:CONFIG_TEXT[k]]
            if s:
                out[k] = s
        elif k == "sheets":
            from app.datasheets.catalog import SHEETS
            if not isinstance(v, list) or any(x not in SHEETS for x in v):
                raise AppError("invalid_sheets", "Unknown data sheet in the mapping", 422, "config")
            out[k] = sorted(set(v))
        else:
            raise AppError("invalid_config", f"'{k}' can't be stored on an integration record", 422, "config")
    return out


class IntegrationCreate(BaseModel):
    company_id: Optional[int] = None
    type: str
    status: str = "configured"
    config: Optional[dict[str, Any]] = None


class IntegrationUpdate(BaseModel):
    status: Optional[str] = None
    type: Optional[str] = None
    config: Optional[dict[str, Any]] = None


class IntegrationRead(BaseModel):
    id: int
    company_id: int
    type: str
    status: str
    config: Optional[dict[str, Any]]
    last_sync_at: Optional[datetime]
    created_at: datetime


@integrations_router.get("", response_model=Page[IntegrationRead])
def list_integrations(session: Session = Depends(get_session),
                      actor: User = Depends(require_permission("integration:manage")),
                      page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    page, page_size = paginate_params(page, page_size)
    stmt = select(Integration)
    if actor.portal_type == "client":
        stmt = stmt.where(Integration.company_id == actor.company_id)
    else:
        ids = accessible_company_ids(session, actor)
        if ids is None:
            from app.models.company import Company
            stmt = stmt.where(Integration.company_id.in_(
                select(Company.id).where(Company.organization_id == actor.organization_id)
            ))
        elif ids:
            stmt = stmt.where(Integration.company_id.in_(ids))
        else:
            return Page(items=[], total=0, page=page, page_size=page_size)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    return Page(items=session.exec(stmt).all(), total=total, page=page, page_size=page_size)


@integrations_router.post("", response_model=IntegrationRead, status_code=201)
def create_integration(body: IntegrationCreate, request: Request,
                       session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("integration:manage"))):
    if actor.portal_type == "client":
        company_id = actor.company_id
    else:
        if body.company_id is None:
            raise AppError("company_required", "company_id required", 422, "company_id")
        if not can_access_company(session, actor, body.company_id):
            raise NotFoundError("Company not found")
        company_id = body.company_id
    if body.status not in INTEGRATION_STATUSES:
        raise AppError("invalid_status", "Status must be configured, disabled or error", 422, "status")
    if not body.type.strip():
        raise AppError("type_required", "Name the system", 422, "type")
    integ = Integration(company_id=company_id, type=body.type.strip()[:60], status=body.status,
                        config=_clean_config(body.config))
    session.add(integ); session.flush()
    log_action(session, actor, "integration.created", "integration", integ.id,
               company_id=company_id, changes={"type": body.type},
               ip_address=request.client.host if request.client else None)
    session.commit(); session.refresh(integ)
    return integ


@integrations_router.patch("/{integration_id}", response_model=IntegrationRead)
def update_integration(integration_id: int, body: IntegrationUpdate, request: Request,
                       session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("integration:manage"))):
    integ = session.get(Integration, integration_id)
    if not integ or not can_access_company(session, actor, integ.company_id):
        raise NotFoundError("Integration not found")
    changes = body.model_dump(exclude_unset=True)
    if "status" in changes and changes["status"] not in INTEGRATION_STATUSES:
        raise AppError("invalid_status", "Status must be configured, disabled or error", 422, "status")
    if "type" in changes:
        if not (changes["type"] or "").strip():
            raise AppError("type_required", "Name the system", 422, "type")
        changes["type"] = changes["type"].strip()[:60]
    if "config" in changes:
        changes["config"] = _clean_config(changes["config"])
    for k, v in changes.items():
        setattr(integ, k, v)
    integ.updated_at = datetime.utcnow()
    session.add(integ); session.flush()
    log_action(session, actor, "integration.updated", "integration", integ.id,
               company_id=integ.company_id, changes=changes,
               ip_address=request.client.host if request.client else None)
    session.commit(); session.refresh(integ)
    return integ
