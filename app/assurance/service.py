"""Assurance engagements over a company's BRSR (see app/models/assurance.py).

Who can do what
---------------
- ``assurance:manage`` (Deloitte administrator, assurance partner): create
  an engagement for a company in their organisation, pick the team,
  start fieldwork, withdraw.
- Team members (``assurance_members``), after declaring independence:
  workpapers, information requests, findings. Only members with
  ``assurance:sign`` can be the partner or the engagement quality reviewer.
- The client (``assurance:respond``, own company only): answer requests with
  text and evidence, give management responses to findings.
- Anyone else with ``assurance:read`` and access to the company sees the
  engagement's status, requests, findings and the sign-off chain, but not
  the assurer's internal working notes.

Every state change is audit-logged in the same transaction; notifications
go through the outbox. Cross-company access returns 404.
"""
from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from typing import Optional

from sqlmodel import Session, select

from app.brsr import evidence as ev_service
from app.brsr import service as brsr
from app.brsr.question_bank import QUESTIONS_BY_ID, SECTION_CODES, SECTIONS
from app.core.errors import AppError, NotFoundError
from app.core.tenancy import can_access_company
from app.models.assurance import (
    LEVELS, MEMBER_ROLES, OPINIONS, AssuranceEngagement, AssuranceFinding, AssuranceItem, AssuranceMember,
    AssuranceRequest,
)
from app.models.brsr import BrsrAnswer, BrsrAnswerHistory, BrsrResponseSet, BrsrSection
from app.models.company import Company
from app.models.consultant_assignment import ConsultantAssignment
from app.models.evidence import EvidenceFile
from app.models.user import User
from app.rbac.service import user_has_permission
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification

SECTION_TITLES = {s["code"]: s["title"] for s in SECTIONS}
SEVERITIES = ("observation", "misstatement", "control_deficiency")
SYSTEM_SOURCES = {"carry_forward": "carried forward from last year's approved BRSR",
                  "bank_upgrade": "recovered from the earlier form during the platform upgrade"}
ACTIVE_STATUSES = ("planning", "fieldwork", "signed")
LEVEL_LABEL = {"limited": "Limited assurance", "reasonable": "Reasonable assurance"}


def _has(session: Session, user: User, permission: str) -> bool:
    """Permission check in the user's own scope (client roles are company-scoped)."""
    return user_has_permission(session, user.id, permission, user.company_id)


def _now() -> datetime:
    return datetime.utcnow()


def _text(value: Optional[str], field: str, label: str, max_len: int = 5000, required: bool = True) -> Optional[str]:
    v = (value or "").strip()
    if required and not v:
        raise AppError("required", f"{label} is required", 422, field)
    if len(v) > max_len:
        raise AppError("too_long", f"{label} is limited to {max_len} characters", 422, field)
    return v or None


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def active_members(session: Session, eng: AssuranceEngagement) -> list[AssuranceMember]:
    return list(session.exec(select(AssuranceMember).where(
        AssuranceMember.engagement_id == eng.id, AssuranceMember.removed_at.is_(None))).all())


def member_of(session: Session, eng: AssuranceEngagement, user: User) -> Optional[AssuranceMember]:
    return session.exec(select(AssuranceMember).where(
        AssuranceMember.engagement_id == eng.id, AssuranceMember.user_id == user.id,
        AssuranceMember.removed_at.is_(None))).first()


def _can_manage(session: Session, actor: User, company_id: int) -> bool:
    if actor.portal_type == "client" or not _has(session, actor, "assurance:manage"):
        return False
    company = session.get(Company, company_id)
    return bool(company and company.organization_id == actor.organization_id)


def access_of(session: Session, actor: User, eng: AssuranceEngagement) -> Optional[str]:
    """'member' | 'manager' | 'client' | 'observer' | None."""
    if member_of(session, eng, actor):
        return "member"
    if _can_manage(session, actor, eng.company_id):
        return "manager"
    if not _has(session, actor, "assurance:read"):
        return None
    if actor.portal_type == "client":
        return "client" if actor.company_id == eng.company_id else None
    return "observer" if can_access_company(session, actor, eng.company_id) else None


def get_engagement(session: Session, actor: User, public_id: str) -> tuple[AssuranceEngagement, str]:
    eng = session.exec(select(AssuranceEngagement).where(AssuranceEngagement.public_id == public_id)).first()
    access = access_of(session, actor, eng) if eng else None
    if not eng or not access:
        raise NotFoundError("Assurance engagement not found")
    return eng, access


def _require_member(session: Session, actor: User, eng: AssuranceEngagement, *, roles=None,
                    declared: bool = True) -> AssuranceMember:
    m = member_of(session, eng, actor)
    if not m:
        raise AppError("not_on_team", "Only members of the assurance team can do this", 403)
    if declared and not m.independence_declared_at:
        raise AppError("independence_not_declared", "Declare your independence for this engagement first", 403)
    if roles and m.role not in roles:
        raise AppError("role_not_allowed", f"Only the {' or '.join(roles)} can do this", 403)
    return m


def _require_status(eng: AssuranceEngagement, *statuses: str) -> None:
    if eng.status not in statuses:
        raise AppError("invalid_state", f"The engagement is {eng.status}; this needs it to be "
                                        f"{' or '.join(statuses)}", 409)


def _require_client(actor: User, eng: AssuranceEngagement) -> None:
    if actor.portal_type != "client" or actor.company_id != eng.company_id:
        raise AppError("client_only", "Only the company being assured can respond", 403)


def _set_of(session: Session, eng: AssuranceEngagement) -> BrsrResponseSet:
    return session.get(BrsrResponseSet, eng.response_set_id)


# ---------------------------------------------------------------------------
# Independence
# ---------------------------------------------------------------------------

