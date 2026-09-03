"""Dashboard V1 tests — consultant summary, client summary, and the
correctness rules that matter most: approved-only filtering, version
isolation (no double-counting across superseded versions), tenant
isolation, unit-scale handling, and honest unavailable states.

Reuses the exact same helper functions already established in
test_layer1_kpi.py rather than duplicating them.
"""
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import (
    _energy_xlsx, _water_xlsx, _set_storage, _seed, _make_site,
    _submitted_version, _approve, _run_kpi_job,
)
from app.models.dataset import Dataset, DatasetVersion
from app.models.kpi import KpiValue
from app.models.consultant_assignment import ConsultantAssignment


# ================== CONSULTANT DASHBOARD ==================

def test_consultant_dashboard_client_counts_and_plan_distribution(client, session):
    org = bootstrap(session)
    make_company(session, org, "Co A", status="Approved")
    make_company(session, org, "Co B", status="Pending")
    c3 = make_company(session, org, "Co C", status="Approved")
    c3.plan = "Enterprise"
    session.add(c3); session.commit()
    admin = make_user(session, "cda@d.com", "deloitte", "Administrator", org=org)

    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin))
    assert r.status_code == 200
    body = r.json()
    assert body["total_clients"] == 3
    assert body["plan_distribution"]["Enterprise"] == 1
    assert body["plan_distribution"]["Basic"] == 2


def test_consultant_dashboard_registration_queue_uses_real_3_state_status_only(client, session):
    org = bootstrap(session)
    make_company(session, org, "Pending Co", status="Pending")
    make_company(session, org, "Approved Co", status="Approved")
    make_company(session, org, "Rejected Co", status="Rejected")
    admin = make_user(session, "rq@d.com", "deloitte", "Administrator", org=org)

    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin))
    statuses = {row["status"] for row in r.json()["registration_queue"]}
    assert statuses.issubset({"Pending", "Approved", "Rejected"})
    assert "Under Review" not in statuses


def test_consultant_dashboard_registration_queue_shows_assigned_consultant(client, session):
    org = bootstrap(session)
    co = make_company(session, org, "Staffed Co")
    consultant = make_user(session, "staffed@d.com", "deloitte", "Consultant", org=org)
    admin = make_user(session, "aca@d.com", "deloitte", "Administrator", org=org)
    session.add(ConsultantAssignment(company_id=co.id, consultant_user_id=consultant.id, is_active=True))
    session.commit()

    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin))
    row = next(x for x in r.json()["registration_queue"] if x["company_name"] == "Staffed Co")
    assert consultant.name in row["assigned_consultants"]


def test_consultant_dashboard_workload_counts_only_active_assignments(client, session):
    org = bootstrap(session)
    co_a = make_company(session, org, "Co A")
    co_b = make_company(session, org, "Co B")
    consultant = make_user(session, "wl@d.com", "deloitte", "Consultant", org=org)
    admin = make_user(session, "wla@d.com", "deloitte", "Administrator", org=org)
    session.add(ConsultantAssignment(company_id=co_a.id, consultant_user_id=consultant.id, is_active=True))
    session.add(ConsultantAssignment(company_id=co_b.id, consultant_user_id=consultant.id, is_active=False))
    session.commit()

    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin))
    row = next(x for x in r.json()["consultant_workload"] if x["name"] == consultant.name)
    assert row["client_count"] == 1


def test_consultant_dashboard_no_revenue_projects_or_utilization_fields(client, session):
    org = bootstrap(session)
    make_company(session, org, "Co")
    admin = make_user(session, "nrf@d.com", "deloitte", "Administrator", org=org)
    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin))
    body = r.json()
    assert "total_revenue" not in body
    assert "revenue" not in str(body).lower()
    for row in body["consultant_workload"]:
        assert "projects" not in row
        assert "utilization" not in row
        assert "pct" not in row


