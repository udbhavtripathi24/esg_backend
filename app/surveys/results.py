"""Survey results: per-question aggregates, scores, findings, export.

Anonymity rules (anonymous surveys):
- nothing is shown until MIN_GROUP people have answered;
- a group (department, organisation…) is only broken out when it has at
  least MIN_GROUP responses;
- individual responses are never listed; free-text answers are listed in
  alphabetical order, not in the order they arrived.

Findings are computed, not generated: plain statements about the numbers
(response rate, strongest and weakest areas, dominant answers, NPS), so
every sentence can be traced back to the table under it.
"""
from __future__ import annotations

import io
import statistics
from collections import Counter
from datetime import datetime
from typing import Any, Optional

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from sqlmodel import Session, select

from app.core.errors import AppError, NotFoundError
from app.models.survey import Survey, SurveyFile, SurveyResponse
from app.surveys import schema
from app.surveys.service import audience_counts, effective_status

MIN_GROUP = 5
MAX_TEXTS = 300


def _submitted(session: Session, sv: Survey) -> list[SurveyResponse]:
    return session.exec(select(SurveyResponse).where(SurveyResponse.survey_id == sv.id,
                                                     SurveyResponse.status == "submitted")
                        .order_by(SurveyResponse.submitted_at, SurveyResponse.id)).all()


def _pct(n: float, d: float) -> Optional[float]:
    return round(100 * n / d, 1) if d else None


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(v, nd)


def _label(q: dict, oid: str) -> str:
    if oid == schema.OTHER:
        return "Other"
    for o in q.get("options", []):
        if o["id"] == oid:
            return o["label"]
    return oid


def nps_score(values: list[int]) -> dict:
    n = len(values)
    pro = sum(1 for v in values if v >= 9)
    det = sum(1 for v in values if v <= 6)
    return {"score": round(100 * (pro - det) / n) if n else None, "promoters": _pct(pro, n),
            "passives": _pct(n - pro - det, n), "detractors": _pct(det, n), "n": n}


