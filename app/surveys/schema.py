"""Survey definitions: structure, conditional logic, answer validation, scoring.

Pure functions — no database access — so the same rules run in the builder,
on the respondent's form, at submission and when re-scoring.

Definition (JSON stored in ``surveys.definition``)::

    {"sections": [
        {"id": "s1", "title": "Governance", "description": "", "weight": 1,
         "show_if": <condition>|null,
         "questions": [
            {"id": "q1", "type": "single_choice", "title": "…", "help": "…",
             "required": true, "weight": 1, "show_if": <condition>|null,
             "options": [{"id": "o1", "label": "Yes", "score": 4},
                         {"id": "o2", "label": "Not applicable", "na": true}],
             "allow_other": false},
            …]}],
     "bands": [{"min": 0, "label": "Beginning"}, {"min": 40, "label": "Developing"}, …]}

Condition::

    {"match": "all"|"any", "rules": [{"question": "q1", "op": "equals", "value": "o1"}, …]}

A rule may only refer to a question that comes *earlier* in the survey, so
logic can never loop and a respondent always sees the cause before the effect.

Answers (``{question_id: value}``):

- short_text / long_text: string
- single_choice / dropdown / yes_no: option id; ``"__other__"`` with the text
  in ``answers["<qid>.other"]``
- multi_choice: list of option ids (may include ``"__other__"``)
- scale / nps: integer; number / percent: number; date: "YYYY-MM-DD"
- matrix: {row_id: column_id}
- file: list of survey-file public ids
- statement: no answer (a block of text between questions)

Scoring (assessments): each scored question earns ``points`` out of
``max``; the question's share (points / max) is weighted within its section;
section percentages are weighted into the total. Questions that are hidden by
logic, answered "not applicable", or have no scores at all don't count.
Unanswered visible scored questions count as zero — an assessment rewards
evidence of practice, and silence is not evidence.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from datetime import date
from typing import Any, Iterable, Optional

from app.core.errors import AppError

OTHER = "__other__"
ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")

CHOICE_TYPES = {"single_choice", "dropdown", "yes_no"}
OPTION_TYPES = CHOICE_TYPES | {"multi_choice"}
NUMERIC_TYPES = {"scale", "nps", "number", "percent"}
TEXT_TYPES = {"short_text", "long_text"}
QUESTION_TYPES = OPTION_TYPES | NUMERIC_TYPES | TEXT_TYPES | {"date", "matrix", "file", "statement"}
SCORABLE_TYPES = OPTION_TYPES | {"scale", "matrix"}

TYPE_LABELS = {
    "short_text": "Short text", "long_text": "Paragraph", "single_choice": "Single choice",
    "multi_choice": "Multiple choice", "dropdown": "Dropdown", "yes_no": "Yes / No", "scale": "Rating scale",
    "nps": "Net Promoter Score", "number": "Number", "percent": "Percentage", "date": "Date",
    "matrix": "Matrix / grid", "file": "File upload", "statement": "Text block",
}

# Which condition operators make sense for which question types
OPS_BY_TYPE = {
    "single_choice": {"equals", "not_equals", "answered", "not_answered"},
    "dropdown": {"equals", "not_equals", "answered", "not_answered"},
    "yes_no": {"equals", "not_equals", "answered", "not_answered"},
    "multi_choice": {"includes", "excludes", "answered", "not_answered"},
    "scale": {"equals", "not_equals", "gte", "lte", "answered", "not_answered"},
    "nps": {"equals", "not_equals", "gte", "lte", "answered", "not_answered"},
    "number": {"equals", "not_equals", "gte", "lte", "answered", "not_answered"},
    "percent": {"equals", "not_equals", "gte", "lte", "answered", "not_answered"},
    "short_text": {"answered", "not_answered"},
    "long_text": {"answered", "not_answered"},
    "date": {"answered", "not_answered"},
    "matrix": {"answered", "not_answered"},
    "file": {"answered", "not_answered"},
}
VALUE_OPS = {"equals", "not_equals", "includes", "excludes", "gte", "lte"}

LIMITS = {"sections": 40, "questions": 250, "options": 60, "rows": 40, "columns": 12, "bands": 8,
          "title": 500, "help": 2000, "label": 300, "short_text": 500, "long_text": 10000, "files": 10}

YES_NO_DEFAULT = [{"id": "yes", "label": "Yes"}, {"id": "no", "label": "No"}]


class DefinitionError(AppError):
    def __init__(self, message: str, path: str | None = None):
        super().__init__("invalid_definition", message, 422, "definition",
                         details={"path": path} if path else None)


class AnswerError(AppError):
    def __init__(self, errors: dict[str, str]):
        n = len(errors)
        super().__init__("invalid_answers", f"{n} answer{'s' if n != 1 else ''} need{'s' if n == 1 else ''} attention",
                         422, "answers", details={"errors": errors})


# ---------------------------------------------------------------------------
# Definition: normalise and check
# ---------------------------------------------------------------------------

def _text(v: Any, limit: int) -> str:
    return ("" if v is None else str(v)).strip()[:limit]


def _num(v: Any, path: str, *, allow_none: bool = True) -> Optional[float]:
    if v is None or v == "":
        if allow_none:
            return None
        raise DefinitionError("A number is required", path)
    if isinstance(v, bool):
        raise DefinitionError("Expected a number", path)
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise DefinitionError("Expected a number", path) from None
    if not math.isfinite(f):
        raise DefinitionError("Expected a finite number", path)
    return int(f) if f.is_integer() else f


def _id(v: Any, path: str, seen: set[str]) -> str:
    s = str(v or "").strip()
    if not ID_RE.match(s):
        raise DefinitionError("Ids may only use letters, digits, '-' and '_' (max 40)", path)
    if s in seen:
        raise DefinitionError(f"Duplicate id '{s}'", path)
    if s == OTHER:
        raise DefinitionError(f"'{OTHER}' is reserved", path)
    seen.add(s)
    return s


def _options(raw: Any, path: str, *, scored_allowed: bool = True, extra: tuple[str, ...] = ()) -> list[dict]:
    if not isinstance(raw, list):
        raise DefinitionError("Options must be a list", path)
    if len(raw) > LIMITS["options"]:
        raise DefinitionError(f"At most {LIMITS['options']} options", path)
    seen: set[str] = set()
    out = []
    for i, o in enumerate(raw):
        p = f"{path}[{i}]"
        if not isinstance(o, dict):
            raise DefinitionError("Each option must be an object", p)
        opt = {"id": _id(o.get("id"), p + ".id", seen), "label": _text(o.get("label"), LIMITS["label"])}
        score = _num(o.get("score"), p + ".score")
        if score is not None and scored_allowed:
            opt["score"] = score
        for flag in ("na", "exclusive") + extra:
            if o.get(flag):
                opt[flag] = True
        out.append(opt)
    return out


def _condition(raw: Any, path: str, earlier: dict[str, dict]) -> Optional[dict]:
    if not raw:
        return None
    if not isinstance(raw, dict):
        raise DefinitionError("A condition must be an object", path)
    rules = raw.get("rules")
    if rules is None and "question" in raw:  # single-rule shorthand
        rules = [raw]
    if not isinstance(rules, list) or not rules:
        return None
    match = raw.get("match", "all")
    if match not in ("all", "any"):
        raise DefinitionError("match must be 'all' or 'any'", path)
    out = []
    for i, r in enumerate(rules[:10]):
        p = f"{path}.rules[{i}]"
        if not isinstance(r, dict):
            raise DefinitionError("Each rule must be an object", p)
        qid = str(r.get("question") or "")
        ref = earlier.get(qid)
        if not ref:
            raise DefinitionError("A rule can only refer to a question that comes before it", p)
        op = r.get("op")
        if op not in OPS_BY_TYPE.get(ref["type"], set()):
            raise DefinitionError(f"'{op}' can't be used with a {TYPE_LABELS[ref['type']].lower()} question", p)
        rule = {"question": qid, "op": op}
        if op in VALUE_OPS:
            v = r.get("value")
            if ref["type"] in OPTION_TYPES:
                ids = {o["id"] for o in ref.get("options", [])} | ({OTHER} if ref.get("allow_other") else set())
                if v not in ids:
                    raise DefinitionError("The rule's answer is not one of that question's options", p)
                rule["value"] = v
            else:
                rule["value"] = _num(v, p + ".value", allow_none=False)
        out.append(rule)
    return {"match": match, "rules": out}


def _question(raw: Any, path: str, ids: set[str], earlier: dict[str, dict]) -> dict:
    if not isinstance(raw, dict):
        raise DefinitionError("Each question must be an object", path)
    qid = _id(raw.get("id"), path + ".id", ids)
    qtype = raw.get("type")
    if qtype not in QUESTION_TYPES:
        raise DefinitionError(f"Unknown question type '{qtype}'", path + ".type")
    q: dict[str, Any] = {"id": qid, "type": qtype, "title": _text(raw.get("title"), LIMITS["title"]),
                         "help": _text(raw.get("help"), LIMITS["help"])}
    if qtype != "statement":
        q["required"] = bool(raw.get("required"))
    w = _num(raw.get("weight"), path + ".weight")
    if w is not None:
        if w < 0 or w > 100:
            raise DefinitionError("Weight must be between 0 and 100", path + ".weight")
        q["weight"] = w

    if qtype in OPTION_TYPES:
        opts = raw.get("options")
        if qtype == "yes_no" and not opts:
            opts = YES_NO_DEFAULT
        q["options"] = _options(opts or [], path + ".options")
        if qtype in ("single_choice", "multi_choice") and raw.get("allow_other"):
            q["allow_other"] = True
        if qtype == "multi_choice":
            for key in ("min_select", "max_select"):
                v = _num(raw.get(key), f"{path}.{key}")
                if v is not None:
                    if not float(v).is_integer() or v < 0:
                        raise DefinitionError("Must be a whole number", f"{path}.{key}")
                    q[key] = int(v)
            if q.get("min_select") and q.get("max_select") and q["min_select"] > q["max_select"]:
                raise DefinitionError("Minimum selections is above the maximum", path)
    elif qtype == "scale":
        lo = _num(raw.get("min", 1), path + ".min", allow_none=False)
        hi = _num(raw.get("max", 5), path + ".max", allow_none=False)
        if lo not in (0, 1) or not float(hi).is_integer() or not (lo < hi <= 10):
            raise DefinitionError("A scale runs from 0 or 1 up to at most 10", path)
        q.update(min=int(lo), max=int(hi), min_label=_text(raw.get("min_label"), 80),
                 max_label=_text(raw.get("max_label"), 80))
        if raw.get("scored") is False:
            q["scored"] = False
    elif qtype == "nps":
        q.update(min_label=_text(raw.get("min_label") or "Not at all likely", 80),
                 max_label=_text(raw.get("max_label") or "Extremely likely", 80))
    elif qtype in ("number", "percent"):
        lo, hi = _num(raw.get("min"), path + ".min"), _num(raw.get("max"), path + ".max")
        if qtype == "percent":
            lo = 0 if lo is None else lo
            hi = 100 if hi is None else hi
        if lo is not None and hi is not None and lo > hi:
            raise DefinitionError("Minimum is above the maximum", path)
        q.update(min=lo, max=hi)
        if qtype == "number":
            q["unit"] = _text(raw.get("unit"), 40)
            if raw.get("integer"):
                q["integer"] = True
    elif qtype in TEXT_TYPES:
        limit = LIMITS[qtype]
        ml = _num(raw.get("max_length"), path + ".max_length")
        q["max_length"] = int(min(limit, ml)) if ml and ml > 0 else limit
        q["placeholder"] = _text(raw.get("placeholder"), 120)
    elif qtype == "matrix":
        rows = raw.get("rows") or []
        cols = raw.get("columns") or []
        if not isinstance(rows, list) or not isinstance(cols, list):
            raise DefinitionError("Rows and columns must be lists", path)
        if len(rows) > LIMITS["rows"] or len(cols) > LIMITS["columns"]:
            raise DefinitionError(f"At most {LIMITS['rows']} rows and {LIMITS['columns']} columns", path)
        q["rows"] = _options(rows, path + ".rows", scored_allowed=False)
        q["columns"] = _options(cols, path + ".columns")
    elif qtype == "file":
        mf = _num(raw.get("max_files"), path + ".max_files")
        q["max_files"] = int(max(1, min(LIMITS["files"], mf or 3)))

    if raw.get("show_if"):
        q["show_if"] = _condition(raw["show_if"], path + ".show_if", earlier)
        if not q["show_if"]:
            q.pop("show_if")
    return q


def normalize_definition(raw: Any) -> dict:
    """Check the structure and return a clean copy. Raises DefinitionError.

    Content that is merely unfinished (an empty title, a choice with no
    options) is allowed here so the builder can autosave; ``publish_problems``
    reports it before the survey goes live.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise DefinitionError("The definition must be an object")
    sections = raw.get("sections") or []
    if not isinstance(sections, list):
        raise DefinitionError("sections must be a list", "sections")
    if len(sections) > LIMITS["sections"]:
        raise DefinitionError(f"At most {LIMITS['sections']} sections", "sections")
    ids: set[str] = set()
    earlier: dict[str, dict] = {}
    out_sections = []
    total = 0
    for si, s in enumerate(sections):
        p = f"sections[{si}]"
        if not isinstance(s, dict):
            raise DefinitionError("Each section must be an object", p)
        sec: dict[str, Any] = {"id": _id(s.get("id"), p + ".id", ids), "title": _text(s.get("title"), LIMITS["title"]),
                               "description": _text(s.get("description"), LIMITS["help"])}
        w = _num(s.get("weight"), p + ".weight")
        if w is not None:
            if w < 0 or w > 100:
                raise DefinitionError("Weight must be between 0 and 100", p + ".weight")
            sec["weight"] = w
        if s.get("show_if"):
            cond = _condition(s["show_if"], p + ".show_if", earlier)
            if cond:
                sec["show_if"] = cond
        qs = s.get("questions") or []
        if not isinstance(qs, list):
            raise DefinitionError("questions must be a list", p + ".questions")
        total += len(qs)
        if total > LIMITS["questions"]:
            raise DefinitionError(f"At most {LIMITS['questions']} questions in a survey", p)
        sec["questions"] = []
        for qi, q in enumerate(qs):
            nq = _question(q, f"{p}.questions[{qi}]", ids, earlier)
            sec["questions"].append(nq)
            if nq["type"] != "statement":
                earlier[nq["id"]] = nq
        out_sections.append(sec)

    bands_raw = raw.get("bands") or []
    if not isinstance(bands_raw, list) or len(bands_raw) > LIMITS["bands"]:
        raise DefinitionError(f"bands must be a list of at most {LIMITS['bands']}", "bands")
    bands = []
    for bi, b in enumerate(bands_raw):
        if not isinstance(b, dict):
            raise DefinitionError("Each band must be an object", f"bands[{bi}]")
        mn = _num(b.get("min"), f"bands[{bi}].min", allow_none=False)
        if not 0 <= mn <= 100:
            raise DefinitionError("A band starts between 0 and 100 %", f"bands[{bi}].min")
        bands.append({"min": mn, "label": _text(b.get("label"), 80), "description": _text(b.get("description"), 500)})
    bands.sort(key=lambda b: b["min"])
    if len({b["min"] for b in bands}) != len(bands):
        raise DefinitionError("Two bands start at the same score", "bands")
    return {"sections": out_sections, "bands": bands}


