"""BRSR questionnaire service.

Transactional operations for the BRSR questionnaire, following the same
conventions as app/services/review_service.py:
- every state change writes an audit entry in the same transaction;
- notifications go through the outbox in the same transaction;
- segregation of duties (the person who submitted a section cannot
  approve it) is enforced here, inside the transaction, when the
  company has it enabled;
- tenancy: cross-company access returns 404, never 403.

Workflow per section:
    draft ──submit──▶ submitted ──approve──▶ approved
      ▲                  │  └──request changes──▶ changes_requested ─┐
      └──── withdraw ────┘                                            │
      ▲                                                               │
      └──────────────── (client edits, resubmits) ◀───────────────────┘
    approved ──reopen (reason required)──▶ draft

Two-tier review (company.brsr_internal_review_required):
    submitted ──client reviewer approves──▶ internally_approved
              ──Deloitte approves──▶ approved
    Either tier may request changes. The submitter, the internal reviewer
    and Deloitte's approver must be three different people.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.brsr import engine, workflow
from app.brsr.question_bank import (
    BANK_VERSION, QUESTIONS, QUESTIONS_BY_ID, SECTION_CODES, SECTIONS, financial_year_labels,
)
from app.core.errors import AppError, NotFoundError
from app.core.tenancy import accessible_company_ids, can_access_company
from app.models.brsr import (
    EDITABLE_STATUSES, BrsrAnswer, BrsrAnswerHistory, BrsrComment, BrsrResponseSet, BrsrSection,
)
from app.models.company import Company
from app.models.consultant_assignment import ConsultantAssignment
from app.models.user import User
from app.rbac.service import user_has_permission
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification

FY_RE = re.compile(r"^(\d{4})-(\d{2})$")


# ---------------------------------------------------------------------------
# Lookups / tenancy
# ---------------------------------------------------------------------------

def validate_fy(fy: str) -> str:
    m = FY_RE.match(fy or "")
    if not m or (int(m.group(1)) + 1) % 100 != int(m.group(2)):
        raise AppError("invalid_financial_year", "Financial year must look like 2024-25", 422, "financial_year")
    if not (2015 <= int(m.group(1)) <= 2100):
        raise AppError("invalid_financial_year", "Financial year is out of range", 422, "financial_year")
    return fy


def previous_fy(fy: str) -> str:
    start = int(fy[:4]) - 1
    return f"{start}-{str(start + 1)[-2:]}"


def fy_period(fy: str) -> tuple[datetime, datetime]:
    """Indian financial year: 1 April – 31 March."""
    start = int(fy[:4])
    return datetime(start, 4, 1), datetime(start + 1, 3, 31)


def resolve_company_id(session: Session, actor: User, company_id: Optional[int]) -> int:
    if actor.portal_type == "client":
        if not actor.company_id:
            raise NotFoundError("Company not found")
        return actor.company_id
    if company_id is None:
        raise AppError("company_id_required", "company_id is required for this actor", 422, "company_id")
    if not can_access_company(session, actor, company_id):
        raise NotFoundError("Company not found")
    return company_id


def get_set(session: Session, actor: User, public_id: str) -> BrsrResponseSet:
    rs = session.exec(select(BrsrResponseSet).where(BrsrResponseSet.public_id == public_id)).first()
    if not rs:
        raise NotFoundError("BRSR response not found")
    if actor.portal_type == "client":
        if rs.company_id != actor.company_id:
            raise NotFoundError("BRSR response not found")
    elif not can_access_company(session, actor, rs.company_id):
        raise NotFoundError("BRSR response not found")
    return rs


def sections_of(session: Session, rs: BrsrResponseSet) -> dict[str, BrsrSection]:
    rows = session.exec(select(BrsrSection).where(BrsrSection.response_set_id == rs.id)).all()
    out = {s.section_code: s for s in rows}
    missing = [c for c in SECTION_CODES if c not in out]
    if missing:
        # Response sets created before a section existed (e.g. the annual-data
        # areas added in bank v2) get it on first access, as a draft.
        for code in missing:
            sec = BrsrSection(response_set_id=rs.id, section_code=code)
            session.add(sec)
            out[code] = sec
        session.commit()
        for code in missing:
            session.refresh(out[code])
    return out


def get_section(session: Session, rs: BrsrResponseSet, code: str) -> BrsrSection:
    if code not in SECTION_CODES:
        raise NotFoundError("Section not found")
    return sections_of(session, rs)[code]


def answer_rows(session: Session, rs: BrsrResponseSet) -> dict[str, BrsrAnswer]:
    rows = session.exec(select(BrsrAnswer).where(BrsrAnswer.response_set_id == rs.id)).all()
    return {a.question_id: a for a in rows}


def answers_dict(session: Session, rs: BrsrResponseSet) -> dict[str, dict]:
    out = {qid: a.value for qid, a in answer_rows(session, rs).items()}
    return inject_readonly(rs, out, session)


def inject_readonly(rs: BrsrResponseSet, answers: dict[str, dict],
                    session: Optional[Session] = None) -> dict[str, dict]:
    """Platform-owned values the client never types: the financial year, and
    — once an assurance engagement is signed and still matches the data —
    Section A Q14/Q15 (assurance provider and type)."""
    a = copy.deepcopy(answers.get("A.I") or {})
    fields = a.setdefault("fields", {})
    fields["financial_year"] = rs.financial_year
    if session is not None:
        from app.assurance.service import LEVEL_LABEL, is_stale, signed_engagement_for
        eng = signed_engagement_for(session, rs)
        if eng and not is_stale(session, eng):
            fields["assurance_provider"] = eng.provider_name
            fields["assurance_type"] = LEVEL_LABEL[eng.level]
    answers = dict(answers)
    answers["A.I"] = a
    if session is not None:
        from app.brsr.sheet_metrics import SHEET_KEY, cached_compute
        answers[SHEET_KEY] = cached_compute(session, rs.company_id, rs.financial_year)
    return answers


def section_data_hash(answers: dict[str, dict], code: str, comp: Optional["engine.Computer"] = None) -> Optional[str]:
    """Fingerprint of the figures in section ``code`` built from the data
    sheets (None when the section has none)."""
    qs = [q for q in QUESTIONS if q["section"] == code and (q.get("derive") or q.get("derive_rows"))]
    if not qs:
        return None
    comp = comp or engine.Computer(answers)
    payload = {q["id"]: engine.computed_view(q["id"], answers, comp) for q in qs}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def data_changed_sections(session: Session, rs: BrsrResponseSet, answers: Optional[dict] = None) -> list[str]:
    """Approved sections whose sheet-built figures changed since approval."""
    secs = sections_of(session, rs)
    flagged = [c for c in SECTION_CODES if secs[c].status == "approved" and secs[c].approved_data_hash]
    if not flagged:
        return []
    answers = answers if answers is not None else answers_dict(session, rs)
    comp = engine.Computer(answers)
    return [c for c in flagged if section_data_hash(answers, c, comp) != secs[c].approved_data_hash]


# ---------------------------------------------------------------------------
# Create (with carry-forward)
# ---------------------------------------------------------------------------

_CARRY = {"py": "cy", "ppy": "py"}  # target period <- source period


def _carry_forward_value(q: dict, prior: dict) -> Optional[dict]:
    """Build this year's starting answer from last year's approved answer:
    - table columns for the previous year(s) come from last year's
      current-year columns (``py_x`` <- ``cy_x``, ``ppy_x`` <- ``py_x``);
    - entity details (A.I) are copied as a starting point.
    Narrative answers are deliberately NOT copied: an unchanged paragraph
    from last year submitted as this year's disclosure is a real risk."""
    if q["id"] == "A.I":
        fields = {k: v for k, v in (prior.get("fields") or {}).items() if k != "financial_year"}
        return {"fields": fields} if fields else None
    if q["type"] == "metrics":
        # Last year's approved annual data becomes this year's previous-year figures.
        values = prior.get("values") or {}
        return {"values": {}, "py": dict(values)} if values else None
    if q["type"] != "table" or prior.get("na"):
        return None
    cols = {c["key"]: c for c in q["columns"]}
    cells: dict[str, dict] = {}
    for c in q["columns"]:
        tgt_period = c.get("period")
        if tgt_period not in _CARRY or c["kind"] == "computed":
            continue
        base = c["key"][len(tgt_period) + 1:]
        src_key = f"{_CARRY[tgt_period]}_{base}"
        if src_key not in cols:
            continue
        for rk, rv in (prior.get("cells") or {}).items():
            if src_key in rv and rv[src_key] is not None:
                cells.setdefault(rk, {})[c["key"]] = rv[src_key]
    return {"cells": cells} if cells else None


