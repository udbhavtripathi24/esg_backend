"""Upgrade BRSR response sets started on an earlier question bank to v3.

v1  every quantitative table was typed directly. Those typed cells are
    still where v3 reads its typed cells (previous-year columns, and figures
    with no data-sheet source), so nothing needs moving; current-year cells
    that v3 builds from the data sheets are simply recomputed on read.
v2  quantitative tables were built from an annual form (``D.<area>``
    answers). v3 builds them from the approved data sheets instead, and the
    figures with no sheet source are typed in the BRSR table itself. The
    upgrade carries the v2 annual figures over:

    * current year — into cells v3 leaves typed (turnover, net worth,
      Scope 1/2/3, states served, upheld POSH complaints, …);
    * previous year — into every previous-year cell, where it stands until
      last year's data sheets are approved.

The upgrade only fills *blank* cells, so it is idempotent and never
overwrites what the client has entered since. v2 annual answers are left in
place (unused) as a record. Every write goes to the answer history with
source ``bank_upgrade``.

Approvals: a section approved before v3 was approved on the figures of
that time (the v2 annual form, or v1's typed tables). The upgrade records a
fingerprint of exactly those figures, so wherever the approved data sheets
now give different figures the section is flagged "data changed since
approval" and the report is held until it is re-approved — an approval is
never silently carried over to numbers nobody reviewed.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.brsr import engine
from app.brsr import formula as F
from app.brsr.annual_data import AREAS, DERIVE, METRICS
from app.brsr.question_bank import BANK_VERSION, QUESTIONS_BY_ID
from app.brsr.sheet_sources import SOURCED
from app.models.brsr import BrsrAnswer, BrsrAnswerHistory, BrsrResponseSet

V2_QUESTIONS = {f"D.{a['code']}" for a in AREAS}


def needs_upgrade(rs: BrsrResponseSet) -> bool:
    return rs.bank_version != BANK_VERSION


def _annual(answers: dict[str, dict]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {"cy": {}, "py": {}}
    for qid in V2_QUESTIONS:
        a = answers.get(qid) or {}
        out["cy"].update(a.get("values") or {})
        out["py"].update(a.get("py") or {})
    return out


def _value(expr: str, vals: dict) -> Any:
    name = expr.strip()
    if name in METRICS and METRICS[name]["kind"] in ("yesno", "choice"):
        return vals.get(name)
    return F.evaluate(expr, lambda n: vals.get(n))


def _blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def carry_over(q: dict, stored: dict, annual: dict) -> tuple[dict, int]:
    """``stored`` with v2 figures filled into v3's typed cells. (value, count)."""
    spec = DERIVE.get(q["id"]) or {}
    v3 = q.get("derive") or {}
    out = {k: (dict(v) if isinstance(v, dict) else v) for k, v in stored.items()}
    n = 0
    if q["type"] == "number":
        if "value" in spec and "value" not in v3 and _blank(out.get("value")):
            val = _value(spec["value"], annual["cy"])
            if val is not None:
                out["value"], n = val, 1
        return out, n
    if q["type"] == "fields":
        fields = dict(out.get("fields") or {})
        for cell, expr in spec.items():
            key = cell.split(".", 1)[1]
            if cell in v3 or not _blank(fields.get(key)):
                continue
            val = _value(expr, annual["cy"])
            if val is not None:
                fields[key], n = val, n + 1
        if n:
            out["fields"] = fields
        return out, n
    if q["type"] != "table":
        return out, 0
    cells = {rk: dict(rv) for rk, rv in (out.get("cells") or {}).items()}
    for r in q["rows"]:
        if r.get("header") or r.get("formula"):
            continue
        for c in q["columns"]:
            if c["kind"] == "computed":
                continue
            key = engine.derive_key(c, r["key"])
            if key is None or key not in spec:
                continue
            period = c.get("period") or "cy"
            typed_now = not (F.names_in(spec[key]) <= SOURCED)
            if period == "cy" and not typed_now:
                continue  # built from the data sheets in v3
            if not _blank(cells.get(r["key"], {}).get(c["key"])):
                continue
            val = _value(spec[key], annual[period])
            if val is not None:
                cells.setdefault(r["key"], {})[c["key"]] = val
                n += 1
    if n:
        out["cells"] = cells
    return out, n


