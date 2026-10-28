"""Financial-year BRSR metrics from approved data-sheet values.

``compute(session, company_id, fy)`` reads the kpi_values of every approved
data-sheet dataset of the company (the latest approved version of each —
the platform-wide rule in app/services/kpi_aggregation.py), rebuilds the
monthly rows, assigns them to the financial year by their own dates, and
applies the rules in app/brsr/sheet_sources.py.

The result is injected into the BRSR answers under ``SHEET_KEY`` so the
engine can derive the quantitative tables; it is recomputed on every read
and frozen into the snapshot when the report is generated, together with
the dataset versions it was built from (``basis``), so the report can
always be traced back to the exact approved uploads.
"""
from __future__ import annotations

import re
from collections import OrderedDict, defaultdict  # noqa: F401
from datetime import date, datetime
from typing import Any, Optional

from sqlmodel import Session, select

from app.brsr.sheet_sources import COMPUTED, SOURCES, Part
from app.datasheets.catalog import SHEETS
from app.datasheets.service import PREFIX

SHEET_KEY = "__sheet_metrics__"


def fy_bounds(fy: str) -> tuple[date, date]:
    m = re.fullmatch(r"(\d{4})-(\d{2})", fy or "")
    if not m:
        raise ValueError(f"Invalid financial year {fy!r}")
    y = int(m.group(1))
    return date(y, 4, 1), date(y + 1, 3, 31)


def previous_fy(fy: str) -> str:
    y = int(fy[:4]) - 1
    return f"{y}-{str(y + 1)[-2:]}"


def current_fy(today: Optional[date] = None) -> str:
    t = today or date.today()
    y = t.year if t.month >= 4 else t.year - 1
    return f"{y}-{str(y + 1)[-2:]}"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------

def _dims(sheet: str) -> list[str]:
    return [c["name"] for c in SHEETS[sheet]["columns"] if c["kind"] in ("text", "choice")]


def load_rows(session: Session, company_id: int, start: date, end: date) -> tuple[dict, list[dict], list[dict]]:
    """({sheet: [row]}, basis, warnings) for rows dated within [start, end].

    A row is {"start", "end", "location", "attrs": {...}, "values": {col: v},
    "version_id", "row"}. Where two approved datasets report the same month,
    location and category, the more recently approved one wins and a warning
    names both, so nothing is counted twice."""
    from app.models.dataset import Dataset, DatasetVersion
    from app.models.kpi import KpiValue
    from app.models.upload_type import UploadType
    from app.services.kpi_aggregation import latest_approved_version_ids

    types = {t.id: t.code[len(PREFIX):] for t in session.exec(
        select(UploadType).where(UploadType.code.startswith(PREFIX, autoescape=True))).all() if t.code[len(PREFIX):] in SHEETS}
    if not types:
        return {}, [], []
    datasets = session.exec(select(Dataset).where(
        Dataset.company_id == company_id, Dataset.upload_type_id.in_(list(types)),
        Dataset.deleted_at.is_(None))).all()
    vmap = latest_approved_version_ids(session, [d.id for d in datasets])
    if not vmap:
        return {}, [], []
    ds_by_id = {d.id: d for d in datasets}
    versions = {v.id: v for v in session.exec(select(DatasetVersion).where(DatasetVersion.id.in_(list(vmap.values())))).all()}
    sheet_of_version = {vid: types[ds_by_id[did].upload_type_id] for did, vid in vmap.items()}

    vals = session.exec(select(KpiValue).where(
        KpiValue.dataset_version_id.in_(list(vmap.values())),
        KpiValue.reporting_period_start <= end, KpiValue.reporting_period_end >= start)).all()

    raw: dict[tuple, dict] = {}
    for kv in vals:
        sheet = sheet_of_version.get(kv.dataset_version_id)
        if not sheet or not kv.kpi_code.startswith(sheet + "."):
            continue
        key = (kv.dataset_version_id, kv.source_file_id, kv.source_row_number)
        r = raw.get(key)
        if r is None:
            attrs = dict(kv.attributes or {})
            r = raw[key] = {"sheet": sheet, "start": kv.reporting_period_start, "end": kv.reporting_period_end,
                            "location": (attrs.get("location") or "").strip(), "attrs": attrs, "values": {},
                            "version_id": kv.dataset_version_id, "row": kv.source_row_number}
        r["values"][kv.kpi_code[len(sheet) + 1:]] = kv.value

    warnings: list[dict] = []
    by_sheet: dict[str, list[dict]] = defaultdict(list)
    seen: dict[tuple, dict] = {}
    # Newest approval first, so the first row seen for a key is the one kept.
    order = sorted(raw.values(), key=lambda r: (-(versions[r["version_id"]].updated_at or datetime.min).timestamp()
                                                 if versions.get(r["version_id"]) else 0, r["row"]))
    for r in order:
        if r["start"] < start or r["end"] > end:
            warnings.append({"sheet": r["sheet"], "code": "row_outside_year",
                             "message": f"A row dated {r['start']} to {r['end']} crosses the financial year "
                                        f"boundary and is not counted."})
            continue
        ident = (r["sheet"], r["start"], r["end"], r["location"].lower()) + tuple(
            str(r["attrs"].get(d) or "").strip().lower() for d in _dims(r["sheet"]))
        if ident in seen:
            if seen[ident]["version_id"] != r["version_id"]:
                warnings.append({"sheet": r["sheet"], "code": "overlap",
                                 "message": f"{r['location'] or 'A location'} {r['start']:%b %Y} is in two approved "
                                            f"uploads; the most recently approved one is used."})
                continue
        else:
            seen[ident] = r
        by_sheet[r["sheet"]].append(r)

    basis = [{"dataset_id": did, "dataset_public_id": ds_by_id[did].public_id, "version_id": vid,
              "version_number": versions[vid].version_number if vid in versions else None,
              "sheet": sheet_of_version[vid]} for did, vid in sorted(vmap.items())]
    return dict(by_sheet), basis, warnings


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

