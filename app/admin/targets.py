"""ESG targets: which figures can be targeted, their yearly values, and status.

Figures come from the approved data sheets through the same rules the BRSR
uses (app/brsr/sheet_metrics.py), so a target and the BRSR always agree.
Figures with no data-sheet rule (Scope 1/2/3 emissions are typed into the
BRSR) are "manual": the company types the value for each year.

Progress and status (a straight-line path from baseline to target):
    progress  = share of the planned change achieved so far
                reduce:   (baseline − current) / (baseline − target)
                increase: (current − baseline) / (target − baseline)
    expected  = share of the time elapsed: (FY − baseline FY) / (target FY − baseline FY)
    achieved  progress ≥ 100 %
    on track  progress ≥ expected − 5 points
    at risk   otherwise (missed, once the target year has passed)
    partial   a total from data sheets covering fewer than 12 months — not
              comparable with a full-year baseline, so not assessed (shares and
              rates are comparable and are assessed)
    no data   no figure for that year
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Optional

from sqlmodel import Session, select

from app.brsr.sheet_metrics import cached_compute, current_fy
from app.brsr.sheet_sources import F
from app.core.errors import AppError, NotFoundError
from app.models.target import EsgTarget
from app.models.user import User
from app.services.audit import log_action

FY_RE = r"^\d{4}-\d{2}$"
TOLERANCE = 5.0
_K = F["kwh_to_gj"]


def _v(m: dict, k: str) -> Optional[float]:
    v = m.get(k)
    return v if isinstance(v, (int, float)) else None


def _sum(m: dict, *keys: str, scale: dict[str, float] | None = None) -> Optional[float]:
    vals = [_v(m, k) * (scale or {}).get(k, 1) for k in keys if _v(m, k) is not None]
    return sum(vals) if vals else None


def _pct(n: Optional[float], d: Optional[float]) -> Optional[float]:
    return None if n is None or not d else 100.0 * n / d


_E_REN = ("elec_ren_kwh", "fuel_ren_gj", "energy_other_ren_gj")
_E_ALL = _E_REN + ("elec_nonren_kwh", "fuel_nonren_gj", "energy_other_nonren_gj")
_KWH = {"elec_ren_kwh": _K, "elec_nonren_kwh": _K}
_WITHDRAW = ("w_surface", "w_ground", "w_third_party", "w_sea", "w_other")
_WASTE = ("ws_plastic", "ws_ewaste", "ws_biomedical", "ws_cnd", "ws_battery", "ws_radioactive", "ws_other_haz",
          "ws_other_nonhaz")
_PEOPLE = [f"{w}_{t}_{g}" for w in ("emp", "wrk") for t in ("perm", "oth") for g in ("m", "f", "o")]
_WOMEN = [k for k in _PEOPLE if k.endswith("_f")]


def _energy(m):
    return _sum(m, *_E_ALL, scale=_KWH)


def _intensity(m):
    e, rev = _energy(m), _v(m, "revenue")
    return None if e is None or not rev else e / (rev / 1e7)


def _ltifr(m):
    lti, hrs = _v(m, "lti_emp"), _v(m, "hours_emp")
    return None if lti is None or not hrs else lti / hrs * 1e6


# key -> label, unit, default direction, group, how it's worked out (None = typed in), note
CATALOG: dict[str, dict[str, Any]] = {
    "energy_total": {"label": "Total energy consumption", "unit": "GJ", "direction": "reduce", "group": "Energy",
                     "fn": _energy, "sheets": ["energy", "dg_sets"]},
    "energy_renewable_share": {"label": "Renewable energy share", "unit": "%", "direction": "increase",
                               "group": "Energy", "sheets": ["energy"],
                               "fn": lambda m: _pct(_sum(m, *_E_REN, scale=_KWH), _energy(m))},
    "grid_electricity": {"label": "Grid electricity purchased", "unit": "kWh", "direction": "reduce",
                         "group": "Energy", "fn": lambda m: _v(m, "elec_nonren_kwh"), "sheets": ["energy"]},
    "energy_intensity": {"label": "Energy intensity", "unit": "GJ per ₹ crore revenue", "direction": "reduce",
                         "group": "Energy", "fn": _intensity, "sheets": ["energy", "dg_sets", "finance"]},
    "scope1": {"label": "Scope 1 emissions", "unit": "tCO₂e", "direction": "reduce", "group": "Emissions",
               "fn": None, "note": "Typed into the BRSR (P6.E7), not a data sheet — enter each year's figure."},
    "scope2": {"label": "Scope 2 emissions", "unit": "tCO₂e", "direction": "reduce", "group": "Emissions",
               "fn": None, "note": "Typed into the BRSR (P6.E7), not a data sheet — enter each year's figure."},
    "water_withdrawal": {"label": "Water withdrawal", "unit": "kL", "direction": "reduce", "group": "Water",
                         "fn": lambda m: _sum(m, *_WITHDRAW), "sheets": ["water"]},
    "water_consumption": {"label": "Water consumption", "unit": "kL", "direction": "reduce", "group": "Water",
                          "fn": lambda m: _v(m, "w_consumption"), "sheets": ["water"]},
    "waste_generated": {"label": "Waste generated", "unit": "t", "direction": "reduce", "group": "Waste",
                        "fn": lambda m: _sum(m, *_WASTE), "sheets": ["waste_management", "e_waste"]},
    "waste_diverted_share": {"label": "Waste recycled, reused or recovered", "unit": "%", "direction": "increase",
                             "group": "Waste", "sheets": ["waste_management", "e_waste"],
                             "fn": lambda m: _pct(_sum(m, "ws_recycled", "ws_reused", "ws_other_recovery"),
                                                  _sum(m, *_WASTE))},
    "women_workforce_share": {"label": "Women in the workforce", "unit": "%", "direction": "increase",
                              "group": "People", "sheets": ["diversity"],
                              "fn": lambda m: _pct(_sum(m, *_WOMEN), _sum(m, *_PEOPLE))},
    "women_board_share": {"label": "Women on the board", "unit": "%", "direction": "increase", "group": "People",
                          "sheets": ["board_of_directors"],
                          "fn": lambda m: _pct(_v(m, "bod_f"), _sum(m, "bod_m", "bod_f"))},
    "ltifr_employees": {"label": "Lost-time injury frequency rate (employees)", "unit": "per million hours",
                        "direction": "reduce", "group": "Safety", "fn": _ltifr, "sheets": ["safety"]},
    "fatalities": {"label": "Fatalities (employees and workers)", "unit": "people", "direction": "reduce",
                   "group": "Safety", "fn": lambda m: _sum(m, "fatal_emp", "fatal_wrk"), "sheets": ["safety"]},
    "suppliers_env_assessed": {"label": "Suppliers assessed on environmental impacts", "unit": "%",
                               "direction": "increase", "group": "Value chain",
                               "sheets": ["supply_chain_admin", "supply_chain_tech"],
                               "fn": lambda m: _pct(_v(m, "vc_env_assessed"), _v(m, "suppliers"))},
    "custom": {"label": "Other (your own figure)", "unit": "", "direction": "reduce", "group": "Other", "fn": None,
               "note": "Name the figure and its unit, then enter each year's value."},
}


# Shares and rates compare fairly over part of a year; totals don't.
PARTIAL_OK = {"energy_renewable_share", "energy_intensity", "waste_diverted_share", "women_workforce_share",
              "women_board_share", "ltifr_employees", "suppliers_env_assessed"}


def catalog() -> list[dict]:
    return [{"key": k, "label": c["label"], "unit": c["unit"], "direction": c["direction"], "group": c["group"],
             "source": "data_sheets" if c["fn"] else "manual", "sheets": c.get("sheets", []),
             "note": c.get("note")} for k, c in CATALOG.items()]


def fy_year(fy: str) -> int:
    return int(fy[:4])


def fy_label(y: int) -> str:
    return f"{y}-{str(y + 1)[-2:]}"


def _check_fy(fy: Any, field: str) -> str:
    import re
    s = str(fy or "").strip()
    if not re.match(FY_RE, s) or int(s[5:]) != (int(s[:4]) + 1) % 100:
        raise AppError("invalid_fy", "Use a financial year like 2024-25", 422, field)
    return s


def sheet_reading(session: Session, company_id: int, metric: str, fy: str) -> tuple[Optional[float], Optional[int]]:
    """(value, months covered) from the approved data sheets for a year."""
    meta = CATALOG.get(metric, {})
    fn: Optional[Callable] = meta.get("fn")
    if fn is None:
        return None, None
    res = cached_compute(session, company_id, fy)
    v = fn(res.get("cy") or {})
    if v is None:
        return None, None
    cov = res.get("cy_coverage") or {}
    months = [cov[sh]["months"] for sh in meta.get("sheets", []) if sh in cov and cov[sh]["months"]]
    return round(v, 4), (min(months) if months else None)


def sheet_value(session: Session, company_id: int, metric: str, fy: str) -> Optional[float]:
    return sheet_reading(session, company_id, metric, fy)[0]


def value_for(session: Session, t: EsgTarget, fy: str) -> tuple[Optional[float], Optional[str], Optional[int]]:
    """(value, source, months) for a year: a typed value wins over the data sheets.
    ``months`` < 12 means the data sheets don't cover the whole year yet."""
    if fy in (t.manual_values or {}) and t.manual_values[fy] is not None:
        return float(t.manual_values[fy]), "manual", None
    v, months = sheet_reading(session, t.company_id, t.metric, fy)
    return (v, "data_sheets", months) if v is not None else (None, None, None)