def _question_result(sv: Survey, q: dict, rows: list[tuple[SurveyResponse, dict]], files: dict[str, SurveyFile],
                     section_id: str) -> dict:
    """rows: (response, visibility) for every submitted response."""
    t = q["type"]
    qid = q["id"]
    shown = [(r, r.answers.get(qid)) for r, vis in rows if vis.get(qid)]
    answered = [(r, v) for r, v in shown if v not in (None, "", [], {})]
    vals = [v for _, v in answered]
    out: dict[str, Any] = {"id": qid, "type": t, "title": q["title"], "section_id": section_id,
                           "shown": len(shown), "answered": len(answered), "required": q.get("required", False)}
    if t in schema.CHOICE_TYPES or t == "multi_choice":
        c: Counter = Counter()
        for v in vals:
            for x in (v if isinstance(v, list) else [v]):
                c[x] += 1
        out["options"] = [{"id": o["id"], "label": o["label"], "count": c.get(o["id"], 0),
                           "percent": _pct(c.get(o["id"], 0), len(answered)),
                           "score": o.get("score"), "na": o.get("na", False)} for o in q["options"]]
        if q.get("allow_other"):
            texts = [r.answers.get(f"{qid}.other") for r, v in answered
                     if v == schema.OTHER or (isinstance(v, list) and schema.OTHER in v)]
            texts = [x for x in texts if x]
            out["other"] = {"count": c.get(schema.OTHER, 0), "percent": _pct(c.get(schema.OTHER, 0), len(answered)),
                            "texts": sorted(texts)[:MAX_TEXTS] if sv.anonymous else texts[:MAX_TEXTS]}
    elif t in ("scale", "nps"):
        lo, hi = (0, 10) if t == "nps" else (q["min"], q["max"])
        c = Counter(vals)
        out["distribution"] = [{"value": v, "count": c.get(v, 0), "percent": _pct(c.get(v, 0), len(vals))}
                               for v in range(lo, hi + 1)]
        out["mean"] = _r(statistics.fmean(vals)) if vals else None
        out["median"] = statistics.median(vals) if vals else None
        out["min"], out["max"] = lo, hi
        out["min_label"], out["max_label"] = q.get("min_label"), q.get("max_label")
        if t == "nps":
            out["nps"] = nps_score(vals)
    elif t in ("number", "percent"):
        nums = [float(v) for v in vals]
        out.update(mean=_r(statistics.fmean(nums)) if nums else None,
                   median=_r(statistics.median(nums)) if nums else None,
                   min=_r(min(nums)) if nums else None, max=_r(max(nums)) if nums else None,
                   total=_r(sum(nums)) if nums and t == "number" else None, unit=q.get("unit"))
    elif t == "date":
        out.update(earliest=min(vals) if vals else None, latest=max(vals) if vals else None)
    elif t in schema.TEXT_TYPES:
        items = [{"text": v, "response_id": None if sv.anonymous else r.public_id,
                  "respondent": None if sv.anonymous else r.respondent_name} for r, v in answered]
        if sv.anonymous:
            items.sort(key=lambda x: x["text"].lower())
        out["answers"] = items[:MAX_TEXTS]
        out["truncated"] = len(items) > MAX_TEXTS
    elif t == "matrix":
        cols = q["columns"]
        has_scores = any("score" in col for col in cols)
        rows_out = []
        for row in q["rows"]:
            picks = [v.get(row["id"]) for v in vals if isinstance(v, dict) and v.get(row["id"])]
            c = Counter(picks)
            entry = {"id": row["id"], "label": row["label"], "answered": len(picks),
                     "counts": [{"column_id": col["id"], "count": c.get(col["id"], 0),
                                 "percent": _pct(c.get(col["id"], 0), len(picks))} for col in cols]}
            if has_scores:
                scored = [next((col["score"] for col in cols if col["id"] == p and "score" in col
                                and not col.get("na")), None) for p in picks]
                scored = [s for s in scored if s is not None]
                entry["average"] = _r(statistics.fmean(scored)) if scored else None
            rows_out.append(entry)
        out["columns"] = [{"id": col["id"], "label": col["label"], "score": col.get("score")} for col in cols]
        out["rows"] = rows_out
        if has_scores:
            scale = [col["score"] for col in cols if "score" in col and not col.get("na")]
            out["scale"] = {"min": min(scale), "max": max(scale)} if scale else None
    elif t == "file":
        fl = []
        for r, v in answered:
            for fid in v:
                f = files.get(fid)
                if f:
                    fl.append({"id": f.public_id, "filename": f.original_filename, "size_bytes": f.size_bytes,
                               "respondent": None if sv.anonymous else r.respondent_name,
                               "response_id": None if sv.anonymous else r.public_id})
        out["files"] = fl[:MAX_TEXTS]
    mx = schema.question_max(q)
    if sv.kind == "assessment" and mx > 0:
        pcts = [r.score["questions"][qid]["points"] / r.score["questions"][qid]["max"]
                for r, _ in shown
                if r.score and qid in r.score.get("questions", {}) and r.score["questions"][qid].get("points") is not None]
        out["average_score"] = _r(100 * statistics.fmean(pcts), 1) if pcts else None
        out["weight"] = q.get("weight", 1)
    return out


def _groups(responses: list[SurveyResponse], anonymous: bool) -> list[dict]:
    c = Counter(r.group_label for r in responses if r.group_label)
    out = []
    for label, n in sorted(c.items(), key=lambda kv: (-kv[1], kv[0])):
        hidden = anonymous and n < MIN_GROUP
        out.append({"label": label, "responses": n, "hidden": hidden})
    return out