def independence_conflicts(session: Session, user: User, eng: AssuranceEngagement) -> list[str]:
    """Reasons ``user`` is not independent of what this engagement assures.
    Assurers must not assure their own work or a client they advise."""
    reasons: list[str] = []
    if user.portal_type == "client":
        reasons.append("works for a client company")
    rs = _set_of(session, eng)
    if rs.created_by == user.id:
        reasons.append("started this BRSR")
    edits = session.exec(select(BrsrAnswerHistory.id).where(
        BrsrAnswerHistory.response_set_id == rs.id, BrsrAnswerHistory.changed_by == user.id,
        BrsrAnswerHistory.source == "edit")).first()
    if edits:
        reasons.append("entered or edited answers in this BRSR")
    secs = session.exec(select(BrsrSection).where(BrsrSection.response_set_id == rs.id)).all()
    if any(s.submitted_by == user.id for s in secs):
        reasons.append("submitted BRSR sections for review")
    if any(s.internal_reviewed_by == user.id for s in secs):
        reasons.append("reviewed BRSR sections internally")
    if any(s.decided_by == user.id for s in secs):
        reasons.append("approved or reviewed BRSR sections")
    if session.exec(select(EvidenceFile.id).where(EvidenceFile.response_set_id == rs.id,
                                                  EvidenceFile.uploaded_by == user.id)).first():
        reasons.append("uploaded evidence for this BRSR")
    if session.exec(select(ConsultantAssignment.id).where(
            ConsultantAssignment.company_id == eng.company_id, ConsultantAssignment.consultant_user_id == user.id,
            ConsultantAssignment.is_active == True)).first():  # noqa: E712
        reasons.append("is an assigned consultant to this client")
    return reasons


def candidates(session: Session, actor: User, eng: AssuranceEngagement) -> list[dict]:
    """Assurance staff in the organisation, with eligibility for this team."""
    if not _can_manage(session, actor, eng.company_id):
        raise AppError("forbidden", "You cannot manage this engagement's team", 403)
    users = session.exec(select(User).where(User.portal_type == "deloitte", User.is_active == True,  # noqa: E712
                                            User.organization_id == actor.organization_id)).all()
    current = {m.user_id: m for m in active_members(session, eng)}
    out = []
    for u in users:
        if not _has(session, u, "assurance:perform"):
            continue
        conflicts = independence_conflicts(session, u, eng)
        out.append({"user_id": u.id, "name": u.name, "email": u.email, "job_role": u.role,
                    "can_sign": _has(session, u, "assurance:sign"),
                    "conflicts": conflicts, "eligible": not conflicts,
                    "on_team_as": current[u.id].role if u.id in current else None})
    return sorted(out, key=lambda r: (not r["eligible"], r["name"]))


# ---------------------------------------------------------------------------
# Engagement lifecycle
# ---------------------------------------------------------------------------

def create_engagement(session: Session, actor: User, *, response_set_id: str, provider_name: str, level: str,
                      scope: Optional[list[str]] = None, standard: Optional[str] = None,
                      ip_address: Optional[str] = None) -> AssuranceEngagement:
    rs = session.exec(select(BrsrResponseSet).where(BrsrResponseSet.public_id == response_set_id)).first()
    if not rs or not _can_manage(session, actor, rs.company_id):
        raise NotFoundError("BRSR response not found")
    if level not in LEVELS:
        raise AppError("invalid_level", "level must be 'limited' or 'reasonable'", 422, "level")
    provider = _text(provider_name, "provider_name", "Assurance provider", 200)
    scope = list(dict.fromkeys(scope or SECTION_CODES))
    bad = [c for c in scope if c not in SECTION_CODES]
    if bad or not scope:
        raise AppError("invalid_scope", f"Unknown BRSR sections in scope: {', '.join(bad) or 'none given'}", 422,
                       "scope")
    scope = [c for c in SECTION_CODES if c in scope]
    existing = session.exec(select(AssuranceEngagement).where(
        AssuranceEngagement.response_set_id == rs.id, AssuranceEngagement.status.in_(ACTIVE_STATUSES))).first()
    # A signed engagement whose BRSR has since changed can be replaced by a
    # re-assurance; it stays on record as superseded.
    superseding = existing if existing and is_stale(session, existing) else None
    if existing and not superseding:
        raise AppError("engagement_exists", f"FY {rs.financial_year} already has an assurance engagement "
                                            f"({existing.status})", 409)
    if superseding:
        superseding.status, superseding.updated_at = "superseded", _now()
        session.add(superseding)
        session.flush()
    eng = AssuranceEngagement(company_id=rs.company_id, response_set_id=rs.id, financial_year=rs.financial_year,
                              level=level, provider_name=provider, scope=scope, created_by=actor.id,
                              standard=_text(standard, "standard", "Standard", 200, required=False)
                              or "ISAE 3000 (Revised) / ICAI SSAE 3000")
    session.add(eng)
    session.flush()
    if superseding:
        superseding.superseded_by_id = eng.id
        session.add(superseding)
    for code in scope:
        session.add(AssuranceItem(engagement_id=eng.id, section_code=code))
    log_action(session, actor, "assurance.engagement.created", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id,
               changes={"financial_year": eng.financial_year, "level": level, "provider": provider,
                        "scope": scope, "supersedes": superseding.public_id if superseding else None},
               ip_address=ip_address)
    session.commit()
    session.refresh(eng)
    return eng


def add_member(session: Session, actor: User, eng: AssuranceEngagement, *, user_id: int, role: str,
               ip_address: Optional[str] = None) -> AssuranceMember:
    if not _can_manage(session, actor, eng.company_id):
        raise AppError("forbidden", "You cannot manage this engagement's team", 403)
    _require_status(eng, "planning", "fieldwork")
    if role not in MEMBER_ROLES:
        raise AppError("invalid_role", f"role must be one of {', '.join(MEMBER_ROLES)}", 422, "role")
    user = session.get(User, user_id)
    if not user or not user.is_active or user.portal_type == "client" or \
            user.organization_id != actor.organization_id:
        raise NotFoundError("User not found")
    if not _has(session, user, "assurance:perform"):
        raise AppError("not_assurance_staff", f"{user.name} does not hold an assurance role", 422, "user_id")
    if role in ("partner", "eqr") and not _has(session, user, "assurance:sign"):
        raise AppError("not_a_partner", f"The {role} must hold the Assurance Partner role", 422, "role")
    conflicts = independence_conflicts(session, user, eng)
    if conflicts:
        raise AppError("independence_conflict",
                       f"{user.name} is not independent of this BRSR: {'; '.join(conflicts)}.", 409, "user_id")
    team = active_members(session, eng)
    if any(m.user_id == user.id for m in team):
        raise AppError("already_member", f"{user.name} is already on the team", 409, "user_id")
    if role in ("partner", "eqr") and any(m.role == role for m in team):
        raise AppError("role_taken", f"The engagement already has a {role}", 409, "role")
    old = session.exec(select(AssuranceMember).where(AssuranceMember.engagement_id == eng.id,
                                                     AssuranceMember.user_id == user.id)).first()
    if old:  # re-added after removal: same row, fresh declaration
        m = old
        m.role, m.removed_at, m.removed_by = role, None, None
        m.added_by, m.added_at, m.independence_declared_at = actor.id, _now(), None
    else:
        m = AssuranceMember(engagement_id=eng.id, user_id=user.id, role=role, added_by=actor.id)
    session.add(m)
    session.flush()
    log_action(session, actor, "assurance.member.added", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"user_id": user.id, "name": user.name, "role": role},
               ip_address=ip_address)
    enqueue_notification(session, event_type="assurance_member_added", recipient_user_ids=[user.id],
                         title=f"Added to assurance of FY {eng.financial_year} BRSR",
                         body=f"You were added as {role}. Declare your independence to start.",
                         entity_type="assurance_engagement", entity_id=eng.id, entity_public_id=eng.public_id)
    session.commit()
    session.refresh(m)
    return m


