"""Shared, pure parsing logic for the pre-approval Data Preview
endpoint (reviews.py).

HONEST NOTE ON A DELIBERATE TRADE-OFF: this module's column-matching
and row-parsing logic closely mirrors kpi_extraction_service.py's own
internal parsing (same header-keyword matching, same domain/column
map). It does NOT import from or refactor that file. This is a
deliberate choice, not an oversight: Layer 1's extraction service has
been treated as LOCKED throughout this project -- extensively tested,
foundational, never to be reopened for refactoring. Modifying it, even
carefully, to share code with a new preview feature would carry real
regression risk on the most critical piece of this system for a purely
architectural tidiness gain. The trade-off accepted here: a small
amount of duplicated parsing logic, in exchange for zero risk to Layer
1. If the two ever need to be reconciled into one shared implementation,
that should be a deliberate, separately-reviewed decision -- not a side
effect of building this preview feature.

This module does NO database writes. It only reads (Dataset,
DatasetVersion, DatasetFile, UploadType, Site, KpiDefinition rows) and
returns plain dicts describing what the file actually contains. This
is deliberate: it is safe to run at ANY point in a dataset's lifecycle
-- draft, submitted, under_review, approved, rejected,
changes_requested -- since it never touches KpiValue or any other
table, and never conflicts with the real, separate extraction pipeline.
"""
from openpyxl import load_workbook
from sqlmodel import Session, select

from app.models.dataset import Dataset, DatasetVersion, DatasetFile
from app.models.upload_type import UploadType
from app.models.kpi import KpiDefinition
from app.models.master_data import Site
from app.storage.factory import get_storage


class FileParseError(Exception):
    """A hard failure — file unreadable, dataset/version missing. NOT
    raised for ordinary per-row data-quality issues (those come back as
    a 'skipped' entry in the result, not an exception)."""


_SITE_KEYWORDS = ["site"]
_PERIOD_KEYWORDS = ["period"]
_UNIT_KEYWORDS = ["unit"]
_WASTE_DISPOSAL_KEYWORDS = ["disposal"]

_DOMAIN_COLUMN_MAP = {
    "energy_data": {
        "value_cols": [("consumption", "energy.consumption", "energy_type", ["energy type", "energy_type"])],
    },
    "water_data": {
        "value_cols": [
            ("withdrawn", "water.withdrawal", "water_source_type", ["source type", "source_type"]),
            ("recycled", "water.recycled", "water_source_type", ["source type", "source_type"]),
        ],
    },
    "emissions_data": {
        "value_cols": [("activity data", "emissions.activity_data", "emission_scope", ["scope"])],
    },
    "waste_data": {
        "value_cols": [("quantity", "waste.generated", "waste_type", ["waste type", "waste_type"])],
    },
}


def _find_col(header_row, keywords):
    for idx, cell in enumerate(header_row):
        if cell is None:
            continue
        text = str(cell).strip().lower()
        if any(kw in text for kw in keywords):
            return idx
    return None


def parse_data_file_rows(session: Session, dataset_version_id: int) -> dict:
    """Parse every real data file attached to this dataset version and
    return exactly what it contains — no database write, works
    regardless of the version's current status.

    Returns {
        "rows": [{"row_number", "site_text", "site_id", "kpi_code",
                   "kpi_display_name", "value", "unit", "attributes",
                   "source_file_id", "source_filename"}, ...],
        "skipped_count": N,
        "upload_type_code": str,
    }
    """
    version = session.get(DatasetVersion, dataset_version_id)
    if not version:
        raise FileParseError(f"DatasetVersion {dataset_version_id} not found")
    dataset = session.get(Dataset, version.dataset_id)
    if not dataset:
        raise FileParseError(f"Dataset {version.dataset_id} not found")
    upload_type = session.get(UploadType, dataset.upload_type_id)
    if not upload_type:
        raise FileParseError(f"UploadType {dataset.upload_type_id} not found")

    domain_map = _DOMAIN_COLUMN_MAP.get(upload_type.code)
    if not domain_map:
        return {"rows": [], "skipped_count": 0, "upload_type_code": upload_type.code}

    data_files = session.exec(
        select(DatasetFile).where(
            DatasetFile.dataset_version_id == dataset_version_id,
            DatasetFile.role == "data",
        )
    ).all()
    if not data_files:
        return {"rows": [], "skipped_count": 0, "upload_type_code": upload_type.code}

    definitions = {
        d.code: d for d in session.exec(
            select(KpiDefinition).where(KpiDefinition.upload_type_id == upload_type.id, KpiDefinition.is_active == True)  # noqa: E712
        ).all()
    }

    sites_by_name = {s.name.strip().lower(): s for s in session.exec(select(Site).where(Site.company_id == dataset.company_id)).all()}
    sites_by_code = {s.code.strip().lower(): s for s in session.exec(select(Site).where(Site.company_id == dataset.company_id)).all()}

    storage = get_storage()
    result_rows: list[dict] = []
    skipped_count = 0

    for f in data_files:
        try:
            file_stream = storage.get(f.storage_key)
        except Exception as e:
            raise FileParseError(f"Could not read file {f.storage_key}: {e}")
        try:
            wb = load_workbook(file_stream, read_only=True, data_only=True)
            ws = wb.active
            rows = list(ws.iter_rows(values_only=True))
        except Exception as e:
            raise FileParseError(f"Could not parse workbook {f.original_filename}: {e}")

        if not rows:
            continue
        header = rows[0]
        site_col = _find_col(header, _SITE_KEYWORDS)
        unit_col = _find_col(header, _UNIT_KEYWORDS)
        disposal_col = _find_col(header, _WASTE_DISPOSAL_KEYWORDS) if upload_type.code == "waste_data" else None

        for row_idx, row in enumerate(rows[1:], start=2):
            if row is None or all(c is None for c in row):
                continue

            site_text = str(row[site_col]).strip() if site_col is not None and row[site_col] is not None else None
            site = None
            if site_text:
                site = sites_by_name.get(site_text.lower()) or sites_by_code.get(site_text.lower())
            unit_val = str(row[unit_col]).strip() if unit_col is not None and row[unit_col] is not None else "unspecified"

            for value_keyword, kpi_code, attr_key, attr_keywords in domain_map["value_cols"]:
                definition = definitions.get(kpi_code)
                value_col = _find_col(header, [value_keyword])
                if value_col is None or definition is None:
                    skipped_count += 1
                    continue

                raw_value = row[value_col]
                try:
                    numeric_value = float(raw_value)
                except (TypeError, ValueError):
                    skipped_count += 1
                    continue

                attr_col = _find_col(header, attr_keywords)
                attributes = {}
                if attr_col is not None and row[attr_col] is not None:
                    attributes[attr_key] = str(row[attr_col]).strip()
                if disposal_col is not None and row[disposal_col] is not None:
                    attributes["disposal_method"] = str(row[disposal_col]).strip()

                result_rows.append({
                    "row_number": row_idx,
                    "site_text": site_text,
                    "site_public_id": site.public_id if site else None,
                    "site_name": site.name if site else None,
                    "kpi_code": kpi_code,
                    "kpi_display_name": definition.display_name,
                    "value": numeric_value,
                    "unit": unit_val,
                    "attributes": attributes,
                    "source_file_id": f.id,
                    "source_filename": f.original_filename,
                })

    return {"rows": result_rows, "skipped_count": skipped_count, "upload_type_code": upload_type.code}
