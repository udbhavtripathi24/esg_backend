"""Consultant assignment tests: create, dedupe, access, denials."""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from app.core.tenancy import can_access_company


def test_create_assignment(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin@deloitte.com", "deloitte", "Administrator", org=org)
    consultant = make_user(session, "con@deloitte.com", "deloitte", "Consultant", org=org)
    co = make_company(session, org, "ClientCo")
    r = client.post("/api/v1/consultant-assignments", json={
        "company_id": co.id, "consultant_user_id": consultant.id, "role_on_account": "Lead",
    }, headers=auth(admin))
    assert r.status_code == 201
    assert r.json()["is_active"] is True


def test_duplicate_active_assignment_prevented(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a@deloitte.com", "deloitte", "Administrator", org=org)
    con = make_user(session, "c@deloitte.com", "deloitte", "Consultant", org=org)
    co = make_company(session, org, "Co")
    p = {"company_id": co.id, "consultant_user_id": con.id}
    assert client.post("/api/v1/consultant-assignments", json=p, headers=auth(admin)).status_code == 201
    dup = client.post("/api/v1/consultant-assignments", json=p, headers=auth(admin))
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "duplicate_assignment"


def test_non_deloitte_user_cannot_be_assigned(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a2@deloitte.com", "deloitte", "Administrator", org=org)
    co = make_company(session, org, "Co")
    client_user = make_user(session, "cu@co.com", "client", "Client Uploader", company=co)
    r = client.post("/api/v1/consultant-assignments", json={
        "company_id": co.id, "consultant_user_id": client_user.id,
    }, headers=auth(admin))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_consultant"


def test_invalid_company_denied(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a3@deloitte.com", "deloitte", "Administrator", org=org)
    con = make_user(session, "c3@deloitte.com", "deloitte", "Consultant", org=org)
    r = client.post("/api/v1/consultant-assignments", json={
        "company_id": 99999, "consultant_user_id": con.id,
    }, headers=auth(admin))
    assert r.status_code == 404


def test_consultant_access_only_via_assignment(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a4@deloitte.com", "deloitte", "Administrator", org=org)
    con = make_user(session, "c4@deloitte.com", "deloitte", "Consultant", org=org)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    # assign consultant to A only
    client.post("/api/v1/consultant-assignments",
                json={"company_id": a.id, "consultant_user_id": con.id}, headers=auth(admin))
    session.refresh(con)
    assert can_access_company(session, con, a.id) is True
    assert can_access_company(session, con, b.id) is False
