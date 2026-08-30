"""Companies CRUD (Stage 3). require_permission + tenant isolation + soft-delete."""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func, or_
from app.db.session import get_session
from app.models.company import Company, CompanyCreate, CompanyRead, CompanyUpdate
from app.api.deps import get_current_user, require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params
from app.core.tenancy import accessible_company_ids, can_access_company
from app.models.user import User

router = APIRouter(prefix="/companies", tags=["companies"])

_SORTABLE = {"name": Company.name, "status": Company.status,
             "created_at": Company.created_at, "registration_date": Company.registration_date}


@router.get("", response_model=Page[CompanyRead])
def list_companies(
    session: Session = Depends(get_session),
    user: User = Depends(require_permission("company:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    status: Optional[str] = None,
    industry: Optional[str] = None,
    sort: str = Query("created_at"),
    order: str = Query("desc", pattern="^(asc|desc)$"),
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(Company).where(Company.deleted_at.is_(None))

    # Tenant scoping
    ids = accessible_company_ids(session, user)
    if ids is None:
        stmt = stmt.where(Company.organization_id == user.organization_id)
    else:
        if not ids:
            return Page(items=[], total=0, page=page, page_size=page_size)
        stmt = stmt.where(Company.id.in_(ids))

    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Company.name.ilike(like), Company.country.ilike(like)))
    if status:
        stmt = stmt.where(Company.status == status)
    if industry:
        stmt = stmt.where(Company.industry == industry)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()

    col = _SORTABLE.get(sort, Company.created_at)
    stmt = stmt.order_by(col.desc() if order == "desc" else col.asc())
    stmt = stmt.offset((page - 1) * page_size).limit(page_size)
    items = session.exec(stmt).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=CompanyRead, status_code=201)
def create_company(
    company_in: CompanyCreate,
    session: Session = Depends(get_session),
    user: User = Depends(require_permission("company:manage")),
):
    data = company_in.model_dump()
    # Tenant ownership from authenticated identity, not free choice: a company is
    # created under the creator's organization.
    data["organization_id"] = user.organization_id
    company = Company(**data)
    session.add(company)
    session.commit()
    session.refresh(company)
    return company


@router.get("/{company_id}", response_model=CompanyRead)
def get_company(
    company_id: int,
    session: Session = Depends(get_session),
    user: User = Depends(require_permission("company:read")),
):
    company = session.get(Company, company_id)
    if not company or company.deleted_at is not None:
        raise NotFoundError("Company not found")
    if not can_access_company(session, user, company_id):
        raise NotFoundError("Company not found")  # 404, not 403 (no existence leak)
    return company


@router.patch("/{company_id}", response_model=CompanyRead)
def update_company(
    company_id: int,
    company_in: CompanyUpdate,
    session: Session = Depends(get_session),
    user: User = Depends(require_permission("company:manage")),
):
    company = session.get(Company, company_id)
    if not company or company.deleted_at is not None:
        raise NotFoundError("Company not found")
    if not can_access_company(session, user, company_id):
        raise NotFoundError("Company not found")
    for field, value in company_in.model_dump(exclude_unset=True).items():
        setattr(company, field, value)
    company.updated_at = datetime.utcnow()
    session.add(company)
    session.commit()
    session.refresh(company)
    return company
