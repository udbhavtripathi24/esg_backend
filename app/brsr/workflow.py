"""Per-question BRSR workflow: SPOCs answer, department reviewers approve.

The engagement's model: each BRSR question belongs to one or more
departments (question_bank.QUESTION_DEPARTMENTS, from the engagement's
question list). For each department the company names

* SPOCs, who answer that department's questions, and
* reviewers, who approve each answer,

as platform users (brsr_department_roles). People are always real accounts
picked in the app; names are never imported from engagement files.

Per question:   open ──submit──▶ submitted ──approve──▶ approved
                                    └──return (note)──▶ returned ─edit─▶ open
Any change to an answer puts it back to "open" — an approval covers the
exact revision that was approved, nothing later.

A section can go to Deloitte only when every answered questionnaire
question in it (and every required one) is approved. Figures built from the
data sheets are approved with their upload, so they are not reviewed again
question by question.

Fallbacks keep small clients workable: a department without SPOCs can be
answered by anyone allowed to edit the BRSR; a department without reviewers
can be approved by any client user with review rights; a company with no
client reviewer at all can have questions approved by its assigned Deloitte
consultant. The submitter never approves their own answer.
"""
from __future__ import annotations

from datetime import datetime
from typing import Iterable, Optional

from sqlmodel import Session, select

from app.brsr import engine
from app.brsr.question_bank import DEPARTMENTS, QUESTIONS_BY_ID
from app.core.errors import AppError
from app.models.brsr import (
    DEPARTMENT_ROLES, EDITABLE_STATUSES, BrsrAnswer, BrsrComment, BrsrDepartmentRole, BrsrQuestionStatus,
    BrsrResponseSet, BrsrSection,
)
from app.models.consultant_assignment import ConsultantAssignment
from app.models.user import User
from app.rbac.service import user_has_permission
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification

REVIEWED_SOURCES = ("qualitative", "both")
MAX_BATCH = 200


def needs_review(q: dict) -> bool:
    return q.get("source", "qualitative") in REVIEWED_SOURCES


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------

def roster(session: Session, company_id: int) -> list[BrsrDepartmentRole]:
    return list(session.exec(select(BrsrDepartmentRole).where(BrsrDepartmentRole.company_id == company_id)
                             .order_by(BrsrDepartmentRole.department, BrsrDepartmentRole.role)).all())


def roster_view(session: Session, company_id: int) -> dict:
    rows = roster(session, company_id)
    users = {u.id: u for u in session.exec(select(User).where(User.id.in_({r.user_id for r in rows}))).all()} \
        if rows else {}
    out = []
    for dept in DEPARTMENTS:
        mine = [r for r in rows if r.department == dept]
        out.append({"department": dept,
                    **{f"{role}s": [{"user_id": r.user_id, "name": users[r.user_id].name if r.user_id in users else None,
                                     "email": users[r.user_id].email if r.user_id in users else None,
                                     "active": bool(users.get(r.user_id) and users[r.user_id].is_active)}
                                    for r in mine if r.role == role] for role in DEPARTMENT_ROLES},
                    "questions": sum(1 for q in QUESTIONS_BY_ID.values() if dept in q.get("departments", [])
                                     and needs_review(q))})
    return {"company_id": company_id, "departments": out}


