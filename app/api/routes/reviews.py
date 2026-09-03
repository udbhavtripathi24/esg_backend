"""Stage 5 routes: reviews, comments, notifications.

The decision endpoint is the critical piece — it wraps every side-effect in
one transaction via review_service.record_decision. See that module for the
transactional contract.
"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Query, Request
from sqlmodel import Session, select, func
from pydantic import BaseModel, Field as PField
from app.db.session import get_session
from app.api.deps import require_permission
from app.core.errors import NotFoundError, AppError
from app.core.pagination import Page, paginate_params
from app.core.tenancy import can_access_company
from app.models.user import User
from app.models.dataset import Dataset, DatasetVersion
from app.models.kpi import KpiValue
from app.models.review import Review, ReviewComment
from app.models.notification import Notification
from app.services.review_service import assign_reviewer, record_decision
from app.services.audit import log_action
from app.services.notification_service import dispatch_pending


# ---------- Reviews ----------

reviews_router = APIRouter(tags=["reviews"])


class ReviewAssign(BaseModel):
    reviewer_user_id: int
    tier: int = 1


class ReviewDecide(BaseModel):
    decision: str  # 'approved' | 'changes_requested' | 'rejected'
    note: str = PField(..., min_length=1)


class ReviewRead(BaseModel):
    public_id: str
    dataset_version_id: int
    reviewer_user_id: Optional[int]
    tier: int
    status: str
    assigned_by: int
    assigned_at: datetime
    decided_at: Optional[datetime]
    decision_note: Optional[str]


def _find_dataset_and_version(session, actor, ds_pid, v_pid):
    """Locate dataset+version with tenant enforcement.

    Access is granted if:
      (a) the actor's normal tenancy allows this company, OR
      (b) the actor is an assigned reviewer on this specific version (case
          for Deloitte reviewers who aren't in the consultant-assignment
          table — reviewer assignment IS the access-grant for this view).
    """
    ds = session.exec(select(Dataset).where(
        Dataset.public_id == ds_pid, Dataset.deleted_at.is_(None)
    )).first()
    if not ds:
        raise NotFoundError("Dataset not found")
    v = session.exec(select(DatasetVersion).where(
        DatasetVersion.public_id == v_pid, DatasetVersion.dataset_id == ds.id
    )).first()
    if not v:
        raise NotFoundError("Version not found")

    if can_access_company(session, actor, ds.company_id):
        return ds, v

    # Fallback: is actor an assigned reviewer on this version?
    is_reviewer = session.exec(select(Review).where(
        Review.dataset_version_id == v.id,
        Review.reviewer_user_id == actor.id,
    )).first()
    if is_reviewer:
        return ds, v
    raise NotFoundError("Dataset not found")


# ---------- Data Preview / Validation (Review Center, backed by Layer 1) ----------

class ValidationFinding(BaseModel):
    severity: str  # 'error' | 'warning' -- see ValidationResult docstring
    code: str
    message: str
    kpi_value_public_id: Optional[str] = None


class ValidationResult(BaseModel):
    is_available: bool
    errors: list[ValidationFinding]
    warnings: list[ValidationFinding]


@reviews_router.get(
    "/datasets/{ds_pid}/versions/{v_pid}/kpi-validation",
    response_model=ValidationResult,
)
def get_kpi_validation(
    ds_pid: str, v_pid: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    """PROVISIONAL v1 structural/data-quality validation ONLY.

    Deliberately, structurally limited to checks computable from data
    already stored in Layer 1's KpiValue rows -- no emission factors, no
    unit conversion, no ESG scoring, no benchmarking, no framework
    compliance checking. Every check here is a pure data-integrity/
    sanity check any structured-data system would apply, independent of
    what the numbers actually mean.

    ERRORS (severity="error") are reserved for findings that mean the
    data is not physically/structurally valid regardless of methodology
    (e.g. a negative quantity for something that can't be negative).
    WARNINGS (severity="warning") flag data-completeness concerns that
    don't invalidate the submission but are worth a reviewer's attention
    (missing site match, missing unit, a metric reported twice for the
    same site).

    is_available=False means no KpiValue rows exist yet for this version
    at all -- either it hasn't been approved/extracted yet, or the
    upload_type has no KPI mapping (see kpi_extraction_service.py). This
    is NOT itself an error; the frontend should show an honest
    "not yet available" state, not a validation failure.
    """
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)

    values = session.exec(select(KpiValue).where(KpiValue.dataset_version_id == v.id)).all()
    if not values:
        return ValidationResult(is_available=False, errors=[], warnings=[])

    errors: list[ValidationFinding] = []
    warnings: list[ValidationFinding] = []

    for kv in values:
        # ERROR: a negative measurement. Every currently-seeded KPI code
        # (consumption, withdrawal, recycled, activity data, generated) is
        # a physical quantity that cannot be negative in the real world —
        # this is a structural sanity check, not a domain-specific rule.
        if kv.value < 0:
            errors.append(ValidationFinding(
                severity="error", code="negative_value",
                message=f"{kv.kpi_code} has a negative value ({kv.value}), which is not valid for this measurement.",
                kpi_value_public_id=kv.public_id,
            ))
        # WARNING: couldn't resolve a registered site for this row.
        if kv.site_id is None:
            warnings.append(ValidationFinding(
                severity="warning", code="unresolved_site",
                message=f"{kv.kpi_code} could not be matched to a registered site.",
                kpi_value_public_id=kv.public_id,
            ))
        # WARNING: no unit was found in the source file for this row.
        if kv.unit == "unspecified":
            warnings.append(ValidationFinding(
                severity="warning", code="missing_unit",
                message=f"{kv.kpi_code} has no specified unit.",
                kpi_value_public_id=kv.public_id,
            ))

    # WARNING: the same (site, metric) reported more than once in this
    # version — could indicate accidental double-counting in the source
    # file. Purely structural (a duplicate-key observation), not a
    # judgment about which value is "correct".
    seen: dict[tuple, bool] = {}
    for kv in values:
        key = (kv.site_id, kv.kpi_code)
        if key in seen:
            warnings.append(ValidationFinding(
                severity="warning", code="duplicate_metric",
                message=f"{kv.kpi_code} was reported more than once for the same site in this submission.",
                kpi_value_public_id=kv.public_id,
            ))
        seen[key] = True

    return ValidationResult(is_available=True, errors=errors, warnings=warnings)


@reviews_router.post(
    "/datasets/{ds_pid}/versions/{v_pid}/reviews",
    response_model=ReviewRead, status_code=201,
)
def assign_review(
    ds_pid: str, v_pid: str, body: ReviewAssign, request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:review")),
):
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)

    # Reviewer must exist, be active, and be in a company/org the actor can
    # see. We enforce reviewer belongs to same organization (cross-org
    # reviewer would be strange in this platform).
    reviewer = session.get(User, body.reviewer_user_id)
    if not reviewer or not reviewer.is_active:
        raise AppError("invalid_reviewer", "Reviewer not found or inactive",
                       422, "reviewer_user_id")
    if reviewer.organization_id != actor.organization_id:
        raise AppError("invalid_reviewer",
                       "Reviewer must be in the same organization", 422,
                       "reviewer_user_id")

    review = assign_reviewer(
        session, actor, ds, v, reviewer,
        tier=body.tier,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    session.refresh(review)
    return review


@reviews_router.get(
    "/datasets/{ds_pid}/versions/{v_pid}/reviews",
    response_model=list[ReviewRead],
)
def list_reviews(
    ds_pid: str, v_pid: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:read")),
):
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)
    return session.exec(
        select(Review).where(Review.dataset_version_id == v.id)
        .order_by(Review.assigned_at.asc())
    ).all()


@reviews_router.post(
    "/datasets/{ds_pid}/versions/{v_pid}/reviews/{rv_pid}/decide",
    response_model=ReviewRead,
)
def decide_review(
    ds_pid: str, v_pid: str, rv_pid: str, body: ReviewDecide, request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("dataset:review")),
):
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)
    review = session.exec(select(Review).where(
        Review.public_id == rv_pid, Review.dataset_version_id == v.id
    )).first()
    if not review:
        raise NotFoundError("Review not found")

    updated = record_decision(
        session, actor, ds, v, review,
        decision=body.decision, note=body.note,
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    session.refresh(updated)
    return updated


# ---------- Comments ----------

comments_router = APIRouter(tags=["comments"])


class CommentCreate(BaseModel):
    body: str = PField(..., min_length=1)
    kind: str = "general"  # general | field | decision
    field_reference: Optional[str] = None
    parent_comment_id: Optional[int] = None


class CommentRead(BaseModel):
    public_id: str
    dataset_version_id: int
    parent_comment_id: Optional[int]
    author_user_id: int
    kind: str
    field_reference: Optional[str]
    body: str
    created_at: datetime


_MAX_THREAD_DEPTH = 3


def _thread_depth(session: Session, parent_id: Optional[int]) -> int:
    """Walk up the parent chain to compute depth. Root = depth 0."""
    depth = 0
    cursor_id = parent_id
    while cursor_id is not None:
        depth += 1
        if depth > _MAX_THREAD_DEPTH:
            return depth  # early-exit; caller will reject
        parent = session.get(ReviewComment, cursor_id)
        if parent is None:
            break
        cursor_id = parent.parent_comment_id
    return depth


@comments_router.post(
    "/datasets/{ds_pid}/versions/{v_pid}/comments",
    response_model=CommentRead, status_code=201,
)
def create_comment(
    ds_pid: str, v_pid: str, body: CommentCreate, request: Request,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("comment:create")),
):
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)

    if body.kind not in ("general", "field", "decision"):
        raise AppError("invalid_kind", "kind must be general|field|decision",
                       422, "kind")

    # Validate parent (if set) belongs to SAME dataset_version — prevents
    # thread pollution across versions.
    if body.parent_comment_id is not None:
        parent = session.get(ReviewComment, body.parent_comment_id)
        if not parent or parent.dataset_version_id != v.id:
            raise AppError("invalid_parent", "Parent comment not in this version",
                           422, "parent_comment_id")
        depth = _thread_depth(session, body.parent_comment_id)
        if depth >= _MAX_THREAD_DEPTH:
            raise AppError(
                "thread_too_deep",
                f"Reply depth cannot exceed {_MAX_THREAD_DEPTH}", 422,
                "parent_comment_id",
            )

    comment = ReviewComment(
        dataset_version_id=v.id,
        parent_comment_id=body.parent_comment_id,
        author_user_id=actor.id,
        kind=body.kind,
        field_reference=body.field_reference,
        body=body.body,
    )
    session.add(comment)
    session.flush()
    log_action(
        session, actor, "comment.created", "review_comment", comment.id,
        comment.public_id, company_id=ds.company_id,
        changes={"kind": body.kind, "parent_comment_id": body.parent_comment_id},
        ip_address=request.client.host if request.client else None,
        user_agent=request.headers.get("user-agent"),
    )
    session.commit()
    session.refresh(comment)
    return comment


@comments_router.get(
    "/datasets/{ds_pid}/versions/{v_pid}/comments",
    response_model=list[CommentRead],
)
def list_comments(
    ds_pid: str, v_pid: str,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("comment:read")),
):
    ds, v = _find_dataset_and_version(session, actor, ds_pid, v_pid)
    return session.exec(
        select(ReviewComment).where(ReviewComment.dataset_version_id == v.id)
        .order_by(ReviewComment.created_at.asc())
    ).all()


# ---------- Notifications ----------

notifications_router = APIRouter(prefix="/notifications", tags=["notifications"])


class NotificationRead(BaseModel):
    id: int
    event_type: str
    entity_type: Optional[str]
    entity_id: Optional[int]
    entity_public_id: Optional[str]
    title: str
    body: Optional[str]
    is_read: bool
    read_at: Optional[datetime]
    created_at: datetime


@notifications_router.get("", response_model=Page[NotificationRead])
def list_notifications(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("notification:read")),
    page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100),
    unread_only: bool = Query(False),
):
    # Dispatch any pending outbox rows so the caller sees their fresh notifications
    dispatch_pending(session)

    page, page_size = paginate_params(page, page_size)
    stmt = select(Notification).where(Notification.recipient_user_id == actor.id)
    if unread_only:
        stmt = stmt.where(Notification.is_read == False)  # noqa: E712
    total = session.exec(select(func.count()).select_from(stmt.subquery())).one()
    stmt = stmt.order_by(Notification.created_at.desc()).offset((page - 1) * page_size).limit(page_size)
    return Page(items=session.exec(stmt).all(), total=total, page=page, page_size=page_size)


@notifications_router.post("/{notif_id}/mark-read", response_model=NotificationRead)
def mark_read(
    notif_id: int,
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("notification:read")),
):
    n = session.get(Notification, notif_id)
    if not n or n.recipient_user_id != actor.id:
        raise NotFoundError("Notification not found")
    if not n.is_read:
        n.is_read = True
        n.read_at = datetime.utcnow()
        session.add(n)
        session.commit()
        session.refresh(n)
    return n


@notifications_router.post("/mark-all-read")
def mark_all_read(
    session: Session = Depends(get_session),
    actor: User = Depends(require_permission("notification:read")),
):
    now = datetime.utcnow()
    unread = session.exec(select(Notification).where(
        Notification.recipient_user_id == actor.id,
        Notification.is_read == False,  # noqa: E712
    )).all()
    for n in unread:
        n.is_read = True
        n.read_at = now
        session.add(n)
    session.commit()
    return {"marked": len(unread)}
