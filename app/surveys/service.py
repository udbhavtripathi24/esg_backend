"""Surveys & assessments: lifecycle, audience, responding, files.

Lifecycle::

    draft ──publish──▶ open ──close──▶ closed ──archive──▶ archived
      │                 ▲    (or closes_at passes)  │
      └─ delete         └──────── reopen ◀──────────┘

``open`` with a future ``opens_at`` is shown as *scheduled*; ``open`` past
``closes_at`` is *closed* — no worker needed, the effective status is
computed on read.

Who may answer:
- a platform user with an invitation (individually or through their
  department), or any user of the company when ``open_to_company``;
- a person with a personal link (an e-mail invitation);
- anyone with the public link, when it is switched on.

Anonymity: for an anonymous survey the response row is stripped of every
identifying field on submission. The invitation keeps only "submitted", so
reminders skip that person, but nothing links it to the answers. Audit
entries for an anonymous submission name the survey, never the response.
"""
from __future__ import annotations

import copy
import hashlib
import io
import re
import secrets
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.brsr.evidence import ALLOWED_LABEL, ALLOWED_MIME, EXTENSIONS, detect_evidence_mime, display_filename
from app.brsr.service import resolve_company_id
from app.core.errors import AppError, NotFoundError
from app.core.tenancy import can_access_company
from app.models.survey import Survey, SurveyFile, SurveyInvite, SurveyResponse
from app.models.user import User
from app.services.audit import log_action
from app.services.notification_service import enqueue_notification
from app.storage import get_storage
from app.surveys import schema, templates

MAX_FILE_BYTES = 25 * 1024 * 1024
EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_INVITES_PER_CALL = 2000
SETTINGS_FIELDS = ("title", "description", "welcome", "thank_you", "category", "kind", "anonymous",
                   "open_to_company", "show_score_to_respondent", "opens_at", "closes_at")


def utcnow() -> datetime:
    return datetime.utcnow()


def naive_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def new_token() -> str:
    return secrets.token_urlsafe(24)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def effective_status(sv: Survey, now: Optional[datetime] = None) -> str:
    now = now or utcnow()
    if sv.is_template:
        return "template"
    if sv.status != "open":
        return sv.status
    if sv.opens_at and now < sv.opens_at:
        return "scheduled"
    if sv.closes_at and now >= sv.closes_at:
        return "closed"
    return "open"


def accepting(sv: Survey) -> bool:
    return effective_status(sv) == "open"


def _ensure_accepting(sv: Survey) -> None:
    st = effective_status(sv)
    if st == "scheduled":
        raise AppError("survey_not_open", f"This survey opens on {sv.opens_at:%d %b %Y, %H:%M} UTC.", 409)
    if st != "open":
        raise AppError("survey_closed", "This survey is closed and no longer accepts responses.", 409)


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------

def _by_public_id(session: Session, public_id: str) -> Survey:
    sv = session.exec(select(Survey).where(Survey.public_id == public_id)).first()
    if not sv:
        raise NotFoundError("Survey not found")
    return sv


def get_managed(session: Session, actor: User, public_id: str) -> Survey:
    """A survey or company template the actor may manage (route checks assessment:manage)."""
    sv = _by_public_id(session, public_id)
    if sv.company_id is None:
        raise NotFoundError("Survey not found")
    if actor.portal_type == "client":
        if sv.company_id != actor.company_id:
            raise NotFoundError("Survey not found")
    elif not can_access_company(session, actor, sv.company_id):
        raise NotFoundError("Survey not found")
    return sv


def _ensure_editable(sv: Survey) -> None:
    if sv.status == "archived":
        raise AppError("survey_archived", "This survey is archived. Restore it to make changes.", 409)


def response_count(session: Session, sv: Survey, status: Optional[str] = None) -> int:
    stmt = select(func.count()).select_from(SurveyResponse).where(SurveyResponse.survey_id == sv.id)
    if status:
        stmt = stmt.where(SurveyResponse.status == status)
    return session.exec(stmt).one()


def has_answers(session: Session, sv: Survey) -> bool:
    return response_count(session, sv) > 0


# ---------------------------------------------------------------------------
# Create / edit
# ---------------------------------------------------------------------------

def _clean_text(v: Any, limit: int) -> Optional[str]:
    s = ("" if v is None else str(v)).strip()[:limit]
    return s or None


def _apply_settings(sv: Survey, data: dict) -> None:
    if "title" in data:
        title = _clean_text(data["title"], 200)
        if not title:
            raise AppError("title_required", "Give the survey a title.", 422, "title")
        sv.title = title
    for f, limit in (("description", 2000), ("welcome", 4000), ("thank_you", 2000)):
        if f in data:
            setattr(sv, f, _clean_text(data[f], limit))
    if "category" in data:
        sv.category = _clean_text(data["category"], 60) or "Custom"
    if "kind" in data:
        if data["kind"] not in ("survey", "assessment"):
            raise AppError("invalid_kind", "kind must be 'survey' or 'assessment'", 422, "kind")
        sv.kind = data["kind"]
    for f in ("anonymous", "open_to_company", "show_score_to_respondent"):
        if f in data and data[f] is not None:
            setattr(sv, f, bool(data[f]))
    for f in ("opens_at", "closes_at"):
        if f in data:
            setattr(sv, f, naive_utc(data[f]))
    if sv.opens_at and sv.closes_at and sv.closes_at <= sv.opens_at:
        raise AppError("invalid_schedule", "The closing date must be after the opening date.", 422, "closes_at")