def set_roster(session: Session, actor: User, company_id: int, entries: list[dict],
               ip_address: Optional[str] = None) -> dict:
    """Replace the company's roster with ``entries`` [{department, user_id, role}]."""
    if len(entries) > 1000:
        raise AppError("payload_too_large", "Too many roster entries", 422)
    clean: set[tuple[str, int, str]] = set()
    user_ids = {e.get("user_id") for e in entries}
    users = {u.id: u for u in session.exec(select(User).where(User.id.in_([i for i in user_ids if isinstance(i, int)]))).all()}
    for e in entries:
        dept, uid, role = e.get("department"), e.get("user_id"), e.get("role")
        if dept not in DEPARTMENTS:
            raise AppError("invalid_department", f"Unknown department {dept!r}", 422, "department")
        if role not in DEPARTMENT_ROLES:
            raise AppError("invalid_role", "role must be 'spoc' or 'reviewer'", 422, "role")
        u = users.get(uid)
        if not u or u.deleted_at is not None or u.company_id != company_id or u.portal_type != "client":
            raise AppError("invalid_user", "Each person must be an active user of this company", 422, "user_id")
        clean.add((dept, uid, role))
    for dept in DEPARTMENTS:
        both = {uid for d, uid, r in clean if d == dept and r == "spoc"} & \
               {uid for d, uid, r in clean if d == dept and r == "reviewer"}
        if both:
            raise AppError("segregation_of_duties",
                           f"{dept}: the same person cannot be both SPOC and reviewer", 422, "user_id")

    current = {(r.department, r.user_id, r.role): r for r in roster(session, company_id)}
    added = sorted(clean - set(current))
    removed = sorted(set(current) - clean)
    for k in removed:
        session.delete(current[k])
    for dept, uid, role in added:
        session.add(BrsrDepartmentRole(company_id=company_id, department=dept, user_id=uid, role=role,
                                       created_by=actor.id))
    if added or removed:
        log_action(session, actor, "brsr.roster.updated", "company", company_id, None, company_id=company_id,
                   changes={"added": [list(a) for a in added], "removed": [list(r) for r in removed]},
                   ip_address=ip_address)
    session.commit()
    return roster_view(session, company_id)


def _ids(rows: Iterable[BrsrDepartmentRole], depts: set[str], role: str) -> set[int]:
    return {r.user_id for r in rows if r.department in depts and r.role == role}


# ---------------------------------------------------------------------------
# Permissions
# ---------------------------------------------------------------------------

def _client_reviewers(session: Session, company_id: int) -> set[int]:
    return {u.id for u in session.exec(select(User).where(User.company_id == company_id, User.is_active == True,  # noqa: E712
                                                            User.deleted_at.is_(None))).all()
            if user_has_permission(session, u.id, "dataset:review", company_id)}


def can_answer(session: Session, actor: User, q: dict, rows: list[BrsrDepartmentRole]) -> bool:
    if actor.portal_type != "client" or not needs_review(q):
        return True
    spocs = _ids(rows, set(q.get("departments", [])), "spoc")
    if not spocs:
        return True
    if actor.id in spocs:
        return True
    # The company's BRSR coordinator (user administrator) may answer anything.
    return user_has_permission(session, actor.id, "user:manage", actor.company_id)


def reviewer_ids(session: Session, company_id: int, q: dict, rows: list[BrsrDepartmentRole]) -> set[int]:
    named = _ids(rows, set(q.get("departments", [])), "reviewer")
    if named:
        return named
    client = _client_reviewers(session, company_id)
    if client:
        return client
    return {a.consultant_user_id for a in session.exec(select(ConsultantAssignment).where(
        ConsultantAssignment.company_id == company_id, ConsultantAssignment.is_active == True)).all()}  # noqa: E712


def can_review(session: Session, actor: User, company_id: int, q: dict, rows: list[BrsrDepartmentRole]) -> bool:
    return actor.id in reviewer_ids(session, company_id, q, rows)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def status_map(session: Session, rs: BrsrResponseSet) -> dict[str, BrsrQuestionStatus]:
    return {s.question_id: s for s in session.exec(select(BrsrQuestionStatus).where(
        BrsrQuestionStatus.response_set_id == rs.id)).all()}


def _get_or_new(session: Session, rs: BrsrResponseSet, qid: str, cache: dict) -> BrsrQuestionStatus:
    st = cache.get(qid)
    if st is None:
        st = BrsrQuestionStatus(response_set_id=rs.id, section_code=QUESTIONS_BY_ID[qid]["section"],
                                question_id=qid, status="open")
        cache[qid] = st
    return st


