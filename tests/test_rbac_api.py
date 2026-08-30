"""Verify permission enforcement through the actual API (allowed vs denied)."""
from sqlmodel import select
from app.models.user import User
from app.models.rbac import Role, UserRole
from app.rbac.seed import seed_rbac
from app.core.security import hash_password, create_access_token


def _bootstrap_user(session, role_code):
    seed_rbac(session)
    u = User(name="T", email=f"{role_code}@x.com", portal_type="client",
             role=role_code, hashed_password=hash_password("x"))
    session.add(u); session.commit(); session.refresh(u)
    role = session.exec(select(Role).where(Role.code == role_code)).first()
    session.add(UserRole(user_id=u.id, role_id=role.id)); session.commit()
    return u


def _auth_header(user):
    token = create_access_token({"sub": str(user.id)})
    return {"Authorization": f"Bearer {token}"}


def test_api_denies_without_permission(client, session):
    # Client Uploader lacks user:read -> /rbac/roles must 403
    user = _bootstrap_user(session, "Client Uploader")
    r = client.get("/api/v1/rbac/roles", headers=_auth_header(user))
    assert r.status_code == 403


def test_api_allows_with_permission(client, session):
    # Administrator has user:read -> /rbac/roles must 200
    user = _bootstrap_user(session, "Administrator")
    r = client.get("/api/v1/rbac/roles", headers=_auth_header(user))
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_api_requires_auth(client):
    r = client.get("/api/v1/rbac/roles")
    assert r.status_code == 401


def test_me_permissions_endpoint(client, session):
    user = _bootstrap_user(session, "Administrator")
    r = client.get("/api/v1/rbac/me/permissions", headers=_auth_header(user))
    assert r.status_code == 200
    assert "user:manage" in r.json()["permissions"]