def remove_member(session: Session, actor: User, eng: AssuranceEngagement, user_id: int,
                  ip_address: Optional[str] = None) -> None:
    if not _can_manage(session, actor, eng.company_id):
        raise AppError("forbidden", "You cannot manage this engagement's team", 403)
    _require_status(eng, "planning", "fieldwork")
    m = session.exec(select(AssuranceMember).where(AssuranceMember.engagement_id == eng.id,
                                                   AssuranceMember.user_id == user_id,
                                                   AssuranceMember.removed_at.is_(None))).first()
    if not m:
        raise NotFoundError("Team member not found")
    m.removed_at, m.removed_by = _now(), actor.id
    session.add(m)
    log_action(session, actor, "assurance.member.removed", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"user_id": user_id, "role": m.role}, ip_address=ip_address)
    session.commit()


def declare_independence(session: Session, actor: User, eng: AssuranceEngagement,
                         ip_address: Optional[str] = None) -> AssuranceMember:
    m = _require_member(session, actor, eng, declared=False)
    _require_status(eng, "planning", "fieldwork")
    conflicts = independence_conflicts(session, actor, eng)
    if conflicts:  # the data may have changed since they were added
        raise AppError("independence_conflict", f"You are not independent of this BRSR: {'; '.join(conflicts)}.",
                       409)
    m.independence_declared_at = _now()
    session.add(m)
    log_action(session, actor, "assurance.independence.declared", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"role": m.role}, ip_address=ip_address)
    session.commit()
    session.refresh(m)
    return m


def start_fieldwork(session: Session, actor: User, eng: AssuranceEngagement,
                    ip_address: Optional[str] = None) -> AssuranceEngagement:
    if not _can_manage(session, actor, eng.company_id):
        raise AppError("forbidden", "You cannot manage this engagement", 403)
    _require_status(eng, "planning")
    team = active_members(session, eng)
    problems = []
    if not any(m.role == "partner" for m in team):
        problems.append("a signing partner")
    if not any(m.role in ("manager", "practitioner") for m in team):
        problems.append("at least one manager or practitioner")
    undeclared = [m for m in team if not m.independence_declared_at]
    if problems:
        raise AppError("team_incomplete", f"The team needs {' and '.join(problems)}", 409)
    if undeclared:
        names = brsr_names(session, [m.user_id for m in undeclared])
        raise AppError("independence_not_declared",
                       f"Waiting for independence declarations from: {', '.join(names.values())}", 409)
    eng.status, eng.fieldwork_started_at, eng.updated_at = "fieldwork", _now(), _now()
    session.add(eng)
    log_action(session, actor, "assurance.fieldwork.started", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, ip_address=ip_address)
    _notify_client(session, eng, "assurance_started", f"Assurance of your FY {eng.financial_year} BRSR has started",
                   f"{eng.provider_name} has started {LEVEL_LABEL[eng.level].lower()} fieldwork.")
    session.commit()
    session.refresh(eng)
    return eng


def withdraw_engagement(session: Session, actor: User, eng: AssuranceEngagement, reason: str,
                        ip_address: Optional[str] = None) -> AssuranceEngagement:
    if not _can_manage(session, actor, eng.company_id):
        raise AppError("forbidden", "You cannot manage this engagement", 403)
    _require_status(eng, "planning", "fieldwork")
    eng.withdrawn_reason = _text(reason, "reason", "Reason", 1000)
    eng.status, eng.withdrawn_by, eng.withdrawn_at, eng.updated_at = "withdrawn", actor.id, _now(), _now()
    session.add(eng)
    log_action(session, actor, "assurance.engagement.withdrawn", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"reason": eng.withdrawn_reason}, ip_address=ip_address)
    session.commit()
    session.refresh(eng)
    return eng


# ---------------------------------------------------------------------------
# Workpapers
# ---------------------------------------------------------------------------

def _item(session: Session, eng: AssuranceEngagement, code: str) -> AssuranceItem:
    item = session.exec(select(AssuranceItem).where(AssuranceItem.engagement_id == eng.id,
                                                    AssuranceItem.section_code == code)).first()
    if not item:
        raise NotFoundError(f"Section {code} is not in the scope of this engagement")
    return item


def item_outdated(item: AssuranceItem, sec: BrsrSection) -> bool:
    """Tested before the section's current approval: the figures changed after
    testing (reopened, corrected, re-approved), so it must be tested again."""
    return bool(item.prepared_at and (sec.status != "approved" or not sec.decided_at
                                      or sec.decided_at > item.prepared_at))


def prepare_item(session: Session, actor: User, eng: AssuranceEngagement, code: str, *, procedures: str,
                 conclusion: str, ip_address: Optional[str] = None) -> AssuranceItem:
    _require_member(session, actor, eng, roles=("practitioner", "manager", "partner"))
    _require_status(eng, "fieldwork")
    item = _item(session, eng, code)
    sec = brsr.get_section(session, _set_of(session, eng), code)
    if item.status == "reviewed" and not item_outdated(item, sec):
        raise AppError("invalid_state", f"The workpaper for {code} has already been reviewed", 409)
    if sec.status != "approved":
        raise AppError("section_not_final", f"Section {code} is {sec.status.replace('_', ' ')}. Assurance is "
                                            f"performed on approved figures only.", 409)
    if conclusion not in ("no_exceptions", "exceptions"):
        raise AppError("invalid_conclusion", "conclusion must be 'no_exceptions' or 'exceptions'", 422, "conclusion")
    if conclusion == "exceptions" and not session.exec(select(AssuranceFinding.id).where(
            AssuranceFinding.engagement_id == eng.id, AssuranceFinding.section_code == code)).first():
        raise AppError("finding_required", f"Record the exceptions as findings on {code} first", 409)
    item.procedures = _text(procedures, "procedures", "Procedures performed", 10000)
    item.conclusion, item.status = conclusion, "prepared"
    item.prepared_by, item.prepared_at, item.updated_at = actor.id, _now(), _now()
    item.reviewed_by = item.reviewed_at = item.review_note = None
    session.add(item)
    log_action(session, actor, "assurance.workpaper.prepared", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"section": code, "conclusion": conclusion},
               ip_address=ip_address)
    session.commit()
    session.refresh(item)
    return item


