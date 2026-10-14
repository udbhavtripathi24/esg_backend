"""BRSR answer engine: normalisation, computation and validation.

One implementation of "what is a valid BRSR answer" for the whole
platform. The API uses it when saving and submitting, the review screen
shows its output, and the report generator calls ``compute_table`` so the
numbers printed in the Word file are exactly the numbers the client and
reviewer saw on screen.

Answer payload shapes (stored as JSON, one row per question):
    text            {"text": str}
    yesno           {"answer": "Yes"|"No"|"NA", "details": str, "link": str}
    number          {"value": number}
    fields          {"fields": {key: value}}
    table           {"cells": {row_key: {col_key: value}}}   inputs only
    rows            {"rows": [{col_key: value}, ...], "none": bool}
    principle_grid  {"cells": {row_key: {"P1".."P9": value}}, "span": {row_key: value}}
Every shape may also carry:
    "na": true, "na_reason": str   question not applicable (with reason)
    "note": str                    footnote printed under the answer

Issue levels:
    error    blocks submission (invalid value, broken constraint)
    warning  shown to client and reviewer, does not block (e.g. a headcount
             that does not match Section A)
Missing required answers are reported separately as completeness, so a
half-filled draft is not a wall of red.
"""
from __future__ import annotations

import math
import re
from typing import Any, Callable, Optional

from app.brsr import formula as F
from app.brsr.question_bank import (
    CONDITIONAL_REQUIRED, CONSISTENCY_CHECKS, PRINCIPLES, QUESTIONS, QUESTIONS_BY_ID, YESNO_OPTIONS,
)

NUMERIC_KINDS = {"count", "number", "amount", "percent", "rate"}
TEXT_KINDS = {"text", "longtext", "email", "url"}
MAX_TEXT = 20000
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
URL_RE = re.compile(r"^(https?://)?[\w.-]+\.[a-z]{2,}(/\S*)?$", re.I)
NIC_RE = re.compile(r"^\d{2,5}$")


class PayloadError(ValueError):
    """Structurally invalid payload (wrong shape / unknown keys). Rejected
    with 422 at save time so garbage never reaches the database."""

    def __init__(self, qid: str, message: str):
        super().__init__(f"{qid}: {message}")
        self.qid = qid
        self.message = message


def _issue(level: str, qid: str, message: str, path: str | None = None) -> dict:
    return {"level": level, "question_id": qid, "path": path, "message": message}


def _is_blank(v: Any) -> bool:
    return v is None or (isinstance(v, str) and v.strip() == "")


def _to_number(v: Any) -> Optional[float]:
    if _is_blank(v):
        return None
    if isinstance(v, bool):
        raise ValueError("not a number")
    if isinstance(v, (int, float)):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            raise ValueError("not a number")
        return float(v)
    s = str(v).strip().replace(",", "")
    try:
        f = float(s)
    except ValueError as exc:
        raise ValueError("not a number") from exc
    if math.isnan(f) or math.isinf(f):
        raise ValueError("not a number")
    return f


# ---------------------------------------------------------------------------
# Normalisation (save time)
# ---------------------------------------------------------------------------

def _norm_scalar(qid: str, kind: str, v: Any, *, options: list[str] | None = None) -> Any:
    """Coerce one input value to its stored form. Numeric strings become
    numbers; text is trimmed. Values that cannot be coerced are kept as
    the raw string so validation can report them, rather than dropped."""
    if _is_blank(v):
        return None
    if kind in NUMERIC_KINDS:
        try:
            n = _to_number(v)
        except ValueError:
            return str(v)
        if n is not None and kind == "count" and n.is_integer():
            return int(n)
        return n
    if kind in TEXT_KINDS:
        if not isinstance(v, (str, int, float)):
            raise PayloadError(qid, "expected text")
        return str(v).strip()
    if kind in ("yesno", "choice"):
        if not isinstance(v, str):
            raise PayloadError(qid, "expected one of the listed options")
        return v.strip()
    raise PayloadError(qid, f"unsupported kind {kind}")  # pragma: no cover