def upgrade_response_set(session: Session, rs: BrsrResponseSet, *, dry_run: bool = False,
                         actor_id: Optional[int] = None) -> dict:
    rows = {a.question_id: a for a in session.exec(
        select(BrsrAnswer).where(BrsrAnswer.response_set_id == rs.id)).all()}
    answers = {qid: a.value for qid, a in rows.items()}
    annual = _annual(answers)
    filled: dict[str, int] = {}
    if annual["cy"] or annual["py"]:
        for qid in DERIVE:
            q = QUESTIONS_BY_ID.get(qid)
            if not q:
                continue
            row = rows.get(qid)
            cur = row.value if row else {}
            if cur.get("na"):
                continue
            new, n = carry_over(q, cur, annual)
            if not n:
                continue
            value = engine.normalize_answer(qid, new)
            if row and value == row.value:
                continue
            filled[qid] = n
            if dry_run:
                continue
            uid = actor_id or rs.created_by
            if row:
                row.value = value
                row.revision += 1
                row.updated_by = uid
                row.updated_at = datetime.utcnow()
                session.add(row)
                rev = row.revision
            else:
                session.add(BrsrAnswer(response_set_id=rs.id, section_code=q["section"], question_id=qid,
                                       value=value, revision=1, updated_by=uid))
                rev = 1
            session.add(BrsrAnswerHistory(response_set_id=rs.id, question_id=qid, revision=rev, value=value,
                                          source="bank_upgrade", changed_by=uid))
    fingerprinted = _fingerprint_old_approvals(session, rs, annual, dry_run)
    report = {"response_set": rs.public_id, "financial_year": rs.financial_year,
              "from_version": rs.bank_version, "to_version": BANK_VERSION,
              "figures_filled": sum(filled.values()), "by_question": filled,
              "approvals_fingerprinted": fingerprinted, "dry_run": dry_run}
    if not dry_run:
        rs.bank_version = BANK_VERSION
        rs.updated_at = datetime.utcnow()
        session.add(rs)
        session.commit()
    return report


def _fingerprint_old_approvals(session: Session, rs: BrsrResponseSet, annual: dict, dry_run: bool) -> list[str]:
    """approved_data_hash for sections approved before v3, from the figures
    they were approved on. Never overwrites a fingerprint already there."""
    from app.brsr.service import inject_readonly, section_data_hash, sections_of
    from app.brsr.sheet_metrics import SHEET_KEY
    secs = sections_of(session, rs)
    todo = [c for c, sec in secs.items() if sec.status == "approved" and not sec.approved_data_hash
            and any(q["section"] == c and (q.get("derive") or q.get("derive_rows")) for q in QUESTIONS_BY_ID.values())]
    if not todo:
        return []
    rows = {a.question_id: a.value for a in session.exec(
        select(BrsrAnswer).where(BrsrAnswer.response_set_id == rs.id)).all()}
    answers = inject_readonly(rs, rows, session)
    # The figures of the time: v2's annual data (v1 sets had none — their
    # tables were typed, and v3 builds them from the sheets instead).
    from app.brsr.sheet_sources import COMPUTED

    def with_computed(vals: dict) -> dict:
        out = dict(vals)
        for key, (deps, fn) in COMPUTED.items():
            if out.get(key) is None:
                out[key] = fn(*[out.get(d) for d in deps])
        return out

    answers[SHEET_KEY] = {"cy": with_computed(annual["cy"]), "py": with_computed(annual["py"]), "cy_tables": {}}
    done = []
    for code in todo:
        h = section_data_hash(answers, code)
        if h and not dry_run:
            secs[code].approved_data_hash = h
            session.add(secs[code])
        done.append(code)
    return done