def iter_questions(defn: dict) -> Iterable[tuple[dict, dict]]:
    for s in defn.get("sections", []):
        for q in s.get("questions", []):
            yield s, q


def questions_by_id(defn: dict) -> dict[str, dict]:
    return {q["id"]: q for _, q in iter_questions(defn)}


def answerable(defn: dict) -> list[dict]:
    return [q for _, q in iter_questions(defn) if q["type"] != "statement"]


def has_scoring(defn: dict) -> bool:
    return any(question_max(q) > 0 for q in answerable(defn))


def publish_problems(defn: dict, kind: str) -> list[dict]:
    """Things to fix before a survey can go live, in builder order."""
    problems: list[dict] = []

    def add(msg, section=None, question=None):
        problems.append({"message": msg, "section": section, "question": question})

    if not answerable(defn):
        add("Add at least one question.")
    for s in defn.get("sections", []):
        if not s["questions"]:
            add(f"Section '{s['title'] or 'Untitled'}' has no questions.", s["id"])
        for n, q in enumerate(s["questions"], 1):
            label = q["title"] or f"Question {n} in '{s['title'] or 'Untitled'}'"
            if not q["title"]:
                add(f"{label} has no text.", s["id"], q["id"])
            if q["type"] in OPTION_TYPES:
                if len(q["options"]) < (1 if q["type"] == "multi_choice" else 2):
                    add(f"'{label}' needs at least {1 if q['type'] == 'multi_choice' else 2} options.", s["id"], q["id"])
                if any(not o["label"] for o in q["options"]):
                    add(f"'{label}' has an option with no text.", s["id"], q["id"])
            if q["type"] == "matrix":
                if not q["rows"] or len(q["columns"]) < 2:
                    add(f"'{label}' needs at least one row and two columns.", s["id"], q["id"])
                if any(not o["label"] for o in q["rows"] + q["columns"]):
                    add(f"'{label}' has a row or column with no text.", s["id"], q["id"])
    if kind == "assessment":
        if not has_scoring(defn):
            add("An assessment needs scores: give points to the answers of at least one question.")
        if defn.get("bands") and defn["bands"][0]["min"] != 0:
            add("The lowest maturity band must start at 0 %.")
        if any(not b["label"] for b in defn.get("bands", [])):
            add("Every maturity band needs a name.")
    return problems


