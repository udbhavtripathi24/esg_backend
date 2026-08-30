"""Review service (Stage 5).

Encapsulates the two critical transactional operations:
1. assign_reviewer — creates review record + moves version to under_review +
   writes audit + fires 'new_assignment' notification outbox event
2. record_decision — records reviewer decision + advances version status +
   advances dataset status + writes audit + fires notification + enqueues
   KPI recalc job (approval only)

Both wrap ALL side-effects in a single transaction. Any failure rolls back
the entire operation — no half-approved state, no orphan notifications,
no missed audit entry.

Segregation-of-duties enforcement lives HERE (inside the transaction) rather
than at the endpoint layer, so a TOCTOU race can't slip past it.
"""
from datetime import datetime
from typing import Optional
from sqlmodel import Session, select
from app.core.errors import AppError
from app.models.user import User
from app.models.company import Company
from app.models.dataset import Dataset, DatasetVersion
from app.models.review import Review
from app.models.processing_job import ProcessingJob
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification


DECISION_STATUSES = {"approved", "changes_requested", "rejected"}


def assign_reviewer(
    session: Session,
    actor: User,
    dataset: Dataset,
    version: DatasetVersion,
    reviewer: User,
    tier: int = 1,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Review:
    """Assign a reviewer to a submitted dataset version.

    Transitions version status: submitted -> under_review.
    Idempotency: if an active review already exists for this (version, reviewer),
    we return it rather than duplicating.
    """
    if version.status not in ("submitted", "under_review"):
        raise AppError(
            "invalid_state",
            f"Reviewers can only be assigned to submitted versions "
            f"(current status: {version.status})",
            409,
        )

    # Idempotency: same reviewer + version + still pending => reuse
    existing = session.exec(select(Review).where(
        Review.dataset_version_id == version.id,
        Review.reviewer_user_id == reviewer.id,
        Review.status == "pending",
    )).first()
    if existing:
        return existing

    review = Review(
        dataset_version_id=version.id,
        reviewer_user_id=reviewer.id,
        tier=tier,
        status="pending",
        assigned_by=actor.id,
    )
    session.add(review)
    session.flush()

    # Transition version if it was still 'submitted'
    if version.status == "submitted":
        version.status = "under_review"
        version.updated_at = datetime.utcnow()
        session.add(version)
        dataset.status = "in_progress"
        dataset.updated_at = datetime.utcnow()
        session.add(dataset)

    log_action(
        session, actor, "review.assigned", "review", review.id, review.public_id,
        company_id=dataset.company_id,
        changes={
            "dataset_version_id": version.id,
            "reviewer_user_id": reviewer.id,
            "tier": tier,
        },
        ip_address=ip_address, user_agent=user_agent,
    )

    # New-assignment notification to the reviewer
    enqueue_notification(
        session,
        event_type="new_assignment",
        recipient_user_ids=[reviewer.id],
        entity_type="dataset_version",
        entity_id=version.id,
        entity_public_id=version.public_id,
        title="You've been assigned a review",
        body=f"Dataset version {version.public_id} is awaiting your review.",
    )

    return review


def record_decision(
    session: Session,
    actor: User,
    dataset: Dataset,
    version: DatasetVersion,
    review: Review,
    decision: str,
    note: str,
    ip_address: Optional[str] = None,
    user_agent: Optional[str] = None,
) -> Review:
    """Record a reviewer's decision on a dataset version.

    Wraps in ONE transaction: decision + version status + dataset status +
    audit + notification + KPI-recalc job (approval only). Any failure ->
    entire operation rolls back.

    Enforces:
    - decision must be one of {approved, changes_requested, rejected}
    - note is required and non-empty
    - version must currently be 'under_review' (optimistic concurrency check)
    - review must be pending (not already decided)
    - actor must be the assigned reviewer
    - segregation of duties (when enabled): actor != version.uploaded_by
    """
    if decision not in DECISION_STATUSES:
        raise AppError(
            "invalid_decision",
            f"decision must be one of {sorted(DECISION_STATUSES)}",
            422, "decision",
        )
    if not note or not note.strip():
        raise AppError("note_required", "A decision note is required", 422, "note")

    if review.status != "pending":
        raise AppError(
            "already_decided",
            f"This review has already been {review.status}",
            409,
        )

    if review.reviewer_user_id != actor.id:
        raise AppError(
            "not_assigned_reviewer",
            "You are not the assigned reviewer for this review",
            403,
        )

    # Optimistic concurrency: version state must still be under_review
    if version.status != "under_review":
        raise AppError(
            "invalid_state",
            f"Cannot decide on a version in state '{version.status}' "
            f"(expected under_review)",
            409,
        )

    # Segregation of duties — inside the transaction, checked freshly
    company = session.get(Company, dataset.company_id)
    if company and company.enforce_upload_approval_segregation:
        if version.uploaded_by == actor.id:
            raise AppError(
                "segregation_of_duties",
                "The user who uploaded this version cannot also approve it",
                403,
            )

    now = datetime.utcnow()

    # 1. Update the review
    review.status = decision
    review.decided_at = now
    review.decision_note = note.strip()
    review.updated_at = now
    session.add(review)

    # 2. Advance version status (decision -> matching version status)
    version.status = decision
    version.updated_at = now
    version.review_decision_summary = {
        "status": decision,
        "reviewer_id": actor.id,
        "reviewer_public_id": actor.email,  # human-readable enough for UI cache
        "decided_at": now.isoformat(),
        "note_excerpt": (note.strip()[:200] + "…") if len(note) > 200 else note.strip(),
    }
    session.add(version)

    # 3. Advance parent dataset status (mapped from decision)
    dataset.status = {
        "approved": "approved",
        "rejected": "rejected",
        "changes_requested": "in_progress",
    }[decision]
    dataset.updated_at = now
    session.add(dataset)

    # 4. Audit
    log_action(
        session, actor,
        f"review.{decision}", "review", review.id, review.public_id,
        company_id=dataset.company_id,
        changes={
            "decision": decision,
            "dataset_version_id": version.id,
            "note_excerpt": note.strip()[:200],
        },
        ip_address=ip_address, user_agent=user_agent,
    )

    # 5. Notify the uploader
    event_type = {
        "approved": "data_approved",
        "rejected": "data_rejected",
        "changes_requested": "changes_requested",
    }[decision]
    enqueue_notification(
        session,
        event_type=event_type,
        recipient_user_ids=[version.uploaded_by],
        entity_type="dataset_version",
        entity_id=version.id,
        entity_public_id=version.public_id,
        title={
            "approved": "Your submission was approved",
            "rejected": "Your submission was rejected",
            "changes_requested": "Changes requested on your submission",
        }[decision],
        body=note.strip()[:500],
    )

    # 6. Approval only: enqueue KPI recalc job
    if decision == "approved":
        job = ProcessingJob(
            job_type="recalculate_kpi_values",
            subject_type="dataset_version",
            subject_id=version.id,
            idempotency_key=f"kpi_recalc_v_{version.id}",
            payload={
                "dataset_id": dataset.id,
                "dataset_version_id": version.id,
                "company_id": dataset.company_id,
            },
        )
        session.add(job)

    return review
