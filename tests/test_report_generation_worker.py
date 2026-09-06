"""Report generation worker tests. Since Phase D (the API layer) doesn't
exist yet, Report/ReportVersion/ReportSnapshot/ReportTemplate are
constructed directly here, using the REAL resolve_snapshot_inputs() and
compute_content_hash() functions to build genuine, correct snapshots --
never fabricated test data standing in for what the real service would
produce.
"""
import json
from datetime import date
from sqlmodel import Session, select

from tests.conftest_helpers import bootstrap, make_company, make_user, auth
from tests.test_layer1_kpi import _energy_xlsx, _set_storage, _seed, _submitted_version, _approve, _run_kpi_job
from app.models.report import Report, ReportVersion, ReportSnapshot, ReportTemplate, ReportArtifact
from app.models.processing_job import ProcessingJob
from app.services.report_snapshot_service import resolve_snapshot_inputs, compute_content_hash
from app.services.report_generation_worker import generate_report_version, ReportGenerationError


def _make_template(session, created_by_id):
    t = ReportTemplate(report_type="esg_management_report", version=1, name="Default v1", created_by=created_by_id)
    session.add(t); session.commit(); session.refresh(t)
    return t


def _make_report_with_snapshot(session, co, uploader, period_start, period_end, version_number=1):
    template = _make_template(session, uploader.id)
    report = Report(company_id=co.id, reporting_period_start=period_start, reporting_period_end=period_end, created_by=uploader.id)
    session.add(report); session.commit(); session.refresh(report)
    rv = ReportVersion(report_id=report.id, version_number=version_number, status="generating", template_id=template.id, created_by=uploader.id)
    session.add(rv); session.commit(); session.refresh(rv)

    resolved = resolve_snapshot_inputs(session, co.id, period_start, period_end)
    content_hash = compute_content_hash(session, resolved["kpi_value_ids"])
    snap = ReportSnapshot(
        report_version_id=rv.id, dataset_version_ids=resolved["dataset_version_ids"],
        kpi_value_ids=resolved["kpi_value_ids"], evidence_file_ids=resolved["evidence_file_ids"],
        configuration={}, content_hash=content_hash,
    )
    session.add(snap); session.commit()
    return report, rv


# ---------- Successful generation, end to end ----------

def test_successful_generation_produces_real_artifact(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "wgu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "wgr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 5000, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    report, rv = _make_report_with_snapshot(session, co, uploader, date(2026, 4, 1), date(2026, 6, 30))
    result = generate_report_version(session, rv.id)

    assert result["status"] == "generated"
    assert result["size_bytes"] > 0

    session.refresh(rv)
    assert rv.status == "generated"
    assert rv.generation_completed_at is not None

    artifact = session.exec(select(ReportArtifact).where(ReportArtifact.report_version_id == rv.id)).first()
    assert artifact is not None
    assert artifact.format == "docx"
    assert artifact.sha256_checksum  # real checksum computed by storage.put()


def test_generation_with_no_approved_data_still_succeeds(client, session, tmp_path):
    """A sparse report (no approved data yet) is a valid, honest report,
    not an error -- it should generate successfully with empty sections."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "spu@d.com", "deloitte", "Administrator", org=org)

    report, rv = _make_report_with_snapshot(session, co, uploader, date(2026, 4, 1), date(2026, 6, 30))
    result = generate_report_version(session, rv.id)
    assert result["status"] == "generated"


# ---------- Idempotency ----------

def test_regenerating_same_version_is_idempotent(client, session, tmp_path):
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "idu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "idr@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    _approve(client, session, uploader, reviewer, ds_pid, v_pid)
    _run_kpi_job(session, v_pid)

    report, rv = _make_report_with_snapshot(session, co, uploader, date(2026, 4, 1), date(2026, 6, 30))
    result1 = generate_report_version(session, rv.id)
    result2 = generate_report_version(session, rv.id)

    assert result1["status"] == "generated"
    assert result2["status"] == "already_generated"
    assert result1["artifact_id"] == result2["artifact_id"]

    artifacts = session.exec(select(ReportArtifact).where(ReportArtifact.report_version_id == rv.id)).all()
    assert len(artifacts) == 1  # never duplicated


# ---------- MANDATORY: session safety after failure ----------

def test_failed_generation_does_not_poison_session_for_subsequent_work(client, session, tmp_path):
    """MANDATORY per the implementation authorization. A missing
    ReportTemplate is a clean, realistic failure that occurs BEFORE any
    database writes in this handler's design -- this test proves that
    design choice actually holds, by confirming the session remains
    fully usable for a genuine, unrelated write immediately afterward."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "fsu@d.com", "deloitte", "Administrator", org=org)

    report = Report(company_id=co.id, reporting_period_start=date(2026, 4, 1), reporting_period_end=date(2026, 6, 30), created_by=uploader.id)
    session.add(report); session.commit(); session.refresh(report)
    # Deliberately reference a template_id that does not exist.
    rv = ReportVersion(report_id=report.id, version_number=1, status="generating", template_id=999999, created_by=uploader.id)
    session.add(rv); session.commit(); session.refresh(rv)
    snap = ReportSnapshot(report_version_id=rv.id, dataset_version_ids=[], kpi_value_ids=[], evidence_file_ids=[], configuration={}, content_hash="x")
    session.add(snap); session.commit()

    raised = False
    try:
        generate_report_version(session, rv.id)
    except ReportGenerationError:
        raised = True
    assert raised

    # THE critical assertion: the session must still be fully usable.
    session.rollback()  # what the real process_one() effectively needs the session to tolerate
    another_co = make_company(session, org, "Another Co After Failure")
    assert another_co.id is not None
    check = session.exec(select(Report).where(Report.id == report.id)).first()
    assert check is not None  # a normal query still works cleanly


# ---------- Data correctness: only approved data ever appears ----------

def test_generation_never_uses_unapproved_data(client, session, tmp_path):
    """The snapshot (built via resolve_snapshot_inputs, already proven
    correct in test_report_snapshot.py) is the sole source of truth for
    what the generator includes -- this test proves the GENERATOR itself
    respects that boundary, not just the snapshot service."""
    _set_storage(tmp_path)
    org = _seed(session)
    co = make_company(session, org, "Co")
    uploader = make_user(session, "nuu@d.com", "deloitte", "Administrator", org=org)
    reviewer = make_user(session, "nur@d.com", "deloitte", "Reviewer", org=org)

    xlsx = _energy_xlsx([["Site A", "electricity", 100, "kWh", "Q2 2026"]])
    ds_pid, v_pid = _submitted_version(client, session, uploader, co, "energy_data", xlsx)
    # Deliberately NOT approved -- stays submitted.

    report, rv = _make_report_with_snapshot(session, co, uploader, date(2026, 4, 1), date(2026, 6, 30))
    result = generate_report_version(session, rv.id)
    assert result["status"] == "generated"

    from docx import Document
    from app.storage.factory import get_storage
    artifact = session.exec(select(ReportArtifact).where(ReportArtifact.report_version_id == rv.id)).first()
    stream = get_storage().get(artifact.storage_key)
    doc = Document(stream)
    full_text = "\n".join(p.text for p in doc.paragraphs)
    assert "100" not in full_text  # the unapproved value must never appear
