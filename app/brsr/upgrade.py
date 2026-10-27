"""Upgrade BRSR response sets started on the v1 question bank.

In v1 the client typed every quantitative BRSR table directly. In v2 those
tables are built from the short annual data form (``D.<area>`` answers).
A v1 set therefore holds its figures in table cells that v2 no longer reads.

This module recovers the annual figures from those cells wherever the v2
mapping can be inverted exactly:

* a cell mapped to a single figure (``"perm_male.total": "emp_perm_m"``);
* a cell mapped to a figure times a constant (electricity: ``kWh × 0.0036``
  → GJ), divided back.

Figures the v1 form never asked for as raw numbers (turnover-rate inputs,
retirement-benefit coverage) cannot be recovered and are left blank for the
client to fill.

The upgrade only ever fills *blank* figures, so it is idempotent and never
overwrites anything the client has entered under v2. Where two v1 tables
disagree about the same figure (e.g. headcount in Section A vs Principle 3)
the first in questionnaire order is kept and the disagreement is reported.
Every write is recorded in the answer history with source ``bank_upgrade``.
"""
from __future__ import annotations

import math
import re
from datetime import datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.brsr import engine
from app.brsr.annual_data import DERIVE, METRIC_AREA, METRICS
from app.brsr.question_bank import BANK_VERSION, QUESTIONS, QUESTIONS_BY_ID
from app.models.brsr import BrsrAnswer, BrsrAnswerHistory, BrsrResponseSet

_IDENT = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*$")
_SCALED = re.compile(r"^\s*([a-z_][a-z0-9_]*)\s*\*\s*([0-9]*\.?[0-9]+)\s*$")


def _inverse(expr: str) -> Optional[tuple[str, float]]:
    """(metric, factor) when ``expr`` is ``metric`` or ``metric * factor``."""
    m = _IDENT.match(expr)
    if m and m.group(1) in METRICS:
        return m.group(1), 1.0
    m = _SCALED.match(expr)
    if m and m.group(1) in METRICS and float(m.group(2)) != 0:
        return m.group(1), float(m.group(2))
    return None


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool) or v is None or v == "":
        return None
    try:
        n = float(v)
    except (TypeError, ValueError):
        return None
    return n if math.isfinite(n) else None


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-6)
    return a == b


def _stored_cells(q: dict, stored: dict, key: str) -> list[tuple[str, Any]]:
    """[(period, value)] for a derive key in a v1 stored answer."""
    if q["type"] == "number":
        return [("cy", stored.get("value"))] if key == "value" else []
    if q["type"] == "fields":
        return [("cy", (stored.get("fields") or {}).get(key.split(".", 1)[1]))]
    rk, base = key.split(".", 1)
    row = (stored.get("cells") or {}).get(rk) or {}
    out = []
    for c in q.get("columns") or []:
        period = c.get("period")
        if period and period not in ("cy", "py"):
            continue
        cbase = c["key"][len(period) + 1:] if period else c["key"]
        if cbase == base and c["key"] in row:
            out.append((period or "cy", row[c["key"]]))
    return out


def recover_metrics(answers: dict[str, dict]) -> dict:
    """Annual figures recoverable from v1 table answers.

    Returns {"cy": {metric: value}, "py": {...}, "conflicts": [...]}."""
    found: dict[str, dict[str, Any]] = {"cy": {}, "py": {}}
    source: dict[tuple[str, str], str] = {}
    conflicts: list[dict] = []
    for q in QUESTIONS:  # questionnaire order decides which source wins
        spec = DERIVE.get(q["id"])
        stored = answers.get(q["id"])
        if not spec or not stored or stored.get("na"):
            continue
        for key, expr in spec.items():
            inv = _inverse(expr)
            if not inv:
                continue
            metric, factor = inv
            kind = METRICS[metric]["kind"]
            for period, raw in _stored_cells(q, stored, key):
                if kind in ("yesno", "choice"):
                    value = raw if isinstance(raw, str) and raw.strip() else None
                else:
                    n = _num(raw)
                    value = None if n is None else (n / factor if factor != 1 else n)
                    if value is not None and kind == "count":
                        value = round(value)
                if value is None:
                    continue
                have = found[period].get(metric)
                if have is None:
                    found[period][metric] = value
                    source[(period, metric)] = q["id"]
                elif not _same(have, value):
                    conflicts.append({"metric": metric, "period": period, "kept": have,
                                      "kept_from": source[(period, metric)], "ignored": value,
                                      "ignored_from": q["id"]})
    return {**found, "conflicts": conflicts}


def needs_upgrade(rs: BrsrResponseSet) -> bool:
    return rs.bank_version != BANK_VERSION


def upgrade_response_set(session: Session, rs: BrsrResponseSet, *, dry_run: bool = False) -> dict:
    """Fill blank annual figures of ``rs`` from its v1 table answers and mark
    the set as on the current bank. Commits unless ``dry_run``."""
    rows = {a.question_id: a for a in session.exec(
        select(BrsrAnswer).where(BrsrAnswer.response_set_id == rs.id)).all()}
    answers = {qid: a.value for qid, a in rows.items()}
    rec = recover_metrics(answers)

    per_q: dict[str, dict[str, dict]] = {}
    for period in ("cy", "py"):
        bucket = "values" if period == "cy" else "py"
        for metric, value in rec[period].items():
            qid = f"D.{METRIC_AREA[metric]}"
            per_q.setdefault(qid, {"values": {}, "py": {}})[bucket][metric] = value

    filled: dict[str, int] = {}
    for qid, new in sorted(per_q.items()):
        row = rows.get(qid)
        cur = row.value if row else {}
        merged = {"values": dict(cur.get("values") or {}), "py": dict(cur.get("py") or {})}
        count = 0
        for bucket in ("values", "py"):
            for metric, value in new[bucket].items():
                if merged[bucket].get(metric) in (None, ""):
                    merged[bucket][metric] = value
                    count += 1
        if not count:
            continue
        value = engine.normalize_answer(qid, {k: v for k, v in {**cur, **merged}.items()})
        filled[qid] = count
        if dry_run:
            continue
        if row:
            row.value = value
            row.revision += 1
            row.updated_at = datetime.utcnow()
            session.add(row)
            rev = row.revision
        else:
            session.add(BrsrAnswer(response_set_id=rs.id, section_code=QUESTIONS_BY_ID[qid]["section"],
                                   question_id=qid, value=value, revision=1, updated_by=rs.created_by))
            rev = 1
        session.add(BrsrAnswerHistory(response_set_id=rs.id, question_id=qid, revision=rev, value=value,
                                      source="bank_upgrade", changed_by=rs.created_by))

    report = {"response_set": rs.public_id, "financial_year": rs.financial_year,
              "from_version": rs.bank_version, "to_version": BANK_VERSION,
              "figures_filled": sum(filled.values()), "by_area": filled,
              "conflicts": rec["conflicts"], "dry_run": dry_run}
    if not dry_run:
        rs.bank_version = BANK_VERSION
        rs.updated_at = datetime.utcnow()
        session.add(rs)
        session.commit()
    return report