def create_set(session: Session, actor: User, company_id: int, fy: str,
               ip_address: Optional[str] = None) -> BrsrResponseSet:
    validate_fy(fy)
    company = session.get(Company, company_id)
    if not company or company.deleted_at is not None:
        raise NotFoundError("Company not found")
    existing = session.exec(select(BrsrResponseSet).where(
        BrsrResponseSet.company_id == company_id, BrsrResponseSet.financial_year == fy)).first()
    if existing:
        raise AppError("brsr_already_exists",
                       f"A BRSR response for FY {fy} already exists ({existing.public_id})", 409)

    prior = session.exec(select(BrsrResponseSet).where(
        BrsrResponseSet.company_id == company_id, BrsrResponseSet.financial_year == previous_fy(fy))).first()

    rs = BrsrResponseSet(company_id=company_id, financial_year=fy, bank_version=BANK_VERSION,
                         carried_forward_from_id=prior.id if prior else None, created_by=actor.id)
    session.add(rs)
    session.flush()
    for code in SECTION_CODES:
        session.add(BrsrSection(response_set_id=rs.id, section_code=code))

    carried = []
    if prior:
        prior_sections = sections_of(session, prior)
        prior_answers = answer_rows(session, prior)
        for qid, pa in prior_answers.items():
            q = QUESTIONS_BY_ID.get(qid)
            if not q:
                continue
            ps = prior_sections.get(q["section"])
            if not ps or ps.status != "approved":
                continue  # only last year's *approved* figures are authoritative
            value = _carry_forward_value(q, pa.value)
            if not value:
                continue
            value = engine.normalize_answer(qid, value)
            session.add(BrsrAnswer(response_set_id=rs.id, section_code=q["section"], question_id=qid,
                                   value=value, revision=1, updated_by=actor.id))
            session.add(BrsrAnswerHistory(response_set_id=rs.id, question_id=qid, revision=1, value=value,
                                          source="carry_forward", changed_by=actor.id))
            carried.append(qid)

    log_action(session, actor, "brsr.response_set.created", "brsr_response_set", rs.id, rs.public_id,
               company_id=company_id, changes={"financial_year": fy, "carried_forward_questions": len(carried),
                                               "carried_forward_from": prior.public_id if prior else None},
               ip_address=ip_address)
    session.commit()
    session.refresh(rs)
    return rs


