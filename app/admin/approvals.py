"""Approval history: every review decision for a company, in one list.

Sources (all already recorded by the platform — nothing new is stored):
- data uploads: the ``reviews`` table (one row per review of a dataset
  version: who, when, decision, note; pending ones are "under review");
- BRSR sections: audit entries ``brsr.section.*`` (submitted, approved
  internally, approved, changes requested, withdrawn);
- reports: audit entries ``report.submitted_for_review`` / ``report.published``;
- assurance: audit entries ``assurance.signed``.

The design's "Framework" column becomes "Type" (data upload / BRSR / report /
assurance): uploads aren't tied to a framework, and a made-up label would
be misleading.
"""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlmodel import Session, select

from app.models.audit_log import AuditLog
from app.models.brsr import BrsrResponseSet
from app.models.dataset import Dataset, DatasetVersion
from app.models.review import Review
from app.models.upload_type import UploadType
from app.models.user import User

STATUS_LABELS = {"approved": "Approved", "internally_approved": "Approved internally", "rejected": "Rejected",
                 "changes_requested": "Changes requested", "under_review": "Under review",
                 "submitted": "Submitted", "withdrawn": "Withdrawn", "published": "Published", "signed": "Signed"}
KIND_LABELS = {"dataset": "Data upload", "brsr": "BRSR", "report": "Report", "assurance": "Assurance"}
AUDIT_ACTIONS = {
    "brsr.section.submitted": ("brsr", "submitted"), "brsr.section.withdrawn": ("brsr", "withdrawn"),
    "brsr.section.approved": ("brsr", "approved"), "brsr.section.internally_approved": ("brsr", "internally_approved"),
    "brsr.section.changes_requested": ("brsr", "changes_requested"),
    "report.submitted_for_review": ("report", "submitted"), "report.published": ("report", "published"),
    "assurance.signed": ("assurance", "signed"),
}
LIMIT = 3000


def _period(start, end) -> str:
    if not start:
        return ""
    if start.year == end.year and start.month == end.month:
        return start.strftime("%b %Y")
    return f"{start.strftime('%b %Y')} – {end.strftime('%b %Y')}"