def create_survey(session: Session, actor: User, company_id: Optional[int], data: dict,
                  ip_address: Optional[str] = None) -> Survey:
    company_id = resolve_company_id(session, actor, company_id)
    source = data.get("template_id")
    defn: dict = {"sections": [{"id": "s1", "title": "Section 1", "description": "", "questions": []}], "bands": []}
    base: dict[str, Any] = {"kind": "survey", "category": "Custom"}
    if source:
        if source.startswith("tpl_"):
            t = templates.built_in(source)
            if not t:
                raise NotFoundError("Template not found")
            defn = t["definition"]
            base = {k: t.get(k) for k in ("kind", "category", "title", "description", "welcome", "thank_you",
                                         "anonymous")}
        else:
            src = get_managed(session, actor, source)
            if src.company_id != company_id:
                raise NotFoundError("Template not found")
            defn = copy.deepcopy(src.definition)
            base = {k: getattr(src, k) for k in ("kind", "category", "title", "description", "welcome", "thank_you",
                                                "anonymous", "open_to_company", "show_score_to_respondent")}
    from app.models.company import Company
    company = session.get(Company, company_id)
    sv = Survey(company_id=company_id, organization_id=company.organization_id if company else None,
                title=base.get("title") or "Untitled survey", definition=schema.normalize_definition(defn),
                created_by=actor.id)
    _apply_settings(sv, {**{k: v for k, v in base.items() if v is not None},
                         **{k: v for k, v in data.items() if k in SETTINGS_FIELDS and v is not None}})
    session.add(sv)
    session.flush()
    log_action(session, actor, "survey.created", "survey", sv.id, sv.public_id, company_id=company_id,
               changes={"title": sv.title, "kind": sv.kind, "from": source}, ip_address=ip_address)
    session.commit()
    session.refresh(sv)
    return sv


def update_survey(session: Session, actor: User, sv: Survey, data: dict, ip_address: Optional[str] = None) -> Survey:
    _ensure_editable(sv)
    rev = data.get("revision")
    if rev is not None and rev != sv.revision:
        raise AppError("survey_conflict", "Someone else changed this survey since you opened it. "
                                          "Reload to see their changes.", 409,
                       details={"revision": sv.revision, "updated_at": sv.updated_at.isoformat()})
    answered = has_answers(session, sv)
    changes: dict[str, Any] = {}
    if "anonymous" in data and data["anonymous"] is not None and bool(data["anonymous"]) != sv.anonymous and answered:
        raise AppError("anonymity_locked", "Anonymity can't change once people have started answering — "
                                           "they answered under the original promise.", 409, "anonymous")
    old_kind = sv.kind
    before = {f: getattr(sv, f) for f in SETTINGS_FIELDS}
    _apply_settings(sv, {k: v for k, v in data.items() if k in SETTINGS_FIELDS})
    for f in SETTINGS_FIELDS:
        if getattr(sv, f) != before[f]:
            changes[f] = getattr(sv, f).isoformat() if isinstance(getattr(sv, f), datetime) else getattr(sv, f)
    rescore = sv.kind != old_kind
    if "definition" in data and data["definition"] is not None:
        new = schema.normalize_definition(data["definition"])
        if answered:
            blocked = schema.locked_changes(sv.definition, new)
            if blocked:
                raise AppError("survey_structure_locked",
                               "This survey already has responses, so existing questions, options, rows and columns "
                               "can't be removed or change type. You can still reword them, add new ones, and change "
                               "logic and scoring.", 409, "definition", details={"problems": blocked})
        if new != sv.definition:
            changes["definition"] = {"hash": schema.definition_hash(new),
                                     "questions": len(schema.answerable(new))}
            rescore = True
            sv.definition = new
    if not changes:
        return sv
    sv.revision += 1
    sv.updated_at = utcnow()
    if rescore and answered:
        changes["rescored"] = rescore_all(session, sv)
    session.add(sv)
    log_action(session, actor, "survey.updated", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes=changes, ip_address=ip_address)
    session.commit()
    session.refresh(sv)
    return sv


def rescore_all(session: Session, sv: Survey) -> int:
    n = 0
    for r in session.exec(select(SurveyResponse).where(SurveyResponse.survey_id == sv.id,
                                                       SurveyResponse.status == "submitted")).all():
        r.score = schema.score_response(sv.definition, r.answers) if sv.kind == "assessment" else None
        session.add(r)
        n += 1
    return n


def publish(session: Session, actor: User, sv: Survey, ip_address: Optional[str] = None) -> Survey:
    if sv.is_template:
        raise AppError("is_template", "Templates can't be published. Create a survey from it.", 409)
    if sv.status != "draft":
        raise AppError("invalid_status", "Only a draft can be published.", 409)
    problems = schema.publish_problems(sv.definition, sv.kind)
    if not sv.title.strip():
        problems.insert(0, {"message": "Give the survey a title.", "section": None, "question": None})
    if problems:
        raise AppError("survey_not_ready", f"{len(problems)} thing{'s' if len(problems) != 1 else ''} to fix before "
                                           "publishing", 422, details={"problems": problems})
    now = utcnow()
    if sv.closes_at and sv.closes_at <= now:
        raise AppError("invalid_schedule", "The closing date is in the past.", 422, "closes_at")
    sv.status = "open"
    sv.published_at = now
    sv.updated_at = now
    sv.revision += 1
    session.add(sv)
    _notify_audience(session, sv, _audience_user_ids(session, sv), "survey_invited")
    log_action(session, actor, "survey.published", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               ip_address=ip_address)
    session.commit()
    session.refresh(sv)
    return sv


def set_status(session: Session, actor: User, sv: Survey, action: str, ip_address: Optional[str] = None) -> Survey:
    st = effective_status(sv)
    now = utcnow()
    if action == "close":
        if st not in ("open", "scheduled"):
            raise AppError("invalid_status", "Only an open survey can be closed.", 409)
        sv.status = "closed"
        sv.closed_at = now
        if sv.closes_at is None or sv.closes_at > now:
            sv.closes_at = now
    elif action == "reopen":
        if sv.status not in ("closed", "open") or st != "closed":
            raise AppError("invalid_status", "Only a closed survey can be reopened.", 409)
        sv.status = "open"
        sv.closed_at = None
        if sv.closes_at and sv.closes_at <= now:
            sv.closes_at = None
    elif action == "archive":
        if sv.status == "archived":
            return sv
        if sv.status == "draft" and not has_answers(session, sv):
            raise AppError("invalid_status", "Delete a draft instead of archiving it.", 409)
        if st in ("open", "scheduled"):
            sv.closes_at = sv.closes_at if sv.closes_at and sv.closes_at <= now else now
        sv.status = "archived"
        sv.archived_at = now
    elif action == "restore":
        if sv.status != "archived":
            raise AppError("invalid_status", "Only an archived survey can be restored.", 409)
        sv.status = "closed" if sv.published_at else "draft"
        sv.archived_at = None
    else:
        raise AppError("invalid_action", f"Unknown action {action}", 422)
    sv.updated_at = now
    sv.revision += 1
    session.add(sv)
    log_action(session, actor, f"survey.{action}", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               ip_address=ip_address)
    session.commit()
    session.refresh(sv)
    return sv


