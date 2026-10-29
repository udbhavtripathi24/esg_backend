"""Admin Console API (client administrators; Deloitte staff for their companies).

    users & roles   GET  user:read      changes  user:manage
    targets         GET  dataset:read   changes  admin:manage
    approvals       GET  audit:read
    factors         GET  dataset:read

Integrations stay at /integrations and audit logs at /audit-logs.
``company_id`` is only used for Deloitte users; client users always act on
their own company.
"""
from typing import Any, Optional

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.admin import approvals, targets
from app.admin import users as admin_users
from app.api.deps import require_permission
from app.brsr.service import resolve_company_id
from app.db.session import get_session
from app.models.user import User

router = APIRouter(prefix="/admin", tags=["admin"])
MAX_IMPORT_BYTES = 2 * 1024 * 1024


def _ip(request: Request) -> Optional[str]:
    return request.client.host if request.client else None


# ---------------------------------------------------------------------------
# Users and roles
# ---------------------------------------------------------------------------

class NewUser(BaseModel):
    company_id: Optional[int] = None
    name: str = Field(..., max_length=200)
    email: str = Field(..., max_length=254)
    department: Optional[str] = Field(None, max_length=200)
    role_code: str


class UserPatch(BaseModel):
    name: Optional[str] = Field(None, max_length=200)
    department: Optional[str] = Field(None, max_length=200)


class RoleBody(BaseModel):
    role_code: str


class StatusBody(BaseModel):
    active: bool


@router.get("/roles")
def list_roles(session: Session = Depends(get_session), _actor: User = Depends(require_permission("user:read"))):
    return admin_users.roles(session)


@router.get("/users")
def list_users(company_id: Optional[int] = None, search: Optional[str] = None, status: Optional[str] = None,
               role: Optional[str] = None, department: Optional[str] = None,
               page: int = Query(1, ge=1), page_size: int = Query(25, ge=1, le=100),
               session: Session = Depends(get_session), actor: User = Depends(require_permission("user:read"))):
    cid = resolve_company_id(session, actor, company_id)
    return admin_users.directory(session, actor, cid, search=search, status=status, role=role,
                                 department=department, page=page, page_size=page_size)


@router.post("/users", status_code=201)
def create_user(body: NewUser, request: Request, session: Session = Depends(get_session),
                actor: User = Depends(require_permission("user:manage"))):
    cid = resolve_company_id(session, actor, body.company_id)
    return admin_users.create(session, actor, cid, name=body.name, email=body.email, department=body.department,
                              role_code=body.role_code, ip_address=_ip(request))


def _user(session: Session, actor: User, user_id: int, company_id: Optional[int]) -> User:
    return admin_users.company_user(session, resolve_company_id(session, actor, company_id), user_id)


@router.patch("/users/{user_id}")
def update_user(user_id: int, body: UserPatch, request: Request, company_id: Optional[int] = None,
                session: Session = Depends(get_session), actor: User = Depends(require_permission("user:manage"))):
    u = _user(session, actor, user_id, company_id)
    return admin_users.update(session, actor, u, body.model_dump(exclude_unset=True), ip_address=_ip(request))


@router.put("/users/{user_id}/role")
def set_role(user_id: int, body: RoleBody, request: Request, company_id: Optional[int] = None,
             session: Session = Depends(get_session), actor: User = Depends(require_permission("user:manage"))):
    u = _user(session, actor, user_id, company_id)
    return admin_users.set_role(session, actor, u, body.role_code, ip_address=_ip(request))


@router.post("/users/{user_id}/status")
def set_status(user_id: int, body: StatusBody, request: Request, company_id: Optional[int] = None,
               session: Session = Depends(get_session), actor: User = Depends(require_permission("user:manage"))):
    u = _user(session, actor, user_id, company_id)
    return admin_users.set_active(session, actor, u, body.active, ip_address=_ip(request))


@router.post("/users/{user_id}/reset-password")
def reset_password(user_id: int, request: Request, company_id: Optional[int] = None,
                   session: Session = Depends(get_session), actor: User = Depends(require_permission("user:manage"))):
    u = _user(session, actor, user_id, company_id)
    return admin_users.reset_password(session, actor, u, ip_address=_ip(request))