def test_consultant_dashboard_unauthorized_org_isolation(client, session):
    org_a = bootstrap(session)
    from app.models.organization import Organization
    org_b = Organization(name="A Different Org")
    session.add(org_b); session.commit(); session.refresh(org_b)

    make_company(session, org_a, "Org A Co")
    make_company(session, org_b, "Org B Co")
    admin_b = make_user(session, "orgb@d.com", "deloitte", "Administrator", org=org_b)

    r = client.get("/api/v1/dashboard/consultant", headers=auth(admin_b))
    names = {row["company_name"] for row in r.json()["registration_queue"]}
    assert "Org A Co" not in names
    assert r.json()["total_clients"] == 1


def test_consultant_dashboard_client_actor_only_sees_own_single_company(client, session):
    org = _seed(session)
    co_own = make_company(session, org, "Own Co")
    co_other = make_company(session, org, "Other Co")
    client_user = make_user(session, "wp@client.com", "client", "Client Uploader", company=co_own)
    r = client.get("/api/v1/dashboard/consultant", headers=auth(client_user))
    assert r.status_code == 200
    body = r.json()
    assert body["total_clients"] == 1
    names = {row["company_name"] for row in body["registration_queue"]}
    assert names == {"Own Co"}
    assert "Other Co" not in names


# ================== CLIENT DASHBOARD: aggregation ==================