def review_item(session: Session, actor: User, eng: AssuranceEngagement, code: str, *, decision: str,
                note: Optional[str], ip_address: Optional[str] = None) -> AssuranceItem:
    _require_member(session, actor, eng, roles=("manager", "partner"))
    _require_status(eng, "fieldwork")
    item = _item(session, eng, code)
    if item.status != "prepared":
        raise AppError("invalid_state", f"The workpaper for {code} is {item.status}, not awaiting review", 409)
    if item.prepared_by == actor.id:
        raise AppError("segregation_of_duties", "A workpaper must be reviewed by someone other than its preparer",
                       403)
    if decision not in ("approve", "return"):
        raise AppError("invalid_decision", "decision must be 'approve' or 'return'", 422, "decision")
    item.review_note = _text(note, "note", "Review note", 5000, required=decision == "return")
    if decision == "approve":
        item.status, item.reviewed_by, item.reviewed_at = "reviewed", actor.id, _now()
    else:
        item.status = "open"
    item.updated_at = _now()
    session.add(item)
    log_action(session, actor, f"assurance.workpaper.{'reviewed' if decision == 'approve' else 'returned'}",
               "assurance_engagement", eng.id, eng.public_id, company_id=eng.company_id,
               changes={"section": code, "note": item.review_note}, ip_address=ip_address)
    if decision == "return" and item.prepared_by:
        enqueue_notification(session, event_type="assurance_workpaper_returned", recipient_user_ids=[item.prepared_by],
                             title=f"Workpaper {code} returned", body=item.review_note,
                             entity_type="assurance_engagement", entity_id=eng.id, entity_public_id=eng.public_id)
    session.commit()
    session.refresh(item)
    return item


# ---------------------------------------------------------------------------
# Information requests
# ---------------------------------------------------------------------------

def _validate_target(eng: AssuranceEngagement, code: str, question_id: Optional[str]) -> None:
    if code not in eng.scope:
        raise AppError("out_of_scope", f"Section {code} is not in the scope of this engagement", 422, "section_code")
    if question_id and (question_id not in QUESTIONS_BY_ID or QUESTIONS_BY_ID[question_id]["section"] != code):
        raise AppError("invalid_question", f"{question_id} is not a question in section {code}", 422, "question_id")


def get_request(session: Session, eng: AssuranceEngagement, public_id: str) -> AssuranceRequest:
    r = session.exec(select(AssuranceRequest).where(AssuranceRequest.public_id == public_id,
                                                    AssuranceRequest.engagement_id == eng.id)).first()
    if not r:
        raise NotFoundError("Information request not found")
    return r


def raise_request(session: Session, actor: User, eng: AssuranceEngagement, *, section_code: str,
                  question_id: Optional[str], title: str, detail: Optional[str], due_date: Optional[date],
                  ip_address: Optional[str] = None) -> AssuranceRequest:
    _require_member(session, actor, eng, roles=("practitioner", "manager", "partner"))
    _require_status(eng, "fieldwork")
    _validate_target(eng, section_code, question_id)
    if due_date and due_date < date.today():
        raise AppError("invalid_due_date", "The due date cannot be in the past", 422, "due_date")
    r = AssuranceRequest(engagement_id=eng.id, section_code=section_code, question_id=question_id,
                         title=_text(title, "title", "Title", 300), detail=_text(detail, "detail", "Detail",
                                                                                  5000, required=False),
                         due_date=due_date, requested_by=actor.id)
    session.add(r)
    session.flush()
    log_action(session, actor, "assurance.request.raised", "assurance_request", r.id, r.public_id,
               company_id=eng.company_id, changes={"section": section_code, "question": question_id,
                                                   "title": r.title}, ip_address=ip_address)
    _notify_client(session, eng, "assurance_request_raised", f"Assurance request: {r.title}",
                   f"{eng.provider_name} asks for evidence on {section_code}"
                   + (f" ({question_id})" if question_id else "")
                   + (f", due {due_date.isoformat()}" if due_date else "") + ".", section_code=section_code)
    session.commit()
    session.refresh(r)
    return r


def upload_request_evidence(session: Session, actor: User, eng: AssuranceEngagement, r: AssuranceRequest, *,
                            filename: str, content: bytes, description: Optional[str],
                            ip_address: Optional[str] = None) -> EvidenceFile:
    _require_client(actor, eng)
    _require_status(eng, "fieldwork")
    if r.status != "open":
        raise AppError("invalid_state", f"This request is {r.status}; evidence can only be added while it is open",
                       409)
    ev = ev_service.store_evidence(session, actor, _set_of(session, eng), section_code=r.section_code,
                                   question_id=r.question_id, filename=filename, content=content,
                                   description=description, assurance_request_id=r.id, ip_address=ip_address)
    session.commit()
    session.refresh(ev)
    return ev


def respond_request(session: Session, actor: User, eng: AssuranceEngagement, r: AssuranceRequest, *, response: str,
                    ip_address: Optional[str] = None) -> AssuranceRequest:
    _require_client(actor, eng)
    _require_status(eng, "fieldwork")
    if r.status != "open":
        raise AppError("invalid_state", f"This request is {r.status}", 409)
    text = _text(response, "response", "Response", 5000, required=False)
    files = _request_files(session, r)
    if not text and not files:
        raise AppError("response_empty", "Add a response or attach at least one evidence file", 422, "response")
    r.response_text, r.status, r.responded_by, r.responded_at = text, "responded", actor.id, _now()
    session.add(r)
    log_action(session, actor, "assurance.request.responded", "assurance_request", r.id, r.public_id,
               company_id=eng.company_id, changes={"files": len(files)}, ip_address=ip_address)
    enqueue_notification(session, event_type="assurance_request_responded", recipient_user_ids=[r.requested_by],
                         title=f"Client responded: {r.title}", body=text,
                         entity_type="assurance_engagement", entity_id=eng.id, entity_public_id=eng.public_id)
    session.commit()
    session.refresh(r)
    return r