def delete_survey(session: Session, actor: User, sv: Survey, ip_address: Optional[str] = None) -> None:
    if has_answers(session, sv):
        raise AppError("survey_has_responses", "This survey has responses. Archive it instead — responses are "
                                               "kept for the record.", 409)
    if not sv.is_template and sv.status != "draft" and sv.status != "archived":
        raise AppError("invalid_status", "Close the survey before deleting it.", 409)
    for inv in session.exec(select(SurveyInvite).where(SurveyInvite.survey_id == sv.id)).all():
        session.delete(inv)
    log_action(session, actor, "survey.deleted", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"title": sv.title, "template": sv.is_template}, ip_address=ip_address)
    session.delete(sv)
    session.commit()


def duplicate(session: Session, actor: User, sv: Survey, *, as_template: bool = False, title: Optional[str] = None,
              ip_address: Optional[str] = None) -> Survey:
    copy_ = Survey(company_id=sv.company_id, organization_id=sv.organization_id, is_template=as_template,
                   kind=sv.kind, category=sv.category,
                   title=_clean_text(title, 200) or (sv.title if as_template else f"Copy of {sv.title}")[:200],
                   description=sv.description, welcome=sv.welcome, thank_you=sv.thank_you,
                   definition=copy.deepcopy(sv.definition), anonymous=sv.anonymous,
                   open_to_company=False if as_template else sv.open_to_company,
                   show_score_to_respondent=sv.show_score_to_respondent, created_by=actor.id)
    session.add(copy_)
    session.flush()
    log_action(session, actor, "survey.saved_as_template" if as_template else "survey.duplicated", "survey",
               copy_.id, copy_.public_id, company_id=sv.company_id, changes={"from": sv.public_id},
               ip_address=ip_address)
    session.commit()
    session.refresh(copy_)
    return copy_


def set_public_link(session: Session, actor: User, sv: Survey, enabled: bool, regenerate: bool = False,
                    ip_address: Optional[str] = None) -> Survey:
    _ensure_editable(sv)
    if sv.is_template:
        raise AppError("is_template", "Templates have no links.", 409)
    if enabled and (regenerate or not sv.public_token):
        sv.public_token = new_token()
    elif not enabled:
        sv.public_token = None
    sv.updated_at = utcnow()
    session.add(sv)
    log_action(session, actor, "survey.public_link", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"enabled": enabled, "regenerated": regenerate}, ip_address=ip_address)
    session.commit()
    session.refresh(sv)
    return sv


# ---------------------------------------------------------------------------
# Audience
# ---------------------------------------------------------------------------

def company_users(session: Session, company_id: int) -> list[User]:
    return session.exec(select(User).where(User.company_id == company_id, User.portal_type == "client",
                                           User.is_active == True, User.deleted_at.is_(None))  # noqa: E712
                        .order_by(User.name)).all()


def audience_options(session: Session, sv: Survey) -> dict:
    users = company_users(session, sv.company_id)
    depts: dict[str, int] = {}
    for u in users:
        if u.department:
            depts[u.department] = depts.get(u.department, 0) + 1
    return {"users": [{"id": u.id, "name": u.name, "email": u.email, "department": u.department} for u in users],
            "departments": [{"name": k, "users": v} for k, v in sorted(depts.items())]}


def _audience_user_ids(session: Session, sv: Survey) -> list[int]:
    if sv.open_to_company:
        ids = {u.id for u in company_users(session, sv.company_id)}
    else:
        ids = set()
    ids |= {i.user_id for i in session.exec(select(SurveyInvite).where(
        SurveyInvite.survey_id == sv.id, SurveyInvite.channel == "user", SurveyInvite.status != "submitted")).all()
        if i.user_id}
    return sorted(ids)


def _notify_audience(session: Session, sv: Survey, user_ids: list[int], event: str) -> None:
    if not user_ids:
        return
    due = f" Please respond by {sv.closes_at:%d %b %Y}." if sv.closes_at else ""
    title = (f"Reminder: {sv.title}" if event == "survey_reminder" else f"New {sv.kind}: {sv.title}")
    body = ("Your response is still open." if event == "survey_reminder" else
            "You've been asked to respond.") + due + (" Your answers are anonymous." if sv.anonymous else "")
    enqueue_notification(session, event_type=event, recipient_user_ids=user_ids, title=title[:200], body=body,
                         entity_type="survey", entity_id=sv.id, entity_public_id=sv.public_id)