# ---------------------------------------------------------------------------
# Save answers
# ---------------------------------------------------------------------------

def save_answers(session: Session, actor: User, rs: BrsrResponseSet, payload: dict[str, dict],
                 ip_address: Optional[str] = None) -> dict:
    """``payload``: {question_id: {"value": {...}, "revision": int|None}}.

    ``revision`` is the revision the client last saw (None for a question
    it has never seen saved). A mismatch means someone else saved in the
    meantime: nothing in the batch is written and 409 is returned with the
    current values, so the client can show them instead of overwriting.
    """
    if not payload:
        raise AppError("empty_payload", "No answers to save", 422)
    if len(payload) > 200:
        raise AppError("payload_too_large", "Save at most 200 answers at a time", 422)

    sections = sections_of(session, rs)
    existing = answer_rows(session, rs)
    roster_rows = workflow.roster(session, rs.company_id)
    normalized: dict[str, dict] = {}
    conflicts = []
    for qid, item in payload.items():
        q = QUESTIONS_BY_ID.get(qid)
        if not q:
            raise AppError("unknown_question", f"Unknown question {qid}", 422, qid)
        if not isinstance(item, dict) or "value" not in item:
            raise AppError("invalid_payload", f"{qid}: expected {{value, revision}}", 422, qid)
        sec = sections[q["section"]]
        if sec.status not in EDITABLE_STATUSES:
            raise AppError("section_locked",
                           f"Section {q['section']} is {sec.status.replace('_', ' ')} and cannot be edited", 409,
                           qid)
        if not workflow.can_answer(session, actor, q, roster_rows):
            raise AppError("forbidden", f"{qid} is answered by the {', '.join(q['departments'])} SPOC", 403, qid)
        cur = existing.get(qid)
        seen = item.get("revision")
        if (cur is None and seen not in (None, 0)) or (cur is not None and seen != cur.revision):
            conflicts.append({"question_id": qid, "current_revision": cur.revision if cur else None,
                              "current_value": cur.value if cur else None})
            continue
        try:
            normalized[qid] = engine.normalize_answer(qid, item["value"])
        except engine.PayloadError as exc:
            raise AppError("invalid_answer", exc.message, 422, qid) from exc
    if conflicts:
        raise ConflictError(conflicts)

    now = datetime.utcnow()
    changed = []
    for qid, value in normalized.items():
        cur = existing.get(qid)
        if cur is not None and cur.value == value:
            continue
        if cur is None:
            cur = BrsrAnswer(response_set_id=rs.id, section_code=QUESTIONS_BY_ID[qid]["section"], question_id=qid,
                             value=value, revision=1, updated_by=actor.id, updated_at=now)
        else:
            cur.value = value
            cur.revision += 1
            cur.updated_by = actor.id
            cur.updated_at = now
        session.add(cur)
        session.add(BrsrAnswerHistory(response_set_id=rs.id, question_id=qid, revision=cur.revision, value=value,
                                      source="edit", changed_by=actor.id, changed_at=now))
        existing[qid] = cur
        changed.append(qid)

    if changed:
        workflow.on_answers_saved(session, rs, changed)
        rs.updated_at = now
        session.add(rs)
        log_action(session, actor, "brsr.answers.saved", "brsr_response_set", rs.id, rs.public_id,
                   company_id=rs.company_id, changes={"questions": changed}, ip_address=ip_address)
    session.commit()

    answers = inject_readonly(rs, {k: v.value for k, v in existing.items()}, session)
    comp = engine.Computer(answers)
    return {
        "saved": changed,
        "answers": {qid: {"revision": existing[qid].revision if qid in existing else None,
                          "issues": engine.validate_answer(qid, answers.get(qid), answers, comp),
                          "computed": engine.computed_view(qid, answers, comp)}
                    for qid in payload},
        "sections": {code: engine.section_report(code, answers)
                     for code in {QUESTIONS_BY_ID[q]["section"] for q in payload}},
        "workflow": workflow.question_states(session, rs, [q for q in payload if workflow.needs_review(QUESTIONS_BY_ID[q])],
                                             actor),
    }


