"""Who may give which role to whom — shared by /users, /rbac/assign and /admin.

Rules (each closes a gap that let a client administrator escalate):
- A role's scope must match the user's portal: client roles only for client
  users, Deloitte roles only for Deloitte users.
- A client actor may only grant or remove client roles, only for users of
  their own company, and the grant is always scoped to that company.
- A Deloitte actor may act on a client user only for a company they can
  access, and on a Deloitte user only within their own organisation.
- Nobody changes their own roles (no self-escalation, no accidental
  self-lockout).
- A company always keeps at least one active Client Administrator.
"""
from __future__ import annotations

from typing import Optional

from sqlmodel import Session, select

from app.core.errors import AppError, NotFoundError
from app.core.tenancy import can_access_company
from app.models.rbac import Role, UserRole
from app.models.user import User

CLIENT_ADMIN = "Client Administrator"


def get_role(session: Session, code: str) -> Role:
    role = session.exec(select(Role).where(Role.code == code)).first()
    if not role:
        raise AppError("invalid_role", f"Unknown role: {code}", 422, "role_code")
    return role


def check_target_visible(session: Session, actor: User, target: User) -> None:
    """404 when the actor may not manage this user at all."""
    if target is None or target.deleted_at is not None:
        raise NotFoundError("User not found")
    if actor.portal_type == "client":
        if target.company_id != actor.company_id or target.portal_type != "client":
            raise NotFoundError("User not found")
        return
    if target.portal_type == "client":
        if target.company_id is None or not can_access_company(session, actor, target.company_id):
            raise NotFoundError("User not found")
    elif target.organization_id not in (None, actor.organization_id):
        raise NotFoundError("User not found")


def check_role_for_portal(role: Role, portal_type: str) -> None:
    if role.scope != portal_type:
        who = "client users" if role.scope == "client" else "Deloitte users"
        raise AppError("role_scope_mismatch", f"'{role.code}' is a role for {who} only", 422, "role_code")


def check_can_grant(session: Session, actor: User, target: User, role: Role) -> None:
    """Raise unless the actor may give (or take away) this role for this user."""
    check_target_visible(session, actor, target)
    if target.id == actor.id:
        raise AppError("own_roles", "You can't change your own roles — ask another administrator", 403)
    check_role_for_portal(role, target.portal_type)
    if actor.portal_type == "client" and role.scope != "client":
        raise AppError("forbidden_role", "Client administrators can only assign client roles", 403, "role_code")


def active_client_admin_ids(session: Session, company_id: int) -> set[int]:
    role = session.exec(select(Role).where(Role.code == CLIENT_ADMIN)).first()
    if not role:
        return set()
    rows = session.exec(
        select(UserRole.user_id).join(User, User.id == UserRole.user_id).where(
            UserRole.role_id == role.id, User.company_id == company_id, User.is_active == True,  # noqa: E712
            User.deleted_at.is_(None),
            (UserRole.company_id == company_id) | (UserRole.company_id.is_(None)))
    ).all()
    return set(rows)


def check_keeps_an_admin(session: Session, target: User, *, losing_admin: bool) -> None:
    """Refuse a change that would leave the company with no active Client Administrator."""
    if not losing_admin or target.portal_type != "client" or not target.company_id:
        return
    admins = active_client_admin_ids(session, target.company_id)
    if target.id in admins and len(admins) <= 1:
        raise AppError("last_admin", "This is the company's only active Client Administrator. "
                                     "Make someone else an administrator first.", 409)


def client_roles(session: Session, user_id: int, company_id: Optional[int]) -> list[UserRole]:
    """The user's client-scope role links that apply to this company."""
    q = select(UserRole, Role).join(Role, Role.id == UserRole.role_id).where(
        UserRole.user_id == user_id, Role.scope == "client")
    return [ur for ur, _r in session.exec(q).all() if ur.company_id in (None, company_id)]