def add_invites(session: Session, actor: User, sv: Survey, *, user_ids: Iterable[int] = (),
                departments: Iterable[str] = (), emails: Iterable[dict] = (),
                ip_address: Optional[str] = None) -> dict:
    """Invite people. Returns the new personal links (tokens) — shown once."""
    _ensure_editable(sv)
    if sv.is_template:
        raise AppError("is_template", "Templates have no audience.", 409)
    if effective_status(sv) == "closed":
        raise AppError("survey_closed", "Reopen the survey before inviting more people.", 409)
    users = {u.id: u for u in company_users(session, sv.company_id)}
    wanted: dict[int, User] = {}
    for uid in user_ids or ():
        if uid not in users:
            raise AppError("invalid_user", "Only active users of this company can be invited.", 422, "user_ids")
        wanted[uid] = users[uid]
    for d in departments or ():
        members = [u for u in users.values() if (u.department or "").strip().lower() == str(d).strip().lower()]
        if not members:
            raise AppError("invalid_department", f"No active users in department '{d}'.", 422, "departments")
        for u in members:
            wanted[u.id] = u
    existing = session.exec(select(SurveyInvite).where(SurveyInvite.survey_id == sv.id)).all()
    have_users = {i.user_id for i in existing if i.user_id}
    have_emails = {i.email for i in existing if i.email}

    clean_emails: dict[str, dict] = {}
    bad: list[str] = []
    for e in emails or ():
        addr = str((e or {}).get("email") or "").strip().lower()
        if not EMAIL_RE.match(addr) or len(addr) > 254:
            bad.append(addr or "(blank)")
            continue
        clean_emails.setdefault(addr, {"name": _clean_text(e.get("name"), 120),
                                       "group": _clean_text(e.get("group"), 120)})
    if bad:
        raise AppError("invalid_email", f"{len(bad)} e-mail address{'es' if len(bad) != 1 else ''} "
                                        f"{'are' if len(bad) != 1 else 'is'} not valid: {', '.join(bad[:5])}",
                       422, "emails", details={"invalid": bad})
    if len(wanted) + len(clean_emails) > MAX_INVITES_PER_CALL:
        raise AppError("too_many", f"Invite at most {MAX_INVITES_PER_CALL} people at a time.", 422)

    added_users = []
    for uid, u in wanted.items():
        if uid in have_users:
            continue
        inv = SurveyInvite(survey_id=sv.id, channel="user", user_id=uid, name=u.name, email=None,
                           group_label=u.department, invited_by=actor.id)
        session.add(inv)
        added_users.append(uid)
    links = []
    for addr, meta in clean_emails.items():
        if addr in have_emails:
            continue
        token = new_token()
        inv = SurveyInvite(survey_id=sv.id, channel="email", email=addr, name=meta["name"], group_label=meta["group"],
                           token_hash=token_hash(token), invited_by=actor.id)
        session.add(inv)
        session.flush()
        links.append({"invite_id": inv.public_id, "email": addr, "name": meta["name"], "group": meta["group"],
                      "token": token})
    skipped = (len(wanted) - len(added_users)) + (len(clean_emails) - len(links))
    if accepting(sv) and added_users:
        _notify_audience(session, sv, added_users, "survey_invited")
    log_action(session, actor, "survey.invited", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"users": len(added_users), "emails": len(links), "skipped": skipped,
                        "departments": list(departments or [])}, ip_address=ip_address)
    session.commit()
    return {"added": len(added_users) + len(links), "skipped": skipped, "links": links}


def get_invite(session: Session, sv: Survey, invite_public_id: str) -> SurveyInvite:
    inv = session.exec(select(SurveyInvite).where(SurveyInvite.public_id == invite_public_id,
                                                  SurveyInvite.survey_id == sv.id)).first()
    if not inv:
        raise NotFoundError("Invitation not found")
    return inv


def remove_invite(session: Session, actor: User, sv: Survey, inv: SurveyInvite, ip_address: Optional[str] = None):
    if inv.status != "pending":
        raise AppError("invite_in_use", "This person has already started answering, so the invitation stays.", 409)
    session.delete(inv)
    log_action(session, actor, "survey.invite_removed", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"invite": inv.public_id}, ip_address=ip_address)
    session.commit()


def new_links(session: Session, actor: User, sv: Survey, invite_ids: Optional[list[str]] = None,
              ip_address: Optional[str] = None) -> list[dict]:
    """New personal links for e-mail invitations that haven't been submitted.
    Earlier links for those people stop working; a half-finished response is
    kept (it belongs to the invitation, not the link)."""
    _ensure_editable(sv)
    stmt = select(SurveyInvite).where(SurveyInvite.survey_id == sv.id, SurveyInvite.channel == "email",
                                      SurveyInvite.status != "submitted")
    rows = session.exec(stmt).all()
    if invite_ids is not None:
        wanted = set(invite_ids)
        rows = [r for r in rows if r.public_id in wanted]
        if len(rows) != len(wanted):
            raise AppError("invalid_invite", "Links can only be renewed for e-mail invitations that haven't been "
                                             "submitted.", 422)
    out = []
    for inv in rows:
        token = new_token()
        inv.token_hash = token_hash(token)
        session.add(inv)
        out.append({"invite_id": inv.public_id, "email": inv.email, "name": inv.name, "group": inv.group_label,
                    "token": token, "status": inv.status})
    log_action(session, actor, "survey.links_renewed", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"count": len(out)}, ip_address=ip_address)
    session.commit()
    return out


def remind(session: Session, actor: User, sv: Survey, ip_address: Optional[str] = None) -> dict:
    if not accepting(sv):
        raise AppError("survey_closed", "Reminders can only be sent while the survey is open.", 409)
    now = utcnow()
    invs = session.exec(select(SurveyInvite).where(SurveyInvite.survey_id == sv.id,
                                                   SurveyInvite.status != "submitted")).all()
    users = [i for i in invs if i.channel == "user" and i.user_id]
    for i in users:
        i.last_reminded_at = now
        session.add(i)
    ids = {i.user_id for i in users}
    if sv.open_to_company:
        done = {i.user_id for i in session.exec(select(SurveyInvite).where(
            SurveyInvite.survey_id == sv.id, SurveyInvite.status == "submitted")).all() if i.user_id}
        ids |= {u.id for u in company_users(session, sv.company_id)} - done
    _notify_audience(session, sv, sorted(ids), "survey_reminder")
    log_action(session, actor, "survey.reminded", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"users": len(ids)}, ip_address=ip_address)
    session.commit()
    return {"reminded": len(ids), "email_not_submitted": sum(1 for i in invs if i.channel == "email")}


def invite_rows(session: Session, sv: Survey) -> list[dict]:
    invs = session.exec(select(SurveyInvite).where(SurveyInvite.survey_id == sv.id)
                        .order_by(SurveyInvite.invited_at, SurveyInvite.id)).all()
    return [{"id": i.public_id, "channel": i.channel, "user_id": i.user_id, "email": i.email, "name": i.name,
             "group": i.group_label, "status": i.status, "invited_at": i.invited_at, "started_at": i.started_at,
             "submitted_at": i.submitted_at, "last_reminded_at": i.last_reminded_at} for i in invs]


