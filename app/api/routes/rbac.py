"""RBAC introspection routes (Stage 2).

- GET /rbac/me/permissions : the caller's effective permission codes.
- GET /rbac/roles          : list roles (requires user:read) — also serves as a
                             concrete permission-gated endpoint proving the
                             allowed/denied path through the API.
"""
from fastapi import APIRouter, Depends
from sqlmodel import Session, select
from app.db.session import get_session
from app.api.deps import get_current_user, require_permission
from app.models.user import User
from app.models.rbac import Role
from app.rbac.service import get_user_permissions

router = APIRouter(prefix="/rbac", tags=["rbac"])


@router.get("/me/permissions")
def my_permissions(
    user: User = Depends(get_current_user),
    session: Session = Depends(get_session),
):
    perms = get_user_permissions(session, user.id, user.company_id)
    return {"user_id": user.id, "permissions": sorted(perms)}


@router.get("/roles")
def list_roles(
    _user: User = Depends(require_permission("user:read")),
    session: Session = Depends(get_session),
):
    roles = session.exec(select(Role)).all()
    return [{"id": r.id, "code": r.code, "scope": r.scope} for r in roles]


# --- Role assignment (Stage 3): powers the Role Assignment UI ---------------
from pydantic import BaseModel
from app.models.rbac import UserRole
from app.models.user import User as _User
from app.models.company import Company
from app.api.deps import require_permission
from app.core.errors import NotFoundError, AppError


class AssignRoleBody(BaseModel):
    user_id: int
    role_code: str
    company_id: int | None = None


@router.post("/assign", status_code=201)
def assign_role(
    body: AssignRoleBody,
    actor: _User = Depends(require_permission("user:manage")),
    session: Session = Depends(get_session),
):
    user = session.get(_User, body.user_id)
    if not user or user.deleted_at is not None:
        raise NotFoundError("User not found")
    role = session.exec(select(Role).where(Role.code == body.role_code)).first()
    if not role:
        raise AppError("invalid_role", f"Unknown role: {body.role_code}", 422, "role_code")
    # Approved fix: reject a company_id outside the actor's own organization.
    # Mirrors the identical, already-tested pattern in
    # consultant_assignments.py's create_assignment(). Currently a
    # data-integrity/defense-in-depth guard (verified: no code path derives
    # get_user_permissions()'s company_id from anything but the acting
    # user's own fixed User.company_id, so a cross-org row is inert today,
    # not actively exploitable) — but correct structural behavior and
    # hardens against future authorization-logic changes.
    if body.company_id is not None:
        company = session.get(Company, body.company_id)
        if not company or company.organization_id != actor.organization_id:
            raise NotFoundError("Company not found")
    exists = session.exec(
        select(UserRole).where(
            UserRole.user_id == body.user_id,
            UserRole.role_id == role.id,
            UserRole.company_id == body.company_id,
        )
    ).first()
    if exists:
        raise AppError("duplicate_role", "User already has this role", 422)
    session.add(UserRole(user_id=body.user_id, role_id=role.id, company_id=body.company_id))
    session.commit()
    return {"ok": True}


@router.delete("/assign")
def remove_role(
    body: AssignRoleBody,
    _actor: _User = Depends(require_permission("user:manage")),
    session: Session = Depends(get_session),
):
    role = session.exec(select(Role).where(Role.code == body.role_code)).first()
    if not role:
        raise NotFoundError("Role not found")
    link = session.exec(
        select(UserRole).where(
            UserRole.user_id == body.user_id,
            UserRole.role_id == role.id,
            UserRole.company_id == body.company_id,
        )
    ).first()
    if not link:
        raise NotFoundError("Assignment not found")
    session.delete(link)
    session.commit()
    return {"ok": True}
