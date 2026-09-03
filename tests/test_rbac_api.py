"""Verify permission enforcement through the actual API (allowed vs denied)."""
from sqlmodel import select
from app.models.user import User
from app.models.rbac import Role, UserRole
from app.models.organization import Organization
from app.models.company import Company
from app.rbac.seed import seed_rbac
from app.core.security import hash_password, create_access_token
from tests.conftest_helpers import bootstrap, make_company, make_user, auth


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


# ---------------------------------------------------------------------------
# /rbac/assign, /rbac/roles — organization-scoped tenancy check (new fix)
# ---------------------------------------------------------------------------

def test_rbac_assign_rejects_cross_org_company(client, session):
    """A company belonging to a DIFFERENT organization must be rejected —
    the exact gap the approved fix closes."""
    org_a = bootstrap(session)
    admin = make_user(session, "admina@deloitte.com", "deloitte", "Administrator", org=org_a)
    target = make_user(session, "target1@deloitte.com", "deloitte", "Consultant", org=org_a)

    org_b = Organization(name="OtherOrg")
    session.add(org_b); session.commit(); session.refresh(org_b)
    foreign_company = Company(name="ForeignCo", organization_id=org_b.id, status="Approved")
    session.add(foreign_company); session.commit(); session.refresh(foreign_company)

    r = client.post("/api/v1/rbac/assign", json={
        "user_id": target.id, "role_code": "Consultant", "company_id": foreign_company.id,
    }, headers=auth(admin))
    assert r.status_code == 404, r.text

    # Confirm no UserRole was created for this cross-org attempt
    rows = session.exec(select(UserRole).where(
        UserRole.user_id == target.id, UserRole.company_id == foreign_company.id
    )).all()
    assert len(rows) == 0


def test_rbac_assign_accepts_same_org_company(client, session):
    """A company in the actor's OWN organization must still succeed —
    the fix must not break legitimate same-org assignment."""
    org = bootstrap(session)
    admin = make_user(session, "admin2@deloitte.com", "deloitte", "Administrator", org=org)
    target = make_user(session, "target2@deloitte.com", "deloitte", "Consultant", org=org)
    co = make_company(session, org, "SameOrgCo")

    r = client.post("/api/v1/rbac/assign", json={
        "user_id": target.id, "role_code": "Consultant", "company_id": co.id,
    }, headers=auth(admin))
    assert r.status_code == 201, r.text

    role = session.exec(select(Role).where(Role.code == "Consultant")).first()
    row = session.exec(select(UserRole).where(
        UserRole.user_id == target.id, UserRole.role_id == role.id, UserRole.company_id == co.id
    )).first()
    assert row is not None


def test_rbac_assign_duplicate_rejected(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin3@deloitte.com", "deloitte", "Administrator", org=org)
    # make_user() already assigns role_code="Consultant" as a UserRole at
    # creation — use a DIFFERENT role ("Reviewer") for the actual assign
    # calls below so the first assignment is genuinely fresh, not an
    # immediate false-positive duplicate.
    target = make_user(session, "target3@deloitte.com", "deloitte", "Consultant", org=org)

    body = {"user_id": target.id, "role_code": "Reviewer", "company_id": None}
    r1 = client.post("/api/v1/rbac/assign", json=body, headers=auth(admin))
    assert r1.status_code == 201, r1.text
    r2 = client.post("/api/v1/rbac/assign", json=body, headers=auth(admin))
    assert r2.status_code == 422
    assert r2.json()["error"]["code"] == "duplicate_role"

    role = session.exec(select(Role).where(Role.code == "Reviewer")).first()
    rows = session.exec(select(UserRole).where(
        UserRole.user_id == target.id, UserRole.role_id == role.id
    )).all()
    assert len(rows) == 1


def test_rbac_remove_assignment(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin4@deloitte.com", "deloitte", "Administrator", org=org)
    target = make_user(session, "target4@deloitte.com", "deloitte", "Consultant", org=org)

    body = {"user_id": target.id, "role_code": "Support", "company_id": None}
    r1 = client.post("/api/v1/rbac/assign", json=body, headers=auth(admin))
    assert r1.status_code == 201, r1.text

    r2 = client.request("DELETE", "/api/v1/rbac/assign", json=body, headers=auth(admin))
    assert r2.status_code == 200, r2.text

    role = session.exec(select(Role).where(Role.code == "Support")).first()
    row = session.exec(select(UserRole).where(
        UserRole.user_id == target.id, UserRole.role_id == role.id
    )).first()
    assert row is None


def test_rbac_roles_returns_real_roles(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin5@deloitte.com", "deloitte", "Administrator", org=org)
    r = client.get("/api/v1/rbac/roles", headers=auth(admin))
    assert r.status_code == 200
    roles = r.json()
    deloitte_roles = {x["code"] for x in roles if x["scope"] == "deloitte"}
    client_roles = {x["code"] for x in roles if x["scope"] == "client"}
    assert deloitte_roles == {"Administrator", "Consultant", "Reviewer", "Support"}
    assert client_roles == {"Client Administrator", "Client Reviewer", "Client Uploader", "Client Approver"}