def audience_counts(session: Session, sv: Survey) -> dict:
    rows = session.exec(select(SurveyInvite.status, SurveyInvite.channel, func.count())
                        .where(SurveyInvite.survey_id == sv.id)
                        .group_by(SurveyInvite.status, SurveyInvite.channel)).all()
    by_status = {"pending": 0, "started": 0, "submitted": 0}
    by_channel = {"user": 0, "email": 0}
    for st, ch, n in rows:
        by_status[st] = by_status.get(st, 0) + n
        by_channel[ch] = by_channel.get(ch, 0) + n
    invited = sum(by_status.values())
    submitted = response_count(session, sv, "submitted")
    in_progress = response_count(session, sv, "in_progress")
    eligible = invited
    if sv.open_to_company:
        eligible = max(invited, len(company_users(session, sv.company_id)) + by_channel["email"])
    return {"invited": invited, **by_status, "users": by_channel["user"], "emails": by_channel["email"],
            "eligible": eligible, "responses": submitted, "in_progress": in_progress,
            "response_rate": round(100 * by_status["submitted"] / eligible) if eligible else None}


# ---------------------------------------------------------------------------
# Responding
# ---------------------------------------------------------------------------

class Respondent:
    """Who is answering: a signed-in user, an e-mail invitation, or the public link."""

    def __init__(self, *, user: Optional[User] = None, invite: Optional[SurveyInvite] = None,
                 resume_key: Optional[str] = None, channel: str = "user"):
        self.user = user
        self.invite = invite
        self.resume_key = resume_key
        self.channel = channel


def eligible_user(session: Session, sv: Survey, user: User) -> Optional[SurveyInvite]:
    """The user's invitation (created on first use when the survey is open to the
    whole company). Raises 404 when the user isn't in the audience."""
    if sv.is_template or sv.status in ("draft", "archived"):
        raise NotFoundError("Survey not found")
    inv = session.exec(select(SurveyInvite).where(SurveyInvite.survey_id == sv.id,
                                                  SurveyInvite.user_id == user.id)).first()
    if inv:
        return inv
    if sv.open_to_company and user.portal_type == "client" and user.company_id == sv.company_id:
        return None
    raise NotFoundError("Survey not found")


def _invite_for(session: Session, sv: Survey, user: User) -> SurveyInvite:
    inv = eligible_user(session, sv, user)
    if inv is None:
        inv = SurveyInvite(survey_id=sv.id, channel="user", user_id=user.id, name=user.name,
                           group_label=user.department, invited_by=sv.created_by)
        session.add(inv)
        session.flush()
    return inv


def current_response(session: Session, sv: Survey, who: Respondent) -> Optional[SurveyResponse]:
    """The respondent's response: in progress, or their submitted one when it
    is still linked to them (never for anonymous surveys)."""
    if who.invite is not None and who.invite.id is not None:
        return session.exec(select(SurveyResponse).where(SurveyResponse.survey_id == sv.id,
                                                         SurveyResponse.invite_id == who.invite.id)
                            .order_by(SurveyResponse.id.desc())).first()
    if who.resume_key:
        return session.exec(select(SurveyResponse).where(SurveyResponse.survey_id == sv.id,
                                                         SurveyResponse.resume_key_hash == token_hash(who.resume_key),
                                                         SurveyResponse.status == "in_progress")).first()
    return None


def _start(session: Session, sv: Survey, who: Respondent) -> SurveyResponse:
    resp = SurveyResponse(survey_id=sv.id, channel=who.channel, answers={},
                          invite_id=who.invite.id if who.invite else None,
                          user_id=who.user.id if who.user else None,
                          respondent_name=(who.invite.name or who.invite.email) if who.invite else None,
                          group_label=who.invite.group_label if who.invite else None)
    if who.channel == "public":
        who.resume_key = who.resume_key or new_token()
        resp.resume_key_hash = token_hash(who.resume_key)
    session.add(resp)
    if who.invite and who.invite.status == "pending":
        who.invite.status = "started"
        who.invite.started_at = utcnow()
        session.add(who.invite)
    session.flush()
    return resp


def _check_files(session: Session, sv: Survey, resp: SurveyResponse, answers: dict) -> None:
    errors = {}
    for qid, q in schema.questions_by_id(sv.definition).items():
        if q["type"] != "file" or not answers.get(qid):
            continue
        ids = answers[qid]
        found = session.exec(select(SurveyFile.public_id).where(
            SurveyFile.response_id == resp.id, SurveyFile.question_id == qid,
            SurveyFile.public_id.in_(ids), SurveyFile.deleted_at.is_(None))).all()
        if set(found) != set(ids):
            errors[qid] = "A file is missing — upload it again"
    if errors:
        raise schema.AnswerError(errors)


def save_draft(session: Session, sv: Survey, who: Respondent, answers: Any, revision: Optional[int] = None) -> dict:
    _ensure_accepting(sv)
    resp = current_response(session, sv, who)
    if resp and resp.status == "submitted":
        raise AppError("already_submitted", "You've already submitted this survey.", 409)
    if resp and revision is not None and revision != resp.revision:
        raise AppError("response_conflict", "These answers were changed in another window. Reload to continue.", 409,
                       details={"revision": resp.revision})
    clean = schema.clean_answers(sv.definition, answers, final=False)
    if resp is None:
        resp = _start(session, sv, who)
    _check_files(session, sv, resp, clean)
    resp.answers = clean
    resp.revision += 1
    resp.updated_at = utcnow()
    session.add(resp)
    session.commit()
    session.refresh(resp)
    return response_state(sv, resp, who)