class ConflictError(AppError):
    def __init__(self, conflicts: list[dict]):
        super().__init__("revision_conflict",
                         "Someone else saved these answers while you were editing. Reload to see their changes.",
                         409)
        self.conflicts = conflicts


# ---------------------------------------------------------------------------
# Section workflow
# ---------------------------------------------------------------------------

def _reviewer_ids(session: Session, company_id: int, exclude: int, *, internal: bool = True,
                  deloitte: bool = True) -> list[int]:
    ids: set[int] = set()
    if internal:
        clients = session.exec(select(User).where(User.company_id == company_id, User.is_active == True)).all()  # noqa: E712
        for u in clients:
            if user_has_permission(session, u.id, "dataset:review", company_id):
                ids.add(u.id)
    if deloitte:
        for a in session.exec(select(ConsultantAssignment).where(
                ConsultantAssignment.company_id == company_id, ConsultantAssignment.is_active == True)).all():  # noqa: E712
            ids.add(a.consultant_user_id)
    ids.discard(exclude)
    return sorted(ids)


def internal_review_required(session: Session, company_id: int) -> bool:
    company = session.get(Company, company_id)
    return bool(company and company.brsr_internal_review_required)


def _clear_review(sec: BrsrSection) -> None:
    sec.decided_by = None
    sec.decided_at = None
    sec.decision_note = None
    sec.internal_reviewed_by = None
    sec.internal_reviewed_at = None
    sec.internal_review_note = None


def submit_section(session: Session, actor: User, rs: BrsrResponseSet, code: str,
                   ip_address: Optional[str] = None) -> BrsrSection:
    sec = get_section(session, rs, code)
    if sec.status not in EDITABLE_STATUSES:
        raise AppError("invalid_state", f"Section {code} is already {sec.status.replace('_', ' ')}", 409)
    answers = answers_dict(session, rs)
    report = engine.section_report(code, answers)
    if not report["ready_to_submit"]:
        raise AppError("section_incomplete",
                       f"Section {code} has {len(report['missing'])} unanswered required question(s) and "
                       f"{len(report['errors'])} error(s). Fix them before submitting.", 422)
    pending = section_pending_reviews(session, rs, code, answers)
    if pending:
        raise AppError("answers_not_approved",
                       f"{len(pending)} answer(s) in section {code} are not approved yet by their department "
                       f"reviewer: {', '.join(pending[:10])}{'…' if len(pending) > 10 else ''}", 422)
    now = datetime.utcnow()
    sec.status = "submitted"
    sec.submitted_by = actor.id
    sec.submitted_at = now
    sec.submission_count += 1
    _clear_review(sec)
    sec.updated_at = now
    session.add(sec)
    log_action(session, actor, "brsr.section.submitted", "brsr_section", sec.id, rs.public_id,
               company_id=rs.company_id,
               changes={"section": code, "financial_year": rs.financial_year,
                        "submission": sec.submission_count, "warnings": len(report["warnings"])},
               ip_address=ip_address)
    # Two-tier: the client's internal reviewers see it first; Deloitte after.
    two_tier = internal_review_required(session, rs.company_id)
    recipients = _reviewer_ids(session, rs.company_id, exclude=actor.id, deloitte=not two_tier)
    if recipients:
        company = session.get(Company, rs.company_id)
        enqueue_notification(
            session, event_type="brsr_section_submitted", recipient_user_ids=recipients,
            title=f"BRSR {code} submitted for {'internal review' if two_tier else 'review'}",
            body=f"{company.name if company else 'A company'} submitted BRSR section {code} for "
                 f"FY {rs.financial_year}.",
            entity_type="brsr_section", entity_id=sec.id, entity_public_id=rs.public_id)
    session.commit()
    session.refresh(sec)
    return sec


def section_pending_reviews(session: Session, rs: BrsrResponseSet, code: str, answers: dict) -> list[str]:
    rows = answer_rows(session, rs)
    return workflow.pending_reviews([q["id"] for q in QUESTIONS if q["section"] == code], answers,
                                    workflow.status_map(session, rs), engine.required_ids(answers),
                                    {k: v.revision for k, v in rows.items()})