def _common(qid: str, raw: dict, out: dict) -> None:
    if raw.get("na"):
        out["na"] = True
        out["na_reason"] = str(raw.get("na_reason") or "").strip()
    if not _is_blank(raw.get("note")):
        out["note"] = str(raw["note"]).strip()[:MAX_TEXT]


def normalize_answer(qid: str, raw: Any) -> dict:
    q = QUESTIONS_BY_ID.get(qid)
    if q is None:
        raise PayloadError(qid, "unknown question")
    if not isinstance(raw, dict):
        raise PayloadError(qid, "answer must be an object")
    out: dict[str, Any] = {}
    _common(qid, raw, out)
    t = q["type"]

    if t == "text":
        txt = raw.get("text")
        if txt is not None and not isinstance(txt, str):
            raise PayloadError(qid, "text must be a string")
        out["text"] = (txt or "").strip()
    elif t == "yesno":
        ans = raw.get("answer")
        out["answer"] = None if _is_blank(ans) else str(ans).strip()
        for k in ("details", "link"):
            if raw.get(k) is not None:
                if not isinstance(raw[k], str):
                    raise PayloadError(qid, f"{k} must be a string")
                out[k] = raw[k].strip()
    elif t == "number":
        out["value"] = _norm_scalar(qid, q["kind"], raw.get("value"))
    elif t == "fields":
        given = raw.get("fields") or {}
        if not isinstance(given, dict):
            raise PayloadError(qid, "fields must be an object")
        known = {f["key"]: f for f in q["fields"]}
        unknown = set(given) - set(known)
        if unknown:
            raise PayloadError(qid, f"unknown fields {sorted(unknown)}")
        out["fields"] = {k: _norm_scalar(qid, known[k]["kind"], v)
                         for k, v in given.items() if not known[k].get("readonly")}
    elif t == "table":
        given = raw.get("cells") or {}
        if not isinstance(given, dict):
            raise PayloadError(qid, "cells must be an object")
        rows = {r["key"]: r for r in q["rows"]}
        cols = {c["key"]: c for c in q["columns"]}
        cells: dict[str, dict] = {}
        for rk, rv in given.items():
            if rk not in rows:
                raise PayloadError(qid, f"unknown row {rk}")
            if not isinstance(rv, dict):
                raise PayloadError(qid, f"row {rk} must be an object")
            r = rows[rk]
            for ck, v in rv.items():
                if ck not in cols:
                    raise PayloadError(qid, f"unknown column {ck}")
                c = cols[ck]
                # Computed cells are never stored, whatever the client sends.
                if r.get("header") or c["kind"] == "computed":
                    continue
                if r.get("formula") and c["kind"] in NUMERIC_KINDS:
                    continue
                nv = _norm_scalar(qid, c["kind"], v, options=c.get("options"))
                if nv is not None:
                    cells.setdefault(rk, {})[ck] = nv
        out["cells"] = cells
    elif t == "rows":
        given = raw.get("rows") or []
        if not isinstance(given, list):
            raise PayloadError(qid, "rows must be a list")
        if len(given) > 200:
            raise PayloadError(qid, "too many rows")
        cols = {c["key"]: c for c in q["columns"]}
        rows_out = []
        for i, rv in enumerate(given):
            if not isinstance(rv, dict):
                raise PayloadError(qid, f"row {i + 1} must be an object")
            unknown = set(rv) - set(cols)
            if unknown:
                raise PayloadError(qid, f"row {i + 1}: unknown columns {sorted(unknown)}")
            nr = {k: _norm_scalar(qid, cols[k]["kind"], v) for k, v in rv.items()}
            if any(v is not None for v in nr.values()):
                rows_out.append(nr)
        out["rows"] = rows_out
        # "None to report" is an explicit answer, only for questions that
        # allow zero rows, and only while no rows are entered.
        if raw.get("none") and not rows_out and q.get("min_rows", 1) == 0:
            out["none"] = True
    elif t == "principle_grid":
        given = raw.get("cells") or {}
        span = raw.get("span") or {}
        if not isinstance(given, dict) or not isinstance(span, dict):
            raise PayloadError(qid, "cells and span must be objects")
        grows = {g["key"]: g for g in q["grid_rows"]}
        cells: dict[str, dict] = {}
        for rk, rv in given.items():
            if rk not in grows or not isinstance(rv, dict):
                raise PayloadError(qid, f"unknown or malformed grid row {rk}")
            bad = set(rv) - set(PRINCIPLES)
            if bad:
                raise PayloadError(qid, f"unknown principles {sorted(bad)}")
            g = grows[rk]
            vals = {p: _norm_scalar(qid, g["kind"], v) for p, v in rv.items()}
            vals = {p: v for p, v in vals.items() if v is not None}
            if vals:
                cells[rk] = vals
        span_out = {}
        for rk, v in span.items():
            if rk not in grows:
                raise PayloadError(qid, f"unknown grid row {rk}")
            if not grows[rk].get("span_allowed"):
                raise PayloadError(qid, f"row {rk} does not accept one answer for all principles")
            nv = _norm_scalar(qid, grows[rk]["kind"], v)
            if nv is not None:
                span_out[rk] = nv
        out["cells"] = cells
        out["span"] = span_out
    else:  # pragma: no cover
        raise PayloadError(qid, f"unsupported question type {t}")
    return out


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

