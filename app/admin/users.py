"""Client user administration: directory, roles, status, passwords, bulk import.

Every change goes through app/rbac/policy.py (scope, tenancy, no self-changes,
last administrator) and leaves an audit entry. Each client user has exactly
one client role; Role Assignment replaces it.

Passwords: the platform sends no e-mail, so a new or reset password is
generated here and returned ONCE to the administrator, who passes it on.
Only its hash is stored.
"""
from __future__ import annotations

import csv
import io
import re
import secrets
from datetime import datetime
from typing import Any, Optional

from sqlalchemy import func
from sqlmodel import Session, select

from app.core.errors import AppError, NotFoundError
from app.core.security import hash_password
from app.models.master_data import Department
from app.models.rbac import Role, UserRole
from app.models.user import User
from app.rbac import policy
from app.services.audit import log_action

EMAIL_RE = re.compile(r"^[^@\s,;<>]+@[^@\s,;<>]+\.[^@\s,;<>]+$")
MAX_IMPORT_ROWS = 500

ROLE_HELP = {
    "Client Administrator": "Manages the company's users, roles, targets and settings, and can do everything an "
                            "approver does.",
    "Client Approver": "Approves the company's data and BRSR sections before they go to Deloitte.",
    "Client Reviewer": "Reviews uploaded data and answers; can't upload or approve.",
    "Client Uploader": "Uploads data sheets and answers questionnaires.",
}
ROLE_ORDER = ["Client Administrator", "Client Approver", "Client Reviewer", "Client Uploader"]
ALIASES = {
    "administrator": "Client Administrator", "admin": "Client Administrator",
    "approver": "Client Approver", "reviewer": "Client Reviewer", "uploader": "Client Uploader",
    **{r.lower(): r for r in ROLE_ORDER},
}


def temp_password() -> str:
    """12 characters with upper, lower, digit and symbol; no look-alikes."""
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnpqrstuvwxyz23456789"
    while True:
        pw = "".join(secrets.choice(alphabet) for _ in range(10))
        pw += secrets.choice("@#$%&*") + secrets.choice("23456789")
        pw = "".join(secrets.SystemRandom().sample(pw, len(pw)))
        if any(c.isupper() for c in pw) and any(c.islower() for c in pw):
            return pw


def roles(session: Session) -> list[dict]:
    rows = {r.code: r for r in session.exec(select(Role).where(Role.scope == "client")).all()}
    return [{"code": c, "label": c.replace("Client ", ""), "description": ROLE_HELP.get(c, rows[c].description)}
            for c in ROLE_ORDER if c in rows] + \
        [{"code": c, "label": c, "description": r.description} for c, r in rows.items() if c not in ROLE_ORDER]


def _role_map(session: Session, users: list[User], company_id: int) -> dict[int, list[str]]:
    if not users:
        return {}
    q = select(UserRole.user_id, UserRole.company_id, Role.code).join(Role, Role.id == UserRole.role_id).where(
        UserRole.user_id.in_([u.id for u in users]), Role.scope == "client")
    out: dict[int, list[str]] = {}
    for uid, cid, code in session.exec(q).all():
        if cid in (None, company_id):
            out.setdefault(uid, []).append(code)
    for v in out.values():
        v.sort(key=lambda c: ROLE_ORDER.index(c) if c in ROLE_ORDER else 99)
    return out


def serialize(u: User, role_codes: list[str], actor: User) -> dict:
    return {"id": u.id, "name": u.name, "email": u.email, "department": u.department,
            "role": role_codes[0] if role_codes else None, "roles": role_codes,
            "status": "active" if u.is_active else "inactive", "is_self": u.id == actor.id,
            "created_at": u.created_at, "updated_at": u.updated_at}


def company_user(session: Session, company_id: int, user_id: int) -> User:
    u = session.get(User, user_id)
    if not u or u.deleted_at is not None or u.company_id != company_id or u.portal_type != "client":
        raise NotFoundError("User not found")
    return u