def withdraw_section(session: Session, actor: User, rs: BrsrResponseSet, code: str,
                     ip_address: Optional[str] = None) -> BrsrSection:
    sec = get_section(session, rs, code)
    if sec.status not in ("submitted", "internally_approved"):
        raise AppError("invalid_state", f"Only a section under review can be withdrawn (status: {sec.status})", 409)
    sec.status = "draft"
    _clear_review(sec)
    sec.updated_at = datetime.utcnow()
    session.add(sec)
    log_action(session, actor, "brsr.section.withdrawn", "brsr_section", sec.id, rs.public_id,
               company_id=rs.company_id, changes={"section": code}, ip_address=ip_address)
    session.commit()
    session.refresh(sec)
    return sec


def decide_section(session: Session, actor: User, rs: BrsrResponseSet, code: str, decision: str, note: str,
                   question_comments: list[dict], ip_address: Optional[str] = None) -> BrsrSection:
    if decision not in ("approved", "changes_requested"):
        raise AppError("invalid_decision", "decision must be 'approved' or 'changes_requested'", 422, "decision")
    if not note or not note.strip():
        raise AppError("note_required", "A decision note is required", 422, "note")
    if decision == "approved" and question_comments:
        raise AppError("invalid_payload", "Question comments are only sent with a request for changes", 422)
    for c in question_comments:
        qid = c.get("question_id")
        if qid not in QUESTIONS_BY_ID or QUESTIONS_BY_ID[qid]["section"] != code:
            raise AppError("invalid_payload", f"{qid} is not a question in section {code}", 422)
        if not (c.get("body") or "").strip():
            raise AppError("invalid_payload", f"Comment for {qid} is empty", 422)

    sec = get_section(session, rs, code)
    company = session.get(Company, rs.company_id)
    two_tier = bool(company and company.brsr_internal_review_required)
    is_client = actor.portal_type == "client"
    if sec.status == "submitted" and two_tier:
        tier = "internal"
        if not is_client:
            raise AppError("internal_review_pending",
                           f"Section {code} is waiting for the company's internal review before Deloitte's", 409)
    elif sec.status == "internally_approved":
        tier = "final"
        if is_client:
            raise AppError("awaiting_deloitte", f"Section {code} is already internally approved and is with Deloitte",
                           409)
    elif sec.status == "submitted":
        tier = "single"
    else:
        raise AppError("invalid_state", f"Section {code} is not awaiting review (status: {sec.status})", 409)
    if company and company.enforce_upload_approval_segregation and sec.submitted_by == actor.id:
        raise AppError("segregation_of_duties", "The person who submitted this section cannot also review it", 403)
    if tier == "final" and sec.internal_reviewed_by == actor.id:
        raise AppError("segregation_of_duties", "The internal reviewer cannot also give Deloitte's approval", 403)

    now = datetime.utcnow()
    if tier == "internal":
        sec.status = "internally_approved" if decision == "approved" else "changes_requested"
        sec.internal_reviewed_by = actor.id
        sec.internal_reviewed_at = now
        sec.internal_review_note = note.strip()
    else:
        sec.status = decision
        sec.decided_by = actor.id
        sec.decided_at = now
        sec.decision_note = note.strip()
    if sec.status == "approved":
        sec.approved_data_hash = section_data_hash(answers_dict(session, rs), code)
    if decision == "changes_requested" and question_comments:
        workflow.return_for_deloitte_comments(session, rs, [c["question_id"] for c in question_comments], actor)
    sec.updated_at = now
    session.add(sec)
    session.add(BrsrComment(response_set_id=rs.id, section_code=code, question_id=None, body=note.strip(),
                            kind="decision", author_id=actor.id, created_at=now))
    for c in question_comments:
        session.add(BrsrComment(response_set_id=rs.id, section_code=code, question_id=c["question_id"],
                                body=c["body"].strip(), kind="review", author_id=actor.id, created_at=now))
    action = sec.status if tier != "internal" or decision != "approved" else "internally_approved"
    log_action(session, actor, f"brsr.section.{action}", "brsr_section", sec.id, rs.public_id,
               company_id=rs.company_id,
               changes={"section": code, "tier": tier, "note_excerpt": note.strip()[:200],
                        "question_comments": len(question_comments)}, ip_address=ip_address)
    if sec.submitted_by:
        title = {"approved": f"BRSR {code} approved",
                 "internally_approved": f"BRSR {code} approved internally — now with Deloitte",
                 "changes_requested": f"Changes requested on BRSR {code}"}[sec.status]
        enqueue_notification(
            session, event_type=f"brsr_section_{sec.status}", recipient_user_ids=[sec.submitted_by],
            title=title, body=note.strip()[:500], entity_type="brsr_section", entity_id=sec.id,
            entity_public_id=rs.public_id)
    if sec.status == "internally_approved":
        deloitte = _reviewer_ids(session, rs.company_id, exclude=actor.id, internal=False)
        if deloitte:
            enqueue_notification(
                session, event_type="brsr_section_submitted", recipient_user_ids=deloitte,
                title=f"BRSR {code} ready for Deloitte review",
                body=f"{company.name if company else 'A company'} approved BRSR section {code} internally for "
                     f"FY {rs.financial_year}.",
                entity_type="brsr_section", entity_id=sec.id, entity_public_id=rs.public_id)
    session.commit()
    session.refresh(sec)
    return sec


