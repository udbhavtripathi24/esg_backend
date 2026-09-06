"""Tests for the admin-triggered password reset endpoint."""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from app.core.security import hash_password


def test_admin_can_reset_a_client_users_password(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "rpa@d.com", "deloitte", "Administrator", org=org)
    client_user = make_user(session, "rpc@c.com", "client", "Client Administrator", company=co)
    client_user.hashed_password = hash_password("OldPass123")
    session.add(client_user); session.commit()

    r = client.post(f"/api/v1/users/{client_user.id}/reset-password",
                    json={"new_password": "BrandNewPass456"}, headers=auth(admin))
    assert r.status_code == 204

    r2 = client.post("/api/v1/auth/login", data={"username": "rpc@c.com", "password": "BrandNewPass456"})
    assert r2.status_code == 200

    r3 = client.post("/api/v1/auth/login", data={"username": "rpc@c.com", "password": "OldPass123"})
    assert r3.status_code == 401


def test_reset_password_rejects_too_short(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "rps@d.com", "deloitte", "Administrator", org=org)
    client_user = make_user(session, "rpss@c.com", "client", "Client Administrator", company=co)

    r = client.post(f"/api/v1/users/{client_user.id}/reset-password",
                    json={"new_password": "short"}, headers=auth(admin))
    assert r.status_code == 422


def test_client_admin_cannot_reset_password_for_another_company(client, session):
    org = bootstrap(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    client_admin_a = make_user(session, "rpxa@c.com", "client", "Client Administrator", company=co_a)
    client_user_b = make_user(session, "rpxb@c.com", "client", "Client Administrator", company=co_b)

    r = client.post(f"/api/v1/users/{client_user_b.id}/reset-password",
                    json={"new_password": "SomePass123"}, headers=auth(client_admin_a))
    assert r.status_code == 404


def test_reset_password_requires_authentication(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    client_user = make_user(session, "rpna@c.com", "client", "Client Administrator", company=co)
    r = client.post(f"/api/v1/users/{client_user.id}/reset-password", json={"new_password": "SomePass123"})
    assert r.status_code == 401


def test_reset_password_requires_user_manage_permission(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "rpnp@c.com", "client", "Client Uploader", company=co)  # lacks user:manage
    target = make_user(session, "rpnt@c.com", "client", "Client Administrator", company=co)

    r = client.post(f"/api/v1/users/{target.id}/reset-password",
                    json={"new_password": "SomePass123"}, headers=auth(uploader))
    assert r.status_code == 403