class Computer:
    """Computes full table grids (inputs + derived cells) for one response
    set. Cross-question references (ref('P6.DEN', 'revenue')) resolve
    through the same instance, memoised, with cycle protection."""

    def __init__(self, answers: dict[str, dict]):
        self.answers = answers
        self._grids: dict[str, dict] = {}
        self._stack: set[tuple[str, str, str]] = set()

    def grid(self, qid: str) -> dict[str, dict[str, Any]]:
        if qid in self._grids:
            return self._grids[qid]
        q = QUESTIONS_BY_ID[qid]
        out: dict[str, dict[str, Any]] = {}
        for r in q["rows"]:
            if r.get("header"):
                continue
            out[r["key"]] = {c["key"]: self.cell(qid, r["key"], c["key"]) for c in q["columns"]}
        self._grids[qid] = out
        return out

    def cell(self, qid: str, rk: str, ck: str) -> Any:
        q = QUESTIONS_BY_ID[qid]
        rows = {r["key"]: r for r in q["rows"]}
        cols = {c["key"]: c for c in q["columns"]}
        r, c = rows[rk], cols[ck]
        if r.get("header"):
            return None
        key = (qid, rk, ck)
        if key in self._stack:
            return None  # cycle guard; bank self-check keeps this unreachable
        self._stack.add(key)
        try:
            if c.get("formula"):
                return F.evaluate(c["formula"], lambda name: self.cell(qid, rk, name),
                                  lambda oq, orow: self._ref(oq, orow, ck))
            if r.get("formula") and c["kind"] in NUMERIC_KINDS:
                return F.evaluate(r["formula"], lambda name: self.cell(qid, name, ck),
                                  lambda oq, orow: self._ref(oq, orow, ck))
            ans = self.answers.get(qid) or {}
            v = (ans.get("cells") or {}).get(rk, {}).get(ck)
            if c["kind"] in NUMERIC_KINDS:
                try:
                    return _to_number(v)
                except ValueError:
                    return None
            return v
        finally:
            self._stack.discard(key)

    def _ref(self, oq: str, orow: str, ck: str) -> Any:
        target = QUESTIONS_BY_ID[oq]
        tcols = {c["key"] for c in target["columns"]}
        if ck not in tcols:
            return None
        return self.cell(oq, orow, ck)

    def scalar(self, cell_ref: tuple[str, str, str]) -> Optional[float]:
        qid, rk, ck = cell_ref
        v = self.cell(qid, rk, ck)
        return v if isinstance(v, (int, float)) else None


