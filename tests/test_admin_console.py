"""Admin Console: role-escalation fixes, user directory, roles, status, passwords,
bulk import, targets, approval history, factors, integrations."""
import io
from datetime import datetime, date

import pytest
from openpyxl import Workbook
from sqlmodel import select

from app.models.audit_log import AuditLog
from app.models.rbac import Role, UserRole
from app.models.user import User
from tests.conftest_helpers import auth, bootstrap, make_company, make_user

API = "/api/v1"


def _world(session):
    org = bootstrap(session)
    co = make_company(session, org, "Acme Ltd")
    other = make_company(session, org, "Other Co")
    cadmin = make_user(session, "admin@acme.com", "client", "Client Administrator", company=co)
    up = make_user(session, "up@acme.com", "client", "Client Uploader", company=co)
    dadmin = make_user(session, "da@deloitte.com", "deloitte", "Administrator", org=org)
    return org, co, other, cadmin, up, dadmin


def _roles_of(session, user_id):
    return sorted(r.code for _ur, r in session.exec(
        select(UserRole, Role).join(Role, Role.id == UserRole.role_id).where(UserRole.user_id == user_id)).all())


def _login(client, email, pw):
    return client.post(f"{API}/auth/login", data={"username": email, "password": pw})


# ---------------------------------------------------------------------------
# The escalation that self-service would have exposed
# ---------------------------------------------------------------------------

