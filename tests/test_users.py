"""User CRUD, password hashing, role assignment, tenant + security tests."""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth


def test_create_user_hashes_password_and_hides_hash(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin@deloitte.com", "deloitte", "Administrator", org=org)
    co = make_company(session, org, "ClientCo")
    r = client.post("/api/v1/users", json={
        "name": "Alice", "email": "alice@clientco.com", "portal_type": "client",
        "role": "Client Uploader", "company_id": co.id, "password": "secret123",
        "role_code": "Client Uploader",
    }, headers=auth(admin))
    assert r.status_code == 201
    body = r.json()
    assert "hashed_password" not in body and "password" not in body  # never leaked


def test_duplicate_email_rejected(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a@deloitte.com", "deloitte", "Administrator", org=org)
    payload = {"name": "Bob", "email": "bob@x.com", "portal_type": "deloitte",
               "role": "Support", "password": "x", "role_code": "Support"}
    assert client.post("/api/v1/users", json=payload, headers=auth(admin)).status_code == 201
    dup = client.post("/api/v1/users", json=payload, headers=auth(admin))
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "email_taken"


def test_invalid_role_rejected(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a2@deloitte.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/users", json={
        "name": "C", "email": "c@x.com", "portal_type": "deloitte", "role": "Nope",
        "password": "x", "role_code": "NonexistentRole",
    }, headers=auth(admin))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_role"


def test_client_admin_creates_user_only_in_own_company(client, session):
    org = bootstrap(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    cadmin = make_user(session, "ca@a.com", "client", "Client Administrator", company=a)
    # Even if they supply company_id=B, it's overridden to their own (A).
    r = client.post("/api/v1/users", json={
        "name": "New", "email": "new@a.com", "portal_type": "client",
        "role": "Client Uploader", "company_id": b.id, "password": "x",
        "role_code": "Client Uploader",
    }, headers=auth(cadmin))
    assert r.status_code == 201
    assert r.json()["company_id"] == a.id  # forced to own company, NOT b


def test_deactivate_user_soft_delete(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a3@deloitte.com", "deloitte", "Administrator", org=org)
    co = make_company(session, org, "Co")
    target = make_user(session, "t@co.com", "client", "Client Uploader", company=co)
    r = client.delete(f"/api/v1/users/{target.id}", headers=auth(admin))
    assert r.status_code == 200
    # now hidden
    assert client.get(f"/api/v1/users/{target.id}", headers=auth(admin)).status_code == 404


def test_cannot_deactivate_self(client, session):
    org = bootstrap(session)
    admin = make_user(session, "self@deloitte.com", "deloitte", "Administrator", org=org)
    r = client.delete(f"/api/v1/users/{admin.id}", headers=auth(admin))
    assert r.status_code == 422


def test_uploader_cannot_manage_users(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "up@co.com", "client", "Client Uploader", company=co)
    # Client Uploader lacks user:manage
    r = client.post("/api/v1/users", json={
        "name": "X", "email": "x@co.com", "portal_type": "client", "role": "Client Uploader",
        "password": "x", "role_code": "Client Uploader",
    }, headers=auth(uploader))
    assert r.status_code == 403


def test_client_cannot_see_other_company_users(client, session):
    org = bootstrap(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    ua = make_user(session, "ua@a.com", "client", "Client Administrator", company=a)
    ub = make_user(session, "ub@b.com", "client", "Client Uploader", company=b)
    # A's admin cannot fetch B's user
    assert client.get(f"/api/v1/users/{ub.id}", headers=auth(ua)).status_code == 404