def compute_table(qid: str, answers: dict[str, dict]) -> dict[str, dict[str, Any]]:
    return Computer(answers).grid(qid)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def _check_scalar(qid: str, kind: str, v: Any, label: str, path: str, *, options: list[str] | None = None,
                  pattern: str | None = None, pattern_hint: str | None = None,
                  minv: float | None = None, maxv: float | None = None) -> list[dict]:
    if _is_blank(v):
        return []
    issues: list[dict] = []
    if kind in NUMERIC_KINDS:
        try:
            n = _to_number(v)
        except ValueError:
            return [_issue("error", qid, f"{label}: '{v}' is not a number", path)]
        if n is None:
            return []
        if n < 0:
            issues.append(_issue("error", qid, f"{label} cannot be negative", path))
        if kind == "count" and not float(n).is_integer():
            issues.append(_issue("error", qid, f"{label} must be a whole number", path))
        if kind == "percent" and n > 100:
            issues.append(_issue("error", qid, f"{label} cannot exceed 100%", path))
        if kind == "rate" and n > 100:
            issues.append(_issue("warning", qid, f"{label} is above 100 — please confirm", path))
        if minv is not None and n < minv:
            issues.append(_issue("error", qid, f"{label} must be at least {minv:g}", path))
        if maxv is not None and n > maxv:
            issues.append(_issue("error", qid, f"{label} must be at most {maxv:g}", path))
    elif kind in ("yesno",):
        if v not in YESNO_OPTIONS:
            issues.append(_issue("error", qid, f"{label}: choose Yes, No or NA", path))
    elif kind == "choice":
        if options and v not in options:
            issues.append(_issue("error", qid, f"{label}: '{v}' is not one of the allowed options", path))
    elif kind == "email":
        if not EMAIL_RE.match(str(v)):
            issues.append(_issue("error", qid, f"{label}: not a valid e-mail address", path))
    elif kind == "url":
        if not URL_RE.match(str(v)):
            issues.append(_issue("error", qid, f"{label}: not a valid web address", path))
    if kind in TEXT_KINDS and isinstance(v, str) and len(v) > MAX_TEXT:
        issues.append(_issue("error", qid, f"{label} is too long", path))
    if pattern and isinstance(v, str) and not re.fullmatch(pattern, v):
        hint = f" ({pattern_hint})" if pattern_hint else ""
        issues.append(_issue("error", qid, f"{label}: invalid format{hint}", path))
    return issues


def _fmt(n: Any) -> str:
    if isinstance(n, float) and n.is_integer():
        n = int(n)
    if isinstance(n, int):
        return f"{n:,}"
    if isinstance(n, float):
        return f"{n:,.2f}"
    return str(n)


def is_answered(q: dict, ans: Optional[dict]) -> bool:
    if not ans:
        return False
    if ans.get("na"):
        return bool((ans.get("na_reason") or "").strip())
    t = q["type"]
    if t == "text":
        return bool((ans.get("text") or "").strip())
    if t == "yesno":
        return not _is_blank(ans.get("answer"))
    if t == "number":
        return not _is_blank(ans.get("value"))
    if t == "fields":
        f = ans.get("fields") or {}
        return all(not _is_blank(f.get(fd["key"])) for fd in q["fields"]
                   if fd.get("required", True) and not fd.get("readonly"))
    if t == "table":
        return any(not _is_blank(v) for rv in (ans.get("cells") or {}).values() for v in rv.values())
    if t == "rows":
        if ans.get("none") and q.get("min_rows", 1) == 0:
            return True
        return len(ans.get("rows") or []) >= max(1, q.get("min_rows", 1))
    if t == "principle_grid":
        cells = ans.get("cells") or {}
        span = ans.get("span") or {}
        for g in q["grid_rows"]:
            if g["key"] in span:
                continue
            row = cells.get(g["key"]) or {}
            if g["kind"] == "yesno" and not all(p in row for p in PRINCIPLES):
                return False
        return bool(cells or span)
    return False  # pragma: no cover


def required_ids(answers: dict[str, dict]) -> set[str]:
    req = {q["id"] for q in QUESTIONS if q["required"]}
    for (qid, dep, when) in CONDITIONAL_REQUIRED:
        dep_ans = answers.get(dep) or {}
        if dep_ans.get("answer") == when:
            req.add(qid)
        else:
            req.discard(qid)
    return req


