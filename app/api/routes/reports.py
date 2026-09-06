"""Report lifecycle API. Thin routing layer — all snapshot/generation
logic lives in report_snapshot_service.py and the worker; this file
handles HTTP concerns, tenancy, and state transitions only.

Tenancy: identical pattern to every other feature this session —
client's own company_id always server-derived, consultant-supplied one
validated via can_access_company(). Cross-company object access returns
404, matching the established IDOR-safe convention exactly.

Version-numbering race safety: protected by the real database unique
constraint on (report_id, version_number) — a concurrent duplicate
INSERT raises IntegrityError, caught here and converted to a clean 409,
never silent duplication or a 500.

Approval race safety: identical pattern to review_service.py's own
record_decision() — an application-level status check
("if rv.status != 'under_review'") before mutation, relying on Postgres's
own row-level locking to correctly serialize concurrent attempts.
"""
from datetime import datetime, date
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel
from sqlmodel import Session, select, func
from sqlalchemy.exc import IntegrityError

from app.db.session import get_session
from app.api.deps import require_permission
from app.core.errors import AppError, NotFoundError
from app.core.tenancy import can_access_company
from app.core.pagination import Page, paginate_params
from app.models.user import User
from app.models.report import Report, ReportVersion, ReportSnapshot, ReportArtifact, ReportTemplate
from app.models.processing_job import ProcessingJob
from app.services.report_snapshot_service import resolve_snapshot_inputs, compute_content_hash
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification
from app.storage.factory import get_storage

router = APIRouter(prefix="/reports", tags=["reports"])


def _resolve_target_company_id(session: Session, actor: User, company_id: Optional[int]) -> int:
    if actor.portal_type == "client":
        return actor.company_id
    if company_id is None:
        raise AppError("company_id_required", "company_id is required for this actor", 422, "company_id")
    if not can_access_company(session, actor, company_id):
        raise NotFoundError("Company not found")
    return company_id


def _find_report(session: Session, actor: User, report_public_id: str) -> Report:
    report = session.exec(select(Report).where(Report.public_id == report_public_id)).first()
    if not report:
        raise NotFoundError("Report not found")
    if actor.portal_type == "client":
        if report.company_id != actor.company_id:
            raise NotFoundError("Report not found")
    elif not can_access_company(session, actor, report.company_id):
        raise NotFoundError("Report not found")
    return report


def _find_report_and_version(session: Session, actor: User, report_public_id: str, version_public_id: str):
    """Two legitimate access paths, checked in order: (1) normal
    tenancy (client's own company, or can_access_company() for a
    non-client actor), or (2) the actor is the specifically assigned
    reviewer for THIS version (ReportVersion.reviewer_user_id) -- an
    assigned reviewer may have no ConsultantAssignment or broader
    company access at all, exactly mirroring the established pattern
    already proven for Review Center's own reviewer-assignment access."""
    report = session.exec(select(Report).where(Report.public_id == report_public_id)).first()
    if not report:
        raise NotFoundError("Report not found")
    rv = session.exec(select(ReportVersion).where(
        ReportVersion.public_id == version_public_id, ReportVersion.report_id == report.id
    )).first()
    if not rv:
        raise NotFoundError("Report version not found")

    if rv.reviewer_user_id == actor.id:
        return report, rv  # assigned-reviewer access path

    if actor.portal_type == "client":
        if report.company_id != actor.company_id:
            raise NotFoundError("Report not found")
    elif not can_access_company(session, actor, report.company_id):
        raise NotFoundError("Report not found")
    return report, rv


def _get_or_create_default_template(session: Session, actor: User) -> ReportTemplate:
    t = session.exec(select(ReportTemplate).where(
        ReportTemplate.report_type == "esg_management_report", ReportTemplate.is_active == True  # noqa: E712
    ).order_by(ReportTemplate.version.desc())).first()
    if t:
        return t
    t = ReportTemplate(report_type="esg_management_report", version=1, name="Default v1", created_by=actor.id)
    session.add(t); session.commit(); session.refresh(t)
    return t


