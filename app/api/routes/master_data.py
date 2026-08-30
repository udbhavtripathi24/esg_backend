"""Master data routes (Stage 4): sites, business units, departments.

All three share the same shape (company-scoped CRUD with soft delete + tenant
isolation), so they live in one file to avoid triplicated boilerplate.

Tenant rules enforced everywhere: company_id is derived from the authenticated
user for client-portal creators; Deloitte users can create for any accessible
company; cross-tenant read/update returns 404 (no existence leakage).

External API surface uses public_id (per approved decision 2); internal FKs
remain integer.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select, func
from pydantic import BaseModel
from app.db.session import get_session
from app.api.deps import require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params
from app.core.tenancy import accessible_company_ids, can_access_company
from app.models.user import User
from app.models.master_data import Site, BusinessUnit, Department
from app.services.audit import log_action


router = APIRouter(prefix="/master-data", tags=["master-data"])


# ---------- Pydantic schemas ----------

class _Base(BaseModel):
    code: str
    name: str
    is_active: bool = True


class SiteCreate(_Base):
    company_id: Optional[int] = None
    country: Optional[str] = None
    region: Optional[str] = None
    site_type: Optional[str] = None


class SiteUpdate(BaseModel):
    name: Optional[str] = None
    country: Optional[str] = None
    region: Optional[str] = None
    site_type: Optional[str] = None
    is_active: Optional[bool] = None


class SiteRead(BaseModel):
    public_id: str
    company_id: int
    code: str
    name: str
    country: Optional[str]
    region: Optional[str]
    site_type: Optional[str]
    is_active: bool
    created_at: datetime


class BUCreate(_Base):
    company_id: Optional[int] = None
    parent_id: Optional[int] = None


class BUUpdate(BaseModel):
    name: Optional[str] = None
    parent_id: Optional[int] = None
    is_active: Optional[bool] = None


class BURead(BaseModel):
    public_id: str
    company_id: int
    code: str
    name: str
    parent_id: Optional[int]
    is_active: bool
    created_at: datetime


class DeptCreate(_Base):
    company_id: Optional[int] = None
    business_unit_id: Optional[int] = None


class DeptUpdate(BaseModel):
    name: Optional[str] = None
    business_unit_id: Optional[int] = None
    is_active: Optional[bool] = None


class DeptRead(BaseModel):
    public_id: str
    company_id: int
    code: str
    name: str
    business_unit_id: Optional[int]
    is_active: bool
    created_at: datetime


# ---------- Shared helpers ----------

def _resolve_company_id(actor: User, requested: Optional[int],
                       session: Session) -> int:
    """Client users are locked to their own company; Deloitte users may supply
    any company they can access. Never trust client input for tenant context."""
    if actor.portal_type == "client":
        if not actor.company_id:
            raise AppError("no_company", "User has no company", 422)
        return actor.company_id
    # Deloitte user
    if requested is None:
        raise AppError("company_required", "company_id is required for Deloitte users", 422, "company_id")
    if not can_access_company(session, actor, requested):
        raise NotFoundError("Company not found")
    return requested


def _visible_scope(session: Session, actor: User, model, stmt):
    """Restrict a query to companies visible to the actor."""
    stmt = stmt.where(model.deleted_at.is_(None))
    if actor.portal_type == "client":
        return stmt.where(model.company_id == actor.company_id)
    ids = accessible_company_ids(session, actor)
    if ids is None:  # org-wide admin
        from app.models.company import Company
        return stmt.where(model.company_id.in_(
            select(Company.id).where(Company.organization_id == actor.organization_id)
        ))
    if not ids:
        return stmt.where(model.company_id.in_([]))
    return stmt.where(model.company_id.in_(ids))


# ---------- Sites ----------

@router.get("/sites", response_model=Page[SiteRead])
def list_sites(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("site:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    company_id: Optional[int] = None,
    search: Optional[str] = None,
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(Site)
    stmt = _visible_scope(session, actor, Site, stmt)
    if company_id is not None:
        stmt = stmt.where(Site.company_id == company_id)
    if search:
        like = f"%{search}%"
        stmt = stmt.where((Site.name.ilike(like)) | (Site.code.ilike(like)))
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(Site.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = session.exec(stmt).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("/sites", response_model=SiteRead, status_code=201)
def create_site(body: SiteCreate, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("site:manage"))):
    company_id = _resolve_company_id(actor, body.company_id, session)
    # Uniqueness: code within company
    dup = session.exec(select(Site).where(
        Site.company_id == company_id, Site.code == body.code, Site.deleted_at.is_(None)
    )).first()
    if dup:
        raise AppError("duplicate_code", f"Site code '{body.code}' already exists in this company",
                       422, "code")
    site = Site(
        company_id=company_id, code=body.code, name=body.name,
        country=body.country, region=body.region, site_type=body.site_type,
        is_active=body.is_active,
    )
    session.add(site); session.flush()
    log_action(session, actor, "site.created", "site", site.id, site.public_id,
               company_id=company_id, changes=body.model_dump(),
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(site)
    return site


@router.get("/sites/{public_id}", response_model=SiteRead)
def get_site(public_id: str, session: Session = Depends(get_session),
             actor: User = Depends(require_permission("site:read"))):
    site = session.exec(select(Site).where(Site.public_id == public_id,
                                            Site.deleted_at.is_(None))).first()
    if not site or not can_access_company(session, actor, site.company_id):
        raise NotFoundError("Site not found")
    return site


@router.patch("/sites/{public_id}", response_model=SiteRead)
def update_site(public_id: str, body: SiteUpdate, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("site:manage"))):
    site = session.exec(select(Site).where(Site.public_id == public_id,
                                            Site.deleted_at.is_(None))).first()
    if not site or not can_access_company(session, actor, site.company_id):
        raise NotFoundError("Site not found")
    changes = body.model_dump(exclude_unset=True)
    before = {k: getattr(site, k) for k in changes}
    for k, v in changes.items():
        setattr(site, k, v)
    site.updated_at = datetime.utcnow()
    session.add(site); session.flush()
    log_action(session, actor, "site.updated", "site", site.id, site.public_id,
               company_id=site.company_id,
               changes={"before": before, "after": changes},
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(site)
    return site


@router.delete("/sites/{public_id}")
def delete_site(public_id: str, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("site:manage"))):
    site = session.exec(select(Site).where(Site.public_id == public_id,
                                            Site.deleted_at.is_(None))).first()
    if not site or not can_access_company(session, actor, site.company_id):
        raise NotFoundError("Site not found")
    site.deleted_at = datetime.utcnow(); site.is_active = False
    session.add(site); session.flush()
    log_action(session, actor, "site.deleted", "site", site.id, site.public_id,
               company_id=site.company_id,
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit()
    return {"ok": True, "public_id": public_id}


# ---------- Business Units ----------

@router.get("/business-units", response_model=Page[BURead])
def list_bus(session: Session = Depends(get_session),
             actor: User = Depends(require_permission("business_unit:read")),
             page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
             company_id: Optional[int] = None):
    page, page_size = paginate_params(page, page_size)
    stmt = _visible_scope(session, actor, BusinessUnit, select(BusinessUnit))
    if company_id is not None:
        stmt = stmt.where(BusinessUnit.company_id == company_id)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(BusinessUnit.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return Page(items=session.exec(stmt).all(), total=total, page=page, page_size=page_size)


@router.post("/business-units", response_model=BURead, status_code=201)
def create_bu(body: BUCreate, request: Request,
              session: Session = Depends(get_session),
              actor: User = Depends(require_permission("business_unit:manage"))):
    company_id = _resolve_company_id(actor, body.company_id, session)
    if session.exec(select(BusinessUnit).where(
        BusinessUnit.company_id == company_id, BusinessUnit.code == body.code,
        BusinessUnit.deleted_at.is_(None))).first():
        raise AppError("duplicate_code", "Business unit code already exists", 422, "code")
    if body.parent_id is not None:
        parent = session.get(BusinessUnit, body.parent_id)
        if not parent or parent.company_id != company_id or parent.deleted_at:
            raise AppError("invalid_parent", "Parent BU not found in this company",
                           422, "parent_id")
    bu = BusinessUnit(company_id=company_id, code=body.code, name=body.name,
                      parent_id=body.parent_id, is_active=body.is_active)
    session.add(bu); session.flush()
    log_action(session, actor, "business_unit.created", "business_unit", bu.id, bu.public_id,
               company_id=company_id, changes=body.model_dump(),
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(bu)
    return bu


@router.get("/business-units/{public_id}", response_model=BURead)
def get_bu(public_id: str, session: Session = Depends(get_session),
           actor: User = Depends(require_permission("business_unit:read"))):
    bu = session.exec(select(BusinessUnit).where(BusinessUnit.public_id == public_id,
                                                   BusinessUnit.deleted_at.is_(None))).first()
    if not bu or not can_access_company(session, actor, bu.company_id):
        raise NotFoundError("Business unit not found")
    return bu


@router.patch("/business-units/{public_id}", response_model=BURead)
def update_bu(public_id: str, body: BUUpdate, request: Request,
              session: Session = Depends(get_session),
              actor: User = Depends(require_permission("business_unit:manage"))):
    bu = session.exec(select(BusinessUnit).where(BusinessUnit.public_id == public_id,
                                                   BusinessUnit.deleted_at.is_(None))).first()
    if not bu or not can_access_company(session, actor, bu.company_id):
        raise NotFoundError("Business unit not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(bu, k, v)
    bu.updated_at = datetime.utcnow()
    session.add(bu); session.flush()
    log_action(session, actor, "business_unit.updated", "business_unit", bu.id, bu.public_id,
               company_id=bu.company_id, changes=changes,
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(bu)
    return bu


@router.delete("/business-units/{public_id}")
def delete_bu(public_id: str, request: Request,
              session: Session = Depends(get_session),
              actor: User = Depends(require_permission("business_unit:manage"))):
    bu = session.exec(select(BusinessUnit).where(BusinessUnit.public_id == public_id,
                                                   BusinessUnit.deleted_at.is_(None))).first()
    if not bu or not can_access_company(session, actor, bu.company_id):
        raise NotFoundError("Business unit not found")
    bu.deleted_at = datetime.utcnow(); bu.is_active = False
    session.add(bu); session.flush()
    log_action(session, actor, "business_unit.deleted", "business_unit", bu.id, bu.public_id,
               company_id=bu.company_id,
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit()
    return {"ok": True, "public_id": public_id}


# ---------- Departments ----------

@router.get("/departments", response_model=Page[DeptRead])
def list_depts(session: Session = Depends(get_session),
               actor: User = Depends(require_permission("department:read")),
               page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
               company_id: Optional[int] = None):
    page, page_size = paginate_params(page, page_size)
    stmt = _visible_scope(session, actor, Department, select(Department))
    if company_id is not None:
        stmt = stmt.where(Department.company_id == company_id)
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(Department.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return Page(items=session.exec(stmt).all(), total=total, page=page, page_size=page_size)


@router.post("/departments", response_model=DeptRead, status_code=201)
def create_dept(body: DeptCreate, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("department:manage"))):
    company_id = _resolve_company_id(actor, body.company_id, session)
    if session.exec(select(Department).where(
        Department.company_id == company_id, Department.code == body.code,
        Department.deleted_at.is_(None))).first():
        raise AppError("duplicate_code", "Department code already exists", 422, "code")
    if body.business_unit_id is not None:
        bu = session.get(BusinessUnit, body.business_unit_id)
        if not bu or bu.company_id != company_id or bu.deleted_at:
            raise AppError("invalid_bu", "Business unit not found in this company",
                           422, "business_unit_id")
    dept = Department(company_id=company_id, code=body.code, name=body.name,
                      business_unit_id=body.business_unit_id, is_active=body.is_active)
    session.add(dept); session.flush()
    log_action(session, actor, "department.created", "department", dept.id, dept.public_id,
               company_id=company_id, changes=body.model_dump(),
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(dept)
    return dept


@router.get("/departments/{public_id}", response_model=DeptRead)
def get_dept(public_id: str, session: Session = Depends(get_session),
             actor: User = Depends(require_permission("department:read"))):
    dept = session.exec(select(Department).where(Department.public_id == public_id,
                                                   Department.deleted_at.is_(None))).first()
    if not dept or not can_access_company(session, actor, dept.company_id):
        raise NotFoundError("Department not found")
    return dept


@router.patch("/departments/{public_id}", response_model=DeptRead)
def update_dept(public_id: str, body: DeptUpdate, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("department:manage"))):
    dept = session.exec(select(Department).where(Department.public_id == public_id,
                                                   Department.deleted_at.is_(None))).first()
    if not dept or not can_access_company(session, actor, dept.company_id):
        raise NotFoundError("Department not found")
    changes = body.model_dump(exclude_unset=True)
    for k, v in changes.items():
        setattr(dept, k, v)
    dept.updated_at = datetime.utcnow()
    session.add(dept); session.flush()
    log_action(session, actor, "department.updated", "department", dept.id, dept.public_id,
               company_id=dept.company_id, changes=changes,
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit(); session.refresh(dept)
    return dept


@router.delete("/departments/{public_id}")
def delete_dept(public_id: str, request: Request,
                session: Session = Depends(get_session),
                actor: User = Depends(require_permission("department:manage"))):
    dept = session.exec(select(Department).where(Department.public_id == public_id,
                                                   Department.deleted_at.is_(None))).first()
    if not dept or not can_access_company(session, actor, dept.company_id):
        raise NotFoundError("Department not found")
    dept.deleted_at = datetime.utcnow(); dept.is_active = False
    session.add(dept); session.flush()
    log_action(session, actor, "department.deleted", "department", dept.id, dept.public_id,
               company_id=dept.company_id,
               ip_address=request.client.host if request.client else None,
               user_agent=request.headers.get("user-agent"))
    session.commit()
    return {"ok": True, "public_id": public_id}
