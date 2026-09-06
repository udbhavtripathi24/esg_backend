"""Report lifecycle API tests -- full journey, tenancy, and state
transitions, via SQLite (this project's established automated-test
convention). The real-Postgres concurrent-regeneration test lives
separately as a live verification script, per this project's own
documented distinction between SQLite automated tests and PostgreSQL
verification.
"""
from datetime import date
from sqlmodel import select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import _energy_xlsx, _set_storage, _seed, _submitted_version, _approve, _run_kpi_job
from app.models.report import Report, ReportVersion


def _real_report_data(client, session, uploader, reviewer, co):
    """Sets up a genuinely real, approved dataset to report against."""
    xlsx = _energy_xlsx([["Site A", "electricity", 5000, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)


# ---------- Readiness ----------

def test_readiness_reflects_real_domain_completeness(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "rdu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "rdr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.get(f"/api/v1/reporting/readiness?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    assert r.status_code == 200
    body = r.json()
    assert body["completeness"]["approved_count"] == 1
    assert body["outstanding"] == []


def test_readiness_shows_outstanding_review_action(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "rou@d.com", "deloitte", "Administrator", org=org)
    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    _submitted_version(client, session, uploader, co, "energy_data", xlsx)  # submitted, never approved

    r = client.get(f"/api/v1/reporting/readiness?period_start=2026-04-01&period_end=2026-06-30&company_id={co.id}", headers=auth(uploader))
    outstanding = r.json()["outstanding"]
    assert len(outstanding) == 1
    assert outstanding[0]["status"] == "submitted"


# ---------- Full real journey ----------

def test_full_report_journey_create_to_publish(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "fju@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "fjr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.post("/api/v1/reports", json={
        "reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id,
    }, headers=auth(uploader))
    assert r.status_code == 201, r.text
    report = r.json()
    assert report["current_version"]["version_number"] == 1
    report_pid = report["public_id"]
    version_pid = report["current_version"]["public_id"]

    # Run the real worker synchronously to complete generation (matches
    # the established test_worker.py pattern of calling handlers directly).
    from app.models.processing_job import ProcessingJob
    from app.workers import _HANDLERS
    job = session.exec(select(ProcessingJob).where(ProcessingJob.job_type == "generate_report")).first()
    _HANDLERS["generate_report"](session, job)

    r = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}", headers=auth(uploader))
    assert r.json()["status"] == "generated"
    assert r.json()["artifact_available"] is True

    r = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/submit-review",
                    json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    assert r.status_code == 200
    assert r.json()["status"] == "under_review"

    r = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/decide",
                    json={"decision": "approved", "note": "Looks good."}, headers=auth(reviewer))
    assert r.status_code == 200
    assert r.json()["status"] == "approved"

    r = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/publish", headers=auth(uploader))
    assert r.status_code == 200
    assert r.json()["status"] == "published"

    r = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}/download", headers=auth(uploader))
    assert r.status_code == 200
    assert "url" in r.json()

    # Actually redeem the signed URL -- proves the SECOND hop works too,
    # not just that the first API call returned something shaped right.
    signed_path = r.json()["url"]
    r_file = client.get(signed_path, headers=auth(uploader))
    assert r_file.status_code == 200
    assert len(r_file.content) > 0
    from docx import Document
    from io import BytesIO
    doc = Document(BytesIO(r_file.content))
    assert any("Deloitte Vista" in p.text for p in doc.paragraphs)


# ---------- changes_requested -> new version ----------

def test_changes_requested_then_regenerate_creates_new_version(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "crv@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "crvr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    report_pid = r.json()["public_id"]
    version_pid = r.json()["current_version"]["public_id"]

    from app.models.processing_job import ProcessingJob
    from app.workers import _HANDLERS
    job = session.exec(select(ProcessingJob).where(ProcessingJob.job_type == "generate_report")).first()
    _HANDLERS["generate_report"](session, job)

    client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/submit-review", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    r = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/decide", json={"decision": "changes_requested", "note": "Fix section X."}, headers=auth(reviewer))
    assert r.json()["status"] == "changes_requested"

    # Old version must remain exactly as it was -- immutable.
    r_old = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}", headers=auth(uploader))
    assert r_old.json()["status"] == "changes_requested"

    r_new = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/regenerate", headers=auth(uploader))
    assert r_new.status_code == 201
    assert r_new.json()["version_number"] == 2


# ---------- Tenant isolation ----------

def test_client_cannot_list_another_companys_reports(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    admin = make_user(session, "tira@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "tirr@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tirb@c.com", "client", "Client Administrator", company=co_b)
    _real_report_data(client, session, admin, reviewer, co_a)

    client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co_a.id}, headers=auth(admin))

    r = client.get("/api/v1/reports", headers=auth(client_b))
    assert r.json()["total"] == 0


def test_client_cannot_open_another_companys_report_detail(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    admin = make_user(session, "tioa@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "tior@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tiob@c.com", "client", "Client Administrator", company=co_b)
    _real_report_data(client, session, admin, reviewer, co_a)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co_a.id}, headers=auth(admin))
    report_pid = r.json()["public_id"]

    r2 = client.get(f"/api/v1/reports/{report_pid}", headers=auth(client_b))
    assert r2.status_code == 404


def test_client_cannot_download_another_companys_artifact(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    admin = make_user(session, "tida@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "tidr@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tidb@c.com", "client", "Client Administrator", company=co_b)
    _real_report_data(client, session, admin, reviewer, co_a)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co_a.id}, headers=auth(admin))
    report_pid, version_pid = r.json()["public_id"], r.json()["current_version"]["public_id"]

    r2 = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}/download", headers=auth(client_b))
    assert r2.status_code == 404