def test_client_dashboard_energy_and_water_aggregation(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "cdu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "cdr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 5000, "kWh", "Q2 2026"], ["Site A", "diesel", 1000, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.status_code == 200
    body = r.json()
    assert body["energy_consumption"]["value"] == 6000
    assert body["energy_consumption"]["unit"] == "kWh"


def test_client_dashboard_unit_scale_conversion_is_deterministic(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "usu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "usr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 2, "MWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_consumption"]["value"] == 2000


def test_client_dashboard_unrecognized_unit_excluded_not_silently_summed(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "uru@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "urr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([
        ["Site A", "electricity", 500, "kWh", "Q2 2026"],
        ["Site A", "gas", 300, "therms", "Q2 2026"],
    ])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    body = r.json()
    assert body["energy_consumption"]["value"] == 500
    assert "therms" in body["energy_consumption"]["excluded_unrecognized_units"]


def test_client_dashboard_water_recycled_percentage(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "wru@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "wrr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _water_xlsx([["Site A", "groundwater", 200, 50, "ML", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "water_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["water_recycled_percentage"] == 25.0


def test_client_dashboard_zero_withdrawal_denominator_returns_none_not_nan(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "zdu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "zdr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _water_xlsx([["Site A", "groundwater", 0, 0, "ML", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "water_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["water_recycled_percentage"] is None


def test_client_dashboard_no_data_at_all_returns_none_not_zero(client, session):
    org = _seed(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ndu@d.com", "deloitte", "Administrator", org=org)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(admin))
    body = r.json()
    assert body["energy_consumption"]["value"] is None
    assert body["water_withdrawal"]["value"] is None
    assert body["water_recycled_percentage"] is None


def test_client_dashboard_carbon_emissions_always_unavailable(client, session):
    org = _seed(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ceu@d.com", "deloitte", "Administrator", org=org)
    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(admin))
    body = r.json()
    assert body["carbon_emissions"]["available"] is False
    assert body["carbon_emissions"]["reason"] == "methodology_not_configured"


# ================== Approved-only filtering / version isolation ==================

def test_client_dashboard_excludes_rejected_dataset(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "reju@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rejr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 9999, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    r = client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    rv_pid = r.json()["public_id"]
    client.post(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide", json={"decision": "rejected", "note": "No."}, headers=auth(reviewer))

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_consumption"]["value"] is None


def test_client_dashboard_excludes_draft_dataset(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dru@d.com", "deloitte", "Administrator", org=org)

    r = client.post("/api/v1/datasets", json={"company_id": co.id, "upload_type_code": "energy_data",
                    "reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30"}, headers=auth(uploader))
    assert r.status_code == 201

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_consumption"]["value"] is None


def test_version_isolation_no_double_counting_across_superseded_versions(client, session, tmp_path):
    """CRITICAL: v1 approved (100), v2 ALSO approved with a DIFFERENT
    value (999). Dashboard must show ONLY v2's value, never 1099."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "visu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "visr@d.com", "deloitte", "Reviewer", org=org)

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

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_consumption"]["value"] == 999


def test_version_isolation_older_approved_version_still_counts_when_newer_is_only_draft(client, session, tmp_path):
    """v1 approved (100), then a new v2 is created but stays draft. The
    dashboard must still show v1's 100."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "olu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "olr@d.com", "deloitte", "Reviewer", org=org)

    xlsx_v1 = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v1_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx_v1)
    _approve(client, session, uploader, reviewer, ds_pid, v1_pid)
    _run_kpi_job(session, v1_pid)

    r = client.post(f"/api/v1/datasets/{ds_pid}/versions", headers=auth(uploader))
    assert r.status_code == 201

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_consumption"]["value"] == 100


# ================== QoQ ==================

def test_qoq_first_period_returns_none(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "qfu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "qfr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_qoq_percentage"] is None


def test_qoq_valid_comparison_across_two_real_periods(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "qvu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "qvr@d.com", "deloitte", "Reviewer", org=org)

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

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_qoq_percentage"] == 50.0


def test_qoq_zero_previous_value_returns_none_not_fabricated(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "qzu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "qzr@d.com", "deloitte", "Reviewer", org=org)

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

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.json()["energy_qoq_percentage"] is None


# ================== Domain completeness / My Tasks ==================

def test_domain_completeness_partial(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dcu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dcr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    body = r.json()["domain_completeness"]
    assert body["approved_count"] == 1
    assert body["total"] == 4
    assert body["percentage"] == 25.0
    assert body["domains"]["energy_data"] is True
    assert body["domains"]["water_data"] is False


def test_my_tasks_derived_status_three_states(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "mtu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "mtr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    xlsx2 = _water_xlsx([["Site A", "groundwater", 10, 5, "ML", "Q2 2026"]])
    _submitted_version(client, session, uploader, co, "water_data", xlsx2)

    r = client.get(f"/api/v1/dashboard/client/tasks?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    tasks = {t["upload_type_code"]: t["status"] for t in r.json()}
    assert tasks["energy_data"] == "approved"
    assert tasks["water_data"] == "awaiting_approval"
    assert tasks["waste_data"] == "not_submitted"
    assert tasks["emissions_data"] == "not_submitted"


def test_my_tasks_no_fake_priority_or_description_fields(client, session):
    org = _seed(session)
    co = make_company(session, org, "Co")
    admin = make_user(session, "ntu@d.com", "deloitte", "Administrator", org=org)
    r = client.get(f"/api/v1/dashboard/client/tasks?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(admin))
    for t in r.json():
        assert set(t.keys()) == {"upload_type_code", "display_name", "status"}


# ================== Tenant isolation ==================

def test_client_dashboard_tenant_isolation_client_cannot_view_another_company(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    uploader_a = make_user(session, "tia@d.com", "deloitte", "Administrator", org=org)
    reviewer_a = make_user(session, "tir@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tib@client.com", "client", "Client Uploader", company=co_b)

    xlsx = _energy_xlsx([["Site A", "electricity", 12345, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader_a, co_a, "energy_data", xlsx)
    _approve(client, session, uploader_a, reviewer_a, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get("/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30", headers=auth(client_b))
    assert r.json()["energy_consumption"]["value"] is None


def test_client_dashboard_ignores_client_supplied_company_id_override(client, session, tmp_path):
    """CRITICAL security test: a client actor supplying a DIFFERENT
    company_id must be completely ignored -- their own always wins."""
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    uploader_a = make_user(session, "ova@d.com", "deloitte", "Administrator", org=org)
    reviewer_a = make_user(session, "ovr@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "ovb@client.com", "client", "Client Uploader", company=co_b)

    xlsx = _energy_xlsx([["Site A", "electricity", 55555, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader_a, co_a, "energy_data", xlsx)
    _approve(client, session, uploader_a, reviewer_a, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co_a.id}", headers=auth(client_b))
    assert r.json()["energy_consumption"]["value"] is None


def test_consultant_previewing_unrelated_company_denied(client, session):
    org = _seed(session)
    co = make_company(session, org, "Unrelated Co")
    narrow_consultant = make_user(session, "narrow@d.com", "deloitte", "Consultant", org=org)
    r = client.get(f"/api/v1/dashboard/client?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(narrow_consultant))
    assert r.status_code == 404