def structure_signature(defn: dict) -> dict:
    """What existing answers depend on: question ids and types, and the ids
    of their options, rows and columns."""
    sig = {}
    for _, q in iter_questions(defn):
        if q["type"] == "statement":
            continue
        sig[q["id"]] = {
            "type": q["type"],
            "options": sorted(o["id"] for o in q.get("options", [])),
            "rows": sorted(o["id"] for o in q.get("rows", [])),
            "columns": sorted(o["id"] for o in q.get("columns", [])),
        }
    return sig


def locked_changes(old: dict, new: dict) -> list[str]:
    """Changes that would orphan existing answers. Rewording, adding
    questions or options, logic and scoring are all fine."""
    o, n = structure_signature(old), structure_signature(new)
    titles = {q["id"]: q["title"] for _, q in iter_questions(old)}
    problems = []
    for qid, sig in o.items():
        name = titles.get(qid) or qid
        if qid not in n:
            problems.append(f"'{name}' was removed")
        elif n[qid]["type"] != sig["type"]:
            problems.append(f"'{name}' changed type")
        else:
            for part, what in (("options", "an option"), ("rows", "a row"), ("columns", "a column")):
                if set(sig[part]) - set(n[qid][part]):
                    problems.append(f"'{name}' lost {what}")
    return problems