def _create_version_with_snapshot(session: Session, report: Report, actor: User, version_number: int) -> ReportVersion:
    """Shared by both initial creation and regeneration. Relies on the
    real database unique constraint (report_id, version_number) to make
    concurrent duplicate-version creation fail safely — see module
    docstring."""
    template = _get_or_create_default_template(session, actor)
    rv = ReportVersion(
        report_id=report.id, version_number=version_number, status="generating",
        template_id=template.id, created_by=actor.id, generation_started_at=datetime.utcnow(),
    )
    session.add(rv)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        raise AppError("version_conflict", "A version with this number was just created by another request.", 409)
    session.refresh(rv)

    resolved = resolve_snapshot_inputs(session, report.company_id, report.reporting_period_start, report.reporting_period_end)
    content_hash = compute_content_hash(session, resolved["kpi_value_ids"])
    snap = ReportSnapshot(
        report_version_id=rv.id, dataset_version_ids=resolved["dataset_version_ids"],
        kpi_value_ids=resolved["kpi_value_ids"], evidence_file_ids=resolved["evidence_file_ids"],
        configuration={}, content_hash=content_hash,
    )
    session.add(snap)
    job = ProcessingJob(
        job_type="generate_report", subject_type="report_version", subject_id=rv.id,
        idempotency_key=f"report-gen-{rv.id}",
    )
    session.add(job)
    log_action(session, actor, "report.snapshot.created", "report_version", rv.id, rv.public_id, company_id=report.company_id,
              changes={"dataset_version_ids": resolved["dataset_version_ids"]})
    session.commit()
    return rv


class ReportCreate(BaseModel):
    reporting_period_start: date
    reporting_period_end: date
    company_id: Optional[int] = None
    report_type: str = "esg_management_report"


class ReportVersionRead(BaseModel):
    public_id: str
    version_number: int
    status: str
    created_at: datetime
    generation_completed_at: Optional[datetime]
    published_at: Optional[datetime]


class ReportRead(BaseModel):
    public_id: str
    report_type: str
    reporting_period_start: str
    reporting_period_end: str
    status: str
    created_at: datetime
    current_version: Optional[ReportVersionRead]


def _to_report_read(session: Session, report: Report) -> ReportRead:
    current = None
    if report.current_version_id:
        rv = session.get(ReportVersion, report.current_version_id)
        if rv:
            current = ReportVersionRead(
                public_id=rv.public_id, version_number=rv.version_number, status=rv.status,
                created_at=rv.created_at, generation_completed_at=rv.generation_completed_at, published_at=rv.published_at,
            )
    return ReportRead(
        public_id=report.public_id, report_type=report.report_type,
        reporting_period_start=str(report.reporting_period_start), reporting_period_end=str(report.reporting_period_end),
        status=report.status, created_at=report.created_at, current_version=current,
    )


@router.post("", response_model=ReportRead, status_code=201)
def create_report(
    body: ReportCreate, request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("report:generate")),
):
    target_company_id = _resolve_target_company_id(session, actor, body.company_id)

    existing = session.exec(select(Report).where(
        Report.company_id == target_company_id, Report.report_type == body.report_type,
        Report.reporting_period_start == body.reporting_period_start,
        Report.reporting_period_end == body.reporting_period_end,
    )).first()
    if existing:
        raise AppError("report_already_exists", f"A report already exists for this period ({existing.public_id}). Use regenerate to create a new version.", 409)

    report = Report(
        company_id=target_company_id, report_type=body.report_type,
        reporting_period_start=body.reporting_period_start, reporting_period_end=body.reporting_period_end,
        created_by=actor.id,
    )
    session.add(report); session.commit(); session.refresh(report)
    log_action(session, actor, "report.created", "report", report.id, report.public_id, company_id=target_company_id,
              ip_address=request.client.host if request.client else None)
    session.commit()

    rv = _create_version_with_snapshot(session, report, actor, version_number=1)
    report.current_version_id = rv.id
    report.status = "generating"
    session.add(report); session.commit(); session.refresh(report)

    return _to_report_read(session, report)


