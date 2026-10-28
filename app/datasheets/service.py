"""Data sheets: parse, validate, template, extract.

A data sheet is one of the manager's 27 templates (app/datasheets/catalog.py)
uploaded through the existing Upload Center flow (dataset → version → file →
review → approval). This module adds what is specific to them:

* ``parse_sheet``    strict validation of an uploaded file against its
                     template — every problem reported with its row and
                     column, so the client can fix the file in one pass;
* ``template_workbook`` the blank Excel template (headers in his exact
                     names, an instructions sheet, drop-downs for choices);
* ``extract_values`` on approval, one raw value per row × numeric column,
                     stamped with the row's own period, location and
                     category, fully traceable to the source row.

Like Layer 1 extraction, nothing here converts units or computes anything:
values are stored as reported. Calculation happens when the BRSR is built
(app/brsr/sheet_metrics.py).
"""
from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Optional

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation

from app.datasheets.catalog import IDENTITY, SHEETS

PREFIX = "sheet_"
MAX_ROWS = 20000
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def upload_type_code(sheet: str) -> str:
    return f"{PREFIX}{sheet}"


def sheet_of_upload_type(code: Optional[str]) -> Optional[str]:
    if code and code.startswith(PREFIX) and code[len(PREFIX):] in SHEETS:
        return code[len(PREFIX):]
    return None


def kpi_code(sheet: str, column: str) -> str:
    return f"{sheet}.{column}"


def all_columns(sheet: str) -> list[dict]:
    return [{"name": n, "kind": "date" if n.endswith("_Date") else "text", "required": True}
            for n in IDENTITY] + SHEETS[sheet]["columns"]


def _norm(s: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(s or "").lower())


# ---------------------------------------------------------------------------
# Parsing and validation
# ---------------------------------------------------------------------------

@dataclass
class ParsedSheet:
    sheet: str
    rows: list[dict] = field(default_factory=list)       # {"row": n, "values": {col: value}}
    errors: list[dict] = field(default_factory=list)     # {"row", "column", "message"}
    warnings: list[dict] = field(default_factory=list)
    period_start: Optional[date] = None
    period_end: Optional[date] = None

    @property
    def ok(self) -> bool:
        return not self.errors

    def summary(self, limit: int = 50) -> dict:
        return {"sheet": self.sheet, "rows": len(self.rows), "ok": self.ok,
                "period_start": self.period_start, "period_end": self.period_end,
                "errors": self.errors[:limit], "error_count": len(self.errors),
                "warnings": self.warnings[:limit], "warning_count": len(self.warnings)}


def _to_date(v: Any) -> Optional[date]:
    if v is None or v == "":
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d", "%d-%b-%Y", "%d %b %Y", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    try:  # Excel serial number in a CSV
        n = float(s)
        if 20000 < n < 80000:
            return date(1899, 12, 30) + timedelta(days=int(n))
    except ValueError:
        pass
    return None