def close_request(session: Session, actor: User, eng: AssuranceEngagement, r: AssuranceRequest, *, decision: str,
                  note: Optional[str], ip_address: Optional[str] = None) -> AssuranceRequest:
    _require_member(session, actor, eng, roles=("practitioner", "manager", "partner"))
    _require_status(eng, "fieldwork")
    if r.status != "responded":
        raise AppError("invalid_state", f"This request is {r.status}, not awaiting your check", 409)
    if decision not in ("accept", "return"):
        raise AppError("invalid_decision", "decision must be 'accept' or 'return'", 422, "decision")
    note = _text(note, "note", "Note", 2000, required=decision == "return")
    if decision == "accept":
        r.status, r.closed_by, r.closed_at = "accepted", actor.id, _now()
    else:
        r.history = [*r.history, {"by": actor.id, "at": _now().isoformat(), "note": note,
                                  "response": r.response_text}]
        r.status, r.response_text, r.responded_by, r.responded_at = "open", None, None, None
    session.add(r)
    log_action(session, actor, f"assurance.request.{'accepted' if decision == 'accept' else 'returned'}",
               "assurance_request", r.id, r.public_id, company_id=eng.company_id, changes={"note": note},
               ip_address=ip_address)
    if decision == "return":
        _notify_client(session, eng, "assurance_request_returned", f"More needed: {r.title}", note,
                       section_code=r.section_code)
    session.commit()
    session.refresh(r)
    return r


def _request_files(session: Session, r: AssuranceRequest) -> list[EvidenceFile]:
    return list(session.exec(select(EvidenceFile).where(EvidenceFile.assurance_request_id == r.id,
                                                        EvidenceFile.deleted_at.is_(None))
                             .order_by(EvidenceFile.uploaded_at)).all())


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def get_finding(session: Session, eng: AssuranceEngagement, public_id: str) -> AssuranceFinding:
    f = session.exec(select(AssuranceFinding).where(AssuranceFinding.public_id == public_id,
                                                    AssuranceFinding.engagement_id == eng.id)).first()
    if not f:
        raise NotFoundError("Finding not found")
    return f


def raise_finding(session: Session, actor: User, eng: AssuranceEngagement, *, section_code: str,
                  question_id: Optional[str], severity: str, description: str,
                  ip_address: Optional[str] = None) -> AssuranceFinding:
    _require_member(session, actor, eng, roles=("practitioner", "manager", "partner"))
    _require_status(eng, "fieldwork")
    _validate_target(eng, section_code, question_id)
    if severity not in SEVERITIES:
        raise AppError("invalid_severity", f"severity must be one of {', '.join(SEVERITIES)}", 422, "severity")
    f = AssuranceFinding(engagement_id=eng.id, section_code=section_code, question_id=question_id, severity=severity,
                         description=_text(description, "description", "Description", 5000), raised_by=actor.id)
    session.add(f)
    session.flush()
    log_action(session, actor, "assurance.finding.raised", "assurance_finding", f.id, f.public_id,
               company_id=eng.company_id, changes={"section": section_code, "question": question_id,
                                                   "severity": severity}, ip_address=ip_address)
    _notify_client(session, eng, "assurance_finding_raised", f"Assurance finding on {section_code}",
                   f"{severity.replace('_', ' ').capitalize()}: {f.description[:300]}", section_code=section_code)
    session.commit()
    session.refresh(f)
    return f


def respond_finding(session: Session, actor: User, eng: AssuranceEngagement, f: AssuranceFinding, *, response: str,
                    ip_address: Optional[str] = None) -> AssuranceFinding:
    _require_client(actor, eng)
    _require_status(eng, "fieldwork")
    if f.status not in ("open", "responded"):
        raise AppError("invalid_state", f"This finding is already {f.status}", 409)
    f.management_response = _text(response, "response", "Management response", 5000)
    f.status, f.responded_by, f.responded_at = "responded", actor.id, _now()
    session.add(f)
    log_action(session, actor, "assurance.finding.responded", "assurance_finding", f.id, f.public_id,
               company_id=eng.company_id, ip_address=ip_address)
    enqueue_notification(session, event_type="assurance_finding_responded", recipient_user_ids=[f.raised_by],
                         title=f"Management response on {f.section_code} finding", body=f.management_response[:500],
                         entity_type="assurance_engagement", entity_id=eng.id, entity_public_id=eng.public_id)
    session.commit()
    session.refresh(f)
    return f


def resolve_finding(session: Session, actor: User, eng: AssuranceEngagement, f: AssuranceFinding, *, outcome: str,
                    note: Optional[str], ip_address: Optional[str] = None) -> AssuranceFinding:
    _require_member(session, actor, eng, roles=("manager", "partner"))
    _require_status(eng, "fieldwork")
    if f.status != "responded":
        raise AppError("invalid_state", "A finding is resolved after the client's management response", 409)
    if outcome not in ("corrected", "uncorrected"):
        raise AppError("invalid_outcome", "outcome must be 'corrected' or 'uncorrected'", 422, "outcome")
    if outcome == "corrected":
        sec = brsr.get_section(session, _set_of(session, eng), f.section_code)
        if sec.status != "approved" or not sec.decided_at or sec.decided_at <= f.raised_at:
            raise AppError("correction_not_approved",
                           f"Section {f.section_code} has not been corrected and re-approved since this finding was "
                           f"raised. The client reopens it, fixes the figure, and it goes through review again.", 409)
    f.resolution_note = _text(note, "note", "Resolution note", 5000, required=outcome == "uncorrected")
    f.status, f.resolved_by, f.resolved_at = outcome, actor.id, _now()
    session.add(f)
    log_action(session, actor, f"assurance.finding.{outcome}", "assurance_finding", f.id, f.public_id,
               company_id=eng.company_id, changes={"note": f.resolution_note}, ip_address=ip_address)
    session.commit()
    session.refresh(f)
    return f


# ---------------------------------------------------------------------------
# Quality review and sign-off
# ---------------------------------------------------------------------------