def is_partial(t: EsgTarget, source: Optional[str], months: Optional[int]) -> bool:
    """A total over part of a year isn't comparable with a full-year baseline."""
    return source == "data_sheets" and months is not None and months < 12 and \
        t.metric not in PARTIAL_OK


def default_fy(session: Session, company_id: int) -> str:
    """The latest year with a full 12 months of approved data, else the latest
    with any data, else the current financial year."""
    cur = fy_year(current_fy())
    any_data = None
    for y in range(cur, cur - 6, -1):
        cov = cached_compute(session, company_id, fy_label(y)).get("cy_coverage") or {}
        most = max((c["months"] for c in cov.values()), default=0)
        if most >= 12:
            return fy_label(y)
        if most and any_data is None:
            any_data = fy_label(y)
    return any_data or current_fy()


def assess(t: EsgTarget, fy: str, current: Optional[float]) -> dict:
    base_y, tgt_y, y = fy_year(t.baseline_fy), fy_year(t.target_fy), fy_year(fy)
    span = (t.baseline_value - t.target_value) if t.direction == "reduce" else (t.target_value - t.baseline_value)
    expected = 100.0 if tgt_y <= base_y else max(0.0, min(100.0, 100.0 * (y - base_y) / (tgt_y - base_y)))
    if current is None:
        return {"progress": None, "expected": round(expected, 1), "status": "no_data"}
    if span == 0:
        met = current <= t.target_value if t.direction == "reduce" else current >= t.target_value
        progress = 100.0 if met else 0.0
    else:
        moved = (t.baseline_value - current) if t.direction == "reduce" else (current - t.baseline_value)
        progress = 100.0 * moved / span
    if progress >= 100 - 1e-9:
        status = "achieved"
    elif y > tgt_y:
        status = "missed"
    elif progress >= expected - TOLERANCE:
        status = "on_track"
    else:
        status = "at_risk"
    return {"progress": round(progress, 1), "expected": round(expected, 1), "status": status}