def _row_value(r: dict, cols) -> Optional[float]:
    total, seen = 0.0, False
    for c in cols:
        name, factor = (c, 1.0) if isinstance(c, str) else c
        v = r["values"].get(name)
        if v is not None:
            total += v * factor
            seen = True
    return total if seen else 0.0  # a blank cell in a reported row counts as 0


def _matches(r: dict, p: Part) -> bool:
    if p.cat and str(r["attrs"].get("Category") or "").lower() != p.cat.lower():
        return False
    for k, v in p.where:
        if str(r["attrs"].get(k) or "").strip().lower() != v.lower():
            return False
    return True


def aggregate_part(rows: list[dict], p: Part) -> Optional[float]:
    if not rows:
        return None  # sheet not reported for the year
    rs = [r for r in rows if _matches(r, p)]
    if not rs:
        return 0.0
    if p.how == "sum":
        out = sum(_row_value(r, p.cols) for r in rs)
    elif p.how == "mean":
        vs = [_row_value(r, p.cols) for r in rs]
        out = sum(vs) / len(vs)
    else:
        groups: dict[tuple, list[dict]] = defaultdict(list)
        for r in rs:
            groups[(r["location"].lower(), str(r["attrs"].get("Category") or "").lower())].append(r)
        picks = []
        for g in groups.values():
            if p.how == "first":
                d = min(r["start"] for r in g)
                picks.append(sum(_row_value(r, p.cols) for r in g if r["start"] == d))
            else:
                d = max(r["end"] for r in g)
                picks.append(sum(_row_value(r, p.cols) for r in g if r["end"] == d))
        if p.across == "mean":
            out = sum(picks) / len(picks)
        elif p.across == "max":
            out = max(picks)
        else:
            out = sum(picks)
    return out * p.scale


def _clean(v: Optional[float]) -> Optional[float]:
    if v is None:
        return None
    r = round(v, 6)
    return int(r) if float(r).is_integer() else r