def assured_content_hash(session: Session, rs: BrsrResponseSet) -> str:
    """Fingerprint of the stored BRSR answers and their revisions. Values the
    platform injects (financial year, assurance provider) are excluded, so
    signing does not change the fingerprint it signs."""
    rows = session.exec(select(BrsrAnswer).where(BrsrAnswer.response_set_id == rs.id)).all()
    payload = {r.question_id: {"v": r.value, "r": r.revision} for r in rows}
    # Figures built from the approved data sheets are part of what is
    # assured: a later correction to a sheet makes the assurance stale. Only
    # included when the company has sheet data, so fingerprints taken before
    # data sheets existed are unchanged.
    from app.brsr.sheet_metrics import cached_compute
    sheet = cached_compute(session, rs.company_id, rs.financial_year)
    if sheet.get("basis"):
        payload["__sheet_metrics__"] = {k: sheet.get(k) for k in ("cy", "py", "cy_tables", "basis")}
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def signoff_blockers(session: Session, eng: AssuranceEngagement) -> list[str]:
    blockers: list[str] = []
    if eng.status != "fieldwork":
        blockers.append(f"the engagement is {eng.status}")
    items = session.exec(select(AssuranceItem).where(AssuranceItem.engagement_id == eng.id)).all()
    secs = brsr.sections_of(session, _set_of(session, eng))
    pending = [i.section_code for i in items if i.status != "reviewed"]
    if pending:
        blockers.append(f"workpapers not yet reviewed: {', '.join(pending)}")
    outdated = [i.section_code for i in items if i.status == "reviewed" and item_outdated(i, secs[i.section_code])]
    if outdated:
        blockers.append(f"sections changed after testing, test again: {', '.join(outdated)}")
    open_reqs = session.exec(select(AssuranceRequest).where(AssuranceRequest.engagement_id == eng.id,
                                                            AssuranceRequest.status != "accepted")).all()
    if open_reqs:
        blockers.append(f"{len(open_reqs)} information request(s) still open")
    open_finds = session.exec(select(AssuranceFinding).where(
        AssuranceFinding.engagement_id == eng.id, AssuranceFinding.status.in_(("open", "responded")))).all()
    if open_finds:
        blockers.append(f"{len(open_finds)} finding(s) not yet resolved")
    not_approved = [c for c in SECTION_CODES if secs[c].status != "approved"]
    if not_approved:
        blockers.append(f"BRSR sections not approved: {', '.join(not_approved)}")
    team = active_members(session, eng)
    if any(m.role == "eqr" for m in team) and not eng.eqr_cleared_at:
        blockers.append("the engagement quality reviewer has not cleared the engagement")
    return blockers


def clear_eqr(session: Session, actor: User, eng: AssuranceEngagement, note: Optional[str],
              ip_address: Optional[str] = None) -> AssuranceEngagement:
    _require_member(session, actor, eng, roles=("eqr",))
    _require_status(eng, "fieldwork")
    others = [b for b in signoff_blockers(session, eng) if "quality reviewer" not in b]
    if others:
        raise AppError("not_ready", f"Not ready for quality review: {'; '.join(others)}", 409)
    eng.eqr_cleared_by, eng.eqr_cleared_at, eng.updated_at = actor.id, _now(), _now()
    eng.eqr_note = _text(note, "note", "Note", 2000, required=False)
    session.add(eng)
    log_action(session, actor, "assurance.eqr.cleared", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id, changes={"note": eng.eqr_note}, ip_address=ip_address)
    session.commit()
    session.refresh(eng)
    return eng