def serialize(session: Session, t: EsgTarget, fy: str, names: dict[int, str]) -> dict:
    current, source, months = value_for(session, t, fy)
    partial = is_partial(t, source, months)
    assessed = assess(t, fy, current)
    if partial:
        assessed = {"progress": None, "expected": assessed["expected"], "status": "partial"}
    hist = []
    last = min(fy_year(fy), fy_year(t.target_fy))
    first = max(fy_year(t.baseline_fy), last - 7)
    for y in range(first, last + 1):
        if y == fy_year(t.baseline_fy):
            hist.append({"fy": fy_label(y), "value": t.baseline_value, "source": "baseline", "partial": False})
        else:
            v, src, m = value_for(session, t, fy_label(y))
            hist.append({"fy": fy_label(y), "value": v, "source": src, "partial": is_partial(t, src, m)})
    meta = CATALOG.get(t.metric, CATALOG["custom"])
    return {"id": t.public_id, "metric": t.metric, "title": t.title, "unit": t.unit, "direction": t.direction,
            "source": "data_sheets" if meta["fn"] else "manual", "group": meta["group"],
            "baseline_fy": t.baseline_fy, "baseline_value": t.baseline_value,
            "target_fy": t.target_fy, "target_value": t.target_value,
            "manual_values": t.manual_values or {}, "notes": t.notes,
            "fy": fy, "current": current, "current_source": source, "months": months, **assessed, "history": hist,
            "updated_by": names.get(t.updated_by or t.created_by), "updated_at": t.updated_at}