def reopen_section(session: Session, actor: User, rs: BrsrResponseSet, code: str, reason: str,
                   ip_address: Optional[str] = None) -> BrsrSection:
    if not reason or not reason.strip():
        raise AppError("reason_required", "Say why an approved section is being reopened", 422, "reason")
    sec = get_section(session, rs, code)
    if sec.status != "approved":
        raise AppError("invalid_state", f"Only an approved section can be reopened (status: {sec.status})", 409)
    now = datetime.utcnow()
    sec.status = "draft"
    sec.approved_data_hash = None
    sec.updated_at = now
    session.add(sec)
    session.add(BrsrComment(response_set_id=rs.id, section_code=code, question_id=None,
                            body=f"Reopened: {reason.strip()}", kind="decision", author_id=actor.id,
                            created_at=now))
    log_action(session, actor, "brsr.section.reopened", "brsr_section", sec.id, rs.public_id,
               company_id=rs.company_id, changes={"section": code, "reason": reason.strip()[:500]},
               ip_address=ip_address)
    session.commit()
    session.refresh(sec)
    return sec


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------

SECTIONS_BY_CODE = {s["code"]: s for s in SECTIONS}


def overview(session: Session, rs: BrsrResponseSet) -> dict:
    answers = answers_dict(session, rs)
    secs = sections_of(session, rs)
    company = session.get(Company, rs.company_id)
    changed = set(data_changed_sections(session, rs, answers))
    statuses = workflow.status_map(session, rs)
    revisions = {k: v.revision for k, v in answer_rows(session, rs).items()}
    out_sections = []
    for code in SECTION_CODES:
        s = secs[code]
        r = engine.section_report(code, answers)
        required = engine.required_ids(answers)
        # Answers that need a reviewer's approval: answered ones, and required ones.
        reviewed = [q for q in QUESTIONS if q["section"] == code and workflow.needs_review(q)
                    and (q["id"] in required or (q["id"] in revisions and engine.is_answered(q, answers.get(q["id"]), answers)))]
        approved_q = sum(1 for q in reviewed if (st := statuses.get(q["id"])) and st.status == "approved"
                         and st.answer_revision == revisions.get(q["id"]))
        meta = SECTIONS_BY_CODE[code]
        out_sections.append({
            "code": code, "kind": meta.get("kind", "disclosure"), "title": meta["title"], "status": s.status, "submitted_at": s.submitted_at, "decided_at": s.decided_at,
            "decision_note": s.decision_note, "submission_count": s.submission_count,
            "questions": r["questions"], "answered": r["answered"], "required": r["required"],
            "required_answered": r["required_answered"], "errors": len(r["errors"]),
            "warnings": len(r["warnings"]), "ready_to_submit": r["ready_to_submit"],
            "data_changed": code in changed,
            "questionnaire": {"total": len(reviewed), "approved": approved_q,
                              "submitted": sum(1 for q in reviewed if (st := statuses.get(q["id"]))
                                               and st.status == "submitted"),
                              "returned": sum(1 for q in reviewed if (st := statuses.get(q["id"]))
                                              and st.status == "returned")},
            "data_questions": sum(1 for q in QUESTIONS if q["section"] == code and q.get("source") == "data"),
        })
    from app.brsr.sheet_metrics import SHEET_KEY
    sheet = answers.get(SHEET_KEY) or {}
    return {
        "public_id": rs.public_id, "company_id": rs.company_id, "company_name": company.name if company else None,
        "financial_year": rs.financial_year, "fy_labels": financial_year_labels(rs.financial_year),
        "bank_version": rs.bank_version, "upgrade_pending": rs.bank_version != BANK_VERSION,
        "internal_review_required": bool(company and company.brsr_internal_review_required),
        "carried_forward": rs.carried_forward_from_id is not None,
        "created_at": rs.created_at, "updated_at": rs.updated_at, "sections": out_sections,
        "all_approved": all(s["status"] == "approved" for s in out_sections),
        "data_changed_sections": sorted(changed),
        "data_sheets": {"warnings": sheet.get("warnings") or [], "basis": sheet.get("basis") or [],
                        "coverage": {k: {"months": v["months"], "rows": v["rows"]}
                                     for k, v in (sheet.get("cy_coverage") or {}).items()}},
    }