def test_client_cannot_regenerate_another_companys_report(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co_a = make_company(session, org, "Company A")
    co_b = make_company(session, org, "Company B")
    admin = make_user(session, "tiga@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "tigr@d.com", "deloitte", "Reviewer", org=org)
    client_b = make_user(session, "tigb@c.com", "client", "Client Administrator", company=co_b)
    _real_report_data(client, session, admin, reviewer, co_a)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co_a.id}, headers=auth(admin))
    report_pid, version_pid = r.json()["public_id"], r.json()["current_version"]["public_id"]

    r2 = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/regenerate", headers=auth(client_b))
    assert r2.status_code == 404


def test_unauthenticated_requests_rejected(client):
    assert client.get("/api/v1/reports").status_code == 401
    assert client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30"}).status_code == 401
    assert client.get("/api/v1/reporting/readiness?period_start=2026-04-01&period_end=2026-06-30").status_code == 401


# ---------- Reviewer assignment enforcement ----------

def test_only_assigned_reviewer_can_decide(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "oaru@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "oarr@d.com", "deloitte", "Reviewer", org=org)
    other_admin = make_user(session, "oaro@d.com", "deloitte", "Administrator", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    report_pid, version_pid = r.json()["public_id"], r.json()["current_version"]["public_id"]

    from app.models.processing_job import ProcessingJob
    from app.workers import _HANDLERS
    job = session.exec(select(ProcessingJob).where(ProcessingJob.job_type == "generate_report")).first()
    _HANDLERS["generate_report"](session, job)

    client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/submit-review", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    r2 = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/decide", json={"decision": "approved", "note": "x"}, headers=auth(other_admin))
    assert r2.status_code == 403


def test_cannot_decide_twice(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "cdtu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "cdtr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    report_pid, version_pid = r.json()["public_id"], r.json()["current_version"]["public_id"]

    from app.models.processing_job import ProcessingJob
    from app.workers import _HANDLERS
    job = session.exec(select(ProcessingJob).where(ProcessingJob.job_type == "generate_report")).first()
    _HANDLERS["generate_report"](session, job)

    client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/submit-review", json={"reviewer_user_id": reviewer.id}, headers=auth(uploader))
    r1 = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/decide", json={"decision": "approved", "note": "x"}, headers=auth(reviewer))
    assert r1.status_code == 200
    r2 = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/decide", json={"decision": "approved", "note": "x again"}, headers=auth(reviewer))
    assert r2.status_code == 409  # already_decided


def test_cannot_publish_before_approval(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "cpbu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "cpbr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    report_pid, version_pid = r.json()["public_id"], r.json()["current_version"]["public_id"]

    r2 = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/publish", headers=auth(uploader))
    assert r2.status_code == 409  # still 'generating', not 'approved'


def test_duplicate_report_for_same_period_rejected(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "durd@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "durr@d.com", "deloitte", "Reviewer", org=org)
    _real_report_data(client, session, uploader, reviewer, co)

    r1 = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    assert r1.status_code == 201
    r2 = client.post("/api/v1/reports", json={"reporting_period_start": "2026-04-01", "reporting_period_end": "2026-06-30", "company_id": co.id}, headers=auth(uploader))
    assert r2.status_code == 409