def _names(session: Session, ts: list[EsgTarget]) -> dict[int, str]:
    ids = {t.updated_by or t.created_by for t in ts}
    return {u.id: u.name for u in session.exec(select(User).where(User.id.in_(ids)))} if ids else {}


def list_targets(session: Session, company_id: int, fy: Optional[str]) -> dict:
    fy = _check_fy(fy, "fy") if fy else default_fy(session, company_id)
    ts = session.exec(select(EsgTarget).where(EsgTarget.company_id == company_id, EsgTarget.is_active == True)  # noqa: E712
                      .order_by(EsgTarget.created_at)).all()
    names = _names(session, ts)
    items = [serialize(session, t, fy, names) for t in ts]
    summary = {s: sum(i["status"] == s for i in items)
               for s in ("on_track", "at_risk", "achieved", "missed", "partial", "no_data")}
    return {"fy": fy, "items": items, "summary": summary, "catalog": catalog()}


def _validate(data: dict, partial: bool = False, current: Optional[EsgTarget] = None) -> dict:
    out: dict[str, Any] = {}
    if "metric" in data or not partial:
        metric = data.get("metric")
        if metric not in CATALOG:
            raise AppError("invalid_metric", "Choose a figure from the list", 422, "metric")
        out["metric"] = metric
    metric = out.get("metric") or (current.metric if current else None)
    meta = CATALOG[metric]
    for f, limit in (("title", 120), ("unit", 40)):
        if f in data or not partial:
            v = str(data.get(f) or "").strip() or (meta["label"] if f == "title" else meta["unit"])
            if not v:
                raise AppError(f"{f}_required", f"Give the target a {f}", 422, f)
            out[f] = v[:limit]
    if "direction" in data or not partial:
        d = data.get("direction") or meta["direction"]
        if d not in ("reduce", "increase"):
            raise AppError("invalid_direction", "Direction must be reduce or increase", 422, "direction")
        out["direction"] = d
    for f in ("baseline_fy", "target_fy"):
        if f in data or not partial:
            out[f] = _check_fy(data.get(f), f)
    for f in ("baseline_value", "target_value"):
        if f in data or not partial:
            try:
                out[f] = float(data.get(f))
            except (TypeError, ValueError):
                raise AppError("invalid_value", "Enter a number", 422, f) from None
    if "manual_values" in data:
        mv = data.get("manual_values") or {}
        if not isinstance(mv, dict):
            raise AppError("invalid_values", "Yearly values must be an object", 422, "manual_values")
        clean = {}
        for k, v in mv.items():
            k = _check_fy(k, "manual_values")
            if v is None or v == "":
                continue
            try:
                clean[k] = float(v)
            except (TypeError, ValueError):
                raise AppError("invalid_value", f"The value for {k} is not a number", 422, "manual_values") from None
        out["manual_values"] = clean
    if "notes" in data:
        out["notes"] = (str(data.get("notes") or "").strip()[:1000]) or None
    by = out.get("baseline_fy") or (current.baseline_fy if current else None)
    ty = out.get("target_fy") or (current.target_fy if current else None)
    if by and ty and fy_year(ty) <= fy_year(by):
        raise AppError("invalid_period", "The target year must be after the baseline year", 422, "target_fy")
    bv = out.get("baseline_value", current.baseline_value if current else None)
    tv = out.get("target_value", current.target_value if current else None)
    d = out.get("direction") or (current.direction if current else None)
    if bv is not None and tv is not None and d:
        if (d == "reduce" and tv > bv) or (d == "increase" and tv < bv):
            raise AppError("target_wrong_way", f"To {d} the figure, the target must be "
                                               f"{'below' if d == 'reduce' else 'above'} the baseline", 422,
                           "target_value")
    return out


