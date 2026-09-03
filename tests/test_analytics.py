"""Analytics V1 tests — covers the full required scenario list (A-AF):
auth, tenant/org/consultant isolation, approved-only filtering across
every non-approved status, version supersession, unit handling (both
convertible and unrecognized), site/domain/unit breakdowns, averages,
period-over-period in all its edge cases, historical trends, domain
completeness, energy-type breakdown, raw emissions, and error handling.

Reuses the exact same helper functions already established in
test_layer1_kpi.py, matching the established convention for this
project rather than duplicating setup logic.
"""
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import (
    _energy_xlsx, _water_xlsx, _set_storage, _seed, _make_site,
    _submitted_version, _approve, _run_kpi_job,
)
from app.models.dataset import Dataset, DatasetVersion


# ================== A. Authentication ==================

def test_analytics_requires_authentication(client, session):
    r = client.get("/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id=1")
    assert r.status_code == 401


# ================== B/C/D. Tenant / consultant / org isolation ==================

def test_client_tenant_isolation(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    uploader_a = make_user(session, "tia@d.com", "deloitte", "Administrator", org=org)
    reviewer_a = make_user(session, "tir@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tib@client.com", "client", "Client Uploader", company=co_b)

    xlsx = _energy_xlsx([["Site A", "electricity", 999, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader_a, co_a, "energy_data", xlsx)
    _approve(client, session, uploader_a, reviewer_a, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get("/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30", headers=auth(client_b))
    assert r.json()["total"] is None


def test_client_supplied_company_id_ignored(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    uploader_a = make_user(session, "ova@d.com", "deloitte", "Administrator", org=org)
    reviewer_a = make_user(session, "ovr@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "ovb@client.com", "client", "Client Uploader", company=co_b)

    xlsx = _energy_xlsx([["Site A", "electricity", 77777, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader_a, co_a, "energy_data", xlsx)
    _approve(client, session, uploader_a, reviewer_a, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co_a.id}", headers=auth(client_b))
    assert r.json()["total"] is None  # ignored -- still sees only their own (empty) data


def test_consultant_assignment_isolation(client, session):
    org = _seed(session)
    co = make_company(session, org, "Unrelated Co")
    narrow_consultant = make_user(session, "narrow@d.com", "deloitte", "Consultant", org=org)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(narrow_consultant))
    assert r.status_code == 404


def test_organization_isolation(client, session):
    org_a = bootstrap(session)
    from app.models.organization import Organization
    org_b = Organization(name="A Different Org")
    session.add(org_b); session.commit(); session.refresh(org_b)
    co_a = make_company(session, org_a, "Org A Co")
    admin_b = make_user(session, "orgb@d.com", "deloitte", "Administrator", org=org_b)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co_a.id}", headers=auth(admin_b))
    assert r.status_code == 404


# ================== E/G/H/I/J/K. Approved-only filtering, every status ==================

def test_draft_version_excluded(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dru@d.com", "deloitte", "Administrator", org=org)
    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30"}, headers=auth(uploader))
    assert r.status_code == 201
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] is None


def test_submitted_not_yet_reviewed_excluded(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "sbu@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] is None


def test_rejected_version_excluded(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "reju@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rejr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide", json={"decision": "rejected", "note": "No."}, headers=auth(reviewer))
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] is None


def test_changes_requested_version_excluded(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "cru@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "crr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide", json={"decision": "changes_requested", "note": "Fix it."}, headers=auth(reviewer))
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] is None


def test_version_supersession_no_double_counting(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "vsu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "vsr@d.com", "deloitte", "Reviewer", org=org)

    xlsx_v1 = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx_v1)
    _approve(client, session, uploader, reviewer, ds_pid, v1_pid)
    _run_kpi_job(session, v1_pid)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    v2_pid = r.json()["public_id"]
    xlsx_v2 = _energy_xlsx([["Site A", "electricity", 999, "kWh", "Q2 2026"]])
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/files",
               files={"file": ("d2.xlsx", xlsx_v2, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v2_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds_pid, v2_pid)
    _run_kpi_job(session, v2_pid)

    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] == 999  # NOT 1099


# ================== L. Different reporting periods ==================

def test_different_periods_independent(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dpu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dpr@d.com", "deloitte", "Reviewer", org=org)

    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-01-01", "reporting_period_end": "2026-03-31"}, headers=auth(uploader))
    ds1_pid = r.json()["public_id"]
    v1_pid = client.get(f"/api/v1/datasets/{ds1_pid}/versions", headers=auth(uploader)).json()[0]["public_id"]
    xlsx1 = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q1 2026"]])
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/files",
               files={"file": ("q1.xlsx", xlsx1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds1_pid, v1_pid)
    _run_kpi_job(session, v1_pid)

    xlsx2 = _energy_xlsx([["Site A", "electricity", 200, "kWh", "Q2 2026"]])
    ds2_pid, v2_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx2)
    _approve(client, session, uploader, reviewer, ds2_pid, v2_pid)
    _run_kpi_job(session, v2_pid)

    r1 = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-01-01&period_end=2026-03-31&company_id={co.id}", headers=auth(uploader))
    r2 = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r1.json()["total"] == 100
    assert r2.json()["total"] == 200  # never 300 -- periods never merge


# ================== M/N/O. Unit handling ==================

def test_compatible_unit_conversion(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "cuc@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "cur@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 1000, "kWh", "Q2 2026"], ["Site A", "electricity", 1, "MWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["total"] == 2000  # 1000 kWh + 1 MWh (converted to 1000 kWh) = 2000 kWh


def test_unknown_unit_excluded_and_flagged(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "uuf@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "uur@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 500, "kWh", "Q2 2026"], ["Site A", "gas", 300, "therms", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    body = r.json()
    assert body["total"] == 500
    assert "therms" in body["excluded_unrecognized_units"]


def test_by_unit_breakdown_never_converts(client, session, tmp_path):
    """The by_unit breakdown must show raw, as-reported totals per unit
    -- never converted, so heterogeneity is visible, not hidden."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "bnc@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "bnr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 1000, "kWh", "Q2 2026"], ["Site A", "electricity", 1, "MWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    by_unit = {u["unit"]: u["total"] for u in r.json()["by_unit"]}
    assert by_unit == {"kWh": 1000, "MWh": 1}  # raw, never merged into one converted number


# ================== P/Q. Site breakdown ==================

def test_site_breakdown_real_sites(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    site_a = _make_site(session, co, "SITE-A", "Site A")
    uploader = make_user(session, "sba@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "sbr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 300, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    by_site = r.json()["by_site"]
    assert len(by_site) == 1
    assert by_site[0]["value"] == 300


def test_null_site_shown_as_unassigned_not_fabricated(client, session, tmp_path):
    """A row whose site text doesn't match any real Site must appear with
    site_id=None -- never a fabricated site."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "nsu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "nsr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Nonexistent Site", "electricity", 200, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    by_site = r.json()["by_site"]
    assert by_site[0]["site_id"] is None
    assert by_site[0]["value"] == 200


# ================== R. Domain/KPI breakdown ==================

def test_domain_summary_all_five_codes(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dsa@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dsr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 400, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/domains?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    codes = {d["kpi_code"] for d in r.json()}
    assert codes == {"energy.consumption", "water.withdrawal", "water.recycled", "emissions.activity_data", "waste.generated"}
    energy_row = next(d for d in r.json() if d["kpi_code"] == "energy.consumption")
    assert energy_row["total"] == 400
    assert energy_row["display_name"] == "Energy Consumption"


# ================== S/T. Unit breakdown / average ==================

def test_average_is_row_level_not_site_level(client, session, tmp_path):
    """Documented definition: average = mean of individual row values,
    NOT mean of per-site totals."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "avu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "avr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([
        ["Site A", "electricity", 100, "kWh", "Q2 2026"],
        ["Site A", "gas", 200, "kWh", "Q2 2026"],
        ["Site A", "diesel", 300, "kWh", "Q2 2026"],
    ])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["average"] == 200.0  # (100+200+300)/3, row-level


# ================== U/V/W/X. Period-over-period ==================

def test_first_period_comparison_none(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "fpc@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "fpr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/period-comparison?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["change_percentage"] is None


def test_valid_period_comparison(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "vpc@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "vpr@d.com", "deloitte", "Reviewer", org=org)
    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-01-01", "reporting_period_end": "2026-03-31"}, headers=auth(uploader))
    ds1_pid = r.json()["public_id"]
    v1_pid = client.get(f"/api/v1/datasets/{ds1_pid}/versions", headers=auth(uploader)).json()[0]["public_id"]
    xlsx1 = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q1 2026"]])
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/files",
               files={"file": ("q1.xlsx", xlsx1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds1_pid, v1_pid)
    _run_kpi_job(session, v1_pid)
    xlsx2 = _energy_xlsx([["Site A", "electricity", 150, "kWh", "Q2 2026"]])
    ds2_pid, v2_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx2)
    _approve(client, session, uploader, reviewer, ds2_pid, v2_pid)
    _run_kpi_job(session, v2_pid)
    r = client.get(f"/api/v1/analytics/period-comparison?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["change_percentage"] == 50.0


def test_zero_previous_value_returns_none(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "zpv@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "zpr@d.com", "deloitte", "Reviewer", org=org)
    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-01-01", "reporting_period_end": "2026-03-31"}, headers=auth(uploader))
    ds1_pid = r.json()["public_id"]
    v1_pid = client.get(f"/api/v1/datasets/{ds1_pid}/versions", headers=auth(uploader)).json()[0]["public_id"]
    xlsx1 = _energy_xlsx([["Site A", "electricity", 0, "kWh", "Q1 2026"]])
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/files",
               files={"file": ("q1.xlsx", xlsx1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds1_pid, v1_pid)
    _run_kpi_job(session, v1_pid)
    xlsx2 = _energy_xlsx([["Site A", "electricity", 150, "kWh", "Q2 2026"]])
    ds2_pid, v2_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx2)
    _approve(client, session, uploader, reviewer, ds2_pid, v2_pid)
    _run_kpi_job(session, v2_pid)
    r = client.get(f"/api/v1/analytics/period-comparison?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["change_percentage"] is None


# ================== Y. Historical trend ==================

def test_historical_trend_no_interpolation(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "htu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "htr@d.com", "deloitte", "Reviewer", org=org)
    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-01-01", "reporting_period_end": "2026-03-31"}, headers=auth(uploader))
    ds1_pid = r.json()["public_id"]
    v1_pid = client.get(f"/api/v1/datasets/{ds1_pid}/versions", headers=auth(uploader)).json()[0]["public_id"]
    xlsx1 = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q1 2026"]])
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/files",
               files={"file": ("q1.xlsx", xlsx1, "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
               data={"role": "data"}, headers=auth(uploader))
    client.post(f"/api/v1/datasets/{ds1_pid}/versions/{v1_pid}/submit", headers=auth(uploader))
    _approve(client, session, uploader, reviewer, ds1_pid, v1_pid)
    _run_kpi_job(session, v1_pid)
    xlsx2 = _energy_xlsx([["Site A", "electricity", 150, "kWh", "Q2 2026"]])
    ds2_pid, v2_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx2)
    _approve(client, session, uploader, reviewer, ds2_pid, v2_pid)
    _run_kpi_job(session, v2_pid)
    r = client.get(f"/api/v1/analytics/trend?kpi_code=energy.consumption&company_id={co.id}", headers=auth(uploader))
    trend = r.json()
    assert len(trend) == 2  # exactly the 2 real periods, nothing invented in between
    assert trend[0]["value"] == 100
    assert trend[1]["value"] == 150


def test_single_period_trend_not_fabricated_into_a_series(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "spt@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "sptr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/trend?kpi_code=energy.consumption&company_id={co.id}", headers=auth(uploader))
    assert len(r.json()) == 1


# ================== Z. Domain completeness ==================

def test_domain_completeness_reused_correctly(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dcr@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dcrr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/completeness?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    body = r.json()
    assert body["approved_count"] == 1
    assert body["total"] == 4
    assert body["domains"]["energy_data"] is True


# ================== AA. Energy type breakdown ==================

def test_energy_type_breakdown_only_real_categories(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "etb@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "etbr@d.com", "deloitte", "Reviewer", org=org)
    xlsx = _energy_xlsx([
        ["Site A", "electricity", 100, "kWh", "Q2 2026"],
        ["Site A", "diesel", 50, "kWh", "Q2 2026"],
    ])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/energy-breakdown?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    types = {t["energy_type"]: t["value"] for t in r.json()}
    assert types == {"electricity": 100, "diesel": 50}
    assert "renewables" not in types  # never invented, only real categories


# ================== AB/AC. Raw emissions, no CO2e ==================

def test_raw_emissions_activity_data_no_co2e_anywhere(client, session, tmp_path):
    """Emissions summary must show raw activity_data only -- no CO2e,
    no Scope label conversion, nothing computed from it."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "reu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rer@d.com", "deloitte", "Reviewer", org=org)
    from tests.test_layer1_kpi import _emissions_xlsx
    xlsx = _emissions_xlsx([["Site A", "Scope 1", 500, "DEFRA 2024 Grid Factor", "L", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "emissions_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=emissions.activity_data&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    body = r.json()
    assert "co2" not in str(body).lower()
    assert "ktco2e" not in str(body).lower()
    assert "scope" not in str(body).lower()
    by_unit = {u["unit"]: u["total"] for u in body["by_unit"]}
    assert by_unit == {"L": 500}  # raw, as-reported, no factor applied


# ================== AD. Empty state ==================

def test_empty_state_no_data_returns_none_not_zero(client, session):
    org = _seed(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "esn@d.com", "deloitte", "Administrator", org=org)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(admin))
    body = r.json()
    assert body["total"] is None
    assert body["average"] is None
    assert body["row_count"] == 0
    assert body["by_site"] == []


# ================== AE/AF. Malformed input / API errors ==================

def test_invalid_kpi_code_returns_422(client, session):
    org = _seed(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ikc@d.com", "deloitte", "Administrator", org=org)
    r = client.get(f"/api/v1/analytics/summary?kpi_code=not.a.real.code&period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(admin))
    assert r.status_code == 422


def test_missing_required_param_returns_422(client, session):
    org = _seed(session)
    admin = make_user(session, "mrp@d.com", "deloitte", "Administrator", org=org)
    r = client.get("/api/v1/analytics/summary?period_start=2026-04-01&period_end=2026-06-30", headers=auth(admin))
    assert r.status_code == 422


def test_consultant_missing_company_id_returns_422(client, session):
    org = _seed(session)
    admin = make_user(session, "cmc@d.com", "deloitte", "Administrator", org=org)
    r = client.get("/api/v1/analytics/summary?kpi_code=energy.consumption&period_start=2026-04-01&period_end=2026-06-30", headers=auth(admin))
    assert r.status_code == 422
