"""Tests for the new self-service change-password endpoint."""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from app.core.security import hash_password, verify_password


def test_change_password_with_correct_current_password(client, session):
    org = bootstrap(session)
    user = make_user(session, "cpu@d.com", "deloitte", "Administrator", org=org)
    user.hashed_password = hash_password("OldPass123")
    session.add(user); session.commit()

    r = client.post("/api/v1/auth/me/change-password",
                    json={"current_password": "OldPass123", "new_password": "NewPass456"},
                    headers=auth(user))
    assert r.status_code == 204

    r2 = client.post("/api/v1/auth/login", data={"username": "cpu@d.com", "password": "NewPass456"})
    assert r2.status_code == 200

    r3 = client.post("/api/v1/auth/login", data={"username": "cpu@d.com", "password": "OldPass123"})
    assert r3.status_code == 401


def test_change_password_rejects_wrong_current_password(client, session):
    org = bootstrap(session)
    user = make_user(session, "cpw@d.com", "deloitte", "Administrator", org=org)
    user.hashed_password = hash_password("RealPass123")
    session.add(user); session.commit()

    r = client.post("/api/v1/auth/me/change-password",
                    json={"current_password": "WrongPassword", "new_password": "NewPass456"},
                    headers=auth(user))
    assert r.status_code == 403

    r2 = client.post("/api/v1/auth/login", data={"username": "cpw@d.com", "password": "RealPass123"})
    assert r2.status_code == 200  # unchanged


def test_change_password_rejects_too_short_new_password(client, session):
    org = bootstrap(session)
    user = make_user(session, "cps@d.com", "deloitte", "Administrator", org=org)
    user.hashed_password = hash_password("RealPass123")
    session.add(user); session.commit()

    r = client.post("/api/v1/auth/me/change-password",
                    json={"current_password": "RealPass123", "new_password": "short"},
                    headers=auth(user))
    assert r.status_code == 422


def test_change_password_requires_authentication(client):
    r = client.post("/api/v1/auth/me/change-password", json={"current_password": "x", "new_password": "yyyyyyyy"})
    assert r.status_code == 401


def test_client_user_can_change_own_password(client, session):
    """Explicitly proves this works for client-portal users, not just Deloitte ones."""
    org = bootstrap(session)
    co = make_company(session, org, "Co")
    client_user = make_user(session, "cpc@c.com", "client", "Client Administrator", company=co)
    client_user.hashed_password = hash_password("ClientPass123")
    session.add(client_user); session.commit()

    r = client.post("/api/v1/auth/me/change-password",
                    json={"current_password": "ClientPass123", "new_password": "NewClientPass456"},
                    headers=auth(client_user))
    assert r.status_code == 204

    r2 = client.post("/api/v1/auth/login", data={"username": "cpc@c.com", "password": "NewClientPass456"})
    assert r2.status_code == 200


def test_change_password_never_affects_a_different_user(client, session):
    """Confirms the endpoint always operates on the authenticated actor,
    never a different, guessed, or supplied user id."""
    org = bootstrap(session)
    user_a = make_user(session, "cpa@d.com", "deloitte", "Administrator", org=org)
    user_a.hashed_password = hash_password("PassA123")
    user_b = make_user(session, "cpb@d.com", "deloitte", "Administrator", org=org)
    user_b.hashed_password = hash_password("PassB123")
    session.add(user_a); session.add(user_b); session.commit()

    client.post("/api/v1/auth/me/change-password",
               json={"current_password": "PassA123", "new_password": "NewPassA456"},
               headers=auth(user_a))

    r = client.post("/api/v1/auth/login", data={"username": "cpb@d.com", "password": "PassB123"})
    assert r.status_code == 200  # user B's password genuinely untouched