def validate_answer(qid: str, ans: Optional[dict], answers: dict[str, dict],
                    computer: Optional[Computer] = None) -> list[dict]:
    """Errors and warnings for one stored answer (no completeness)."""
    q = QUESTIONS_BY_ID[qid]
    if not ans:
        return []
    issues: list[dict] = []
    if ans.get("na"):
        if not (ans.get("na_reason") or "").strip():
            issues.append(_issue("error", qid, "Give a short reason when marking a question not applicable"))
        return issues
    t = q["type"]

    if t == "text":
        txt = ans.get("text") or ""
        if q.get("max_words") and len(txt.split()) > q["max_words"]:
            issues.append(_issue("error", qid, f"Keep this answer within {q['max_words']} words "
                                               f"(currently {len(txt.split())})"))
    elif t == "yesno":
        a = ans.get("answer")
        if a and a not in YESNO_OPTIONS:
            issues.append(_issue("error", qid, "Choose Yes, No or NA"))
        if q.get("details_required_when") and a == q["details_required_when"] and \
                _is_blank(ans.get("details")):
            issues.append(_issue("error", qid, f"{q.get('details_label') or 'Details'} required when the answer "
                                               f"is {a}", "details"))
        if q.get("link") and ans.get("link") and not URL_RE.match(ans["link"]):
            issues.append(_issue("error", qid, "Policy web-link is not a valid web address", "link"))
    elif t == "number":
        issues += _check_scalar(qid, q["kind"], ans.get("value"), "Value", "value")
    elif t == "fields":
        f = ans.get("fields") or {}
        for fd in q["fields"]:
            issues += _check_scalar(qid, fd["kind"], f.get(fd["key"]), fd["label"], f"fields.{fd['key']}",
                                    options=fd.get("options"), pattern=fd.get("pattern"),
                                    pattern_hint=fd.get("pattern_hint"), minv=fd.get("min"), maxv=fd.get("max"))
    elif t == "table":
        comp = computer or Computer(answers)
        cells = ans.get("cells") or {}
        cols = {c["key"]: c for c in q["columns"]}
        rows = {r["key"]: r for r in q["rows"]}
        for rk, rv in cells.items():
            for ck, v in rv.items():
                c = cols[ck]
                issues += _check_scalar(qid, c["kind"], v, f"{rows[rk]['label']} / {c['label'] or ck}",
                                        f"cells.{rk}.{ck}", options=c.get("options"))
        # Constraints are checked on computed grids so totals rows are covered too.
        grid = comp.grid(qid)
        for rk, rv in grid.items():
            for c in q["columns"]:
                if "lte" in c:
                    a, b = rv.get(c["key"]), rv.get(c["lte"])
                    if isinstance(a, (int, float)) and isinstance(b, (int, float)) and a > b:
                        other = cols[c["lte"]]
                        issues.append(_issue(
                            "error", qid,
                            f"{rows[rk]['label']}: {c['label']} ({_fmt(a)}) cannot exceed "
                            f"{other['label']} ({_fmt(b)})", f"cells.{rk}.{c['key']}"))
        if qid in ("A.20a", "A.20b"):
            for rk, rv in grid.items():
                t_, m, fe = rv.get("total"), rv.get("male"), rv.get("female")
                if all(isinstance(x, (int, float)) for x in (t_, m, fe)) and m + fe != t_:
                    issues.append(_issue(
                        "warning", qid, f"{rows[rk]['label']}: Male ({_fmt(m)}) + Female ({_fmt(fe)}) does not "
                                        f"equal Total ({_fmt(t_)}). Fine only if other genders are included.",
                        f"cells.{rk}.total"))
        if qid == "P6.E9":
            for p in ("cy_v", "py_v"):
                tot = grid.get("total", {}).get(p)
                handled = (grid.get("recovered_total", {}).get(p) or 0) + (grid.get("disposed_total", {}).get(p) or 0)
                if isinstance(tot, (int, float)) and handled > tot + 1e-9:
                    issues.append(_issue("warning", qid, f"Waste recovered + disposed ({_fmt(handled)}) is more than "
                                                         f"waste generated ({_fmt(tot)}) — please check",
                                         f"cells.total.{p}"))
    elif t == "rows":
        cols = {c["key"]: c for c in q["columns"]}
        rws = ans.get("rows") or []
        if q.get("max_rows") and len(rws) > q["max_rows"]:
            issues.append(_issue("error", qid, f"At most {q['max_rows']} rows are allowed"))
        for i, rv in enumerate(rws):
            for ck, v in rv.items():
                issues += _check_scalar(qid, cols[ck]["kind"], v, f"Row {i + 1} / {cols[ck]['label']}",
                                        f"rows.{i}.{ck}", options=cols[ck].get("options"))
        checks = q.get("checks") or []
        if "sum_pct_turnover" in checks and rws:
            total = 0.0
            for rv in rws:
                try:
                    total += _to_number(rv.get("pct_turnover")) or 0
                except ValueError:
                    pass
            if total > 100 + 1e-9:
                issues.append(_issue("error", qid, f"% of turnover adds up to {total:.2f}% — cannot exceed 100%"))
            elif total < 90 - 1e-9:
                issues.append(_issue("warning", qid, f"Listed items account for {total:.2f}% of turnover; SEBI asks "
                                                     f"for items covering at least 90%"))
        if "nic_code" in checks:
            for i, rv in enumerate(rws):
                v = rv.get("nic_code")
                if not _is_blank(v) and not NIC_RE.match(str(v)):
                    issues.append(_issue("error", qid, f"Row {i + 1}: NIC code should be 2–5 digits (NIC 2008)",
                                         f"rows.{i}.nic_code"))
    elif t == "principle_grid":
        grows = {g["key"]: g for g in q["grid_rows"]}
        for rk, rv in (ans.get("cells") or {}).items():
            g = grows[rk]
            for p, v in rv.items():
                issues += _check_scalar(qid, g["kind"], v, f"{p} / {g['label'][:60]}", f"cells.{rk}.{p}",
                                        options=g.get("options"))
    return issues