def departments(session: Session, company_id: int) -> list[str]:
    names = {d.name for d in session.exec(select(Department).where(
        Department.company_id == company_id, Department.deleted_at.is_(None))).all()}
    names |= {d for d in session.exec(select(User.department).where(
        User.company_id == company_id, User.deleted_at.is_(None), User.department.is_not(None))).all() if d}
    return sorted(names, key=str.lower)


def directory(session: Session, actor: User, company_id: int, *, search: Optional[str] = None,
              status: Optional[str] = None, role: Optional[str] = None, department: Optional[str] = None,
              page: int = 1, page_size: int = 25) -> dict:
    users = session.exec(select(User).where(User.company_id == company_id, User.portal_type == "client",
                                            User.deleted_at.is_(None)).order_by(func.lower(User.name))).all()
    rm = _role_map(session, users, company_id)
    rows = [serialize(u, rm.get(u.id, []), actor) for u in users]
    counts = {"total": len(rows), "active": sum(r["status"] == "active" for r in rows),
              "inactive": sum(r["status"] == "inactive" for r in rows),
              "no_role": sum(not r["roles"] for r in rows),
              "by_role": {c: sum(r["role"] == c for r in rows) for c in ROLE_ORDER}}
    s = (search or "").strip().lower()
    if s:
        rows = [r for r in rows if s in r["name"].lower() or s in r["email"].lower()
                or s in (r["department"] or "").lower()]
    if status in ("active", "inactive"):
        rows = [r for r in rows if r["status"] == status]
    if role == "none":
        rows = [r for r in rows if not r["roles"]]
    elif role:
        rows = [r for r in rows if r["role"] == role]
    if department:
        rows = [r for r in rows if (r["department"] or "") == department]
    total = len(rows)
    page_size = max(1, min(100, page_size))
    page = max(1, page)
    return {"items": rows[(page - 1) * page_size: page * page_size], "total": total, "page": page,
            "page_size": page_size, "counts": counts, "departments": departments(session, company_id),
            "roles": roles(session)}


def _clean_name(v: Any, field: str, limit: int, required: bool = True) -> Optional[str]:
    s = ("" if v is None else str(v)).strip()
    if not s:
        if required:
            raise AppError(f"{field}_required", f"{field.replace('_', ' ').capitalize()} is required", 422, field)
        return None
    if len(s) > limit:
        raise AppError(f"{field}_too_long", f"Keep the {field} under {limit} characters", 422, field)
    return s


def _resolve_role(session: Session, code: Optional[str]) -> Role:
    name = ALIASES.get((code or "").strip().lower())
    if not name:
        raise AppError("invalid_role", f"Unknown role: {code}", 422, "role_code")
    role = policy.get_role(session, name)
    policy.check_role_for_portal(role, "client")
    return role


def _email_taken(session: Session, email: str) -> bool:
    return session.exec(select(User.id).where(func.lower(User.email) == email.lower())).first() is not None


def create(session: Session, actor: User, company_id: int, *, name: str, email: str,
           department: Optional[str], role_code: str, ip_address: Optional[str] = None) -> dict:
    name = _clean_name(name, "name", 120)
    email = (email or "").strip().lower()
    if not EMAIL_RE.match(email) or len(email) > 254:
        raise AppError("invalid_email", "Enter a valid e-mail address", 422, "email")
    if _email_taken(session, email):
        raise AppError("email_taken", "A user with this e-mail already exists", 422, "email")
    role = _resolve_role(session, role_code)
    pw = temp_password()
    u = User(name=name, email=email, portal_type="client", role=role.code,
             department=_clean_name(department, "department", 80, required=False),
             company_id=company_id, hashed_password=hash_password(pw))
    session.add(u)
    session.flush()
    session.add(UserRole(user_id=u.id, role_id=role.id, company_id=company_id))
    log_action(session, actor, "user.created", "user", u.id, None, company_id=company_id,
               changes={"email": email, "role": role.code, "department": u.department}, ip_address=ip_address)
    session.commit()
    session.refresh(u)
    return {"user": serialize(u, [role.code], actor), "temporary_password": pw}