def section_detail(session: Session, rs: BrsrResponseSet, code: str, actor: Optional[User] = None) -> dict:
    sec = get_section(session, rs, code)
    rows = answer_rows(session, rs)
    answers = inject_readonly(rs, {k: v.value for k, v in rows.items()}, session)
    comp = engine.Computer(answers)
    report = engine.section_report(code, answers)
    issues_by_q: dict[str, list] = {}
    for i in report["errors"] + report["warnings"]:
        issues_by_q.setdefault(i["question_id"], []).append(i)
    comments = session.exec(select(BrsrComment).where(
        BrsrComment.response_set_id == rs.id, BrsrComment.section_code == code
    ).order_by(BrsrComment.created_at)).all()
    authors = {u.id: u for u in session.exec(select(User).where(
        User.id.in_({c.author_id for c in comments}
                    | {x for x in (sec.submitted_by, sec.decided_by, sec.internal_reviewed_by) if x})
    )).all()} if comments or sec.submitted_by or sec.decided_by or sec.internal_reviewed_by else {}
    required = engine.required_ids(answers)
    q_out = {}
    for q in QUESTIONS:
        if q["section"] != code:
            continue
        a = rows.get(q["id"])
        q_out[q["id"]] = {
            "value": answers.get(q["id"]) if (a or q["id"] == "A.I") else None,
            "revision": a.revision if a else None,
            "updated_at": a.updated_at if a else None,
            "computed": engine.computed_view(q["id"], answers, comp),
            "issues": issues_by_q.get(q["id"], []),
            "answered": engine.is_answered(q, answers.get(q["id"]), answers),
            "missing_fields": report["missing_fields"].get(q["id"], []) if q["type"] == "metrics" else [],
            "hidden_groups": engine.hidden_groups(q, answers) if q["type"] == "metrics" else [],
            "required": q["id"] in required,
        }

    def uname(uid: Optional[int]) -> Optional[str]:
        return authors[uid].name if uid in authors else None

    states = workflow.question_states(session, rs, [qid for qid in q_out if workflow.needs_review(QUESTIONS_BY_ID[qid])],
                                      actor) if actor is not None else {}
    for qid, st in states.items():
        q_out[qid]["workflow"] = st
    from app.brsr.sheet_metrics import SHEET_KEY
    sheet = answers.get(SHEET_KEY) or {}
    sec_sheets = sorted({s for q in QUESTIONS if q["section"] == code for s in q.get("derived_from") or []})
    return {
        "data_changed": sec.status == "approved" and bool(sec.approved_data_hash)
                        and section_data_hash(answers, code, comp) != sec.approved_data_hash,
        "pending_reviews": section_pending_reviews(session, rs, code, answers),
        "data_sheets": {"sheets": sec_sheets,
                        "coverage": {k: (sheet.get("cy_coverage") or {}).get(k) for k in sec_sheets},
                        "warnings": [w for w in sheet.get("warnings") or [] if w.get("sheet") in sec_sheets]},
        "section": code, "status": sec.status, "submitted_by": uname(sec.submitted_by),
        "submitted_at": sec.submitted_at, "decided_by": uname(sec.decided_by), "decided_at": sec.decided_at,
        "decision_note": sec.decision_note, "submission_count": sec.submission_count,
        "internal_review_required": internal_review_required(session, rs.company_id),
        "internal_reviewed_by": uname(sec.internal_reviewed_by), "internal_reviewed_at": sec.internal_reviewed_at,
        "internal_review_note": sec.internal_review_note,
        "editable": sec.status in EDITABLE_STATUSES, "report": report, "answers": q_out,
        "evidence": _section_evidence(session, rs, code),
        "comments": [{"public_id": c.public_id, "question_id": c.question_id, "body": c.body, "kind": c.kind,
                      "author": uname(c.author_id), "created_at": c.created_at} for c in comments],
    }


def _section_evidence(session: Session, rs: BrsrResponseSet, code: str) -> list[dict]:
    from app.brsr import evidence as ev_service
    files = ev_service.list_evidence(session, rs, code)
    names = ev_service.user_names(session, [f.uploaded_by for f in files])
    return [ev_service.serialize(f, names) for f in files]


def answer_history(session: Session, rs: BrsrResponseSet, qid: str) -> list[dict]:
    if qid not in QUESTIONS_BY_ID:
        raise NotFoundError("Question not found")
    rows = session.exec(select(BrsrAnswerHistory).where(
        BrsrAnswerHistory.response_set_id == rs.id, BrsrAnswerHistory.question_id == qid
    ).order_by(BrsrAnswerHistory.revision.desc())).all()
    users = {u.id: u.name for u in session.exec(select(User).where(
        User.id.in_({r.changed_by for r in rows}))).all()} if rows else {}
    return [{"revision": r.revision, "value": r.value, "source": r.source, "changed_by": users.get(r.changed_by),
             "changed_at": r.changed_at} for r in rows]


