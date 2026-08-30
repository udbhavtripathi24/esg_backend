"""RBAC resolution: compute a user's effective permission set from the DB.

Resolves user -> user_roles -> roles -> role_permissions -> permissions.
Not cached in the JWT (decision #6): permission changes take effect without
re-login. Called per-request by the require_permission dependency.
"""
from sqlmodel import Session, select
from app.models.rbac import UserRole, Role, RolePermission, Permission


def get_user_permissions(session: Session, user_id: int, company_id: int | None = None) -> set[str]:
    """Return the set of permission codes granted to a user.

    company_id: when provided, includes roles scoped to that company plus
    company-agnostic roles (user_roles.company_id IS NULL). When None, only
    company-agnostic roles are considered.
    """
    # role ids for this user (global roles + optionally company-scoped)
    stmt = select(UserRole.role_id).where(UserRole.user_id == user_id)
    role_ids = set()
    for ur in session.exec(select(UserRole).where(UserRole.user_id == user_id)).all():
        if ur.company_id is None or (company_id is not None and ur.company_id == company_id):
            role_ids.add(ur.role_id)

    if not role_ids:
        return set()

    perm_ids = set()
    for rp in session.exec(select(RolePermission).where(RolePermission.role_id.in_(role_ids))).all():
        perm_ids.add(rp.permission_id)

    if not perm_ids:
        return set()

    codes = set()
    for p in session.exec(select(Permission).where(Permission.id.in_(perm_ids))).all():
        codes.add(p.code)
    return codes


def user_has_permission(session: Session, user_id: int, permission_code: str, company_id: int | None = None) -> bool:
    return permission_code in get_user_permissions(session, user_id, company_id)
