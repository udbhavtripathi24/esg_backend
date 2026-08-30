"""Consultant assignment routes (Stage 3).

Rules enforced:
- consultant must be a Deloitte/organization user (portal_type == 'deloitte')
- company must belong to the same organization as the actor
- duplicate ACTIVE assignments prevented (service-level + DB constraint)
- removal is soft (is_active=False), preserving history
"""
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select
from app.db.session import get_session
from app.models.consultant_assignment import (
    ConsultantAssignment, ConsultantAssignmentCreate,
    ConsultantAssignmentRead, ConsultantAssignmentUpdate,
)
from app.models.user import User
from app.models.company import Company
from app.api.deps import require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params

router = APIRouter(prefix="/consultant-assignments", tags=["consultant-assignments"])


@router.get("", response_model=Page[ConsultantAssignmentRead])
def list_assignments(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    company_id: int | None = None,
    active_only: bool = True,
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(ConsultantAssignment)
    if active_only:
        stmt = stmt.where(ConsultantAssignment.is_active == True)  # noqa: E712
    if company_id is not None:
        stmt = stmt.where(ConsultantAssignment.company_id == company_id)
    rows = session.exec(stmt.offset((page - 1) * page_size).limit(page_size)).all()
    total = len(session.exec(stmt).all())
    return Page(items=rows, total=total, page=page, page_size=page_size)


@router.post("", response_model=ConsultantAssignmentRead, status_code=201)
def create_assignment(
    body: ConsultantAssignmentCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
):
    consultant = session.get(User, body.consultant_user_id)
    if not consultant or consultant.deleted_at is not None:
        raise NotFoundError("Consultant not found")
    if consultant.portal_type != "deloitte":
        raise AppError("invalid_consultant", "Assigned user must be a Deloitte consultant", 422, "consultant_user_id")

    company = session.get(Company, body.company_id)
    if not company or company.deleted_at is not None:
        raise NotFoundError("Company not found")
    # Company must be in the same organization as the actor.
    if company.organization_id != actor.organization_id:
        raise NotFoundError("Company not found")

    # Prevent duplicate ACTIVE assignment.
    existing = session.exec(
        select(ConsultantAssignment).where(
            ConsultantAssignment.company_id == body.company_id,
            ConsultantAssignment.consultant_user_id == body.consultant_user_id,
        )
    ).first()
    if existing and existing.is_active:
        raise AppError("duplicate_assignment", "This consultant is already assigned to this company", 422)
    if existing and not existing.is_active:
        existing.is_active = True
        existing.role_on_account = body.role_on_account
        existing.updated_at = datetime.utcnow()
        session.add(existing); session.commit(); session.refresh(existing)
        return existing

    row = ConsultantAssignment(**body.model_dump())
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


@router.patch("/{assignment_id}", response_model=ConsultantAssignmentRead)
def update_assignment(
    assignment_id: int,
    body: ConsultantAssignmentUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
):
    row = session.get(ConsultantAssignment, assignment_id)
    if not row:
        raise NotFoundError("Assignment not found")
    company = session.get(Company, row.company_id)
    if not company or company.organization_id != actor.organization_id:
        raise NotFoundError("Assignment not found")
    for field, value in body.model_dump(exclude_unset=True).items():
        setattr(row, field, value)
    row.updated_at = datetime.utcnow()
    session.add(row)
    session.commit()
    session.refresh(row)
    return row