def consistency_warnings(answers: dict[str, dict], computer: Optional[Computer] = None) -> list[dict]:
    comp = computer or Computer(answers)
    out = []
    for (label, left, right) in CONSISTENCY_CHECKS:
        cells = right if isinstance(right, list) else [right]
        lq = left[0]
        if (answers.get(lq) or {}).get("na") or any((answers.get(c[0]) or {}).get("na") for c in cells):
            continue
        a = comp.scalar(left)
        vals = [comp.scalar(c) for c in cells]
        if a is None or any(v is None for v in vals):
            continue
        b = sum(vals)  # type: ignore[arg-type]
        if abs(a - b) > 1e-9:
            out.append(_issue("warning", lq, f"{label} ({_fmt(a)} vs {_fmt(b)})", "consistency"))
    return out


def section_report(section: str, answers: dict[str, dict]) -> dict:
    """Completeness + issues for one section, as shown in the questionnaire
    and enforced at submission."""
    comp = Computer(answers)
    req = required_ids(answers)
    questions = [q for q in QUESTIONS if q["section"] == section]
    missing, issues = [], []
    answered = 0
    for q in questions:
        a = answers.get(q["id"])
        ok = is_answered(q, a)
        if ok:
            answered += 1
        elif q["id"] in req:
            missing.append(q["id"])
        issues += validate_answer(q["id"], a, answers, comp)
    section_ids = {q["id"] for q in questions}
    issues += [w for w in consistency_warnings(answers, comp) if w["question_id"] in section_ids]
    required_total = len([q for q in questions if q["id"] in req])
    required_answered = required_total - len(missing)
    return {
        "section": section,
        "questions": len(questions),
        "answered": answered,
        "required": required_total,
        "required_answered": required_answered,
        "missing": missing,
        "errors": [i for i in issues if i["level"] == "error"],
        "warnings": [i for i in issues if i["level"] == "warning"],
        "ready_to_submit": not missing and not any(i["level"] == "error" for i in issues),
    }


def computed_view(qid: str, answers: dict[str, dict], computer: Optional[Computer] = None) -> Optional[dict]:
    """Derived cells for display alongside the stored inputs (tables only)."""
    q = QUESTIONS_BY_ID[qid]
    if q["type"] != "table":
        return None
    comp = computer or Computer(answers)
    grid = comp.grid(qid)
    rows = {r["key"]: r for r in q["rows"]}
    cols = {c["key"]: c for c in q["columns"]}
    out: dict[str, dict] = {}
    for rk, rv in grid.items():
        for ck, v in rv.items():
            if cols[ck]["kind"] == "computed" or (rows[rk].get("formula") and cols[ck]["kind"] in NUMERIC_KINDS):
                out.setdefault(rk, {})[ck] = v
    return out


Lookup = Callable[[str], Any]
