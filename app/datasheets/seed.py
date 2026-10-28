"""Idempotent registration of the data-sheet upload types and KPI definitions.

Production gets these from migration a7c3e9f15b20 (frozen copy of the
catalogue). This helper does the same from the live catalogue for test
databases built with ``create_all`` and for local scripts. It only adds what
is missing; it never changes or removes anything.
"""
from __future__ import annotations

from sqlmodel import Session, select

from app.datasheets.catalog import SHEETS
from app.datasheets.service import XLSX, kpi_code, upload_type_code
from app.models.kpi import KpiDefinition
from app.models.upload_type import UploadType


def ensure_datasheet_types(session: Session) -> dict:
    types = {t.code: t for t in session.exec(select(UploadType).where(UploadType.code.startswith("sheet_", autoescape=True))).all()}
    existing = {c for c in session.exec(select(KpiDefinition.code)).all()}
    added_types = added_defs = 0
    for code, spec in SHEETS.items():
        ut = types.get(upload_type_code(code))
        if ut is None:
            ut = UploadType(code=upload_type_code(code), display_name=f"{spec['title']} (data sheet)",
                            purpose=f"BRSR data sheet: {spec['title']}. One row per month and location.",
                            allowed_mime_types=[XLSX, "text/csv"], template_ref=f"datasheet:{code}",
                            validation_policy_ref="datasheet-strict", processing_mode="async")
            session.add(ut)
            session.flush()
            added_types += 1
        for c in spec["columns"]:
            if c["kind"] != "number" or kpi_code(code, c["name"]) in existing:
                continue
            session.add(KpiDefinition(code=kpi_code(code, c["name"]), display_name=c["name"].replace("_", " "),
                                      unit_hint=c.get("unit"), upload_type_id=ut.id))
            added_defs += 1
    session.commit()
    return {"upload_types_added": added_types, "kpi_definitions_added": added_defs}