def _scores_summary(sv: Survey, responses: list[SurveyResponse]) -> Optional[dict]:
    if sv.kind != "assessment":
        return None
    pcts = [r.score["percent"] for r in responses if r.score and r.score.get("percent") is not None]
    bands = sv.definition.get("bands", [])
    band_counts = Counter(r.score.get("band") for r in responses if r.score)
    sections = []
    for s in sv.definition["sections"]:
        vals = [r.score["sections"][s["id"]]["percent"] for r in responses
                if r.score and s["id"] in r.score.get("sections", {})]
        if vals or any(schema.question_max(q) > 0 for q in s["questions"]):
            sections.append({"id": s["id"], "title": s["title"], "average": _r(statistics.fmean(vals), 1) if vals else None,
                             "responses": len(vals), "weight": s.get("weight", 1)})
    avg = statistics.fmean(pcts) if pcts else None
    return {"average": _r(avg, 1), "median": _r(statistics.median(pcts), 1) if pcts else None,
            "min": _r(min(pcts), 1) if pcts else None, "max": _r(max(pcts), 1) if pcts else None,
            "band": (schema.band_for(sv.definition, avg) or {}).get("label") if avg is not None else None,
            "bands": [{"label": b["label"], "min": b["min"], "description": b.get("description"),
                       "count": band_counts.get(b["label"], 0)} for b in bands],
            "distribution": [{"from": lo, "to": lo + 10, "count": sum(1 for p in pcts if lo <= p < lo + 10 or
                                                                      (lo == 90 and p == 100))}
                             for lo in range(0, 100, 10)],
            "sections": sections}


def _findings(sv: Survey, summary: dict, scores: Optional[dict], questions: list[dict]) -> list[dict]:
    out: list[dict] = []

    def add(tone, text, question=None):
        out.append({"tone": tone, "text": text, "question": question})

    rate = summary.get("response_rate")
    if rate is not None and summary.get("eligible"):
        tone = "positive" if rate >= 70 else "attention" if rate < 40 else "info"
        add(tone, f"{rate}% response rate — {summary['submitted_invites']} of {summary['eligible']} people asked "
                  f"have answered.")
    if scores and scores.get("average") is not None:
        add("info", f"Average score {scores['average']}%" + (f" — {scores['band']}." if scores.get("band") else "."))
        secs = [s for s in scores["sections"] if s["average"] is not None]
        if len(secs) >= 2:
            best = max(secs, key=lambda s: s["average"])
            worst = min(secs, key=lambda s: s["average"])
            if best["average"] - worst["average"] >= 10:
                add("positive", f"Strongest area: {best['title']} ({best['average']}%).")
                add("attention", f"Weakest area: {worst['title']} ({worst['average']}%) — "
                                 f"{round(best['average'] - worst['average'])} points behind the strongest.")
        weak = sorted([q for q in questions if q.get("average_score") is not None and q["average_score"] < 50],
                      key=lambda q: q["average_score"])[:3]
        for q in weak:
            add("attention", f"Improvement area: “{q['title']}” scores {q['average_score']}% on average.", q["id"])
    for q in questions:
        if q["type"] == "nps" and q.get("nps", {}).get("n"):
            n = q["nps"]
            tone = "positive" if n["score"] >= 30 else "attention" if n["score"] < 0 else "info"
            add(tone, f"Net Promoter Score {n['score']:+d} for “{q['title']}” ({n['promoters']}% promoters, "
                      f"{n['detractors']}% detractors).", q["id"])
    for q in questions:
        if q["type"] in schema.CHOICE_TYPES and q["answered"] >= 3:
            top = max(q["options"], key=lambda o: o["count"])
            if top["percent"] and top["percent"] >= 60:
                add("info", f"{round(top['percent'])}% answered “{top['label']}” to “{q['title']}”.", q["id"])
        if q["type"] == "matrix" and any(r.get("average") is not None for r in q.get("rows", [])):
            rated = [r for r in q["rows"] if r.get("average") is not None and r["answered"] >= 1]
            hi = max(rated, key=lambda r: r["average"]) if rated else None
            lo = min(rated, key=lambda r: r["average"]) if rated else None
            if len(rated) >= 3 and hi["average"] - lo["average"] >= 0.2:
                top = (q.get("scale") or {}).get("max")
                of = f" of {top:g}" if top is not None else ""
                add("info", f"“{q['title']}”: highest is {hi['label']} ({hi['average']:.1f}{of}), lowest is "
                            f"{lo['label']} ({lo['average']:.1f}{of}).", q["id"])
    return out[:10]