def submit(session: Session, sv: Survey, who: Respondent, answers: Any, *, respondent_name: Optional[str] = None,
           organisation: Optional[str] = None, ip_address: Optional[str] = None) -> dict:
    _ensure_accepting(sv)
    resp = current_response(session, sv, who)
    if resp and resp.status == "submitted":
        raise AppError("already_submitted", "You've already submitted this survey.", 409)
    if who.invite and who.invite.status == "submitted":
        raise AppError("already_submitted", "You've already submitted this survey.", 409)
    clean = schema.clean_answers(sv.definition, answers, final=True)
    if resp is None:
        resp = _start(session, sv, who)
    _check_files(session, sv, resp, clean)
    # Files given for questions that logic later hid are withdrawn with them
    keep = {fid for qid, q in schema.questions_by_id(sv.definition).items() if q["type"] == "file"
            for fid in clean.get(qid, [])}
    for f in session.exec(select(SurveyFile).where(SurveyFile.response_id == resp.id,
                                                   SurveyFile.deleted_at.is_(None))).all():
        if f.public_id not in keep:
            f.deleted_at = utcnow()
            session.add(f)
    now = utcnow()
    score = schema.score_response(sv.definition, clean) if sv.kind == "assessment" else None
    if sv.anonymous:
        resp = _anonymise(session, sv, resp, clean, score, now)
    else:
        resp.answers = clean
        resp.score = score
        resp.status = "submitted"
        resp.submitted_at = now
        resp.updated_at = now
        resp.revision += 1
        resp.resume_key_hash = None
        if who.channel == "public":
            resp.respondent_name = _clean_text(respondent_name, 120)
            resp.group_label = _clean_text(organisation, 120)
    if who.invite:
        who.invite.status = "submitted"
        # For an anonymous survey the time is not kept: it would match the response
        who.invite.submitted_at = None if sv.anonymous else now
        session.add(who.invite)
    session.add(resp)
    if sv.anonymous:
        # No actor and no response id: the audit trail must not re-link people to answers
        log_action(session, None, "survey.response_submitted", "survey", sv.id, sv.public_id,
                   company_id=sv.company_id, changes={"anonymous": True})
    else:
        log_action(session, who.user, "survey.response_submitted", "survey_response", resp.id, resp.public_id,
                   company_id=sv.company_id, changes={"survey": sv.public_id, "channel": who.channel},
                   ip_address=ip_address)
    session.commit()
    session.refresh(resp)
    out = {"submitted": True, "submitted_at": resp.submitted_at, "thank_you": sv.thank_you}
    if sv.show_score_to_respondent and resp.score:
        out["score"] = _respondent_score(sv, resp.score)
    return out


def _anonymise(session: Session, sv: Survey, draft: SurveyResponse, answers: dict, score: Optional[dict],
               now: datetime) -> SurveyResponse:
    """Replace the draft with a new, unlinked row. A new row (new id, new
    public id) means neither the draft's creation order nor its ids tie the
    answers to the invitation; times are kept to the day only."""
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    final = SurveyResponse(survey_id=sv.id, channel=draft.channel, group_label=draft.group_label, status="submitted",
                           answers=answers, score=score, started_at=day, updated_at=day, submitted_at=day)
    session.add(final)
    session.flush()
    for f in session.exec(select(SurveyFile).where(SurveyFile.response_id == draft.id)).all():
        f.response_id = final.id
        f.uploaded_at = day
        session.add(f)
    session.flush()
    session.delete(draft)
    return final


def _respondent_score(sv: Survey, score: dict) -> dict:
    band = schema.band_for(sv.definition, score.get("percent"))
    return {"percent": score.get("percent"), "band": band["label"] if band else None,
            "band_description": band.get("description") if band else None,
            "sections": [{"title": s["title"], "percent": score["sections"][s["id"]]["percent"]}
                         for s in sv.definition["sections"] if s["id"] in score.get("sections", {})]}


def response_state(sv: Survey, resp: Optional[SurveyResponse], who: Respondent) -> dict:
    if resp is None:
        return {"status": "not_started", "answers": {}, "revision": None, "progress": schema.progress(sv.definition, {}),
                "resume_key": who.resume_key}
    out = {"status": resp.status, "answers": resp.answers if resp.status == "in_progress" or not sv.anonymous else {},
           "revision": resp.revision, "progress": schema.progress(sv.definition, resp.answers),
           "updated_at": resp.updated_at, "submitted_at": resp.submitted_at,
           "resume_key": who.resume_key if resp.status == "in_progress" else None}
    if resp.status == "submitted" and sv.show_score_to_respondent and resp.score:
        out["score"] = _respondent_score(sv, resp.score)
    return out


def form_payload(session: Session, sv: Survey, who: Respondent) -> dict:
    """What the respondent's form needs: the survey (without scores), their
    answers so far, and any files they've attached."""
    resp = current_response(session, sv, who)
    if sv.anonymous and who.invite and who.invite.status == "submitted":
        state = {"status": "submitted", "answers": {}, "revision": None, "progress": None,
                 "submitted_at": who.invite.submitted_at}
    else:
        state = response_state(sv, resp, who)
    files = []
    if resp is not None and resp.status == "in_progress":
        files = [file_json(f) for f in session.exec(select(SurveyFile).where(
            SurveyFile.response_id == resp.id, SurveyFile.deleted_at.is_(None))).all()]
    from app.models.company import Company
    company = session.get(Company, sv.company_id)
    return {"survey": {"id": sv.public_id, "title": sv.title, "description": sv.description, "welcome": sv.welcome,
                       "thank_you": sv.thank_you, "kind": sv.kind, "anonymous": sv.anonymous,
                       "closes_at": sv.closes_at, "opens_at": sv.opens_at, "status": effective_status(sv),
                       "company": company.name if company else None,
                       "definition": schema.respondent_view(sv.definition),
                       "minutes": templates.estimated_minutes(sv.definition),
                       "ask_identity": who.channel == "public" and not sv.anonymous},
            "response": state, "files": files,
            "respondent": {"name": who.invite.name if who.invite else (who.user.name if who.user else None),
                           "email": who.invite.email if who.invite else None}}


# ---------------------------------------------------------------------------
# In-app respondents
# ---------------------------------------------------------------------------

def my_surveys(session: Session, user: User) -> list[dict]:
    invs = {i.survey_id: i for i in session.exec(select(SurveyInvite).where(SurveyInvite.user_id == user.id)).all()}
    ids = set(invs)
    if user.portal_type == "client" and user.company_id:
        ids |= set(session.exec(select(Survey.id).where(Survey.company_id == user.company_id,
                                                         Survey.open_to_company == True,  # noqa: E712
                                                         Survey.is_template == False)).all())  # noqa: E712
    if not ids:
        return []
    out = []
    for sv in session.exec(select(Survey).where(Survey.id.in_(ids), Survey.status.in_(("open", "closed")))).all():
        st = effective_status(sv)
        inv = invs.get(sv.id)
        mine = "submitted" if inv and inv.status == "submitted" else ("in_progress" if inv and inv.status == "started"
                                                                       else "not_started")
        if st != "open" and mine == "not_started":
            continue  # nothing to do and nothing to look back at
        progress = None
        if mine == "in_progress":
            resp = session.exec(select(SurveyResponse).where(SurveyResponse.invite_id == inv.id,
                                                             SurveyResponse.status == "in_progress")).first()
            progress = schema.progress(sv.definition, resp.answers)["percent"] if resp else 0
        out.append({"id": sv.public_id, "title": sv.title, "description": sv.description, "kind": sv.kind,
                    "category": sv.category, "anonymous": sv.anonymous, "status": st, "my_status": mine,
                    "progress": progress, "opens_at": sv.opens_at, "closes_at": sv.closes_at,
                    "submitted_at": inv.submitted_at if inv else None,
                    "minutes": templates.estimated_minutes(sv.definition)})
    order = {"in_progress": 0, "not_started": 1, "submitted": 2}
    out.sort(key=lambda r: (r["status"] != "open", order[r["my_status"]],
                            r["closes_at"] or datetime.max))
    return out


