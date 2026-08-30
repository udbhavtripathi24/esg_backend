"""Company CRUD + tenant isolation tests."""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth


def test_admin_creates_and_lists_company(client, session):
    org = bootstrap(session)
    admin = make_user(session, "admin@deloitte.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/companies", json={"name": "NewCo", "status": "Pending"}, headers=auth(admin))
    assert r.status_code == 201
    assert r.json()["name"] == "NewCo"
    assert r.json()["organization_id"] == org.id  # ownership from actor

    lst = client.get("/api/v1/companies", headers=auth(admin))
    assert lst.status_code == 200
    body = lst.json()
    assert set(body.keys()) == {"items", "total", "page", "page_size"}
    assert body["total"] >= 1


def test_get_and_update_company(client, session):
    org = bootstrap(session)
    admin = make_user(session, "a@deloitte.com", "deloitte", "Administrator", org=org)
    co = make_company(session, org, "EditMe")
    g = client.get(f"/api/v1/companies/{co.id}", headers=auth(admin))
    assert g.status_code == 200
    u = client.patch(f"/api/v1/companies/{co.id}", json={"status": "Approved"}, headers=auth(admin))
    assert u.status_code == 200 and u.json()["status"] == "Approved"


def test_client_sees_only_own_company(client, session):
    org = bootstrap(session)
    a = make_company(session, org, "CompanyA")
    b = make_company(session, org, "CompanyB")
    client_user = make_user(session, "u@a.com", "client", "Client Administrator", company=a)
    # can access own
    assert client.get(f"/api/v1/companies/{a.id}", headers=auth(client_user)).status_code == 200
    # cannot access other -> 404 (no existence leak)
    assert client.get(f"/api/v1/companies/{b.id}", headers=auth(client_user)).status_code == 404
    # list shows only own
    lst = client.get("/api/v1/companies", headers=auth(client_user)).json()
    assert lst["total"] == 1 and lst["items"][0]["id"] == a.id


def test_client_cannot_modify_other_company(client, session):
    org = bootstrap(session)
    a = make_company(session, org, "A")
    b = make_company(session, org, "B")
    cu = make_user(session, "x@a.com", "client", "Client Administrator", company=a)
    # Client Administrator lacks company:manage -> 403 on any create/update
    r = client.patch(f"/api/v1/companies/{b.id}", json={"status": "Approved"}, headers=auth(cu))
    assert r.status_code in (403, 404)


def test_pagination_and_search(client, session):
    org = bootstrap(session)
    admin = make_user(session, "adm@deloitte.com", "deloitte", "Administrator", org=org)
    for i in range(5):
        make_company(session, org, f"Corp{i}")
    r = client.get("/api/v1/companies?page=1&page_size=2", headers=auth(admin)).json()
    assert r["page_size"] == 2 and len(r["items"]) == 2 and r["total"] >= 5
    s = client.get("/api/v1/companies?search=Corp3", headers=auth(admin)).json()
    assert any(c["name"] == "Corp3" for c in s["items"])