def update(session: Session, actor: User, u: User, data: dict, ip_address: Optional[str] = None) -> dict:
    changes = {}
    if "name" in data:
        changes["name"] = _clean_name(data["name"], "name", 120)
    if "department" in data:
        changes["department"] = _clean_name(data["department"], "department", 80, required=False)
    changes = {k: v for k, v in changes.items() if getattr(u, k) != v}
    if changes:
        for k, v in changes.items():
            setattr(u, k, v)
        u.updated_at = datetime.utcnow()
        session.add(u)
        log_action(session, actor, "user.updated", "user", u.id, None, company_id=u.company_id,
                   changes={"email": u.email, **changes}, ip_address=ip_address)
        session.commit()
        session.refresh(u)
    return serialize(u, _role_map(session, [u], u.company_id).get(u.id, []), actor)


def set_role(session: Session, actor: User, u: User, role_code: str, ip_address: Optional[str] = None) -> dict:
    role = _resolve_role(session, role_code)
    policy.check_can_grant(session, actor, u, role)
    links = policy.client_roles(session, u.id, u.company_id)
    current = {session.get(Role, lk.role_id).code for lk in links}
    if current == {role.code}:
        return serialize(u, [role.code], actor)
    if policy.CLIENT_ADMIN in current and role.code != policy.CLIENT_ADMIN:
        policy.check_keeps_an_admin(session, u, losing_admin=True)
    for lk in links:
        session.delete(lk)
    session.flush()
    session.add(UserRole(user_id=u.id, role_id=role.id, company_id=u.company_id))
    u.role = role.code
    u.updated_at = datetime.utcnow()
    session.add(u)
    log_action(session, actor, "user.role_changed", "user", u.id, None, company_id=u.company_id,
               changes={"email": u.email, "from": sorted(current), "to": role.code}, ip_address=ip_address)
    session.commit()
    session.refresh(u)
    return serialize(u, [role.code], actor)


def set_active(session: Session, actor: User, u: User, active: bool, ip_address: Optional[str] = None) -> dict:
    if u.id == actor.id:
        raise AppError("self_deactivate", "You can't change your own account's status", 422)
    if u.is_active != active:
        if not active:
            policy.check_keeps_an_admin(session, u, losing_admin=True)
        u.is_active = active
        u.updated_at = datetime.utcnow()
        session.add(u)
        log_action(session, actor, "user.activated" if active else "user.deactivated", "user", u.id, None,
                   company_id=u.company_id, changes={"email": u.email}, ip_address=ip_address)
        session.commit()
        session.refresh(u)
    return serialize(u, _role_map(session, [u], u.company_id).get(u.id, []), actor)


def reset_password(session: Session, actor: User, u: User, ip_address: Optional[str] = None) -> dict:
    if u.id == actor.id:
        raise AppError("own_password", "Change your own password in Settings → Security", 422)
    pw = temp_password()
    u.hashed_password = hash_password(pw)
    u.updated_at = datetime.utcnow()
    session.add(u)
    log_action(session, actor, "user.password_reset_by_admin", "user", u.id, None, company_id=u.company_id,
               changes={"email": u.email}, ip_address=ip_address)
    session.commit()
    return {"user_id": u.id, "email": u.email, "temporary_password": pw}


# ---------------------------------------------------------------------------
# Bulk import
# ---------------------------------------------------------------------------

HEADERS = {
    "name": {"name", "full name", "employee", "employee name"},
    "email": {"email", "e-mail", "email id", "email address", "mail"},
    "department": {"department", "dept", "team", "function"},
    "role": {"role", "access", "role code"},
}


def _norm(h: Any) -> str:
    return re.sub(r"[\s_]+", " ", str(h or "")).strip().lower()