def user_respondent(session: Session, sv: Survey, user: User, *, create: bool) -> Respondent:
    inv = _invite_for(session, sv, user) if create else eligible_user(session, sv, user)
    return Respondent(user=user, invite=inv, channel="user")


# ---------------------------------------------------------------------------
# Link respondents (no account)
# ---------------------------------------------------------------------------

def by_link(session: Session, token: str) -> tuple[Survey, Respondent]:
    if not token or len(token) > 100:
        raise NotFoundError("This link isn't valid.")
    inv = session.exec(select(SurveyInvite).where(SurveyInvite.token_hash == token_hash(token))).first()
    if inv:
        sv = session.get(Survey, inv.survey_id)
        who = Respondent(invite=inv, channel="email")
    else:
        sv = session.exec(select(Survey).where(Survey.public_token == token)).first()
        who = Respondent(channel="public")
    if not sv or sv.is_template or sv.status in ("draft", "archived"):
        raise NotFoundError("This link isn't valid.")
    return sv, who


# ---------------------------------------------------------------------------
# Files
# ---------------------------------------------------------------------------

def file_json(f: SurveyFile) -> dict:
    return {"id": f.public_id, "question_id": f.question_id, "filename": f.original_filename,
            "mime_type": f.mime_type, "kind": ALLOWED_MIME.get(f.mime_type), "size_bytes": f.size_bytes,
            "uploaded_at": f.uploaded_at}


def upload_file(session: Session, sv: Survey, who: Respondent, question_id: str, filename: str,
                content: bytes) -> dict:
    _ensure_accepting(sv)
    q = schema.questions_by_id(sv.definition).get(question_id)
    if not q or q["type"] != "file":
        raise AppError("invalid_question", "That question doesn't take files.", 422, "question_id")
    resp = current_response(session, sv, who)
    if resp and resp.status == "submitted":
        raise AppError("already_submitted", "You've already submitted this survey.", 409)
    if not content:
        raise AppError("empty_file", "The file is empty.", 400, "file")
    if len(content) > MAX_FILE_BYTES:
        raise AppError("file_too_large", f"Files are limited to {MAX_FILE_BYTES // 1024 // 1024} MB.", 400, "file")
    mime = detect_evidence_mime(filename, content)
    if mime not in ALLOWED_MIME:
        raise AppError("file_type_not_allowed", f"This file type isn't accepted. Use {ALLOWED_LABEL}.", 400, "file")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in EXTENSIONS[mime]:
        raise AppError("file_type_mismatch", f"The file's content is {ALLOWED_MIME[mime]}, which doesn't match its "
                                             f"{'.' + ext if ext else 'missing'} extension.", 400, "file")
    if resp is None:
        resp = _start(session, sv, who)
    live = session.exec(select(func.count()).select_from(SurveyFile).where(
        SurveyFile.response_id == resp.id, SurveyFile.question_id == question_id,
        SurveyFile.deleted_at.is_(None))).one()
    if live >= q.get("max_files", 3):
        raise AppError("too_many_files", f"At most {q.get('max_files', 3)} files for this question.", 409)
    name = display_filename(filename)
    f = SurveyFile(survey_id=sv.id, response_id=resp.id, question_id=question_id, storage_key="",
                   original_filename=name, mime_type=mime, size_bytes=0, sha256_checksum="")
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)[:100]
    f.storage_key = f"companies/{sv.company_id}/surveys/{sv.public_id}/responses/{resp.public_id}/{f.public_id}_{safe}"
    stored = get_storage().put(f.storage_key, io.BytesIO(content), mime_type=mime)
    f.size_bytes, f.sha256_checksum = stored.size_bytes, stored.sha256_checksum
    session.add(f)
    # The file becomes part of the answer straight away, so a reload shows it
    answers = dict(resp.answers or {})
    answers[question_id] = list(answers.get(question_id) or []) + [f.public_id]
    resp.answers = answers
    resp.revision += 1
    resp.updated_at = utcnow()
    session.add(resp)
    session.commit()
    session.refresh(f)
    session.refresh(resp)
    return {"file": file_json(f), "response": response_state(sv, resp, who)}


def remove_file(session: Session, sv: Survey, who: Respondent, file_public_id: str) -> dict:
    _ensure_accepting(sv)
    resp = current_response(session, sv, who)
    f = session.exec(select(SurveyFile).where(SurveyFile.public_id == file_public_id,
                                              SurveyFile.deleted_at.is_(None))).first()
    if not resp or not f or f.response_id != resp.id:
        raise NotFoundError("File not found")
    if resp.status != "in_progress":
        raise AppError("already_submitted", "You've already submitted this survey.", 409)
    f.deleted_at = utcnow()
    session.add(f)
    answers = dict(resp.answers or {})
    if f.question_id in answers:
        answers[f.question_id] = [x for x in answers[f.question_id] if x != f.public_id]
        if not answers[f.question_id]:
            answers.pop(f.question_id)
    resp.answers = answers
    resp.revision += 1
    resp.updated_at = utcnow()
    session.add(resp)
    session.commit()
    session.refresh(resp)
    return {"response": response_state(sv, resp, who)}


