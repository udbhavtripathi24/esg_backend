"""Extraction of approved data-sheet versions into KpiValue rows.

Called from kpi_extraction_service.extract_kpi_values_for_version when the
dataset's upload type is a data sheet (``sheet_<code>``). The caller has
already done the idempotency check and resolved the dataset and upload type;
this function only produces the rows and hands them to the shared, conflict-
safe writer, so the guarantees of the Layer 1 pipeline are unchanged:

* one KpiValue per (source row, numeric column) — the existing unique key;
* every value traceable to its file and 1-based row number;
* values stored exactly as reported (unit from the template, no conversion);
* only codes with an active KpiDefinition are written.

Each row carries its own period (Start_Date..End_Date), so a single file can
hold a whole year of monthly rows.
"""
from __future__ import annotations

import logging
from typing import Optional

from sqlmodel import Session, select

from app.datasheets.service import kpi_code, parse_sheet, value_rows
from app.models.kpi import KpiDefinition, KpiValue

log = logging.getLogger(__name__)


def extract_sheet_version(session: Session, *, sheet: str, version, dataset, upload_type,
                          job_id: Optional[int], write) -> dict:
    from app.models.dataset import DatasetFile
    from app.models.master_data import Site
    from app.storage.factory import get_storage

    files = session.exec(select(DatasetFile).where(
        DatasetFile.dataset_version_id == version.id, DatasetFile.role == "data",
        DatasetFile.deleted_at.is_(None))).all()
    if not files:
        return {"rows_written": 0, "rows_skipped": 0, "reason": "no_data_file"}

    valid = {c: v for c, v in session.exec(select(KpiDefinition.code, KpiDefinition.version).where(
        KpiDefinition.upload_type_id == upload_type.id, KpiDefinition.is_active == True)).all()}  # noqa: E712
    sites = session.exec(select(Site).where(Site.company_id == dataset.company_id)).all()
    site_by = {}
    for s in sites:
        for k in (s.name, getattr(s, "code", None)):
            if k:
                site_by[k.strip().lower()] = s.id

    storage = get_storage()
    out: list[KpiValue] = []
    skipped = 0
    for f in files:
        try:
            stream = storage.get(f.storage_key)
            content = stream.read() if hasattr(stream, "read") else bytes(stream)
        except Exception as e:  # noqa: BLE001
            from app.services.kpi_extraction_service import ExtractionError
            raise ExtractionError(f"Could not read file {f.storage_key}: {e}") from e
        parsed = parse_sheet(sheet, f.original_filename, content)
        if not parsed.ok:
            # Upload-time validation should have stopped this; never write a
            # partial picture of an invalid file.
            from app.services.kpi_extraction_service import ExtractionError
            first = parsed.errors[0]
            raise ExtractionError(f"{f.original_filename} failed validation "
                                  f"(row {first.get('row')}, {first.get('column')}): {first['message']}")
        for v in value_rows(parsed):
            code = kpi_code(sheet, v["column"])
            if code not in valid:
                skipped += 1
                continue
            loc = (v["attributes"].get("location") or "").strip().lower()
            out.append(KpiValue(
                dataset_id=dataset.id, dataset_version_id=version.id, source_file_id=f.id,
                source_row_number=v["row"], extraction_job_id=job_id, company_id=dataset.company_id,
                site_id=site_by.get(loc), kpi_code=code, kpi_definition_version=valid[code],
                value=v["value"], unit=v["unit"], attributes=v["attributes"],
                reporting_period_start=v["start"], reporting_period_end=v["end"]))
    if skipped:
        log.warning("datasheet %s: %d value(s) had no active KpiDefinition and were skipped", sheet, skipped)
    return write(session, out, skipped)