@router.post("/users/import")
async def import_users(request: Request, file: UploadFile = File(...), dry_run: bool = Form(True),
                       company_id: Optional[int] = Form(None), session: Session = Depends(get_session),
                       actor: User = Depends(require_permission("user:manage"))):
    cid = resolve_company_id(session, actor, company_id)
    content = await file.read(MAX_IMPORT_BYTES + 1)
    if len(content) > MAX_IMPORT_BYTES:
        from app.core.errors import AppError
        raise AppError("file_too_large", "The import file is limited to 2 MB", 400, "file")
    return admin_users.run_import(session, actor, cid, file.filename or "users", content, dry_run=dry_run,
                                  ip_address=_ip(request))


@router.get("/users/import-template")
def import_template(_actor: User = Depends(require_permission("user:manage"))):
    body = "Name,Email,Department,Role\nAsha Verma,asha.verma@yourcompany.com,Sustainability,Approver\n"
    return Response(body, media_type="text/csv",
                    headers={"Content-Disposition": 'attachment; filename="users-import-template.csv"'})


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

class TargetBody(BaseModel):
    company_id: Optional[int] = None
    metric: Optional[str] = None
    title: Optional[str] = Field(None, max_length=200)
    unit: Optional[str] = Field(None, max_length=60)
    direction: Optional[str] = None
    baseline_fy: Optional[str] = None
    baseline_value: Optional[float] = None
    target_fy: Optional[str] = None
    target_value: Optional[float] = None
    manual_values: Optional[dict[str, Any]] = None
    notes: Optional[str] = Field(None, max_length=2000)


@router.get("/targets")
def list_targets(company_id: Optional[int] = None, fy: Optional[str] = None, session: Session = Depends(get_session),
                 actor: User = Depends(require_permission("dataset:read"))):
    return targets.list_targets(session, resolve_company_id(session, actor, company_id), fy)


@router.get("/targets/metric-value")
def metric_value(metric: str, fy: str, company_id: Optional[int] = None, session: Session = Depends(get_session),
                 actor: User = Depends(require_permission("dataset:read"))):
    """A figure for one year, to pre-fill a baseline."""
    cid = resolve_company_id(session, actor, company_id)
    fy = targets._check_fy(fy, "fy")
    return {"metric": metric, "fy": fy, "value": targets.sheet_value(session, cid, metric, fy)}


@router.post("/targets", status_code=201)
def create_target(body: TargetBody, request: Request, fy: Optional[str] = None, session: Session = Depends(get_session),
                  actor: User = Depends(require_permission("admin:manage"))):
    cid = resolve_company_id(session, actor, body.company_id)
    t = targets.create(session, actor, cid, body.model_dump(exclude_unset=True, exclude={"company_id"}),
                       ip_address=_ip(request))
    return targets.serialize(session, t, fy or targets.default_fy(session, cid), targets.names_for(session, [t]))


@router.put("/targets/{target_id}")
def update_target(target_id: str, body: TargetBody, request: Request, fy: Optional[str] = None,
                  session: Session = Depends(get_session), actor: User = Depends(require_permission("admin:manage"))):
    cid = resolve_company_id(session, actor, body.company_id)
    t = targets.update(session, actor, targets.get(session, cid, target_id),
                       body.model_dump(exclude_unset=True, exclude={"company_id"}), ip_address=_ip(request))
    return targets.serialize(session, t, fy or targets.default_fy(session, cid), targets.names_for(session, [t]))


@router.delete("/targets/{target_id}", status_code=204)
def delete_target(target_id: str, request: Request, company_id: Optional[int] = None,
                  session: Session = Depends(get_session), actor: User = Depends(require_permission("admin:manage"))):
    cid = resolve_company_id(session, actor, company_id)
    targets.remove(session, actor, targets.get(session, cid, target_id), ip_address=_ip(request))
    return Response(status_code=204)


# ---------------------------------------------------------------------------
# Approval history and factors
# ---------------------------------------------------------------------------

@router.get("/approvals")
def approval_history(company_id: Optional[int] = None, kind: Optional[str] = None, status: Optional[str] = None,
                     search: Optional[str] = None, page: int = Query(1, ge=1),
                     page_size: int = Query(25, ge=1, le=100), session: Session = Depends(get_session),
                     actor: User = Depends(require_permission("audit:read"))):
    cid = resolve_company_id(session, actor, company_id)
    return approvals.history(session, cid, kind=kind, status=status, search=search, page=page, page_size=page_size)


@router.get("/factors")
def factors(_actor: User = Depends(require_permission("dataset:read"))):
    return approvals.factors()