def respondent_view(defn: dict) -> dict:
    """The definition as a respondent sees it: no scores, weights or bands
    (people answer what is true, not what earns points)."""
    out = {"sections": []}
    for s in defn.get("sections", []):
        sec = {k: v for k, v in s.items() if k not in ("weight", "questions")}
        sec["questions"] = []
        for q in s["questions"]:
            qq = {k: v for k, v in q.items() if k != "weight"}
            for part in ("options", "columns"):
                if part in qq:
                    qq[part] = [{k: v for k, v in o.items() if k != "score"} for o in qq[part]]
            sec["questions"].append(qq)
        out["sections"].append(sec)
    return out


def definition_hash(defn: dict) -> str:
    return hashlib.sha256(json.dumps(defn, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Conditional logic
# ---------------------------------------------------------------------------

def _is_answered(q: dict, v: Any) -> bool:
    if v is None:
        return False
    if isinstance(v, str):
        return v.strip() != ""
    if isinstance(v, (list, dict)):
        return len(v) > 0
    return True


def _rule_holds(rule: dict, qmap: dict, answers: dict, visible: dict[str, bool]) -> bool:
    qid = rule["question"]
    q = qmap.get(qid)
    v = answers.get(qid) if (q and visible.get(qid)) else None
    op = rule["op"]
    if op == "answered":
        return _is_answered(q, v)
    if op == "not_answered":
        return not _is_answered(q, v)
    if v is None:
        return False
    target = rule.get("value")
    if op == "includes":
        return isinstance(v, list) and target in v
    if op == "excludes":
        return isinstance(v, list) and target not in v
    if op in ("equals", "not_equals"):
        same = v == target
        if not same and isinstance(v, (int, float)) and isinstance(target, (int, float)):
            same = float(v) == float(target)
        return same if op == "equals" else not same
    if not isinstance(v, (int, float)) or isinstance(v, bool):
        return False
    return v >= target if op == "gte" else v <= target


def _cond_holds(cond: Optional[dict], qmap: dict, answers: dict, visible: dict[str, bool]) -> bool:
    if not cond:
        return True
    results = [_rule_holds(r, qmap, answers, visible) for r in cond["rules"]]
    return all(results) if cond["match"] == "all" else any(results)


def visibility(defn: dict, answers: dict) -> dict[str, bool]:
    """question id → shown? A question inside a hidden section is hidden; a
    rule about a hidden question sees no answer."""
    qmap = questions_by_id(defn)
    visible: dict[str, bool] = {}
    for s in defn.get("sections", []):
        sec_on = _cond_holds(s.get("show_if"), qmap, answers, visible)
        for q in s["questions"]:
            visible[q["id"]] = sec_on and _cond_holds(q.get("show_if"), qmap, answers, visible)
    return visible


# ---------------------------------------------------------------------------
# Answers
# ---------------------------------------------------------------------------

def _check_value(q: dict, v: Any, answers: dict) -> tuple[Any, Optional[str]]:
    """(clean value, error). A blank answer comes back as (None, None)."""
    t = q["type"]
    if v is None or (isinstance(v, str) and not v.strip()) or (isinstance(v, (list, dict)) and not v):
        return None, None
    if t in TEXT_TYPES:
        if not isinstance(v, str):
            return None, "Expected text"
        v = v.strip()
        if len(v) > q.get("max_length", LIMITS[t]):
            return None, f"Keep it under {q.get('max_length', LIMITS[t])} characters"
        return v, None
    if t in CHOICE_TYPES:
        ids = {o["id"] for o in q["options"]}
        if v == OTHER and q.get("allow_other"):
            if not str(answers.get(f"{q['id']}.other") or "").strip():
                return None, "Please say what 'Other' is"
            return v, None
        if v not in ids:
            return None, "Choose one of the options"
        return v, None
    if t == "multi_choice":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return None, "Choose from the options"
        ids = {o["id"] for o in q["options"]} | ({OTHER} if q.get("allow_other") else set())
        picked = list(dict.fromkeys(v))
        if any(x not in ids for x in picked):
            return None, "Choose from the options"
        exclusive = {o["id"] for o in q["options"] if o.get("exclusive")}
        if len(picked) > 1 and exclusive & set(picked):
            label = next(o["label"] for o in q["options"] if o["id"] in exclusive & set(picked))
            return None, f"'{label}' can't be combined with other answers"
        if OTHER in picked and not str(answers.get(f"{q['id']}.other") or "").strip():
            return None, "Please say what 'Other' is"
        if q.get("max_select") and len(picked) > q["max_select"]:
            return None, f"Choose at most {q['max_select']}"
        if q.get("min_select") and len(picked) < q["min_select"]:
            return None, f"Choose at least {q['min_select']}"
        return picked, None
    if t in NUMERIC_TYPES:
        if isinstance(v, bool):
            return None, "Expected a number"
        if isinstance(v, str):
            try:
                v = float(v.replace(",", "").strip())
            except ValueError:
                return None, "Expected a number"
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            return None, "Expected a number"
        if t in ("scale", "nps"):
            lo, hi = (0, 10) if t == "nps" else (q["min"], q["max"])
            if not float(v).is_integer() or not lo <= v <= hi:
                return None, f"Choose a value from {lo} to {hi}"
            return int(v), None
        if q.get("integer") and not float(v).is_integer():
            return None, "Enter a whole number"
        if q.get("min") is not None and v < q["min"]:
            return None, f"Must be at least {q['min']}"
        if q.get("max") is not None and v > q["max"]:
            return None, f"Must be at most {q['max']}"
        return (int(v) if float(v).is_integer() else v), None
    if t == "date":
        try:
            return date.fromisoformat(str(v)[:10]).isoformat(), None
        except ValueError:
            return None, "Enter a date (YYYY-MM-DD)"
    if t == "matrix":
        if not isinstance(v, dict):
            return None, "Expected an answer per row"
        rows = {r["id"] for r in q["rows"]}
        cols = {c["id"] for c in q["columns"]}
        clean = {}
        for r, c in v.items():
            if c in (None, ""):
                continue
            if r not in rows or c not in cols:
                return None, "Choose a column for each row"
            clean[r] = c
        return (clean or None), None
    if t == "file":
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            return None, "Expected uploaded files"
        v = list(dict.fromkeys(v))
        if len(v) > q.get("max_files", 3):
            return None, f"At most {q.get('max_files', 3)} files"
        return v, None
    return None, None


def clean_answers(defn: dict, answers: Any, *, final: bool) -> dict:
    """Validate answers against the definition.

    Draft (``final=False``): checks what is there, ignores unknown keys and
    doesn't enforce "required" — people save half-way.
    Final: also requires every visible required question, and drops answers
    to questions that logic has hidden (so a changed mind leaves no trace).
    Raises AnswerError with one message per question.
    """
    if answers is None:
        answers = {}
    if not isinstance(answers, dict):
        raise AppError("invalid_answers", "Answers must be an object", 422, "answers")
    qmap = questions_by_id(defn)
    clean: dict[str, Any] = {}
    errors: dict[str, str] = {}
    for qid, q in qmap.items():
        if q["type"] == "statement" or qid not in answers:
            continue
        v, err = _check_value(q, answers.get(qid), answers)
        if err:
            errors[qid] = err
        elif v is not None:
            clean[qid] = v
            if (v == OTHER or (isinstance(v, list) and OTHER in v)):
                clean[f"{qid}.other"] = str(answers.get(f"{qid}.other")).strip()[:LIMITS["short_text"]]
    vis = visibility(defn, clean)
    if final:
        clean = {k: v for k, v in clean.items() if vis.get(k.split(".")[0], False)}
        errors = {k: v for k, v in errors.items() if vis.get(k, False)}
        for qid, q in qmap.items():
            if not vis.get(qid) or q["type"] == "statement" or qid in errors:
                continue
            if q.get("required"):
                v = clean.get(qid)
                if v is None:
                    errors[qid] = "This question needs an answer"
                elif q["type"] == "matrix" and len(v) < len(q["rows"]):
                    errors[qid] = "Answer every row"
    if errors:
        raise AnswerError(errors)
    return clean


def progress(defn: dict, answers: dict) -> dict:
    vis = visibility(defn, answers)
    shown = [q for q in answerable(defn) if vis.get(q["id"])]
    done = [q for q in shown if _is_answered(q, answers.get(q["id"]))]
    return {"answered": len(done), "total": len(shown),
            "percent": round(100 * len(done) / len(shown)) if shown else 0}


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

def question_max(q: dict) -> float:
    t = q["type"]
    if t in CHOICE_TYPES:
        scores = [o["score"] for o in q["options"] if "score" in o and not o.get("na")]
        return max(scores) if scores else 0
    if t == "multi_choice":
        return sum(o["score"] for o in q["options"] if o.get("score", 0) > 0 and not o.get("na"))
    if t == "scale":
        return 0 if q.get("scored") is False else q["max"] - q["min"]
    if t == "matrix":
        scores = [c["score"] for c in q["columns"] if "score" in c and not c.get("na")]
        return max(scores) * len(q["rows"]) if scores else 0
    return 0


def question_points(q: dict, v: Any) -> Optional[float]:
    """Points earned, or None when the answer takes the question out of
    scoring (not applicable)."""
    t = q["type"]
    if v is None:
        return 0
    if t in CHOICE_TYPES:
        opt = next((o for o in q["options"] if o["id"] == v), None)
        if opt is None:  # "Other"
            return 0
        if opt.get("na"):
            return None
        return opt.get("score", 0)
    if t == "multi_choice":
        picked = [o for o in q["options"] if o["id"] in v]
        if picked and all(o.get("na") for o in picked):
            return None
        return max(0, min(question_max(q), sum(o.get("score", 0) for o in picked)))
    if t == "scale":
        return v - q["min"]
    if t == "matrix":
        cols = {c["id"]: c for c in q["columns"]}
        total, applicable = 0.0, 0
        for r in q["rows"]:
            c = cols.get(v.get(r["id"])) if isinstance(v, dict) else None
            if c and c.get("na"):
                continue
            applicable += 1
            total += c.get("score", 0) if c else 0
        if applicable == 0:
            return None
        # Scale to the full matrix so N/A rows don't inflate or deflate the maximum
        per_row_max = question_max(q) / len(q["rows"])
        return total * len(q["rows"]) / applicable if per_row_max else 0
    return 0


def band_for(defn: dict, percent: Optional[float]) -> Optional[dict]:
    if percent is None:
        return None
    found = None
    for b in defn.get("bands", []):
        if percent + 1e-9 >= b["min"]:
            found = b
    return found


def score_response(defn: dict, answers: dict) -> Optional[dict]:
    """Score one response. None when the survey has nothing to score."""
    if not has_scoring(defn):
        return None
    vis = visibility(defn, answers)
    sections = {}
    total_w = total_acc = 0.0
    points_sum = max_sum = 0.0
    questions = {}
    for s in defn["sections"]:
        acc = w_sum = s_points = s_max = 0.0
        for q in s["questions"]:
            mx = question_max(q)
            if mx <= 0 or not vis.get(q["id"]):
                continue
            pts = question_points(q, answers.get(q["id"]))
            if pts is None:
                questions[q["id"]] = {"points": None, "max": mx, "na": True}
                continue
            w = q.get("weight", 1)
            acc += w * pts / mx
            w_sum += w
            s_points += pts
            s_max += mx
            questions[q["id"]] = {"points": round(pts, 2), "max": mx}
        if w_sum > 0:
            pct = 100 * acc / w_sum
            sections[s["id"]] = {"percent": round(pct, 1), "points": round(s_points, 2), "max": s_max}
            sw = s.get("weight", 1)
            total_acc += sw * pct
            total_w += sw
            points_sum += s_points
            max_sum += s_max
    percent = round(total_acc / total_w, 1) if total_w > 0 else None
    band = band_for(defn, percent)
    return {"percent": percent, "points": round(points_sum, 2), "max": max_sum,
            "band": band["label"] if band else None, "sections": sections, "questions": questions}
