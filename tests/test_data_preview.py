"""Tests for the real pre-approval Data Preview endpoint -- the fix for
a genuine gap found during manual testing (a reviewer could not see a
submission's actual data before deciding whether to approve it).
"""
from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import _energy_xlsx, _set_storage, _seed, _submitted_version, _approve, _run_kpi_job


def test_preview_available_before_any_review_action(client, session, tmp_path):
    """The core fix: preview must work on a freshly-submitted version,
    before any review decision has been made -- this is the exact
    scenario that was previously broken."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpu@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 5000, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)

    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))
    assert r.status_code == 200
    body = r.json()
    assert len(body["rows"]) == 1
    assert body["rows"][0]["value"] == 5000
    assert body["rows"][0]["unit"] == "kWh"
    assert body["rows"][0]["kpi_code"] == "energy.consumption"


def test_preview_available_on_a_draft_never_submitted(client, session, tmp_path):
    """Even a draft (never submitted at all) can be previewed -- the
    file itself was already uploaded, so there's real data to show."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpd@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30"}, headers=auth(uploader))
    ds_pid = r.json()["public_id"]
    v_pid = client.get(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader)).json()[0]["public_id"]
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/files",
               files={"file": ("d.xlsx", xlsx, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))

    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 1


def test_preview_still_available_after_approval(client, session, tmp_path):
    """The fix doesn't take anything away -- preview keeps working after
    approval too, not just before."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpa@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dpar@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)

    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))
    assert r.status_code == 200
    assert len(r.json()["rows"]) == 1


def test_preview_reveals_the_exact_real_problem_found_in_manual_testing(client, session, tmp_path):
    """Directly recreates the real scenario that motivated this fix: a
    file with a negative value, an unrecognized unit, and an unmatched
    site. Confirms a reviewer using this endpoint would have genuinely
    seen all three problems BEFORE deciding, not after."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpf@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([
        ["Head Office", "electricity", -500, "kWh", "Q2 2026"],
        ["Head Office", "gas", 200, "therms", "Q2 2026"],
        ["Unknown Facility", "electricity", 1000, "kWh", "Q2 2026"],
    ])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)

    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))
    assert r.status_code == 200
    rows = r.json()["rows"]
    assert len(rows) == 3
    values = [row["value"] for row in rows]
    assert -500 in values  # the negative value is genuinely visible, not hidden
    units = [row["unit"] for row in rows]
    assert "therms" in units  # the unrecognized unit is genuinely visible
    site_ids = [row["site_public_id"] for row in rows]
    assert None in site_ids  # the unmatched site genuinely shows as unresolved, not fabricated


def test_preview_never_writes_kpi_values(client, session, tmp_path):
    """Viewing a preview must never itself trigger extraction or create
    a KpiValue row -- this is a read, not a side-effecting action."""
    from sqlmodel import select
    from app.models.kpi import KpiValue

    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpn@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)

    client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))
    client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(uploader))  # twice, to be sure

    kvs = session.exec(select(KpiValue)).all()
    assert len(kvs) == 0  # still zero -- preview truly has no side effects


def test_preview_tenant_isolation(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    admin = make_user(session, "dpta@d.com", "deloitte", "Administrator", org=org)
    client_b = make_user(session, "dptb@c.com", "client", "Client Uploader", company=co_b)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, admin, co_a, "energy_data", xlsx)

    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview", headers=auth(client_b))
    assert r.status_code == 404


def test_preview_requires_authentication(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpau@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    r = client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/data-preview")
    assert r.status_code == 401
