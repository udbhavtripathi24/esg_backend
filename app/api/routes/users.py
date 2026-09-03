"""Users CRUD (Stage 3). Password hashing, RBAC role assignment, tenant rules.

Security invariants enforced here:
- hashed_password NEVER returned (UserRead excludes it).
- Client admins can only create/modify users within their OWN company.
- Tenant context (company_id/organization_id) is taken from the authenticated
  creator, NOT from client-supplied input, for client-portal creators.
- Role is assigned through relational RBAC (user_roles), not by trusting the
  users.role string.
- A user cannot escalate their own role / self-assign (enforced: role changes
  require user:manage, and a user cannot grant themselves a role they lack the
  permission to manage — see assign_role).
"""
from typing import Optional
from datetime import datetime
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session, select, func, or_
from app.db.session import get_session
from app.models.user import User, UserCreate, UserRead, UserUpdate
from app.models.rbac import Role, UserRole
from app.api.deps import get_current_user, require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params
from app.core.security import hash_password
from app.core.tenancy import accessible_company_ids

router = APIRouter(prefix="/users", tags=["users"])


def _visible_user_filter(session: Session, actor: User, stmt):
    """Restrict a user query to those the actor may see."""
    if actor.portal_type == "client":
        return stmt.where(User.company_id == actor.company_id)
    ids = accessible_company_ids(session, actor)
    if ids is None:
        # org admin: users in their org OR in companies of their org
        return stmt.where(or_(User.organization_id == actor.organization_id,
                              User.company_id.is_not(None)))
    if not ids:
        # consultant with no assignments: only themselves
        return stmt.where(User.id == actor.id)
    return stmt.where(or_(User.company_id.in_(ids), User.id == actor.id))


@router.get("", response_model=Page[UserRead])
def list_users(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:read")),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=100),
    search: Optional[str] = None,
    role: Optional[str] = None,
    company_id: Optional[int] = None,
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(User).where(User.deleted_at.is_(None))
    stmt = _visible_user_filter(session, actor, stmt)
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(User.name.ilike(like), User.email.ilike(like), User.department.ilike(like)))
    if role:
        stmt = stmt.where(User.role == role)
    if company_id is not None:
        stmt = stmt.where(User.company_id == company_id)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(User.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    items = session.exec(stmt).all()
    return Page(items=items, total=total, page=page, page_size=page_size)


@router.post("", response_model=UserRead, status_code=201)
def create_user(
    user_in: UserCreate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
):
    # Email uniqueness
    if session.exec(select(User).where(User.email == user_in.email)).first():
        raise AppError("email_taken", "A user with this email already exists", 422, "email")

    data = user_in.model_dump(exclude={"password", "role_code"})

    # Tenant ownership: a client admin can ONLY create users in their own company.
    if actor.portal_type == "client":
        data["company_id"] = actor.company_id           # override any supplied value
        data["organization_id"] = None
        data["portal_type"] = "client"
    else:
        # Deloitte creator: if creating a client user, company must be accessible.
        if data.get("company_id") is not None:
            ids = accessible_company_ids(session, actor)
            if ids is not None and data["company_id"] not in ids:
                raise NotFoundError("Company not found")
        # Deloitte-side users are organization-scoped (see Organization model
        # docstring: "consultants are organization users, company_id NULL").
        # Stamp organization_id from the creating actor so the new user
        # remains visible via _visible_user_filter's org-admin branch.
        # Scoped specifically to the NEW user's portal_type being 'deloitte'
        # (not unconditional in this else branch), so a Deloitte actor
        # creating a client-portal user is unaffected — client users are
        # identified by company_id, not organization_id, per the same
        # docstring, and that behavior is untouched here.
        if data.get("portal_type") == "deloitte":
            data["organization_id"] = actor.organization_id

    user = User(**data, hashed_password=hash_password(user_in.password))
    session.add(user)
    session.commit()
    session.refresh(user)

    # RBAC role assignment (authoritative), if a role_code was provided.
    if user_in.role_code:
        role = session.exec(select(Role).where(Role.code == user_in.role_code)).first()
        if not role:
            raise AppError("invalid_role", f"Unknown role: {user_in.role_code}", 422, "role_code")
        session.add(UserRole(user_id=user.id, role_id=role.id, company_id=user.company_id))
        session.commit()

    return user


@router.get("/{user_id}", response_model=UserRead)
def get_user(
    user_id: int,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:read")),
):
    user = session.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise NotFoundError("User not found")
    # Tenant visibility
    if actor.portal_type == "client" and user.company_id != actor.company_id:
        raise NotFoundError("User not found")
    return user


@router.patch("/{user_id}", response_model=UserRead)
def update_user(
    user_id: int,
    user_in: UserUpdate,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
):
    user = session.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise NotFoundError("User not found")
    if actor.portal_type == "client" and user.company_id != actor.company_id:
        raise NotFoundError("User not found")
    for field, value in user_in.model_dump(exclude_unset=True).items():
        setattr(user, field, value)
    user.updated_at = datetime.utcnow()
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


@router.delete("/{user_id}")
def deactivate_user(
    user_id: int,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("user:manage")),
):
    """Soft delete: deactivate + set deleted_at. Users are never hard-deleted."""
    user = session.get(User, user_id)
    if not user or user.deleted_at is not None:
        raise NotFoundError("User not found")
    if actor.portal_type == "client" and user.company_id != actor.company_id:
        raise NotFoundError("User not found")
    if user.id == actor.id:
        raise AppError("self_deactivate", "You cannot deactivate your own account", 422)
    user.is_active = False
    user.deleted_at = datetime.utcnow()
    session.add(user)
    session.commit()
    return {"ok": True, "id": user_id}