def sign(session: Session, actor: User, eng: AssuranceEngagement, *, opinion: str, basis: Optional[str],
         statement: Optional[str], ip_address: Optional[str] = None) -> AssuranceEngagement:
    from app.assurance import documents

    _require_member(session, actor, eng, roles=("partner",))
    if not _has(session, actor, "assurance:sign"):
        raise AppError("forbidden", "Only an assurance partner can sign", 403)
    blockers = signoff_blockers(session, eng)
    if blockers:
        raise AppError("not_ready", f"Not ready to sign: {'; '.join(blockers)}", 409)
    if opinion not in OPINIONS:
        raise AppError("invalid_opinion", f"opinion must be one of {', '.join(OPINIONS)}", 422, "opinion")
    eng.opinion_basis = _text(basis, "basis", "Basis for the opinion", 5000, required=opinion != "unmodified")
    rs = _set_of(session, eng)
    now = _now()
    eng.opinion, eng.signed_by, eng.signed_at = opinion, actor.id, now
    eng.signed_content_hash = assured_content_hash(session, rs)
    eng.statement_text = _text(statement, "statement", "Statement", 20000, required=False) or \
        documents.default_statement(session, eng, signer=actor)
    eng.status, eng.updated_at = "signed", now
    eng.signed_register = [{k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
                           for row in register_rows(session, eng)]
    documents.store_signed_documents(session, eng)
    session.add(eng)
    log_action(session, actor, "assurance.signed", "assurance_engagement", eng.id, eng.public_id,
               company_id=eng.company_id,
               changes={"opinion": opinion, "content_hash": eng.signed_content_hash,
                        "statement_sha256": eng.statement_sha256, "register_sha256": eng.register_sha256},
               ip_address=ip_address)
    team = [m.user_id for m in active_members(session, eng) if m.user_id != actor.id]
    _notify_client(session, eng, "assurance_signed", f"FY {eng.financial_year} BRSR assurance signed",
                   f"{eng.provider_name} issued a {opinion} opinion ({LEVEL_LABEL[eng.level].lower()}).",
                   extra=team)
    session.commit()
    session.refresh(eng)
    return eng


def is_stale(session: Session, eng: AssuranceEngagement) -> bool:
    """Signed, but the BRSR has changed since: needs re-assurance."""
    return eng.status == "signed" and eng.signed_content_hash != assured_content_hash(session, _set_of(session, eng))


def signed_engagement_for(session: Session, rs: BrsrResponseSet) -> Optional[AssuranceEngagement]:
    return session.exec(select(AssuranceEngagement).where(AssuranceEngagement.response_set_id == rs.id,
                                                          AssuranceEngagement.status == "signed")).first()


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

def _notify_client(session: Session, eng: AssuranceEngagement, event: str, title: str, body: Optional[str], *,
                   section_code: Optional[str] = None, extra: Optional[list[int]] = None) -> None:
    """The client's administrators, plus whoever submitted the section concerned."""
    ids: set[int] = set(extra or [])
    for u in session.exec(select(User).where(User.company_id == eng.company_id, User.is_active == True)).all():  # noqa: E712
        if _has(session, u, "user:manage"):
            ids.add(u.id)
    if section_code:
        sec = brsr.get_section(session, _set_of(session, eng), section_code)
        if sec.submitted_by:
            ids.add(sec.submitted_by)
    if ids:
        enqueue_notification(session, event_type=event, recipient_user_ids=sorted(ids), title=title, body=body,
                             entity_type="assurance_engagement", entity_id=eng.id, entity_public_id=eng.public_id)


# ---------------------------------------------------------------------------
# Read models
# ---------------------------------------------------------------------------

def brsr_names(session: Session, ids) -> dict[int, str]:
    return ev_service.user_names(session, ids)


def register_rows(session: Session, eng: AssuranceEngagement) -> list[dict]:
    """The sign-off chain: for every section in scope, every person involved
    in preparing, reviewing, approving and assuring it."""
    rs = _set_of(session, eng)
    secs = brsr.sections_of(session, rs)
    items = {i.section_code: i for i in session.exec(select(AssuranceItem).where(
        AssuranceItem.engagement_id == eng.id)).all()}
    section_of_q = {qid: q["section"] for qid, q in QUESTIONS_BY_ID.items()}
    preparers: dict[str, dict[int, int]] = {}
    system: dict[str, set[str]] = {}
    last_edit: dict[str, datetime] = {}
    for h in session.exec(select(BrsrAnswerHistory).where(BrsrAnswerHistory.response_set_id == rs.id)).all():
        code = section_of_q.get(h.question_id)
        if not code:
            continue
        if h.source != "edit":  # figures the platform filled, recorded as such
            system.setdefault(code, set()).add(SYSTEM_SOURCES.get(h.source, h.source))
            continue
        preparers.setdefault(code, {})
        preparers[code][h.changed_by] = preparers[code].get(h.changed_by, 0) + 1
        if code not in last_edit or h.changed_at > last_edit[code]:
            last_edit[code] = h.changed_at
    ev_counts: dict[str, int] = {}
    for ev in session.exec(select(EvidenceFile).where(EvidenceFile.response_set_id == rs.id,
                                                      EvidenceFile.deleted_at.is_(None))).all():
        ev_counts[ev.section_code] = ev_counts.get(ev.section_code, 0) + 1
    ids = {uid for p in preparers.values() for uid in p}
    for c in eng.scope:
        s, i = secs[c], items.get(c)
        ids |= {s.submitted_by, s.internal_reviewed_by, s.decided_by}
        if i:
            ids |= {i.prepared_by, i.reviewed_by}
    names = brsr_names(session, ids)
    rows = []
    for c in eng.scope:
        s, i = secs[c], items.get(c)
        prep = sorted(preparers.get(c, {}).items(), key=lambda kv: -kv[1])
        rows.append({
            "section": c, "title": SECTION_TITLES.get(c, c),
            "prepared_by": [names.get(uid, f"user {uid}") for uid, _ in prep], "last_edited_at": last_edit.get(c),
            "system_sources": sorted(system.get(c, [])),
            "evidence_files": ev_counts.get(c, 0),
            "submitted_by": names.get(s.submitted_by), "submitted_at": s.submitted_at,
            "internal_reviewed_by": names.get(s.internal_reviewed_by), "internal_reviewed_at": s.internal_reviewed_at,
            "approved_by": names.get(s.decided_by) if s.status == "approved" else None,
            "approved_at": s.decided_at if s.status == "approved" else None,
            "section_status": s.status,
            "tested_by": names.get(i.prepared_by) if i else None, "tested_at": i.prepared_at if i else None,
            "workpaper_reviewed_by": names.get(i.reviewed_by) if i else None,
            "workpaper_reviewed_at": i.reviewed_at if i else None,
            "conclusion": i.conclusion if i else None, "workpaper_status": i.status if i else None,
        })
    return rows


def _request_out(session: Session, r: AssuranceRequest, names: dict[int, str]) -> dict:
    files = _request_files(session, r)
    fnames = brsr_names(session, [f.uploaded_by for f in files])
    return {
        "public_id": r.public_id, "section_code": r.section_code, "question_id": r.question_id, "title": r.title,
        "detail": r.detail, "due_date": r.due_date, "status": r.status,
        "overdue": bool(r.due_date and r.status == "open" and r.due_date < date.today()),
        "requested_by": names.get(r.requested_by), "requested_at": r.requested_at,
        "response_text": r.response_text, "responded_by": names.get(r.responded_by), "responded_at": r.responded_at,
        "closed_by": names.get(r.closed_by), "closed_at": r.closed_at,
        "history": [{**h, "by": names.get(h.get("by"), "assurer")} for h in r.history],
        "evidence": [ev_service.serialize(f, fnames) for f in files],
    }


def _finding_out(f: AssuranceFinding, names: dict[int, str]) -> dict:
    return {
        "public_id": f.public_id, "section_code": f.section_code, "question_id": f.question_id,
        "severity": f.severity, "description": f.description, "status": f.status,
        "raised_by": names.get(f.raised_by), "raised_at": f.raised_at,
        "management_response": f.management_response, "responded_by": names.get(f.responded_by),
        "responded_at": f.responded_at, "resolved_by": names.get(f.resolved_by), "resolved_at": f.resolved_at,
        "resolution_note": f.resolution_note,
    }


def detail(session: Session, actor: User, eng: AssuranceEngagement, access: str) -> dict:
    team = active_members(session, eng)
    items = session.exec(select(AssuranceItem).where(AssuranceItem.engagement_id == eng.id)).all()
    reqs = session.exec(select(AssuranceRequest).where(AssuranceRequest.engagement_id == eng.id)
                        .order_by(AssuranceRequest.requested_at)).all()
    finds = session.exec(select(AssuranceFinding).where(AssuranceFinding.engagement_id == eng.id)
                         .order_by(AssuranceFinding.raised_at)).all()
    ids = {eng.created_by, eng.signed_by, eng.eqr_cleared_by, eng.withdrawn_by} | {m.user_id for m in team} | \
        {i.prepared_by for i in items} | {i.reviewed_by for i in items} | \
        {x for r in reqs for x in (r.requested_by, r.responded_by, r.closed_by)} | \
        {h.get("by") for r in reqs for h in r.history} | \
        {x for f in finds for x in (f.raised_by, f.responded_by, f.resolved_by)}
    names = brsr_names(session, ids)
    company = session.get(Company, eng.company_id)
    assurer_view = access in ("member", "manager")
    me = member_of(session, eng, actor)

    secs = brsr.sections_of(session, _set_of(session, eng))

    def item_out(i: AssuranceItem) -> dict:
        out = {"section_code": i.section_code, "title": SECTION_TITLES.get(i.section_code), "status": i.status,
               "section_status": secs[i.section_code].status,
               "outdated": i.status == "reviewed" and item_outdated(i, secs[i.section_code])}
        if assurer_view or eng.status == "signed":
            out |= {"conclusion": i.conclusion, "prepared_by": names.get(i.prepared_by), "prepared_at": i.prepared_at,
                    "reviewed_by": names.get(i.reviewed_by), "reviewed_at": i.reviewed_at}
        if assurer_view:  # the assurer's working notes stay with the assurer
            out |= {"procedures": i.procedures, "review_note": i.review_note, "prepared_by_id": i.prepared_by}
        return out

    return {
        "public_id": eng.public_id, "company_id": eng.company_id, "company_name": company.name if company else None,
        "response_set_id": _set_of(session, eng).public_id, "financial_year": eng.financial_year,
        "level": eng.level, "level_label": LEVEL_LABEL[eng.level], "standard": eng.standard,
        "provider_name": eng.provider_name, "scope": eng.scope, "status": eng.status,
        "created_by": names.get(eng.created_by), "created_at": eng.created_at,
        "fieldwork_started_at": eng.fieldwork_started_at,
        "eqr_cleared_by": names.get(eng.eqr_cleared_by), "eqr_cleared_at": eng.eqr_cleared_at,
        "signed_by": names.get(eng.signed_by), "signed_at": eng.signed_at, "opinion": eng.opinion,
        "opinion_basis": eng.opinion_basis, "statement_text": eng.statement_text if eng.status == "signed" else None,
        "statement_sha256": eng.statement_sha256, "register_sha256": eng.register_sha256,
        "withdrawn_reason": eng.withdrawn_reason,
        "superseded_by": (session.get(AssuranceEngagement, eng.superseded_by_id).public_id
                          if eng.superseded_by_id else None),
        "reassurance_required": is_stale(session, eng),
        "access": access,
        "can_manage": _can_manage(session, actor, eng.company_id),
        "my_role": me.role if me else None,
        "my_independence_declared": bool(me and me.independence_declared_at),
        "team": [{"user_id": m.user_id, "name": names.get(m.user_id), "role": m.role,
                  "independence_declared_at": m.independence_declared_at, "added_at": m.added_at} for m in team],
        "items": [item_out(i) for i in sorted(items, key=lambda i: SECTION_CODES.index(i.section_code))],
        "requests": [_request_out(session, r, names) for r in reqs],
        "findings": [_finding_out(f, names) for f in finds],
        "register": eng.signed_register if eng.status == "signed" and eng.signed_register else register_rows(session, eng),
        "register_frozen": bool(eng.status == "signed" and eng.signed_register),
        "signoff_blockers": signoff_blockers(session, eng) if eng.status == "fieldwork" else [],
        "counts": {
            "workpapers_reviewed": sum(1 for i in items if i.status == "reviewed"), "workpapers": len(items),
            "requests_open": sum(1 for r in reqs if r.status == "open"),
            "requests_awaiting_assurer": sum(1 for r in reqs if r.status == "responded"),
            "findings_open": sum(1 for f in finds if f.status in ("open", "responded")),
            "uncorrected": sum(1 for f in finds if f.status == "uncorrected"),
        },
    }


def summary(session: Session, eng: AssuranceEngagement) -> dict:
    company = session.get(Company, eng.company_id)
    names = brsr_names(session, [eng.signed_by])
    reqs = session.exec(select(AssuranceRequest.status).where(AssuranceRequest.engagement_id == eng.id)).all()
    return {"public_id": eng.public_id, "company_id": eng.company_id,
            "company_name": company.name if company else None, "financial_year": eng.financial_year,
            "level": eng.level, "level_label": LEVEL_LABEL[eng.level], "provider_name": eng.provider_name,
            "status": eng.status, "opinion": eng.opinion, "signed_by": names.get(eng.signed_by),
            "signed_at": eng.signed_at, "requests_open": sum(1 for s in reqs if s == "open"),
            "reassurance_required": is_stale(session, eng), "created_at": eng.created_at}


def list_engagements(session: Session, actor: User) -> list[dict]:
    if not _has(session, actor, "assurance:read"):
        raise AppError("forbidden", "You cannot view assurance engagements", 403)
    out = []
    for eng in session.exec(select(AssuranceEngagement).order_by(AssuranceEngagement.created_at.desc())).all():
        if access_of(session, actor, eng):
            out.append(summary(session, eng))
    return out


def section_for_assurer(session: Session, actor: User, eng: AssuranceEngagement, access: str, code: str) -> dict:
    """Read-only BRSR section for the assurance team (they have no general
    access to the client's data — only through their engagement)."""
    if access not in ("member", "manager"):
        raise NotFoundError("Not found")
    if code not in eng.scope:
        raise NotFoundError(f"Section {code} is not in scope")
    out = brsr.section_detail(session, _set_of(session, eng), code)
    out["editable"] = False
    return out


def assurance_report_block(session: Session, rs: BrsrResponseSet) -> Optional[dict]:
    """What the BRSR report prints about assurance: only a signed engagement
    whose fingerprint still matches the data."""
    eng = signed_engagement_for(session, rs)
    if not eng or is_stale(session, eng):
        return None
    names = brsr_names(session, [eng.signed_by])
    return {"provider_name": eng.provider_name, "level": eng.level, "level_label": LEVEL_LABEL[eng.level],
            "standard": eng.standard, "opinion": eng.opinion, "opinion_basis": eng.opinion_basis,
            "signed_by": names.get(eng.signed_by), "signed_at": eng.signed_at.isoformat() if eng.signed_at else None,
            "content_hash": eng.signed_content_hash, "statement_sha256": eng.statement_sha256,
            "register": eng.signed_register or [
                {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
                for row in register_rows(session, eng)]}


def assurable_sets(session: Session, actor: User) -> list[dict]:
    """BRSRs in the actor's organisation that an engagement could cover —
    company, year and approval progress only, never the data itself."""
    if actor.portal_type == "client" or not _has(session, actor, "assurance:manage"):
        raise AppError("forbidden", "You cannot open assurance engagements", 403)
    rows = session.exec(select(BrsrResponseSet, Company).where(
        BrsrResponseSet.company_id == Company.id, Company.organization_id == actor.organization_id)
        .order_by(Company.name, BrsrResponseSet.financial_year.desc())).all()
    live = {e.response_set_id: e for e in session.exec(select(AssuranceEngagement).where(
        AssuranceEngagement.status.in_(ACTIVE_STATUSES))).all()}
    out = []
    for rs, company in rows:
        secs = brsr.sections_of(session, rs)
        approved = sum(1 for c in SECTION_CODES if secs[c].status == "approved")
        eng = live.get(rs.id)
        stale = bool(eng and is_stale(session, eng))
        out.append({"response_set_id": rs.public_id, "company_id": company.id, "company_name": company.name,
                    "financial_year": rs.financial_year, "sections_approved": approved,
                    "sections": len(SECTION_CODES),
                    "engagement": ({"public_id": eng.public_id, "status": eng.status, "needs_reassurance": stale}
                                   if eng else None)})
    return out