def file_download(session: Session, actor: User, sv: Survey, file_public_id: str,
                  ip_address: Optional[str] = None) -> dict:
    f = session.exec(select(SurveyFile).where(SurveyFile.public_id == file_public_id,
                                              SurveyFile.survey_id == sv.id,
                                              SurveyFile.deleted_at.is_(None))).first()
    resp = session.get(SurveyResponse, f.response_id) if f else None
    if not f or not resp or resp.status != "submitted":
        raise NotFoundError("File not found")
    url = get_storage().signed_url(f.storage_key, expires_in=300)
    log_action(session, actor, "survey.file_downloaded", "survey", sv.id, sv.public_id, company_id=sv.company_id,
               changes={"file": f.public_id}, ip_address=ip_address)
    session.commit()
    return {"url": url, "expires_in": 300, "filename": f.original_filename}


# ---------------------------------------------------------------------------
# Serialisation for managers
# ---------------------------------------------------------------------------

def list_managed(session: Session, actor: User, company_id: Optional[int], *, templates_only: bool = False) -> list[dict]:
    cid = resolve_company_id(session, actor, company_id)
    rows = session.exec(select(Survey).where(Survey.company_id == cid, Survey.is_template == templates_only)
                        .order_by(Survey.updated_at.desc())).all()
    if not rows:
        return []
    ids = [s.id for s in rows]
    resp = {}
    for sid, st, n in session.exec(select(SurveyResponse.survey_id, SurveyResponse.status, func.count())
                                   .where(SurveyResponse.survey_id.in_(ids))
                                   .group_by(SurveyResponse.survey_id, SurveyResponse.status)).all():
        resp.setdefault(sid, {})[st] = n
    inv = {}
    for sid, st, n in session.exec(select(SurveyInvite.survey_id, SurveyInvite.status, func.count())
                                   .where(SurveyInvite.survey_id.in_(ids))
                                   .group_by(SurveyInvite.survey_id, SurveyInvite.status)).all():
        inv.setdefault(sid, {})[st] = n
    company_size = None
    names = {u.id: u.name for u in session.exec(select(User).where(User.id.in_({s.created_by for s in rows}))).all()}
    avg = {}
    for sid, score in session.exec(select(SurveyResponse.survey_id, SurveyResponse.score).where(
            SurveyResponse.survey_id.in_(ids), SurveyResponse.status == "submitted")).all():
        if score and score.get("percent") is not None:
            avg.setdefault(sid, []).append(score["percent"])
    out = []
    for s in rows:
        invited = sum(inv.get(s.id, {}).values())
        submitted_inv = inv.get(s.id, {}).get("submitted", 0)
        eligible = invited
        if s.open_to_company:
            if company_size is None:
                company_size = len(company_users(session, cid))
            eligible = max(invited, company_size)
        responses = resp.get(s.id, {}).get("submitted", 0)
        scores = avg.get(s.id)
        out.append({
            "id": s.public_id, "title": s.title, "description": s.description, "kind": s.kind, "category": s.category,
            "status": effective_status(s), "anonymous": s.anonymous, "open_to_company": s.open_to_company,
            "public_link": bool(s.public_token), "opens_at": s.opens_at, "closes_at": s.closes_at,
            "created_by": names.get(s.created_by), "created_at": s.created_at, "updated_at": s.updated_at,
            "published_at": s.published_at, "questions": templates.question_count(s.definition),
            "invited": invited, "eligible": eligible, "responses": responses,
            "in_progress": resp.get(s.id, {}).get("in_progress", 0),
            "response_rate": round(100 * submitted_inv / eligible) if eligible else None,
            "average_score": round(sum(scores) / len(scores), 1) if scores else None,
        })
    return out


def detail(session: Session, sv: Survey) -> dict:
    answered = has_answers(session, sv)
    creator = session.get(User, sv.created_by)
    return {
        "id": sv.public_id, "company_id": sv.company_id, "is_template": sv.is_template, "kind": sv.kind,
        "category": sv.category, "title": sv.title, "description": sv.description, "welcome": sv.welcome,
        "thank_you": sv.thank_you, "definition": sv.definition, "revision": sv.revision, "status": effective_status(sv),
        "stored_status": sv.status, "opens_at": sv.opens_at, "closes_at": sv.closes_at, "anonymous": sv.anonymous,
        "open_to_company": sv.open_to_company, "show_score_to_respondent": sv.show_score_to_respondent,
        "public_token": sv.public_token, "created_by": creator.name if creator else None,
        "created_at": sv.created_at, "updated_at": sv.updated_at, "published_at": sv.published_at,
        "closed_at": sv.closed_at, "archived_at": sv.archived_at,
        "has_responses": answered, "structure_locked": answered,
        "problems": schema.publish_problems(sv.definition, sv.kind),
        "audience": audience_counts(session, sv) if not sv.is_template else None,
        "minutes": templates.estimated_minutes(sv.definition),
    }


def template_list(session: Session, actor: User, company_id: Optional[int]) -> list[dict]:
    out = [templates.summary(templates.built_in(t["id"])) for t in templates.BUILT_IN]
    for s in list_managed(session, actor, company_id, templates_only=True):
        sv = _by_public_id(session, s["id"])
        out.append({"id": s["id"], "built_in": False, "kind": s["kind"], "category": s["category"],
                    "title": s["title"], "description": s["description"], "anonymous": s["anonymous"],
                    "sections": len(sv.definition.get("sections", [])), "questions": s["questions"],
                    "minutes": templates.estimated_minutes(sv.definition), "created_by": s["created_by"],
                    "updated_at": s["updated_at"]})
    return out


def template_preview(session: Session, actor: User, template_id: str, company_id: Optional[int]) -> dict:
    if template_id.startswith("tpl_"):
        t = templates.built_in(template_id)
        if not t:
            raise NotFoundError("Template not found")
        return {**templates.summary(t), "definition": t["definition"], "welcome": t.get("welcome")}
    sv = get_managed(session, actor, template_id)
    if not sv.is_template:
        raise NotFoundError("Template not found")
    return {"id": sv.public_id, "built_in": False, "kind": sv.kind, "category": sv.category, "title": sv.title,
            "description": sv.description, "definition": sv.definition, "welcome": sv.welcome,
            "questions": templates.question_count(sv.definition), "minutes": templates.estimated_minutes(sv.definition)}