@router.get("", response_model=Page[ReportRead])
def list_reports(
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    company_id: Optional[int] = None,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("report:read")),
):
    page, page_size = paginate_params(page, page_size)
    stmt = select(Report)
    if actor.portal_type == "client":
        stmt = stmt.where(Report.company_id == actor.company_id)
    else:
        from app.core.tenancy import accessible_company_ids
        ids = accessible_company_ids(session, actor)
        if ids is None:
            from app.models.company import Company
            stmt = stmt.where(Report.company_id.in_(select(Company.id).where(Company.organization_id == actor.organization_id)))
        elif ids:
            stmt = stmt.where(Report.company_id.in_(ids))
        else:
            return Page(items=[], total=0, page=page, page_size=page_size)
    if company_id is not None:
        stmt = stmt.where(Report.company_id == company_id)

    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(Report.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    rows = session.exec(stmt).all()
    return Page(items=[_to_report_read(session, r) for r in rows], total=total, page=page, page_size=page_size)


@router.get("/{report_public_id}", response_model=ReportRead)
def get_report(report_public_id: str, session: Session = Depends(get_session), actor: User = Depends(require_permission("report:read"))):
    report = _find_report(session, actor, report_public_id)
    return _to_report_read(session, report)


@router.get("/{report_public_id}/versions", response_model=list[ReportVersionRead])
def list_report_versions(report_public_id: str, session: Session = Depends(get_session), actor: User = Depends(require_permission("report:read"))):
    report = _find_report(session, actor, report_public_id)
    versions = session.exec(select(ReportVersion).where(ReportVersion.report_id == report.id).order_by(ReportVersion.version_number)).all()
    return [ReportVersionRead(public_id=v.public_id, version_number=v.version_number, status=v.status,
                              created_at=v.created_at, generation_completed_at=v.generation_completed_at, published_at=v.published_at)
            for v in versions]


class ReportVersionDetailRead(ReportVersionRead):
    dataset_version_ids: list[int]
    kpi_value_count: int
    artifact_available: bool
    failure_reason: Optional[str]


@router.get("/{report_public_id}/versions/{version_public_id}", response_model=ReportVersionDetailRead)
def get_report_version(report_public_id: str, version_public_id: str, session: Session = Depends(get_session), actor: User = Depends(require_permission("report:read"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    snap = session.exec(select(ReportSnapshot).where(ReportSnapshot.report_version_id == rv.id)).first()
    artifact = session.exec(select(ReportArtifact).where(ReportArtifact.report_version_id == rv.id)).first()
    return ReportVersionDetailRead(
        public_id=rv.public_id, version_number=rv.version_number, status=rv.status,
        created_at=rv.created_at, generation_completed_at=rv.generation_completed_at, published_at=rv.published_at,
        dataset_version_ids=snap.dataset_version_ids if snap else [],
        kpi_value_count=len(snap.kpi_value_ids) if snap else 0,
        artifact_available=artifact is not None, failure_reason=rv.failure_reason,
    )


@router.post("/{report_public_id}/versions/{version_public_id}/regenerate", response_model=ReportVersionRead, status_code=201)
def regenerate_report(report_public_id: str, version_public_id: str, session: Session = Depends(get_session), actor: User = Depends(require_permission("report:generate"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    max_version = session.exec(select(func.max(ReportVersion.version_number)).where(ReportVersion.report_id == report.id)).one()
    new_rv = _create_version_with_snapshot(session, report, actor, version_number=(max_version or 0) + 1)
    report.current_version_id = new_rv.id
    report.status = "generating"
    session.add(report); session.commit()
    return ReportVersionRead(public_id=new_rv.public_id, version_number=new_rv.version_number, status=new_rv.status,
                             created_at=new_rv.created_at, generation_completed_at=None, published_at=None)


class SubmitReviewBody(BaseModel):
    reviewer_user_id: int


@router.post("/{report_public_id}/versions/{version_public_id}/submit-review", response_model=ReportVersionRead)
def submit_report_for_review(report_public_id: str, version_public_id: str, body: SubmitReviewBody,
                             session: Session = Depends(get_session), actor: User = Depends(require_permission("report:generate"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    if rv.status != "generated":
        raise AppError("invalid_state", f"Cannot submit for review from status '{rv.status}'", 409)
    rv.status = "under_review"
    rv.reviewer_user_id = body.reviewer_user_id
    session.add(rv)
    log_action(session, actor, "report.submitted_for_review", "report_version", rv.id, rv.public_id, company_id=report.company_id)
    enqueue_notification(session, event_type="report_submitted_for_review", recipient_user_ids=[body.reviewer_user_id],
                         title="A report is awaiting your review", entity_type="report_version",
                         entity_id=rv.id, entity_public_id=rv.public_id)
    session.commit()
    return ReportVersionRead(public_id=rv.public_id, version_number=rv.version_number, status=rv.status,
                             created_at=rv.created_at, generation_completed_at=rv.generation_completed_at, published_at=rv.published_at)


class DecideBody(BaseModel):
    decision: str
    note: str


@router.post("/{report_public_id}/versions/{version_public_id}/decide", response_model=ReportVersionRead)
def decide_report(report_public_id: str, version_public_id: str, body: DecideBody,
                  session: Session = Depends(get_session), actor: User = Depends(require_permission("report:read"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    if body.decision not in ("approved", "changes_requested"):
        raise AppError("invalid_decision", "decision must be 'approved' or 'changes_requested'", 422, "decision")
    if rv.status != "under_review":
        raise AppError("already_decided", f"This report version is not under review (status: '{rv.status}')", 409)
    if rv.reviewer_user_id != actor.id:
        raise AppError("not_assigned_reviewer", "You are not the assigned reviewer for this report version", 403)

    rv.status = body.decision
    rv.reviewed_at = datetime.utcnow()
    if body.decision == "approved":
        rv.approved_at = datetime.utcnow()
    session.add(rv)
    log_action(session, actor, f"report.{body.decision}", "report_version", rv.id, rv.public_id, company_id=report.company_id, changes={"note": body.note})
    title = "Your report was approved" if body.decision == "approved" else "Changes were requested on your report"
    enqueue_notification(session, event_type=f"report_{body.decision}", recipient_user_ids=[report.created_by],
                         title=title, entity_type="report_version", entity_id=rv.id, entity_public_id=rv.public_id)
    session.commit()
    return ReportVersionRead(public_id=rv.public_id, version_number=rv.version_number, status=rv.status,
                             created_at=rv.created_at, generation_completed_at=rv.generation_completed_at, published_at=rv.published_at)


@router.post("/{report_public_id}/versions/{version_public_id}/publish", response_model=ReportVersionRead)
def publish_report(report_public_id: str, version_public_id: str, session: Session = Depends(get_session), actor: User = Depends(require_permission("report:generate"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    if rv.status != "approved":
        raise AppError("invalid_state", f"Cannot publish from status '{rv.status}' -- must be 'approved' first", 409)
    rv.status = "published"
    rv.published_at = datetime.utcnow()
    session.add(rv)
    report.status = "published"
    session.add(report)
    log_action(session, actor, "report.published", "report_version", rv.id, rv.public_id, company_id=report.company_id)
    session.commit()
    return ReportVersionRead(public_id=rv.public_id, version_number=rv.version_number, status=rv.status,
                             created_at=rv.created_at, generation_completed_at=rv.generation_completed_at, published_at=rv.published_at)


@router.get("/{report_public_id}/versions/{version_public_id}/download")
def download_report(report_public_id: str, version_public_id: str, request: Request,
                    session: Session = Depends(get_session), actor: User = Depends(require_permission("report:read"))):
    report, rv = _find_report_and_version(session, actor, report_public_id, version_public_id)
    artifact = session.exec(select(ReportArtifact).where(ReportArtifact.report_version_id == rv.id)).first()
    if not artifact:
        raise NotFoundError("No artifact is available for this report version yet")
    signed = get_storage().signed_url(artifact.storage_key, expires_in=300)
    log_action(session, actor, "report.downloaded", "report_artifact", artifact.id, artifact.public_id, company_id=report.company_id,
              ip_address=request.client.host if request.client else None)
    session.commit()
    return {"url": signed, "expires_in": 300, "filename": artifact.original_filename}