def _to_number(v: Any) -> tuple[Optional[float], bool]:
    """(value, ok). Blank is (None, True)."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None, True
    if isinstance(v, bool):
        return None, False
    if isinstance(v, (int, float)):
        return float(v), v == v and v not in (float("inf"), float("-inf"))
    s = str(v).strip().replace(",", "")
    try:
        return float(s), True
    except ValueError:
        return None, False


def _read_table(filename: str, content: bytes) -> list[list[Any]]:
    name = (filename or "").lower()
    if name.endswith(".csv"):
        text = content.decode("utf-8-sig", errors="replace")
        return [row for row in csv.reader(io.StringIO(text))]
    wb = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    ws = wb.worksheets[0]
    out = []
    for r in ws.iter_rows(values_only=True):
        out.append(list(r))
        if len(out) > MAX_ROWS + 1:
            break
    return out


def parse_sheet(sheet: str, filename: str, content: bytes) -> ParsedSheet:
    spec = SHEETS[sheet]
    res = ParsedSheet(sheet=sheet)
    try:
        table = _read_table(filename, content)
    except Exception:  # noqa: BLE001 — any parse failure is the same answer for the user
        res.errors.append({"row": None, "column": None,
                           "message": "The file could not be read. Upload the Excel template (.xlsx) or a CSV."})
        return res
    table = [r for r in table if r and any(c not in (None, "") for c in r)]
    if not table:
        res.errors.append({"row": None, "column": None, "message": "The file is empty."})
        return res
    if len(table) - 1 > MAX_ROWS:
        res.errors.append({"row": None, "column": None, "message": f"At most {MAX_ROWS} rows per file."})
        return res

    header = [str(c).strip() if c is not None else "" for c in table[0]]
    expected = all_columns(sheet)
    by_norm = {_norm(c["name"]): c for c in expected}
    col_index: dict[str, int] = {}
    for i, h in enumerate(header):
        if not h:
            continue
        c = by_norm.get(_norm(h))
        if c is None:
            res.warnings.append({"row": 1, "column": h,
                                 "message": f"Column '{h}' is not part of the {spec['title']} template and is ignored."})
        elif c["name"] in col_index:
            res.errors.append({"row": 1, "column": h, "message": f"Column '{c['name']}' appears twice."})
        else:
            col_index[c["name"]] = i
    missing_required = [c["name"] for c in expected if c.get("required") and c["name"] not in col_index]
    if missing_required:
        res.errors.append({"row": 1, "column": None,
                           "message": f"This doesn't look like the {spec['title']} template: missing column(s) "
                                      f"{', '.join(missing_required)}. Download the template and copy your data into it."})
        return res
    own = [c["name"] for c in spec["columns"] if not c.get("required")]
    found = [n for n in own if n in col_index]
    if own and len(found) * 2 < len(own):
        res.errors.append({"row": 1, "column": None,
                           "message": f"This doesn't look like the {spec['title']} template: only {len(found)} of its "
                                      f"{len(own)} columns are in the file. Check you picked the right sheet, or "
                                      f"download the template and copy your data into it."})
        res.warnings = []
        return res
    missing_other = [c["name"] for c in expected if not c.get("required") and c["name"] not in col_index]
    if missing_other:
        res.warnings.append({"row": 1, "column": None,
                             "message": f"{len(missing_other)} template column(s) are missing and will be treated as "
                                        f"not reported: {', '.join(missing_other[:8])}"
                                        + ("…" if len(missing_other) > 8 else "")})

    seen: dict[tuple, int] = {}
    numeric_idx = [col_index[c["name"]] for c in expected if c["kind"] == "number" and c["name"] in col_index]
    identity_idx = [col_index[n] for n in IDENTITY if n in col_index]
    skipped = 0
    for n, raw in enumerate(table[1:], start=2):
        def _blank(i: int) -> bool:
            return i >= len(raw) or raw[i] is None or (isinstance(raw[i], str) and not raw[i].strip())
        # Filler rows: a default value dragged down a text column, but no
        # period, location or figures. Skip them instead of failing the file.
        if all(_blank(i) for i in identity_idx) and all(_blank(i) for i in numeric_idx):
            skipped += 1
            continue
        row: dict[str, Any] = {}
        errs_before = len(res.errors)
        for c in expected:
            if c["name"] not in col_index:
                continue
            i = col_index[c["name"]]
            v = raw[i] if i < len(raw) else None
            name, kind = c["name"], c["kind"]
            if kind == "date":
                d = _to_date(v)
                if d is None:
                    res.errors.append({"row": n, "column": name, "message": "Enter a date (e.g. 2024-04-01)"})
                row[name] = d
            elif kind == "number":
                num, ok = _to_number(v)
                if not ok:
                    res.errors.append({"row": n, "column": name, "message": f"'{v}' is not a number"})
                elif num is not None and num < 0:
                    res.errors.append({"row": n, "column": name, "message": "Cannot be negative"})
                row[name] = num if ok else None
            else:
                s = str(v).strip() if v not in (None, "") else ""
                if c.get("required") and not s:
                    res.errors.append({"row": n, "column": name, "message": "Required"})
                if kind == "choice" and s:
                    match = next((o for o in c["options"] if o.lower() == s.lower()), None)
                    if match is None:
                        res.errors.append({"row": n, "column": name,
                                           "message": f"'{s}' is not one of: {', '.join(c['options'])}"})
                    s = match or s
                row[name] = s or None
        start, end = row.get("Start_Date"), row.get("End_Date")
        if start and end:
            if end < start:
                res.errors.append({"row": n, "column": "End_Date", "message": "End date is before the start date"})
            elif (end - start).days > 366:
                res.errors.append({"row": n, "column": "End_Date", "message": "A row covers at most one year"})
        if len(res.errors) == errs_before and start and end:
            key = (start, end, (row.get("Location") or "").lower(), (row.get("Category") or "").lower()) + \
                tuple((row.get(c["name"]) or "").lower() for c in spec["columns"] if c["kind"] in ("text", "choice")
                      and c["name"] != "Category")
            if key in seen:
                res.warnings.append({"row": n, "column": None,
                                     "message": f"Same period, location and category as row {seen[key]}: both are "
                                                f"counted. Remove one if it is a duplicate."})
            else:
                seen[key] = n
            res.period_start = min(filter(None, [res.period_start, start]))
            res.period_end = max(filter(None, [res.period_end, end]))
        res.rows.append({"row": n, "values": row})
    if skipped:
        res.warnings.append({"row": None, "column": None,
                             "message": f"{skipped} row(s) had no dates, location or figures and were skipped."})
    if not res.rows:
        res.errors.append({"row": None, "column": None, "message": "The file has headers but no data rows."})
    return res


def row_keys(parsed: ParsedSheet) -> set[tuple]:
    """Identity of each row: period, location, category and text dimensions."""
    spec = SHEETS[parsed.sheet]
    dims = [c["name"] for c in spec["columns"] if c["kind"] in ("text", "choice")]
    out = set()
    for r in parsed.rows:
        v = r["values"]
        out.add((v.get("Start_Date"), v.get("End_Date"), (v.get("Location") or "").strip().lower())
                + tuple((str(v.get(d)) if v.get(d) is not None else "").strip().lower() for d in dims))
    return out


# ---------------------------------------------------------------------------
# Template
# ---------------------------------------------------------------------------

def template_workbook(sheet: str) -> bytes:
    spec = SHEETS[sheet]
    cols = all_columns(sheet)
    wb = Workbook()
    ws = wb.active
    ws.title = spec["sheet_name"][:31]
    head_fill = PatternFill("solid", fgColor="26890D")
    for i, c in enumerate(cols, start=1):
        cell = ws.cell(row=1, column=i, value=c["name"])
        cell.font = Font(bold=True, color="FFFFFF")
        cell.fill = head_fill
        cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.column_dimensions[get_column_letter(i)].width = max(14, min(42, len(c["name"]) + 2))
        if c["kind"] == "choice":
            dv = DataValidation(type="list", formula1='"' + ",".join(c["options"]) + '"', allow_blank=not c.get("required"))
            dv.add(f"{get_column_letter(i)}2:{get_column_letter(i)}5000")
            ws.add_data_validation(dv)
        if c["kind"] == "date":
            for r in range(2, 200):
                ws.cell(row=r, column=i).number_format = "yyyy-mm-dd"
    ws.freeze_panes = "A2"
    info = wb.create_sheet("Instructions")
    info.column_dimensions["A"].width = 52
    info.column_dimensions["B"].width = 18
    info.column_dimensions["C"].width = 60
    info.append([f"{spec['title']} — data sheet template"])
    info["A1"].font = Font(bold=True, size=13)
    info.append(["One row per month (Start_Date to End_Date) and location"
                 + (", and per Category (Employee or Worker)." if any(c["name"] == "Category" for c in cols) else ".")])
    info.append(["Leave a cell blank if not applicable. Do not rename or reorder the headers."])
    info.append([])
    info.append(["Column", "Type", "Notes"])
    for c in info[5]:
        c.font = Font(bold=True)
    for c in cols:
        notes = c.get("unit") or ""
        if c["kind"] == "choice":
            notes = "One of: " + ", ".join(c["options"])
        if c.get("hint"):
            notes = c["hint"]
        if c["kind"] == "date":
            notes = "Date, e.g. 2024-04-01"
        info.append([c["name"], c["kind"] + (" (required)" if c.get("required") else ""), notes])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def value_rows(parsed: ParsedSheet) -> list[dict]:
    """One dict per (row, numeric column) with a reported value."""
    spec = SHEETS[parsed.sheet]
    attr_cols = [c["name"] for c in spec["columns"] if c["kind"] in ("text", "choice")]
    out = []
    for r in parsed.rows:
        v = r["values"]
        attrs = {"location": v.get("Location"), "business": v.get("Businesses")}
        for a in attr_cols:
            if v.get(a) is not None:
                attrs[a] = v[a]
        for c in spec["columns"]:
            if c["kind"] != "number" or v.get(c["name"]) is None:
                continue
            out.append({"row": r["row"], "column": c["name"], "value": v[c["name"]], "unit": c.get("unit") or "",
                        "attributes": attrs, "start": v["Start_Date"], "end": v["End_Date"]})
    return out
