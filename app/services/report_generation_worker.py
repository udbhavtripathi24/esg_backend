"""Report generation worker handler.

CRITICAL SAFETY DESIGN, identical in spirit to
app/services/kpi_extraction_service.py's own documented reasoning: the
worker runner (app/workers/__init__.py's process_one()) does NOT call
session.rollback() on handler failure — it reuses the same session to
update the job's own status afterward. This function is therefore
structured to do ALL resolution, generation, and validation work FIRST,
touching the database only ONCE, at the very end, via a single
transaction that creates the ReportArtifact row and updates
ReportVersion's status together. If anything fails before that point,
the session has nothing uncommitted on it, and the outer job-status
update proceeds cleanly.

Storage write happens BEFORE the database commit (matching the
already-reviewed, already-accepted failure-recovery design): if the
worker crashes after the storage write but before the database commit,
the result is an orphaned, never-referenced file — acceptable for MVP,
retry-safe, because the storage key is deterministic and safe to
overwrite on retry.
"""
import logging
from datetime import datetime
from sqlmodel import Session, select

from app.models.report import Report, ReportVersion, ReportSnapshot, ReportArtifact, ReportTemplate
from app.models.kpi import KpiValue, KpiDefinition
from app.models.dataset import DatasetFile
from app.models.company import Company
from app.services.kpi_aggregation import domain_completeness
from app.services.report_docx_generator import build_report_docx, GENERATOR_VERSION
from app.services.report_validation import validate_generated_docx
from app.services.audit import log_action
from app.storage.factory import get_storage

log = logging.getLogger("report_generation")


class ReportGenerationError(Exception):
    """A hard failure — genuinely missing records, storage failure, or a
    document that fails technical validation. NOT raised for ordinary
    'no approved data' cases, which are a valid (if sparse) report, not
    an error."""


def generate_report_version(session: Session, report_version_id: int, job_id: int | None = None) -> dict:
    """Entry point, called from the recalculate-style worker job.
    Returns a summary dict on success. Raises ReportGenerationError on a
    hard failure, letting the existing job-retry/dead-letter machinery
    handle it exactly like any other job failure."""
    rv = session.get(ReportVersion, report_version_id)
    if not rv:
        raise ReportGenerationError(f"ReportVersion {report_version_id} not found")

    # Idempotency: if an artifact already exists for this version+format,
    # don't regenerate -- matches the exact pattern already proven in
    # kpi_extraction_service.py (check before doing any work).
    existing = session.exec(
        select(ReportArtifact).where(
            ReportArtifact.report_version_id == report_version_id,
            ReportArtifact.format == "docx",
        )
    ).first()
    if existing:
        log.info(f"artifact already exists for report_version_id={report_version_id}, skipping")
        return {"status": "already_generated", "artifact_id": existing.id}

    report = session.get(Report, rv.report_id)
    if not report:
        raise ReportGenerationError(f"Report {rv.report_id} not found")
    snapshot = session.exec(select(ReportSnapshot).where(ReportSnapshot.report_version_id == report_version_id)).first()
    if not snapshot:
        raise ReportGenerationError(f"No ReportSnapshot exists for ReportVersion {report_version_id}")
    template = session.get(ReportTemplate, rv.template_id)
    if not template:
        raise ReportGenerationError(f"ReportTemplate {rv.template_id} not found")
    company = session.get(Company, report.company_id)
    if not company:
        raise ReportGenerationError(f"Company {report.company_id} not found")

    # ---- Resolve all data referenced by the (already frozen) snapshot ----
    kpi_value_rows = session.exec(
        select(KpiValue).where(KpiValue.id.in_(snapshot.kpi_value_ids))
    ).all() if snapshot.kpi_value_ids else []

    definitions = {d.code: d for d in session.exec(select(KpiDefinition)).all()}
    kpi_rows_for_doc = [
        {
            "kpi_code": kv.kpi_code,
            "display_name": definitions[kv.kpi_code].display_name if kv.kpi_code in definitions else kv.kpi_code,
            "value": kv.value, "unit": kv.unit, "site_id": kv.site_id, "attributes": kv.attributes,
        }
        for kv in kpi_value_rows
    ]

    evidence_files = session.exec(
        select(DatasetFile).where(DatasetFile.id.in_(snapshot.evidence_file_ids))
    ).all() if snapshot.evidence_file_ids else []
    evidence_for_doc = [{"original_filename": f.original_filename, "public_id": f.public_id} for f in evidence_files]

    completeness = domain_completeness(session, report.company_id, report.reporting_period_start, report.reporting_period_end)

    # ---- Build the document entirely in memory (no DB/storage touched yet) ----
    doc_bytes = build_report_docx(
        company_name=company.name,
        report_type=report.report_type,
        period_start=report.reporting_period_start,
        period_end=report.reporting_period_end,
        version_number=rv.version_number,
        generated_at=datetime.utcnow(),
        template_name=template.name,
        kpi_rows=kpi_rows_for_doc,
        domain_completeness=completeness,
        evidence_files=evidence_for_doc,
        excluded_unrecognized_units=[],  # unit-conversion exclusions are Analytics/Dashboard concerns; raw values are used as-is here
    )

    validation = validate_generated_docx(doc_bytes, expected_kpi_value_count=len(kpi_rows_for_doc))
    if not validation["valid"]:
        raise ReportGenerationError(f"Technical validation failed: {'; '.join(validation['errors'])}")

    # ---- Storage write happens before the DB commit (see module docstring) ----
    storage_key = f"companies/{report.company_id}/reports/{report.public_id}/versions/v{rv.version_number}/report.docx"
    storage = get_storage()
    import io
    stored = storage.put(
        storage_key, io.BytesIO(doc_bytes),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    # ---- Single, atomic DB write: artifact + version status together ----
    artifact = ReportArtifact(
        report_version_id=report_version_id, format="docx", storage_key=storage_key,
        original_filename=f"{report.report_type}_v{rv.version_number}.docx",
        mime_type=stored.mime_type, size_bytes=stored.size_bytes, sha256_checksum=stored.sha256_checksum,
    )
    session.add(artifact)
    rv.status = "generated"
    rv.generation_completed_at = datetime.utcnow()
    session.add(rv)
    log_action(session, None, "report.generation.completed", "report_version", rv.id, rv.public_id,
              company_id=report.company_id, changes={"artifact_format": "docx", "size_bytes": stored.size_bytes})
    session.commit()
    session.refresh(artifact)

    return {"status": "generated", "artifact_id": artifact.id, "size_bytes": stored.size_bytes}