def metrics_for(rows_by_sheet: dict[str, list[dict]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, src in SOURCES.items():
        parts = [aggregate_part(rows_by_sheet.get(p.sheet, []), p) for p in src.parts]
        present = [v for v in parts if v is not None]
        out[key] = _clean(sum(present)) if present else None
    for key, (deps, fn) in COMPUTED.items():
        out[key] = _clean(fn(*[out.get(d) for d in deps]))
    return out


def tables_for(rows_by_sheet: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Row-wise BRSR tables (one BRSR row per entity in the sheet)."""
    out: dict[str, list[dict]] = {}
    rows = rows_by_sheet.get("community_development") or []
    if rows:
        projects: dict[str, dict] = {}
        for r in rows:
            name = str(r["attrs"].get("CSR_Project") or "").strip() or "Unnamed project"
            p = projects.setdefault(name.lower(), {"project": name, "b": 0.0, "v": 0.0})
            p["b"] += r["values"].get("No_of_beneficiaries_from_the_CSR_project") or 0
            p["v"] += r["values"].get("No_of_beneficiaries_from_vulnerable_and_marginalised_groups") or 0
        out["csr_projects"] = [{"project": p["project"], "beneficiaries": _clean(p["b"]),
                                "pct_vulnerable": _clean(round(p["v"] * 100 / p["b"], 2)) if p["b"] else None}
                               for p in sorted(projects.values(), key=lambda x: x["project"].lower())]
    return out


def coverage_for(rows_by_sheet: dict[str, list[dict]], fy_start: date, fy_end: date) -> dict[str, dict]:
    out = {}
    for sheet in SHEETS:
        rows = rows_by_sheet.get(sheet) or []
        months = sorted({(r["start"].year, r["start"].month) for r in rows})
        locs: dict[str, date] = {}
        for r in rows:
            k = r["location"] or "—"
            locs[k] = max(locs.get(k, r["end"]), r["end"])
        last = max(locs.values()) if locs else None
        stale = sorted(k for k, d in locs.items() if last and (last - d).days > 31)
        out[sheet] = {"months": len(months), "month_list": [f"{y}-{m:02d}" for y, m in months],
                      "rows": len(rows), "locations": sorted(locs), "stale_locations": stale,
                      "last_period_end": last.isoformat() if last else None,
                      "categories": sorted({str(r["attrs"].get("Category")) for r in rows
                                            if r["attrs"].get("Category")})}
    return out


def compute(session: Session, company_id: int, fy: str) -> dict:
    cy_start, cy_end = fy_bounds(fy)
    pfy = previous_fy(fy)
    py_start, py_end = fy_bounds(pfy)
    rows, basis, warnings = load_rows(session, company_id, py_start, cy_end)
    split: dict[str, dict[str, list]] = {"cy": defaultdict(list), "py": defaultdict(list)}
    for sheet, rs in rows.items():
        for r in rs:
            if r["start"] >= cy_start:
                split["cy"][sheet].append(r)
            elif r["end"] <= py_end:
                split["py"][sheet].append(r)
            else:
                warnings.append({"sheet": sheet, "code": "row_outside_year",
                                 "message": f"A row dated {r['start']} to {r['end']} crosses the financial-year "
                                            f"boundary and is not counted."})
    out = {"fy": fy, "py_fy": pfy, "basis": basis}
    for period, (s, e) in (("cy", (cy_start, cy_end)), ("py", (py_start, py_end))):
        out[period] = metrics_for(split[period])
        out[f"{period}_tables"] = tables_for(split[period])
        out[f"{period}_coverage"] = coverage_for(split[period], s, e)
    out["warnings"] = _dedupe(warnings) + _coverage_warnings(out)
    return out


def _dedupe(ws: list[dict]) -> list[dict]:
    seen, out = set(), []
    for w in ws:
        k = (w.get("sheet"), w["message"])
        if k not in seen:
            seen.add(k)
            out.append(w)
    return out


def _coverage_warnings(res: dict) -> list[dict]:
    from app.brsr.sheet_sources import SHEETS_USED
    out = []
    cov = res["cy_coverage"]
    for sheet in sorted(SHEETS_USED):
        c = cov[sheet]
        title = SHEETS[sheet]["title"]
        if c["months"] == 0:
            out.append({"sheet": sheet, "code": "missing", "message": f"{title}: no approved data for FY {res['fy']}."})
        elif c["months"] < 12:
            out.append({"sheet": sheet, "code": "partial",
                        "message": f"{title}: {c['months']} of 12 months approved for FY {res['fy']}."})
        if c["stale_locations"]:
            out.append({"sheet": sheet, "code": "stale_location",
                        "message": f"{title}: no data for the last month from {', '.join(c['stale_locations'])}."})
    out += plausibility_warnings(res["cy"], res["fy"])
    wrk = sum(res["cy"].get(k) or 0 for k in ("wrk_perm_m", "wrk_perm_f", "wrk_perm_o", "wrk_oth_m", "wrk_oth_f",
                                               "wrk_oth_o"))
    if wrk:
        for sheet in ("employee_wellbeing", "trainings", "wages_remuneration"):
            if cov[sheet]["rows"] and "Worker" not in cov[sheet]["categories"]:
                out.append({"sheet": sheet, "code": "no_worker_rows",
                            "message": f"{SHEETS[sheet]['title']}: the Diversity sheet reports workers but this "
                                       f"sheet has no Worker rows, so worker figures show 0."})
    return out


HEADCOUNT = ("emp_perm_m", "emp_perm_f", "emp_perm_o", "emp_oth_m", "emp_oth_f", "emp_oth_o",
             "wrk_perm_m", "wrk_perm_f", "wrk_perm_o", "wrk_oth_m", "wrk_oth_f", "wrk_oth_o")


def plausibility_warnings(m: dict, fy: str) -> list[dict]:
    """Narrow checks for figures that are almost certainly a unit mistake.
    Warnings only — never blockers."""
    out = []
    people = sum(m.get(k) or 0 for k in HEADCOUNT)
    kwh = (m.get("elec_ren_kwh") or 0) + (m.get("elec_nonren_kwh") or 0)
    plants = (m.get("plants_national") or 0) + (m.get("plants_international") or 0)
    if people and not plants and kwh / people > 100_000:
        out.append({"sheet": "energy", "code": "implausible",
                    "message": f"Energy: electricity of {round(kwh / people):,} kWh per person in FY {fy} looks very "
                               f"high for an entity with no plants (offices typically use a few thousand). Check the "
                               f"unit: the sheet expects kWh, not Wh or GJ."})
    return out


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------
# Approved versions are immutable and extraction writes each version once,
# so (approved version ids, number of extracted values) identifies the data
# exactly. A new approval or a finished extraction changes the key.

_CACHE: "OrderedDict[tuple, dict]" = None  # type: ignore[assignment]
_CACHE_SIZE = 64


def fingerprint(session: Session, company_id: int) -> tuple:
    from sqlalchemy import func

    from app.models.dataset import Dataset
    from app.models.kpi import KpiValue
    from app.models.upload_type import UploadType
    from app.services.kpi_aggregation import latest_approved_version_ids

    type_ids = [t.id for t in session.exec(select(UploadType).where(UploadType.code.startswith(PREFIX, autoescape=True))).all()]
    if not type_ids:
        return ()
    ds_ids = session.exec(select(Dataset.id).where(Dataset.company_id == company_id,
                                                   Dataset.upload_type_id.in_(type_ids),
                                                   Dataset.deleted_at.is_(None))).all()
    vmap = latest_approved_version_ids(session, list(ds_ids))
    if not vmap:
        return ()
    n = session.exec(select(func.count(KpiValue.id)).where(
        KpiValue.dataset_version_id.in_(list(vmap.values())))).one()
    return tuple(sorted(vmap.items())) + (("values", n),)


def cached_compute(session: Session, company_id: int, fy: str) -> dict:
    global _CACHE
    from collections import OrderedDict
    if _CACHE is None:
        _CACHE = OrderedDict()
    key = (company_id, fy, fingerprint(session, company_id))
    hit = _CACHE.get(key)
    if hit is not None:
        _CACHE.move_to_end(key)
        return hit
    res = compute(session, company_id, fy)
    _CACHE[key] = res
    while len(_CACHE) > _CACHE_SIZE:
        _CACHE.popitem(last=False)
    return res


def sheet_coverage(session: Session, company_id: int, fy: Optional[str]) -> dict:
    fy = fy or current_fy()
    res = cached_compute(session, company_id, fy)
    return {"fy": fy, "coverage": res["cy_coverage"], "py_coverage": res["py_coverage"],
            "warnings": res["warnings"], "basis": res["basis"],
            "sheets": [{"code": c, "title": s["title"], "departments": s["departments"]} for c, s in SHEETS.items()]}