def on_answers_saved(session: Session, rs: BrsrResponseSet, changed: list[str]) -> None:
    """Any change puts a question back to "open". Caller commits."""
    if not changed:
        return
    cache = status_map(session, rs)
    now = datetime.utcnow()
    for qid in changed:
        st = cache.get(qid)
        if st and st.status != "open":
            st.status = "open"
            st.answer_revision = None
            st.reviewed_by = st.reviewed_at = None
            st.updated_at = now
            session.add(st)


def pending_reviews(q_ids: Iterable[str], answers: dict, statuses: dict[str, BrsrQuestionStatus],
                    required: set[str], revisions: dict[str, int]) -> list[str]:
    """Questions that must be approved before their section can be submitted."""
    out = []
    for qid in q_ids:
        q = QUESTIONS_BY_ID[qid]
        if not needs_review(q):
            continue
        answered = qid in revisions and engine.is_answered(q, answers.get(qid), answers)
        if not answered and qid not in required:
            continue
        st = statuses.get(qid)
        if not st or st.status != "approved" or st.answer_revision != revisions.get(qid):
            out.append(qid)
    return out


def _check_section_open(session: Session, rs: BrsrResponseSet, code: str) -> None:
    sec = session.exec(select(BrsrSection).where(BrsrSection.response_set_id == rs.id,
                                                 BrsrSection.section_code == code)).first()
    if sec and sec.status not in EDITABLE_STATUSES:
        raise AppError("section_locked", f"Section {code} is {sec.status.replace('_', ' ')}", 409)


def _validate_ids(qids: list[str]) -> list[dict]:
    if not qids:
        raise AppError("empty_payload", "No questions given", 422)
    if len(qids) > MAX_BATCH:
        raise AppError("payload_too_large", f"At most {MAX_BATCH} questions at a time", 422)
    out = []
    for qid in qids:
        q = QUESTIONS_BY_ID.get(qid)
        if not q:
            raise AppError("unknown_question", f"Unknown question {qid}", 422, qid)
        if not needs_review(q):
            raise AppError("not_reviewed_per_question",
                           f"{qid} is built from the data sheets and is approved with the section", 422, qid)
        out.append(q)
    return out


def submit_questions(session: Session, actor: User, rs: BrsrResponseSet, qids: list[str],
                     ip_address: Optional[str] = None) -> dict:
    from app.brsr.service import answers_dict
    qs = _validate_ids(qids)
    rows = roster(session, rs.company_id)
    answers = answers_dict(session, rs)
    stored = {a.question_id: a for a in session.exec(select(BrsrAnswer).where(
        BrsrAnswer.response_set_id == rs.id, BrsrAnswer.question_id.in_(qids))).all()}
    cache = status_map(session, rs)
    comp = engine.Computer(answers)
    now = datetime.utcnow()
    notify: dict[int, list[str]] = {}
    for q in qs:
        qid = q["id"]
        _check_section_open(session, rs, q["section"])
        if not can_answer(session, actor, q, rows):
            raise AppError("forbidden", f"{qid} is answered by the {', '.join(q['departments'])} SPOC", 403, qid)
        if qid not in stored or not engine.is_answered(q, answers.get(qid), answers):
            raise AppError("unanswered", f"Answer {qid} before sending it for review", 422, qid)
        errors = [i for i in engine.validate_answer(qid, answers.get(qid), answers, comp) if i["level"] == "error"]
        if errors:
            raise AppError("invalid_answer", f"{qid}: {errors[0]['message']}", 422, qid)
        st = _get_or_new(session, rs, qid, cache)
        if st.status in ("submitted", "approved") and st.answer_revision == stored[qid].revision:
            continue
        st.status, st.answer_revision = "submitted", stored[qid].revision
        st.submitted_by, st.submitted_at = actor.id, now
        st.reviewed_by = st.reviewed_at = None
        st.note, st.updated_at = None, now
        session.add(st)
        for uid in reviewer_ids(session, rs.company_id, q, rows) - {actor.id}:
            notify.setdefault(uid, []).append(qid)
    log_action(session, actor, "brsr.questions.submitted", "brsr_response_set", rs.id, rs.public_id,
               company_id=rs.company_id, changes={"questions": [q["id"] for q in qs]}, ip_address=ip_address)
    for uid, ids in notify.items():
        enqueue_notification(session, event_type="brsr_question_submitted", recipient_user_ids=[uid],
                             title=f"{len(ids)} BRSR answer(s) to review",
                             body=f"FY {rs.financial_year}: {', '.join(ids[:12])}{'…' if len(ids) > 12 else ''}",
                             entity_type="brsr_response_set", entity_id=rs.id, entity_public_id=rs.public_id)
    session.commit()
    return question_states(session, rs, [q["id"] for q in qs], actor)