def build(session: Session, sv: Survey, group: Optional[str] = None) -> dict:
    responses = _submitted(session, sv)
    groups = _groups(responses, sv.anonymous)
    audience = audience_counts(session, sv)
    base = {"survey": {"id": sv.public_id, "title": sv.title, "kind": sv.kind, "anonymous": sv.anonymous,
                       "status": effective_status(sv), "definition": sv.definition},
            "groups": groups, "group": group, "min_group": MIN_GROUP}
    summary = {"responses": len(responses), "in_progress": audience["in_progress"], "eligible": audience["eligible"],
               "invited": audience["invited"], "submitted_invites": audience["submitted"],
               "response_rate": audience["response_rate"],
               "first_response": responses[0].submitted_at if responses else None,
               "last_response": responses[-1].submitted_at if responses else None}
    if group:
        g = next((x for x in groups if x["label"] == group), None)
        if not g:
            raise NotFoundError("No responses for that group")
        if g["hidden"]:
            raise AppError("group_too_small", f"Groups with fewer than {MIN_GROUP} responses aren't shown, to keep "
                                              f"answers anonymous.", 422, "group")
        responses = [r for r in responses if r.group_label == group]
        summary["responses_in_group"] = len(responses)
    if sv.anonymous and len(responses) < MIN_GROUP:
        return {**base, "summary": summary, "withheld": True,
                "withheld_reason": f"Results appear once {MIN_GROUP} people have answered, so no one can be "
                                   f"identified ({len(responses)} so far).",
                "scores": None, "questions": [], "findings": [], "by_group": []}
    file_ids = [fid for r in responses for q in schema.answerable(sv.definition) if q["type"] == "file"
                for fid in (r.answers.get(q["id"]) or [])]
    files = {f.public_id: f for f in session.exec(select(SurveyFile).where(SurveyFile.public_id.in_(file_ids),
                                                                           SurveyFile.deleted_at.is_(None))).all()} \
        if file_ids else {}
    rows = [(r, schema.visibility(sv.definition, r.answers)) for r in responses]
    questions = [_question_result(sv, q, rows, files, s["id"]) for s, q in schema.iter_questions(sv.definition)
                 if q["type"] != "statement"]
    scores = _scores_summary(sv, responses)
    by_group = []
    if sv.kind == "assessment" and not group:
        for g in groups:
            if g["hidden"]:
                continue
            pcts = [r.score["percent"] for r in responses if r.group_label == g["label"] and r.score
                    and r.score.get("percent") is not None]
            by_group.append({"label": g["label"], "responses": g["responses"],
                             "average": _r(statistics.fmean(pcts), 1) if pcts else None})
    findings = _findings(sv, summary, scores, questions) if responses else []
    return {**base, "summary": summary, "withheld": False, "scores": scores, "questions": questions,
            "findings": findings, "by_group": by_group}


# ---------------------------------------------------------------------------
# Individual responses (never for anonymous surveys)
# ---------------------------------------------------------------------------

def _no_individuals(sv: Survey) -> None:
    if sv.anonymous:
        raise AppError("anonymous_survey", "Individual responses aren't shown for anonymous surveys.", 403)


def response_list(session: Session, sv: Survey) -> list[dict]:
    _no_individuals(sv)
    out = []
    for r in reversed(_submitted(session, sv)):
        email = None
        if r.invite_id:
            from app.models.survey import SurveyInvite
            inv = session.get(SurveyInvite, r.invite_id)
            email = inv.email if inv else None
        out.append({"id": r.public_id, "respondent": r.respondent_name or ("Public link" if r.channel == "public" else None),
                    "email": email, "group": r.group_label, "channel": r.channel, "submitted_at": r.submitted_at,
                    "score": (r.score or {}).get("percent"), "band": (r.score or {}).get("band")})
    return out


