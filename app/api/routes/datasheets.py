"""Data sheets: catalogue, Excel templates, dry-run validation, coverage.

Uploading itself goes through the normal Upload Center endpoints (create a
dataset of upload type ``sheet_<code>`` → version → file → submit → review);
those enforce the same strict validation (datasets.upload_file).
"""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import Response
from sqlmodel import Session, select

from app.api.deps import require_permission
from app.api.routes.datasets import content_disposition
from app.core.errors import AppError, NotFoundError
from app.datasheets.catalog import IDENTITY, SHEETS
from app.datasheets.service import XLSX, all_columns, parse_sheet, template_workbook, upload_type_code
from app.db.session import get_session
from app.models.upload_type import UploadType
from app.models.user import User

router = APIRouter(prefix="/datasheets", tags=["datasheets"])

MAX_CHECK_BYTES = 25 * 1024 * 1024


def _spec(code: str) -> dict:
    if code not in SHEETS:
        raise NotFoundError("Unknown data sheet")
    return SHEETS[code]


@router.get("")
def list_sheets(session: Session = Depends(get_session),
                _actor: User = Depends(require_permission("dataset:read"))):
    types = {t.code: t for t in session.exec(select(UploadType).where(UploadType.code.startswith("sheet_", autoescape=True))).all()}
    out = []
    for code, s in SHEETS.items():
        ut = types.get(upload_type_code(code))
        out.append({
            "code": code, "title": s["title"], "departments": s["departments"],
            "upload_type_code": upload_type_code(code),
            "upload_type_id": getattr(ut, "id", None),
            "available": bool(ut and ut.is_active),
            "has_category": any(c["name"] == "Category" for c in s["columns"]),
            "column_count": len(s["columns"]),
        })
    return {"identity_columns": IDENTITY, "sheets": out}


@router.get("/coverage")
def coverage(company_id: Optional[int] = None, fy: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}$"),
             session: Session = Depends(get_session),
             actor: User = Depends(require_permission("dataset:read"))):
    """Months of approved data per sheet for a financial year (e.g. 2024-25)."""
    from app.brsr.service import resolve_company_id
    from app.brsr.sheet_metrics import sheet_coverage
    cid = resolve_company_id(session, actor, company_id)
    return sheet_coverage(session, cid, fy)


@router.get("/formula-sheet")
def formula_sheet(_actor: User = Depends(require_permission("dataset:read"))):
    """How every quantitative BRSR figure is calculated, for sign-off (Excel)."""
    from app.datasheets.formula_sheet import build
    return Response(build(), media_type=XLSX,
                    headers={"Content-Disposition": content_disposition("BRSR-formula-sheet.xlsx")})


@router.get("/{code}")
def sheet_detail(code: str, _actor: User = Depends(require_permission("dataset:read"))):
    s = _spec(code)
    return {"code": code, "title": s["title"], "departments": s["departments"],
            "upload_type_code": upload_type_code(code), "columns": all_columns(code)}


@router.get("/{code}/template")
def sheet_template(code: str, _actor: User = Depends(require_permission("dataset:read"))):
    s = _spec(code)
    name = f"{s['template_file']}-template.xlsx"
    return Response(template_workbook(code), media_type=XLSX,
                    headers={"Content-Disposition": content_disposition(name)})


@router.post("/{code}/check")
async def check_sheet(code: str, file: UploadFile = File(...),
                      _actor: User = Depends(require_permission("file:upload"))):
    """Validate a file against the template without storing anything."""
    _spec(code)
    content = await file.read()
    if len(content) > MAX_CHECK_BYTES:
        raise AppError("file_too_large", "Files may be at most 25 MB", 413)
    return parse_sheet(code, file.filename or "", content).summary(limit=200)