def get(session: Session, company_id: int, public_id: str) -> EsgTarget:
    t = session.exec(select(EsgTarget).where(EsgTarget.public_id == public_id, EsgTarget.company_id == company_id,
                                             EsgTarget.is_active == True)).first()  # noqa: E712
    if not t:
        raise NotFoundError("Target not found")
    return t


def create(session: Session, actor: User, company_id: int, data: dict, ip_address: Optional[str] = None) -> EsgTarget:
    v = _validate(data)
    n = len(session.exec(select(EsgTarget.id).where(EsgTarget.company_id == company_id,
                                                    EsgTarget.is_active == True)).all())  # noqa: E712
    if n >= 50:
        raise AppError("too_many_targets", "A company can have at most 50 active targets", 409)
    t = EsgTarget(company_id=company_id, created_by=actor.id, manual_values=v.pop("manual_values", {}), **v)
    session.add(t)
    session.flush()
    log_action(session, actor, "target.created", "esg_target", t.id, t.public_id, company_id=company_id,
               changes={"title": t.title, "metric": t.metric, "baseline": [t.baseline_fy, t.baseline_value],
                        "target": [t.target_fy, t.target_value]}, ip_address=ip_address)
    session.commit()
    session.refresh(t)
    return t


def update(session: Session, actor: User, t: EsgTarget, data: dict, ip_address: Optional[str] = None) -> EsgTarget:
    v = _validate(data, partial=True, current=t)
    changes = {k: val for k, val in v.items() if getattr(t, k) != val}
    if changes:
        for k, val in changes.items():
            setattr(t, k, val)
        t.updated_by = actor.id
        t.updated_at = datetime.utcnow()
        session.add(t)
        log_action(session, actor, "target.updated", "esg_target", t.id, t.public_id, company_id=t.company_id,
                   changes={"title": t.title, **changes}, ip_address=ip_address)
        session.commit()
        session.refresh(t)
    return t


def remove(session: Session, actor: User, t: EsgTarget, ip_address: Optional[str] = None) -> None:
    t.is_active = False
    t.updated_by = actor.id
    t.updated_at = datetime.utcnow()
    session.add(t)
    log_action(session, actor, "target.removed", "esg_target", t.id, t.public_id, company_id=t.company_id,
               changes={"title": t.title}, ip_address=ip_address)
    session.commit()


def names_for(session: Session, ts: list[EsgTarget]) -> dict[int, str]:
    return _names(session, ts)
