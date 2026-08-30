"""Idempotent RBAC seeder.

Re-runnable: inserts missing roles/permissions/mappings, never duplicates.
Safe to call on every startup or via the seed script.
"""
from sqlmodel import Session, select
from app.models.rbac import Role, Permission, RolePermission
from app.rbac.definitions import PERMISSIONS, ROLES, ROLE_PERMISSIONS
from app.core.logging import get_logger

log = get_logger("rbac.seed")


def seed_rbac(session: Session) -> dict:
    counts = {"permissions": 0, "roles": 0, "mappings": 0}

    # Permissions
    perm_by_code: dict[str, Permission] = {}
    for code, desc in PERMISSIONS:
        existing = session.exec(select(Permission).where(Permission.code == code)).first()
        if not existing:
            existing = Permission(code=code, description=desc)
            session.add(existing)
            session.flush()
            counts["permissions"] += 1
        perm_by_code[code] = existing

    # Roles
    role_by_code: dict[str, Role] = {}
    for code, scope, desc in ROLES:
        existing = session.exec(select(Role).where(Role.code == code)).first()
        if not existing:
            existing = Role(code=code, scope=scope, description=desc)
            session.add(existing)
            session.flush()
            counts["roles"] += 1
        role_by_code[code] = existing

    # Role -> permission mappings
    for role_code, perm_codes in ROLE_PERMISSIONS.items():
        role = role_by_code[role_code]
        for pc in perm_codes:
            perm = perm_by_code[pc]
            exists = session.exec(
                select(RolePermission).where(
                    RolePermission.role_id == role.id,
                    RolePermission.permission_id == perm.id,
                )
            ).first()
            if not exists:
                session.add(RolePermission(role_id=role.id, permission_id=perm.id))
                counts["mappings"] += 1

    session.commit()
    log.info("rbac_seeded", **counts)
    return counts