def list_sets(session: Session, actor: User, company_id: Optional[int]) -> list[dict]:
    stmt = select(BrsrResponseSet)
    if actor.portal_type == "client":
        stmt = stmt.where(BrsrResponseSet.company_id == actor.company_id)
    else:
        ids = accessible_company_ids(session, actor)
        if ids is None:
            stmt = stmt.where(BrsrResponseSet.company_id.in_(
                select(Company.id).where(Company.organization_id == actor.organization_id)))
        elif ids:
            stmt = stmt.where(BrsrResponseSet.company_id.in_(ids))
        else:
            return []
        if company_id is not None:
            stmt = stmt.where(BrsrResponseSet.company_id == company_id)
    sets = session.exec(stmt.order_by(BrsrResponseSet.financial_year.desc())).all()
    out = []
    for rs in sets:
        secs = sections_of(session, rs)
        company = session.get(Company, rs.company_id)
        out.append({
            "public_id": rs.public_id, "company_id": rs.company_id, "company_name": company.name if company else None,
            "financial_year": rs.financial_year, "updated_at": rs.updated_at,
            "status_counts": {st: sum(1 for s in secs.values() if s.status == st)
                              for st in ("draft", "submitted", "approved", "changes_requested")},
        })
    return out


def review_queue(session: Session, actor: User) -> list[dict]:
    """Submitted sections the actor may review (not ones they submitted)."""
    stmt = select(BrsrSection, BrsrResponseSet).where(
        BrsrSection.response_set_id == BrsrResponseSet.id,
        BrsrSection.status.in_(("submitted", "internally_approved")))
    if actor.portal_type == "client":
        stmt = stmt.where(BrsrResponseSet.company_id == actor.company_id)
    else:
        ids = accessible_company_ids(session, actor)
        if ids is None:
            stmt = stmt.where(BrsrResponseSet.company_id.in_(
                select(Company.id).where(Company.organization_id == actor.organization_id)))
        elif ids:
            stmt = stmt.where(BrsrResponseSet.company_id.in_(ids))
        else:
            return []
    out = []
    for sec, rs in session.exec(stmt.order_by(BrsrSection.submitted_at)).all():
        company = session.get(Company, rs.company_id)
        two_tier = bool(company and company.brsr_internal_review_required)
        stage = "internal" if (two_tier and sec.status == "submitted") else "final"
        # Each tier sees only what is waiting for it
        if actor.portal_type == "client" and (stage != "internal" and two_tier):
            continue
        if actor.portal_type != "client" and stage == "internal":
            continue
        submitter = session.get(User, sec.submitted_by) if sec.submitted_by else None
        internal = session.get(User, sec.internal_reviewed_by) if sec.internal_reviewed_by else None
        out.append({
            "stage": stage, "status": sec.status,
            "internal_reviewed_by": internal.name if internal else None,
            "response_set_id": rs.public_id, "company_name": company.name if company else None,
            "financial_year": rs.financial_year, "section": sec.section_code, "submitted_at": sec.submitted_at,
            "submitted_by": submitter.name if submitter else None, "submission_count": sec.submission_count,
            "own_submission": sec.submitted_by == actor.id,
        })
    return out


# ---------------------------------------------------------------------------
# Frozen snapshot for report generation
# ---------------------------------------------------------------------------

def frozen_snapshot(session: Session, rs: BrsrResponseSet) -> dict:
    """Everything the report needs, frozen at generation time. Refuses
    unless every section is approved — a BRSR filing is all-or-nothing."""
    secs = sections_of(session, rs)
    not_ready = [c for c in SECTION_CODES if secs[c].status != "approved"]
    if not_ready:
        raise AppError("brsr_not_ready",
                       f"Every BRSR section must be approved before the report can be generated. Not yet "
                       f"approved: {', '.join(not_ready)}", 422)
    rows = answer_rows(session, rs)
    answers = inject_readonly(rs, {k: v.value for k, v in rows.items()}, session)
    changed = data_changed_sections(session, rs, answers)
    if changed:
        raise AppError("brsr_data_changed",
                       f"Approved data-sheet figures changed after these sections were approved: "
                       f"{', '.join(changed)}. Reopen and re-approve them before generating the report.", 422)
    company = session.get(Company, rs.company_id)
    payload = {
        "response_set_id": rs.public_id, "financial_year": rs.financial_year, "bank_version": rs.bank_version,
        "company_name": company.name if company else None, "answers": answers,
        "answer_revisions": {k: v.revision for k, v in rows.items()},
        "approvals": {c: {"decided_by": secs[c].decided_by,
                          "decided_at": secs[c].decided_at.isoformat() if secs[c].decided_at else None,
                          "internal_reviewed_by": secs[c].internal_reviewed_by,
                          "internal_reviewed_at": (secs[c].internal_reviewed_at.isoformat()
                                                   if secs[c].internal_reviewed_at else None)}
                      for c in SECTION_CODES},
    }
    from app.assurance.service import assurance_report_block
    payload["assurance"] = assurance_report_block(session, rs)
    payload["content_hash"] = hashlib.sha256(
        json.dumps({"answers": answers, "revisions": payload["answer_revisions"]}, sort_keys=True,
                   default=str).encode()).hexdigest()
    return payload

