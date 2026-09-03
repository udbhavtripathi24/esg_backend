"""Review Center — Data Preview / Validation endpoint tests.

Reuses the exact same helper functions already established in
test_layer1_kpi.py (dataset/version/approval setup, xlsx builders) rather
than duplicating them.
"""
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import (
    _energy_xlsx, _set_storage, _seed, _make_site, _submitted_version, _approve, _run_kpi_job,
)
from app.models.dataset import DatasetVersion
from app.models.kpi import KpiValue


def _get_validation(client, uploader, ds_pid, v_pid):
    return client.get(f"/api/v1/datasets/{ds_pid}/versions/{v_pid}/kpi-validation", headers=auth(uploader))


# ---------- Availability ----------

def test_validation_not_available_before_extraction(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "nau@d.com", "deloitte", "Administrator", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    # Submitted but not yet approved -- no extraction has run.
    r = _get_validation(client, uploader, ds_pid, v_pid)
    assert r.status_code == 200
    assert r.json()["is_available"] is False
    assert r.json()["errors"] == []
    assert r.json()["warnings"] == []


def test_validation_available_after_approval_and_extraction(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    site = _make_site(session, co, "SITE-A", "Site A")
    uploader = make_user(session, "avu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "avr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, uploader, ds_pid, v_pid)
    assert r.json()["is_available"] is True
    assert r.json()["errors"] == []
    assert r.json()["warnings"] == []


# ---------- Errors ----------

def test_validation_flags_negative_value_as_error(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    _make_site(session, co, "SITE-A", "Site A")
    uploader = make_user(session, "negu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "negr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", -50, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, uploader, ds_pid, v_pid)
    body = r.json()
    assert body["is_available"] is True
    assert len(body["errors"]) == 1
    assert body["errors"][0]["severity"] == "error"
    assert body["errors"][0]["code"] == "negative_value"
    assert "-50" in body["errors"][0]["message"] or "-50.0" in body["errors"][0]["message"]


# ---------- Warnings ----------

def test_validation_flags_unresolved_site_as_warning(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    # Deliberately no Site row created -- "Site Z" will not resolve.
    uploader = make_user(session, "usu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "usr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site Z", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, uploader, ds_pid, v_pid)
    body = r.json()
    assert body["errors"] == []
    assert any(w["code"] == "unresolved_site" for w in body["warnings"])


def test_validation_flags_missing_unit_as_warning(client, session, tmp_path):
    """Unit column is absent from the file entirely -- extraction falls
    back to 'unspecified', which validation should flag."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    _make_site(session, co, "SITE-A", "Site A")
    uploader = make_user(session, "muu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "mur@d.com", "deloitte", "Reviewer", org=org)

    from openpyxl import Workbook
    import io
    wb = Workbook(); ws = wb.active
    ws.append(["Site name or ID", "Energy type (electricity, gas, diesel)", "Consumption value", "Reporting period"])
    ws.append(["Site A", "electricity", 100, "Q2 2026"])
    buf = io.BytesIO(); wb.save(buf)

    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", buf.getvalue())
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, uploader, ds_pid, v_pid)
    body = r.json()
    assert any(w["code"] == "missing_unit" for w in body["warnings"])


def test_validation_flags_duplicate_metric_as_warning(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    _make_site(session, co, "SITE-A", "Site A")
    uploader = make_user(session, "dupu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dupr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([
        ["Site A", "electricity", 100, "kWh", "Q2 2026"],
        ["Site A", "electricity", 150, "kWh", "Q2 2026"],  # same site + same metric, reported twice
    ])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, uploader, ds_pid, v_pid)
    body = r.json()
    assert any(w["code"] == "duplicate_metric" for w in body["warnings"])


# ---------- Tenancy ----------

def test_validation_tenant_isolation(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    uploader_a = make_user(session, "vtia@d.com", "deloitte", "Administrator", org=org)
    reviewer_a = make_user(session, "vtir@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "vtib@client.com", "client", "Client Uploader", company=co_b)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader_a, co_a, "energy_data", xlsx)
    _approve(client, session, uploader_a, reviewer_a, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    # Company B's client must be refused (404, not a leaky 403 -- matches
    # the exact existing IDOR-safe convention used everywhere else).
    r = _get_validation(client, client_b, ds_pid, v_pid)
    assert r.status_code == 404


def test_validation_denies_unrelated_deloitte_user_without_reviewer_assignment(client, session, tmp_path):
    """A Deloitte user who is neither org-wide-scoped to this company nor
    assigned as this version's reviewer must be denied, matching the
    exact same access rule already enforced for reviews/comments."""
    _set_storage(tmp_path)
    org = _seed(session)
    from app.models.organization import Organization
    org2 = Organization(name="A Different Org")  # NOT via bootstrap(), which always names it "Deloitte" and would collide
    session.add(org2); session.commit(); session.refresh(org2)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "dnyu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "dnyr@d.com", "deloitte", "Reviewer", org=org)
    unrelated = make_user(session, "dnyx@d.com", "deloitte", "Administrator", org=org2)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    r = _get_validation(client, unrelated, ds_pid, v_pid)
    assert r.status_code == 404