def test_client_admin_cannot_escalate(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    h = auth(cadmin)
    # Deloitte Administrator for themselves — the original hole
    r = client.post(f"{API}/rbac/assign", json={"user_id": cadmin.id, "role_code": "Administrator",
                                                 "company_id": None}, headers=h)
    assert r.status_code == 403 and r.json()["error"]["code"] == "own_roles"
    # ...or for a colleague
    r = client.post(f"{API}/rbac/assign", json={"user_id": up.id, "role_code": "Administrator"}, headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "role_scope_mismatch"
    # ...or by creating a user with it
    r = client.post(f"{API}/users", json={"name": "X", "email": "x@acme.com", "portal_type": "client",
                                          "role": "Administrator", "password": "pw", "role_code": "Administrator"},
                    headers=h)
    assert r.status_code == 422 and r.json()["error"]["code"] == "role_scope_mismatch"
    assert session.exec(select(User).where(User.email == "x@acme.com")).first() is None
    # ...or by touching a Deloitte user
    r = client.request("DELETE", f"{API}/rbac/assign", json={"user_id": dadmin.id, "role_code": "Administrator"},
                       headers=h)
    assert r.status_code == 404
    # Client roles within the company still work, always scoped to the company
    r = client.post(f"{API}/rbac/assign", json={"user_id": up.id, "role_code": "Client Reviewer",
                                                 "company_id": other.id}, headers=h)
    assert r.status_code == 201
    link = session.exec(select(UserRole).join(Role, Role.id == UserRole.role_id)
                        .where(UserRole.user_id == up.id, Role.code == "Client Reviewer")).one()
    assert link.company_id == co.id
    assert _roles_of(session, cadmin.id) == ["Client Administrator"]
    # Deloitte admins can't hand a Deloitte role to a client user either
    r = client.post(f"{API}/rbac/assign", json={"user_id": up.id, "role_code": "Consultant"}, headers=auth(dadmin))
    assert r.status_code == 422


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def test_user_directory_lifecycle(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    stranger = make_user(session, "s@other.com", "client", "Client Uploader", company=other)
    h = auth(cadmin)

    d = client.get(f"{API}/admin/users", headers=h).json()
    assert d["counts"]["total"] == 2 and {u["email"] for u in d["items"]} == {"admin@acme.com", "up@acme.com"}
    assert [r["code"] for r in d["roles"]][:4] == ["Client Administrator", "Client Approver", "Client Reviewer",
                                                   "Client Uploader"]

    # Add a user: one-time password, lower-cased e-mail, and they can sign in
    r = client.post(f"{API}/admin/users", json={"name": "Asha Verma", "email": "Asha@Acme.com",
                                                 "department": "Sustainability", "role_code": "approver"}, headers=h)
    assert r.status_code == 201, r.text
    new = r.json()
    assert new["user"]["email"] == "asha@acme.com" and new["user"]["role"] == "Client Approver"
    assert len(new["temporary_password"]) == 12
    assert _login(client, "asha@acme.com", new["temporary_password"]).status_code == 200
    dup = client.post(f"{API}/admin/users", json={"name": "A", "email": "ASHA@acme.com", "role_code": "Uploader"},
                      headers=h)
    assert dup.status_code == 422 and dup.json()["error"]["code"] == "email_taken"
    uid = new["user"]["id"]

    # Filters
    assert client.get(f"{API}/admin/users", params={"role": "Client Approver"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/admin/users", params={"search": "sustain"}, headers=h).json()["total"] == 1
    assert "Sustainability" in d["departments"] or "Sustainability" in client.get(
        f"{API}/admin/users", headers=h).json()["departments"]

    # Edit, role, status, password
    r = client.patch(f"{API}/admin/users/{uid}", json={"department": "Finance"}, headers=h)
    assert r.json()["department"] == "Finance"
    r = client.put(f"{API}/admin/users/{uid}/role", json={"role_code": "Client Administrator"}, headers=h)
    assert r.json()["role"] == "Client Administrator" and _roles_of(session, uid) == ["Client Administrator"]
    r = client.put(f"{API}/admin/users/{cadmin.id}/role", json={"role_code": "Client Uploader"}, headers=h)
    assert r.status_code == 403  # own role
    r = client.post(f"{API}/admin/users/{up.id}/status", json={"active": False}, headers=h)
    assert r.json()["status"] == "inactive"
    assert _login(client, "up@acme.com", "pw").status_code in (400, 401, 403)
    client.post(f"{API}/admin/users/{up.id}/status", json={"active": True}, headers=h)
    assert _login(client, "up@acme.com", "pw").status_code == 200
    pw = client.post(f"{API}/admin/users/{up.id}/reset-password", headers=h).json()["temporary_password"]
    assert _login(client, "up@acme.com", pw).status_code == 200
    assert client.post(f"{API}/admin/users/{cadmin.id}/reset-password", headers=h).status_code == 422

    # Other companies' users don't exist for this admin
    assert client.patch(f"{API}/admin/users/{stranger.id}", json={"name": "Z"}, headers=h).status_code == 404
    assert client.put(f"{API}/admin/users/{stranger.id}/role", json={"role_code": "Uploader"},
                      headers=h).status_code == 404

    # Every change is in the audit log
    actions = set(session.exec(select(AuditLog.action).where(AuditLog.company_id == co.id)).all())
    assert {"user.created", "user.updated", "user.role_changed", "user.deactivated", "user.activated",
            "user.password_reset_by_admin"} <= actions


def test_company_keeps_an_administrator(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    # A Deloitte admin can't remove the company's only Client Administrator
    for call in (lambda: client.post(f"{API}/admin/users/{cadmin.id}/status", params={"company_id": co.id},
                                     json={"active": False}, headers=auth(dadmin)),
                 lambda: client.put(f"{API}/admin/users/{cadmin.id}/role", params={"company_id": co.id},
                                    json={"role_code": "Client Uploader"}, headers=auth(dadmin)),
                 lambda: client.delete(f"{API}/users/{cadmin.id}", headers=auth(dadmin))):
        r = call()
        assert r.status_code == 409 and r.json()["error"]["code"] == "last_admin", r.text
    # Once there's a second administrator, it's fine
    client.put(f"{API}/admin/users/{up.id}/role", params={"company_id": co.id},
               json={"role_code": "Client Administrator"}, headers=auth(dadmin))
    r = client.put(f"{API}/admin/users/{cadmin.id}/role", params={"company_id": co.id},
                   json={"role_code": "Client Approver"}, headers=auth(dadmin))
    assert r.status_code == 200 and r.json()["role"] == "Client Approver"


def test_bulk_import(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    h = auth(cadmin)
    bad = ("Name,Email,Department,Role\n"
           "Ravi Kumar,ravi@acme.com,Operations,Uploader\n"
           ",nobody@acme.com,,\n"
           "Dup,up@acme.com,,\n"
           "Meera,meera@acme.com,HR,Superuser\n"
           "Ravi Again,RAVI@acme.com,,\n")
    r = client.post(f"{API}/admin/users/import", files={"file": ("u.csv", bad, "text/csv")},
                    data={"dry_run": "true"}, headers=h)
    assert r.status_code == 200, r.text
    rows = {x["row"]: x for x in r.json()["rows"]}
    assert r.json()["ok"] == 1 and r.json()["with_errors"] == 4
    assert rows[3]["errors"] == ["Name is missing"] and "already exists" in rows[4]["errors"][0]
    assert "Unknown role" in rows[5]["errors"][0] and "twice" in rows[6]["errors"][0]
    r = client.post(f"{API}/admin/users/import", files={"file": ("u.csv", bad, "text/csv")},
                    data={"dry_run": "false"}, headers=h)
    assert r.status_code == 422 and session.exec(select(User).where(User.email == "ravi@acme.com")).first() is None

    wb = Workbook()
    ws = wb.active
    ws.append(["Full name", "E-mail", "Dept", "Role"])
    ws.append(["Ravi Kumar", "ravi@acme.com", "Operations", "approver"])
    ws.append(["Neha Singh", "neha@acme.com", None, None])
    buf = io.BytesIO()
    wb.save(buf)
    r = client.post(f"{API}/admin/users/import", files={"file": ("u.xlsx", buf.getvalue(), "application/octet-stream")},
                    data={"dry_run": "false"}, headers=h)
    assert r.status_code == 200, r.text
    made = {x["email"]: x for x in r.json()["created"]}
    assert made["ravi@acme.com"]["role"] == "Client Approver" and made["neha@acme.com"]["role"] == "Client Uploader"
    assert _login(client, "neha@acme.com", made["neha@acme.com"]["temporary_password"]).status_code == 200
    assert client.post(f"{API}/admin/users/import", files={"file": ("u.csv", "Who,What\n1,2\n", "text/csv")},
                       data={"dry_run": "true"}, headers=h).json()["error"]["code"] == "missing_columns"
    # Uploaders can't import
    assert client.post(f"{API}/admin/users/import", files={"file": ("u.csv", bad, "text/csv")},
                       headers=auth(up)).status_code == 403


# ---------------------------------------------------------------------------
# Targets
# ---------------------------------------------------------------------------

def test_targets_from_data_sheets_and_typed(client, session):
    from app.admin.targets import CATALOG
    from app.brsr.sheet_metrics import compute
    from tests import brsr_fixture
    org, co, other, cadmin, up, dadmin = _world(session)
    brsr_fixture.seed_sheet_data(session, co, up)
    h = auth(cadmin)

    cy = CATALOG["energy_total"]["fn"](compute(session, co.id, "2024-25")["cy"])
    py = CATALOG["energy_total"]["fn"](compute(session, co.id, "2023-24")["cy"])
    assert cy and py
    pre = client.get(f"{API}/admin/targets/metric-value", params={"metric": "energy_total", "fy": "2023-24"},
                     headers=h).json()
    assert pre["value"] == pytest.approx(py)

    body = {"metric": "energy_total", "baseline_fy": "2023-24", "baseline_value": py, "target_fy": "2027-28",
            "target_value": py * 0.8}
    r = client.post(f"{API}/admin/targets", params={"fy": "2024-25"}, json=body, headers=h)
    assert r.status_code == 201, r.text
    t = r.json()
    assert t["title"] == "Total energy consumption" and t["unit"] == "GJ" and t["current"] == pytest.approx(cy)
    # The fixture's sheets cover one month a year: a one-month total isn't compared with a baseline
    assert t["months"] == 1 and t["status"] == "partial" and t["progress"] is None and t["expected"] == 25.0
    assert [x["fy"] for x in t["history"]] == ["2023-24", "2024-25"] and t["history"][1]["partial"]

    # A share is comparable over part of a year, so it is assessed
    share = CATALOG["energy_renewable_share"]["fn"]
    s_py = share(compute(session, co.id, "2023-24")["cy"])
    s_cy = share(compute(session, co.id, "2024-25")["cy"])
    r = client.post(f"{API}/admin/targets", params={"fy": "2024-25"}, headers=h, json={
        "metric": "energy_renewable_share", "baseline_fy": "2023-24", "baseline_value": s_py,
        "target_fy": "2025-26", "target_value": s_py + 10})
    rs = r.json()
    want = 100 * (s_cy - s_py) / 10
    assert rs["status"] != "partial" and rs["progress"] == pytest.approx(round(want, 1)) and rs["expected"] == 50.0
    assert rs["status"] == ("achieved" if want >= 100 else "on_track" if want >= 45 else "at_risk")
    # With no year asked for, the list opens on a year that has data
    assert client.get(f"{API}/admin/targets", headers=h).json()["fy"] in ("2024-25", "2023-24")

    # A typed figure (Scope 1) with its own yearly values
    r = client.post(f"{API}/admin/targets", params={"fy": "2025-26"}, headers=h, json={
        "metric": "scope1", "baseline_fy": "2023-24", "baseline_value": 1000, "target_fy": "2025-26",
        "target_value": 800, "manual_values": {"2024-25": 900, "2025-26": 850}})
    s1 = r.json()
    assert s1["current"] == 850 and s1["current_source"] == "manual"
    assert s1["progress"] == 75.0 and s1["expected"] == 100.0 and s1["status"] == "at_risk"
    lst = client.get(f"{API}/admin/targets", params={"fy": "2026-27"}, headers=h).json()
    assert {i["status"] for i in lst["items"] if i["metric"] == "scope1"} == {"no_data"}
    r = client.put(f"{API}/admin/targets/{s1['id']}", params={"fy": "2026-27"},
                   json={"manual_values": {"2026-27": 790}}, headers=h)
    assert r.json()["status"] == "achieved"

    # Validation and permissions
    bad = client.post(f"{API}/admin/targets", headers=h, json={**body, "target_value": py * 1.2})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "target_wrong_way"
    assert client.post(f"{API}/admin/targets", headers=h, json={**body, "target_fy": "2022-23"}).status_code == 422
    assert client.post(f"{API}/admin/targets", headers=h, json={**body, "baseline_fy": "2023-25"}).status_code == 422
    assert client.get(f"{API}/admin/targets", headers=auth(up)).status_code == 200
    assert client.post(f"{API}/admin/targets", headers=auth(up), json=body).status_code == 403
    assert client.delete(f"{API}/admin/targets/{s1['id']}", headers=h).status_code == 204
    assert len(client.get(f"{API}/admin/targets", headers=h).json()["items"]) == 2
    other_admin = make_user(session, "admin@other.com", "client", "Client Administrator", company=other)
    assert client.delete(f"{API}/admin/targets/{t['id']}", headers=auth(other_admin)).status_code == 404


# ---------------------------------------------------------------------------
# Approval history, factors, integrations
# ---------------------------------------------------------------------------

def test_approval_history(client, session):
    from app.datasheets.seed import ensure_datasheet_types
    from app.models.brsr import BrsrResponseSet
    from app.models.dataset import Dataset, DatasetVersion
    from app.models.review import Review
    from app.models.upload_type import UploadType
    org, co, other, cadmin, up, dadmin = _world(session)
    ensure_datasheet_types(session)
    ut = session.exec(select(UploadType).where(UploadType.code == "sheet_energy")).one()
    ds = Dataset(company_id=co.id, upload_type_id=ut.id, reporting_period_start=date(2024, 4, 1),
                 reporting_period_end=date(2024, 6, 30), created_by=up.id)
    session.add(ds)
    session.flush()
    v = DatasetVersion(dataset_id=ds.id, version_number=1, status="approved", uploaded_by=up.id)
    session.add(v)
    session.flush()
    session.add(Review(dataset_version_id=v.id, reviewer_user_id=dadmin.id, status="approved", assigned_by=up.id,
                       decided_at=datetime(2025, 5, 2), decision_note="Matches the bills"))
    v2 = DatasetVersion(dataset_id=ds.id, version_number=2, status="submitted", uploaded_by=up.id)
    session.add(v2)
    session.flush()
    session.add(Review(dataset_version_id=v2.id, reviewer_user_id=dadmin.id, status="pending", assigned_by=up.id))
    rs = BrsrResponseSet(company_id=co.id, financial_year="2024-25", bank_version="x", created_by=up.id)
    session.add(rs)
    session.flush()
    session.add(AuditLog(company_id=co.id, actor_user_id=dadmin.id, action="brsr.section.approved",
                         entity_type="brsr_section", entity_id=1, entity_public_id=rs.public_id,
                         changes={"section": "P6", "tier": "final", "note_excerpt": "Looks right"},
                         occurred_at=datetime(2025, 6, 1)))
    session.add(AuditLog(company_id=other.id, actor_user_id=dadmin.id, action="brsr.section.approved",
                         entity_type="brsr_section", entity_id=2, changes={"section": "A"}))
    session.commit()

    h = auth(cadmin)
    r = client.get(f"{API}/admin/approvals", headers=h).json()
    assert r["total"] == 3
    top = r["items"][0]
    assert top["status"] == "under_review" and top["by"] is None
    brsr = next(i for i in r["items"] if i["kind"] == "brsr")
    assert brsr["title"] == "BRSR FY 2024-25 · Section P6" and brsr["status_label"] == "Approved" and \
        brsr["by"] == "da" and brsr["comment"] == "Looks right" and brsr["detail"] == "Deloitte review"
    ds_row = next(i for i in r["items"] if i["kind"] == "dataset" and i["status"] == "approved")
    assert ds_row["title"] == ut.display_name and "version 1" in ds_row["detail"] and ds_row["comment"] == \
        "Matches the bills" and ds_row["submitted_by"] == "up"
    assert client.get(f"{API}/admin/approvals", params={"kind": "brsr"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/admin/approvals", params={"status": "approved"}, headers=h).json()["total"] == 2
    assert client.get(f"{API}/admin/approvals", params={"search": "bills"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/admin/approvals", headers=auth(up)).status_code == 403


def test_factors_and_integrations(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    h = auth(cadmin)
    f = {x["key"]: x for x in client.get(f"{API}/admin/factors", headers=h).json()["items"]}
    assert f["diesel_gj_per_l"]["value"] == 0.03612 and f["diesel_gj_per_l"]["needs_signoff"]
    assert any("dg_sets" in u for u in f["diesel_gj_per_l"]["used_for"])

    r = client.post(f"{API}/integrations", json={"type": "Workday HCM", "config": {
        "domain": "HR", "system": "Workday HCM", "sheets": ["diversity", "wages_remuneration"], "owner": "HR ops"}},
        headers=h)
    assert r.status_code == 201, r.text
    assert r.json()["config"]["sheets"] == ["diversity", "wages_remuneration"]
    iid = r.json()["id"]
    assert client.patch(f"{API}/integrations/{iid}", json={"status": "connected"}, headers=h).status_code == 422
    assert client.patch(f"{API}/integrations/{iid}", json={"config": {"password": "x"}}, headers=h).status_code == 422
    assert client.patch(f"{API}/integrations/{iid}", json={"config": {"sheets": ["nope"]}}, headers=h).status_code == 422
    assert client.patch(f"{API}/integrations/{iid}", json={"status": "disabled"}, headers=h).json()["status"] == "disabled"


def test_audit_log_names_and_filters(client, session):
    org, co, other, cadmin, up, dadmin = _world(session)
    h = auth(cadmin)
    client.post(f"{API}/admin/users", json={"name": "Asha", "email": "asha@acme.com", "role_code": "Uploader"}, headers=h)
    session.add(AuditLog(company_id=co.id, actor_user_id=up.id, action="dataset_version.submitted",
                         entity_type="dataset_version", entity_id=1, occurred_at=datetime(2024, 1, 5)))
    session.add(AuditLog(company_id=other.id, actor_user_id=dadmin.id, action="user.created",
                         entity_type="user", entity_id=99))
    session.commit()
    r = client.get(f"{API}/audit-logs", params={"action": "user."}, headers=h).json()
    assert r["total"] == 1 and r["items"][0]["actor_name"] == "admin" and r["items"][0]["company_name"] == "Acme Ltd"
    assert client.get(f"{API}/audit-logs", params={"action": "dataset*"}, headers=h).json()["total"] == 1
    assert client.get(f"{API}/audit-logs", params={"action": "user.created"}, headers=h).json()["total"] == 1
    old = client.get(f"{API}/audit-logs", params={"date_to": "2024-12-31T23:59:59Z"}, headers=h).json()
    assert [i["action"] for i in old["items"]] == ["dataset_version.submitted"]
    mine = client.get(f"{API}/audit-logs", params={"actor_user_id": up.id}, headers=h).json()
    assert mine["total"] == 1
