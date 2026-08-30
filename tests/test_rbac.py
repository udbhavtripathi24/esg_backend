"""Stage 2 RBAC tests: seed, mappings, and allowed/denied permission checks.

Uses the SQLite fixture from conftest (schema created from the same SQLModel
metadata), so these run without Postgres. Migration-against-Postgres is verified
separately (see DEVELOPMENT/Stage 2 report).
"""
from sqlmodel import select
from app.models.rbac import Role, Permission, RolePermission, UserRole
from app.models.user import User
from app.rbac.seed import seed_rbac
from app.rbac.service import get_user_permissions, user_has_permission
from app.rbac.definitions import PERMISSIONS, ROLES, ROLE_PERMISSIONS
from app.core.security import hash_password


def _seed(session):
    return seed_rbac(session)


def test_role_seed(session):
    _seed(session)
    roles = session.exec(select(Role)).all()
    assert len(roles) == len(ROLES)
    codes = {r.code for r in roles}
    assert "Administrator" in codes and "Client Uploader" in codes


def test_permission_seed(session):
    _seed(session)
    perms = session.exec(select(Permission)).all()
    assert len(perms) == len(PERMISSIONS)
    assert {p.code for p in perms} >= {"dataset:review", "user:manage"}


def test_role_permission_mapping(session):
    _seed(session)
    admin = session.exec(select(Role).where(Role.code == "Administrator")).first()
    admin_perm_count = len(session.exec(
        select(RolePermission).where(RolePermission.role_id == admin.id)
    ).all())
    assert admin_perm_count == len(ROLE_PERMISSIONS["Administrator"])


def test_seed_is_idempotent(session):
    first = _seed(session)
    second = seed_rbac(session)
    assert second == {"permissions": 0, "roles": 0, "mappings": 0}
    assert first["roles"] > 0


def _make_user(session, email, company_id=None):
    u = User(
        name="Test", email=email, portal_type="client", role="Client Uploader",
        company_id=company_id, hashed_password=hash_password("x"),
    )
    session.add(u)
    session.commit()
    session.refresh(u)
    return u


def _assign(session, user, role_code, company_id=None):
    role = session.exec(select(Role).where(Role.code == role_code)).first()
    session.add(UserRole(user_id=user.id, role_id=role.id, company_id=company_id))
    session.commit()


def test_user_role_mapping(session):
    _seed(session)
    user = _make_user(session, "uploader@acme.com")
    _assign(session, user, "Client Uploader")
    links = session.exec(select(UserRole).where(UserRole.user_id == user.id)).all()
    assert len(links) == 1


def test_allowed_permission(session):
    _seed(session)
    user = _make_user(session, "uploader2@acme.com")
    _assign(session, user, "Client Uploader")
    # Client Uploader has dataset:create
    assert user_has_permission(session, user.id, "dataset:create")


def test_denied_permission(session):
    _seed(session)
    user = _make_user(session, "uploader3@acme.com")
    _assign(session, user, "Client Uploader")
    # Client Uploader does NOT have user:manage
    assert not user_has_permission(session, user.id, "user:manage")
    perms = get_user_permissions(session, user.id)
    assert "user:manage" not in perms and "dataset:create" in perms


def test_user_with_no_roles_has_no_permissions(session):
    _seed(session)
    user = _make_user(session, "noroles@acme.com")
    assert get_user_permissions(session, user.id) == set()