def review_questions(session: Session, actor: User, rs: BrsrResponseSet, qids: list[str], decision: str,
                     note: Optional[str], ip_address: Optional[str] = None) -> dict:
    if decision not in ("approved", "returned"):
        raise AppError("invalid_decision", "decision must be 'approved' or 'returned'", 422, "decision")
    note = (note or "").strip()
    if decision == "returned" and not note:
        raise AppError("note_required", "Say what needs to change", 422, "note")
    if len(note) > 5000:
        raise AppError("note_too_long", "Keep the note under 5,000 characters", 422, "note")
    qs = _validate_ids(qids)
    rows = roster(session, rs.company_id)
    cache = status_map(session, rs)
    stored = {a.question_id: a for a in session.exec(select(BrsrAnswer).where(
        BrsrAnswer.response_set_id == rs.id, BrsrAnswer.question_id.in_(qids))).all()}
    now = datetime.utcnow()
    submitters: dict[int, list[str]] = {}
    for q in qs:
        qid = q["id"]
        _check_section_open(session, rs, q["section"])
        st = cache.get(qid)
        if not st or st.status != "submitted":
            raise AppError("invalid_state", f"{qid} is not waiting for review", 409, qid)
        if not can_review(session, actor, rs.company_id, q, rows):
            raise AppError("forbidden", f"You are not a reviewer for {qid}", 403, qid)
        ans = stored.get(qid)
        if actor.id in (st.submitted_by, ans.updated_by if ans else None):
            raise AppError("segregation_of_duties", f"{qid}: you cannot review an answer you wrote or submitted",
                           403, qid)
        if not ans or ans.revision != st.answer_revision:
            raise AppError("stale", f"{qid} changed after it was submitted; it must be submitted again", 409, qid)
        st.status = decision
        st.reviewed_by, st.reviewed_at, st.note, st.updated_at = actor.id, now, note or None, now
        session.add(st)
        if decision == "returned":
            session.add(BrsrComment(response_set_id=rs.id, section_code=q["section"], question_id=qid, body=note,
                                    kind="review", author_id=actor.id, created_at=now))
        if st.submitted_by:
            submitters.setdefault(st.submitted_by, []).append(qid)
    log_action(session, actor, f"brsr.questions.{decision}", "brsr_response_set", rs.id, rs.public_id,
               company_id=rs.company_id, changes={"questions": [q["id"] for q in qs], "note": note[:200]},
               ip_address=ip_address)
    for uid, ids in submitters.items():
        enqueue_notification(
            session, event_type=f"brsr_question_{decision}", recipient_user_ids=[uid],
            title=(f"{len(ids)} BRSR answer(s) approved" if decision == "approved"
                   else f"{len(ids)} BRSR answer(s) returned for changes"),
            body=(note[:500] or None) if decision == "returned" else ", ".join(ids[:12]),
            entity_type="brsr_response_set", entity_id=rs.id, entity_public_id=rs.public_id)
    session.commit()
    return question_states(session, rs, [q["id"] for q in qs], actor)


def return_for_deloitte_comments(session: Session, rs: BrsrResponseSet, qids: list[str], actor: User) -> None:
    """Deloitte requested changes on these questions: send them back. Caller commits."""
    cache = status_map(session, rs)
    now = datetime.utcnow()
    for qid in qids:
        q = QUESTIONS_BY_ID.get(qid)
        if not q or not needs_review(q):
            continue
        st = _get_or_new(session, rs, qid, cache)
        st.status, st.reviewed_by, st.reviewed_at, st.updated_at = "returned", actor.id, now, now
        st.note = "Changes requested by Deloitte"
        session.add(st)