def display_value(q: dict, v: Any, answers: dict, files: dict[str, SurveyFile]) -> Any:
    t = q["type"]
    if v is None or v == "" or v == [] or v == {}:
        return None
    if t in schema.CHOICE_TYPES:
        return f"Other: {answers.get(q['id'] + '.other', '')}" if v == schema.OTHER else _label(q, v)
    if t == "multi_choice":
        return "; ".join(f"Other: {answers.get(q['id'] + '.other', '')}" if x == schema.OTHER else _label(q, x)
                         for x in v)
    if t == "matrix":
        cols = {c["id"]: c["label"] for c in q["columns"]}
        return {r["id"]: cols.get(v.get(r["id"])) for r in q["rows"]}
    if t == "file":
        return "; ".join(files[f].original_filename for f in v if f in files)
    return v


def response_detail(session: Session, sv: Survey, response_public_id: str) -> dict:
    _no_individuals(sv)
    r = session.exec(select(SurveyResponse).where(SurveyResponse.public_id == response_public_id,
                                                  SurveyResponse.survey_id == sv.id,
                                                  SurveyResponse.status == "submitted")).first()
    if not r:
        raise NotFoundError("Response not found")
    files = {f.public_id: f for f in session.exec(select(SurveyFile).where(SurveyFile.response_id == r.id,
                                                                           SurveyFile.deleted_at.is_(None))).all()}
    vis = schema.visibility(sv.definition, r.answers)
    sections = []
    for s in sv.definition["sections"]:
        items = []
        for q in s["questions"]:
            if q["type"] == "statement" or not vis.get(q["id"]):
                continue
            v = r.answers.get(q["id"])
            item = {"id": q["id"], "type": q["type"], "title": q["title"],
                    "value": display_value(q, v, r.answers, files), "raw": v}
            if q["type"] == "matrix":
                item["rows"] = [{"label": row["label"], "value": (item["value"] or {}).get(row["id"])}
                                for row in q["rows"]]
            if q["type"] == "file":
                item["files"] = [{"id": f, "filename": files[f].original_filename, "size_bytes": files[f].size_bytes}
                                 for f in (v or []) if f in files]
            if r.score and q["id"] in r.score.get("questions", {}):
                item["score"] = r.score["questions"][q["id"]]
            items.append(item)
        sec = {"id": s["id"], "title": s["title"], "questions": items}
        if r.score and s["id"] in r.score.get("sections", {}):
            sec["score"] = r.score["sections"][s["id"]]
        sections.append(sec)
    return {"id": r.public_id, "respondent": r.respondent_name, "group": r.group_label, "channel": r.channel,
            "submitted_at": r.submitted_at, "score": r.score, "sections": sections}


# ---------------------------------------------------------------------------
# Excel export
# ---------------------------------------------------------------------------

HEAD = PatternFill("solid", fgColor="E8F3E8")
BOLD = Font(bold=True)


def _sheet_header(ws, headers: list[str]) -> None:
    ws.append(headers)
    for c in ws[1]:
        c.font = BOLD
        c.fill = HEAD
        c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"