def history(session: Session, company_id: int, *, kind: Optional[str] = None, status: Optional[str] = None,
            search: Optional[str] = None, page: int = 1, page_size: int = 25) -> dict:
    items: list[dict] = []

    q = (select(Review, DatasetVersion, Dataset, UploadType)
         .join(DatasetVersion, DatasetVersion.id == Review.dataset_version_id)
         .join(Dataset, Dataset.id == DatasetVersion.dataset_id)
         .join(UploadType, UploadType.id == Dataset.upload_type_id)
         .where(Dataset.company_id == company_id)
         .order_by(Review.updated_at.desc()).limit(LIMIT))
    user_ids: set[int] = set()
    for rv, ver, ds, ut in session.exec(q).all():
        st = rv.status if rv.status != "pending" else "under_review"
        user_ids |= {rv.reviewer_user_id, ver.uploaded_by}
        items.append({"id": f"rv:{rv.public_id}", "kind": "dataset", "title": ut.display_name,
                      "detail": f"{_period(ds.reporting_period_start, ds.reporting_period_end)} · version "
                                f"{ver.version_number}",
                      "ref": ds.public_id, "status": st, "by_id": rv.reviewer_user_id if st != "under_review" else None,
                      "submitted_by_id": ver.uploaded_by,
                      "date": rv.decided_at or rv.assigned_at, "comment": rv.decision_note,
                      "tier": "Deloitte" if rv.tier >= 2 else None})

    logs = session.exec(select(AuditLog).where(AuditLog.company_id == company_id,
                                               AuditLog.action.in_(list(AUDIT_ACTIONS)))
                        .order_by(AuditLog.occurred_at.desc()).limit(LIMIT)).all()
    fy_by_set = {}
    set_ids = {lg.entity_public_id for lg in logs if lg.action.startswith("brsr.") and lg.entity_public_id}
    if set_ids:
        fy_by_set = {rs.public_id: rs.financial_year for rs in session.exec(
            select(BrsrResponseSet).where(BrsrResponseSet.public_id.in_(set_ids))).all()}
    for lg in logs:
        k, st = AUDIT_ACTIONS[lg.action]
        ch = lg.changes or {}
        user_ids.add(lg.actor_user_id)
        if k == "brsr":
            fy = fy_by_set.get(lg.entity_public_id)
            title = f"BRSR{f' FY {fy}' if fy else ''} · Section {ch.get('section', '')}".strip()
            detail = {"internal": "Company review", "final": "Deloitte review", "single": "Review"}.get(
                ch.get("tier"), "") if st not in ("submitted", "withdrawn") else ""
            comment = ch.get("note_excerpt")
        elif k == "report":
            title, detail, comment = "ESG report", (ch.get("title") or ch.get("report_type") or ""), ch.get("note")
        else:
            op = (ch.get("opinion") or "").replace("_", " ")
            title, detail, comment = "Assurance opinion", f"{op.capitalize()} opinion" if op else "", None
        items.append({"id": f"al:{lg.id}", "kind": k, "title": title, "detail": detail,
                      "ref": lg.entity_public_id, "status": st, "by_id": lg.actor_user_id, "submitted_by_id": None,
                      "date": lg.occurred_at, "comment": comment, "tier": None})

    names = {u.id: u.name for u in session.exec(select(User).where(User.id.in_({i for i in user_ids if i})))} \
        if any(user_ids) else {}
    for it in items:
        it["by"] = names.get(it.pop("by_id"))
        it["submitted_by"] = names.get(it.pop("submitted_by_id"))
        it["kind_label"] = KIND_LABELS[it["kind"]]
        it["status_label"] = STATUS_LABELS.get(it["status"], it["status"].replace("_", " ").capitalize())
    items.sort(key=lambda i: i["date"] or datetime.min, reverse=True)

    counts = {"total": len(items),
              "by_status": {s: sum(i["status"] == s for i in items) for s in STATUS_LABELS},
              "by_kind": {k: sum(i["kind"] == k for i in items) for k in KIND_LABELS}}
    if kind:
        items = [i for i in items if i["kind"] == kind]
    if status:
        items = [i for i in items if i["status"] == status]
    s = (search or "").strip().lower()
    if s:
        items = [i for i in items if any(s in (i.get(f) or "").lower()
                                         for f in ("title", "detail", "by", "submitted_by", "comment"))]
    page_size = max(1, min(100, page_size))
    page = max(1, page)
    return {"items": items[(page - 1) * page_size: page * page_size], "total": len(items), "page": page,
            "page_size": page_size, "counts": counts,
            "statuses": [{"code": k, "label": v} for k, v in STATUS_LABELS.items()],
            "kinds": [{"code": k, "label": v} for k, v in KIND_LABELS.items()]}


def factors() -> dict:
    """The conversion factors behind the BRSR figures (read-only; changed only
    with sign-off, in code, so every figure stays reproducible)."""
    from app.brsr.sheet_sources import FACTORS, SOURCES, describe
    used: dict[str, set[str]] = {k: set() for k in FACTORS}
    for metric in SOURCES:
        d = describe(metric) or {}
        for p in d.get("parts", []):
            for col, val in (p.get("factors") or {}).items():
                for key, f in FACTORS.items():
                    if abs(f["value"] - val) < 1e-12:
                        used[key].add(f"{p['sheet']} · {col}")
            if p.get("scale"):
                for key, f in FACTORS.items():
                    if abs(f["value"] - p["scale"]) < 1e-12:
                        used[key].add(f"{p['sheet']} · {', '.join(p['columns'])}")
    return {"items": [{"key": k, "label": f["label"], "value": f["value"], "basis": f["basis"],
                       "needs_signoff": f["signoff"], "used_for": sorted(used[k])}
                      for k, f in FACTORS.items()],
            "note": "These factors convert data-sheet units into BRSR units (e.g. litres of diesel into GJ). They "
                    "are fixed in the platform so every figure can be reproduced; a change is made with sign-off "
                    "and shows up as 'data changed' on approved BRSR sections."}