def question_states(session: Session, rs: BrsrResponseSet, qids: Optional[list[str]], actor: User) -> dict:
    """{qid: {status, submitted_by, reviewed_by, note, can_answer, can_review, reviewers}}."""
    rows = roster(session, rs.company_id)
    cache = status_map(session, rs)
    stored = {a.question_id: a for a in session.exec(select(BrsrAnswer).where(
        BrsrAnswer.response_set_id == rs.id)).all()}
    ids = qids if qids is not None else [qid for qid, q in QUESTIONS_BY_ID.items() if needs_review(q)]
    people = {st.submitted_by for st in cache.values()} | {st.reviewed_by for st in cache.values()}
    names = {u.id: u.name for u in session.exec(select(User).where(User.id.in_({p for p in people if p}))).all()} \
        if any(people) else {}
    reviewer_cache: dict[tuple, set[int]] = {}
    out = {}
    for qid in ids:
        q = QUESTIONS_BY_ID[qid]
        st = cache.get(qid)
        ans = stored.get(qid)
        status = st.status if st else "open"
        if status in ("submitted", "approved") and (not ans or ans.revision != st.answer_revision):
            status = "open"  # edited since (defensive; saves already reset it)
        key = tuple(sorted(q.get("departments", [])))
        if key not in reviewer_cache:
            reviewer_cache[key] = reviewer_ids(session, rs.company_id, q, rows)
        revs = reviewer_cache[key]
        out[qid] = {
            "status": status,
            "submitted_by": names.get(st.submitted_by) if st else None,
            "submitted_at": st.submitted_at if st else None,
            "reviewed_by": names.get(st.reviewed_by) if st else None,
            "reviewed_at": st.reviewed_at if st else None,
            "note": st.note if st else None,
            "can_answer": can_answer(session, actor, q, rows),
            "can_review": status == "submitted" and actor.id in revs and actor.id != (st.submitted_by if st else None)
                          and actor.id != (ans.updated_by if ans else None),
        }
    return out


def question_queue(session: Session, actor: User) -> list[dict]:
    """Answers waiting for this person's approval, across their companies."""
    from app.core.tenancy import accessible_company_ids
    from app.models.company import Company
    allowed = accessible_company_ids(session, actor)
    if allowed is None:  # organisation-wide administrator
        allowed = {c.id for c in session.exec(select(Company).where(
            Company.organization_id == actor.organization_id)).all()}
    if not allowed:
        return []
    sets = {s.id: s for s in session.exec(select(BrsrResponseSet).where(
        BrsrResponseSet.company_id.in_(list(allowed)))).all()}
    if not sets:
        return []
    pending = session.exec(select(BrsrQuestionStatus).where(
        BrsrQuestionStatus.response_set_id.in_(list(sets)), BrsrQuestionStatus.status == "submitted")).all()
    rosters: dict[int, list] = {}
    out = []
    names = {u.id: u.name for u in session.exec(select(User).where(
        User.id.in_({p.submitted_by for p in pending if p.submitted_by}))).all()} if pending else {}
    for st in pending:
        rs = sets[st.response_set_id]
        q = QUESTIONS_BY_ID.get(st.question_id)
        if not q:
            continue
        rows = rosters.setdefault(rs.company_id, roster(session, rs.company_id))
        if actor.id == st.submitted_by or not can_review(session, actor, rs.company_id, q, rows):
            continue
        out.append({"response_set": rs.public_id, "company_id": rs.company_id, "financial_year": rs.financial_year,
                    "section": st.section_code, "question_id": st.question_id, "number": q.get("number"),
                    "prompt": q.get("prompt", "")[:200], "departments": q.get("departments", []),
                    "submitted_by": names.get(st.submitted_by), "submitted_at": st.submitted_at})
    return sorted(out, key=lambda r: (r["submitted_at"] or datetime.min))