def export_xlsx(session: Session, sv: Survey) -> bytes:
    res = build(session, sv)
    responses = _submitted(session, sv)
    if sv.anonymous:
        if len(responses) < MIN_GROUP:
            raise AppError("results_withheld", res.get("withheld_reason") or "Not enough responses yet.", 409)
        responses = sorted(responses, key=lambda r: r.public_id)  # not arrival order
    wb = Workbook()

    ws = wb.active
    ws.title = "Summary"
    s = res["summary"]
    rows = [("Survey", sv.title), ("Type", sv.kind.title()), ("Status", res["survey"]["status"]),
            ("Anonymous", "Yes" if sv.anonymous else "No"), ("Responses", s["responses"]),
            ("People asked", s["eligible"]), ("Response rate (%)", s["response_rate"]),
            ("Exported (UTC)", datetime.utcnow().strftime("%Y-%m-%d %H:%M"))]
    if res.get("scores"):
        rows += [("Average score (%)", res["scores"]["average"]), ("Band", res["scores"]["band"])]
    for k, v in rows:
        ws.append([k, v])
        ws.cell(ws.max_row, 1).font = BOLD
    if res.get("scores"):
        ws.append([])
        ws.append(["Section", "Average score (%)"])
        for c in ws[ws.max_row]:
            c.font = BOLD
        for sec in res["scores"]["sections"]:
            ws.append([sec["title"], sec["average"]])
    if res["findings"]:
        ws.append([])
        ws.append(["Key findings"])
        ws.cell(ws.max_row, 1).font = BOLD
        for f in res["findings"]:
            ws.append([f["text"]])
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 60

    qs = schema.answerable(sv.definition)
    files = {f.public_id: f for f in session.exec(select(SurveyFile).where(
        SurveyFile.survey_id == sv.id, SurveyFile.deleted_at.is_(None))).all()}
    ws = wb.create_sheet("Responses")
    ident = [] if sv.anonymous else ["Respondent", "Group", "Channel", "Submitted (UTC)"]
    if sv.anonymous:
        ident = ["Group"]
    score_cols = ["Score (%)", "Band"] if sv.kind == "assessment" else []
    qcols: list[tuple[dict, Optional[dict]]] = []
    for q in qs:
        if q["type"] == "matrix":
            qcols += [(q, row) for row in q["rows"]]
        else:
            qcols.append((q, None))
    _sheet_header(ws, ["Response"] + ident + score_cols +
                  [f"{q['title']} — {row['label']}" if row else q["title"] for q, row in qcols])
    for i, r in enumerate(responses, 1):
        line: list[Any] = [i if sv.anonymous else r.public_id]
        if sv.anonymous:
            g = r.group_label
            hidden = {x["label"] for x in res["groups"] if x["hidden"]}
            line.append(None if g in hidden else g)
        else:
            line += [r.respondent_name, r.group_label, r.channel,
                     r.submitted_at.strftime("%Y-%m-%d %H:%M") if r.submitted_at else None]
        if score_cols:
            line += [(r.score or {}).get("percent"), (r.score or {}).get("band")]
        for q, row in qcols:
            v = display_value(q, r.answers.get(q["id"]), r.answers, files)
            line.append(v.get(row["id"]) if row and isinstance(v, dict) else (None if row else v))
        ws.append(line)
    for idx in range(1, ws.max_column + 1):
        ws.column_dimensions[get_column_letter(idx)].width = 22 if idx > 1 else 14

    ws = wb.create_sheet("Questions")
    _sheet_header(ws, ["Question", "Type", "Answer / statistic", "Count", "Percent"])
    for q in res["questions"]:
        title, t = q["title"], schema.TYPE_LABELS[q["type"]]
        ws.append([title, t, "Answered", q["answered"], None])
        if "options" in q:
            for o in q["options"]:
                ws.append([None, None, o["label"], o["count"], o["percent"]])
            if q.get("other"):
                ws.append([None, None, "Other", q["other"]["count"], q["other"]["percent"]])
        elif q["type"] in ("scale", "nps"):
            ws.append([None, None, "Average", q["mean"], None])
            for d in q["distribution"]:
                ws.append([None, None, d["value"], d["count"], d["percent"]])
            if q.get("nps"):
                ws.append([None, None, "Net Promoter Score", q["nps"]["score"], None])
        elif q["type"] in ("number", "percent"):
            for k in ("mean", "median", "min", "max", "total"):
                if q.get(k) is not None:
                    ws.append([None, None, k.title(), q[k], None])
        elif q["type"] == "matrix":
            for row in q["rows"]:
                ws.append([None, None, row["label"] + (f" (average {row['average']})" if row.get("average") is not None
                                                       else ""), row["answered"], None])
        if q.get("average_score") is not None:
            ws.append([None, None, "Average score (%)", q["average_score"], None])
    ws.column_dimensions["A"].width = 60
    ws.column_dimensions["B"].width = 18
    ws.column_dimensions["C"].width = 50

    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
