"""Background worker (Stage 4).

Poll-based worker with SKIP LOCKED so multiple workers coexist safely.
Runs as a separate process: `python -m app.workers.runner`.

Handlers registered as pure functions -> keyed by job_type. Adding a new job
type is: write a handler, register it. No worker changes required.

Deliberately minimal for Stage 4 — proves the plumbing end-to-end with two
skeleton job types (checksum verify + metadata extract). Real jobs (template
validation, KPI recalc) plug in later without touching this loop.
"""
import logging
import time
import traceback
from datetime import datetime
from typing import Callable
from sqlmodel import Session, select
from sqlalchemy import text
from app.db.session import engine
from app.models.processing_job import ProcessingJob
from app.models.dataset import DatasetFile
from app.storage import get_storage
import hashlib


log = logging.getLogger("worker")


# ------- Handler registry -------

Handler = Callable[[Session, ProcessingJob], None]
_HANDLERS: dict[str, Handler] = {}


def register(job_type: str):
    def _decorate(fn: Handler) -> Handler:
        _HANDLERS[job_type] = fn
        return fn
    return _decorate


# ------- Handlers -------

@register("verify_file_checksum")
def _verify_checksum(session: Session, job: ProcessingJob) -> None:
    """Re-read the stored file and confirm sha256 matches what we recorded on
    upload. Protects against silent storage corruption."""
    f = session.get(DatasetFile, job.subject_id)
    if not f:
        raise RuntimeError(f"file {job.subject_id} not found")
    storage = get_storage()
    stream = storage.get(f.storage_key)
    h = hashlib.sha256()
    while chunk := stream.read(65536):
        h.update(chunk)
    if h.hexdigest() != f.sha256_checksum:
        raise RuntimeError(
            f"checksum mismatch for {f.public_id}: "
            f"expected {f.sha256_checksum}, got {h.hexdigest()}"
        )
    # success -> nothing more to write; the job's completed_at is set by the loop




@register("recalculate_kpi_values")
def _recalc_kpi_values(session: Session, job: ProcessingJob) -> None:
    """Real Layer 1 extraction — reads the approved dataset version's data
    file(s) and writes raw, as-reported KpiValue rows.

    NOTE on scope: this remains the same trigger point already established
    in Stage 5 (enqueued only on review approval — see
    app/services/review_service.py's record_decision). What changed is
    the body: it now does real extraction instead of only logging. Real
    KPI *calculation* (applying emission factors, computing scores) is
    still explicitly NOT here — see app/models/kpi.py and
    app/services/kpi_extraction_service.py for the full boundary
    rationale. This function only extracts what was actually reported.
    """
    from app.services.kpi_extraction_service import extract_kpi_values_for_version, ExtractionError
    try:
        result = extract_kpi_values_for_version(session, job.subject_id, job_id=job.id)
        log.info(f"kpi extraction for dataset_version_id={job.subject_id}: {result}")
    except ExtractionError as e:
        # Re-raise so the existing job-retry/dead-letter machinery in
        # process_one() handles it exactly like any other job failure —
        # no new failure-handling path invented here.
        raise RuntimeError(str(e))

@register("extract_file_metadata")
def _extract_metadata(session: Session, job: ProcessingJob) -> None:
    """Skeleton — real implementation later. For now, just proves the pipeline."""
    f = session.get(DatasetFile, job.subject_id)
    if not f:
        raise RuntimeError(f"file {job.subject_id} not found")
    # Real implementation would peek into xlsx/pdf and store row counts, page
    # counts, sheet names, etc. Deliberately not built in Stage 4.


# ------- Loop -------

def _claim_job(session: Session) -> ProcessingJob | None:
    """Claim one pending job using SKIP LOCKED for concurrent worker safety."""
    now = datetime.utcnow()
    # Postgres-specific: SKIP LOCKED prevents two workers grabbing the same row
    row = session.exec(text(
        """
        SELECT id FROM processing_jobs
        WHERE status = 'pending' AND scheduled_at <= :now
        ORDER BY scheduled_at ASC
        LIMIT 1
        FOR UPDATE SKIP LOCKED
        """
    ), params={"now": now}).first()
    if not row:
        return None
    job = session.get(ProcessingJob, row[0])
    if not job:
        return None
    # Idempotency: if a completed job with the same key exists, short-circuit
    if job.idempotency_key:
        prior = session.exec(select(ProcessingJob).where(
            ProcessingJob.idempotency_key == job.idempotency_key,
            ProcessingJob.status == "completed",
            ProcessingJob.id != job.id,
        )).first()
        if prior:
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.updated_at = datetime.utcnow()
            session.add(job)
            session.commit()
            return None
    job.status = "running"
    job.attempts += 1
    job.started_at = now
    job.updated_at = now
    session.add(job)
    session.commit()
    return job


def process_one() -> bool:
    """Process a single job. Returns True if work was done. Used by tests too."""
    with Session(engine) as session:
        job = _claim_job(session)
        job_id = job.id if job else None
    if not job_id:
        return False

    with Session(engine) as session:
        job = session.get(ProcessingJob, job_id)  # rebind in fresh session
        handler = _HANDLERS.get(job.job_type)
        try:
            if not handler:
                raise RuntimeError(f"no handler for job_type '{job.job_type}'")
            handler(session, job)
            job.status = "completed"
            job.completed_at = datetime.utcnow()
            job.last_error = None
        except Exception as e:
            job.last_error = f"{e}\n{traceback.format_exc()[:2000]}"
            if job.attempts >= job.max_attempts:
                job.status = "dead"
            else:
                job.status = "pending"  # will be retried
        job.updated_at = datetime.utcnow()
        session.add(job)
        session.commit()
    return True


def run_forever(poll_interval: float = 2.0):  # pragma: no cover
    log.info("worker started")
    while True:
        try:
            worked = process_one()
            if not worked:
                time.sleep(poll_interval)
        except Exception:
            log.exception("worker loop error")
            time.sleep(poll_interval)