def _read_rows(filename: str, content: bytes) -> list[list[Any]]:
    if not content:
        raise AppError("empty_file", "The file is empty", 400, "file")
    if content[:2] == b"PK":  # xlsx
        from openpyxl import load_workbook
        try:
            wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
        except Exception:
            raise AppError("unreadable_file", "Couldn't read this Excel file", 400, "file") from None
        ws = wb.worksheets[0]
        return [list(r) for r in ws.iter_rows(values_only=True)]
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
    dialect = csv.excel_tab if text.count("\t") > text.count(",") else csv.excel
    return [r for r in csv.reader(io.StringIO(text), dialect)]


def parse_import(session: Session, filename: str, content: bytes) -> dict:
    raw = _read_rows(filename, content)
    raw = [r for r in raw if any(str(c or "").strip() for c in r)]
    if not raw:
        raise AppError("empty_file", "The file has no rows", 400, "file")
    head = [_norm(h) for h in raw[0]]
    col = {}
    for key, names in HEADERS.items():
        for i, h in enumerate(head):
            if h in names:
                col[key] = i
                break
    missing = [k for k in ("name", "email") if k not in col]
    if missing:
        raise AppError("missing_columns", "The first row must be headers with at least Name and Email "
                                          "(Department and Role are optional)", 422, "file",
                       details={"missing": missing, "found": raw[0]})
    body = raw[1:]
    if len(body) > MAX_IMPORT_ROWS:
        raise AppError("too_many_rows", f"Import at most {MAX_IMPORT_ROWS} people at a time", 422, "file")

    def cell(r, k):
        i = col.get(k)
        return str(r[i]).strip() if i is not None and i < len(r) and r[i] is not None else ""

    existing = {e.lower() for e in session.exec(select(User.email)).all()}
    seen: set[str] = set()
    rows = []
    for n, r in enumerate(body, start=2):
        name, email = cell(r, "name"), cell(r, "email").lower()
        dept, role_in = cell(r, "department"), cell(r, "role")
        errors = []
        if not name:
            errors.append("Name is missing")
        elif len(name) > 120:
            errors.append("Name is too long")
        if not EMAIL_RE.match(email):
            errors.append("E-mail is not valid")
        elif email in seen:
            errors.append("E-mail appears twice in the file")
        elif email in existing:
            errors.append("A user with this e-mail already exists")
        if len(dept) > 80:
            errors.append("Department is too long")
        role = ALIASES.get(role_in.lower()) if role_in else "Client Uploader"
        if not role:
            errors.append(f"Unknown role '{role_in}' — use Administrator, Approver, Reviewer or Uploader")
        seen.add(email)
        rows.append({"row": n, "name": name, "email": email, "department": dept or None, "role": role,
                     "role_defaulted": not role_in, "errors": errors})
    return {"rows": rows, "ok": sum(not r["errors"] for r in rows), "with_errors": sum(bool(r["errors"]) for r in rows)}


def run_import(session: Session, actor: User, company_id: int, filename: str, content: bytes, *,
               dry_run: bool, ip_address: Optional[str] = None) -> dict:
    parsed = parse_import(session, filename, content)
    if dry_run:
        return {**parsed, "created": []}
    if parsed["with_errors"]:
        raise AppError("import_has_errors", f"{parsed['with_errors']} row(s) have problems — fix them and try "
                                            "again. Nothing was imported.", 422, "file", details=parsed)
    role_by_code = {r: _resolve_role(session, r) for r in {x["role"] for x in parsed["rows"]}}
    created = []
    for r in parsed["rows"]:
        role = role_by_code[r["role"]]
        pw = temp_password()
        u = User(name=r["name"], email=r["email"], portal_type="client", role=role.code, department=r["department"],
                 company_id=company_id, hashed_password=hash_password(pw))
        session.add(u)
        session.flush()
        session.add(UserRole(user_id=u.id, role_id=role.id, company_id=company_id))
        created.append({"id": u.id, "name": u.name, "email": u.email, "department": u.department,
                        "role": role.code, "temporary_password": pw})
    log_action(session, actor, "user.bulk_imported", "company", company_id, None, company_id=company_id,
               changes={"count": len(created), "file": (filename or "")[:120]}, ip_address=ip_address)
    session.commit()
    return {**parsed, "created": created}
