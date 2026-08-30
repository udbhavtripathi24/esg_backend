"""Seed the initial upload types (Stage 4).

Only the SLOTS — not any KPI formulas, templates, or validation rules.
Real templates plug in later when the KPI catalogue arrives.

Aligned with stakeholder's MVP KPI scope: Waste, Energy, Emissions, Water.
"""
from sqlmodel import Session, select
from app.db.session import engine
from app.models.upload_type import UploadType


XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
XLS = "application/vnd.ms-excel"
CSV = "text/csv"
PDF = "application/pdf"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


UPLOAD_TYPES = [
    # Structured/quantitative — Excel/CSV per approved decision 4
    dict(code="energy_data", display_name="Energy data",
         purpose="Site-level energy consumption records for the reporting period",
         allowed_mime_types=[XLSX, XLS, CSV], processing_mode="async"),
    dict(code="water_data", display_name="Water data",
         purpose="Site-level water withdrawal, discharge, and recycling records",
         allowed_mime_types=[XLSX, XLS, CSV], processing_mode="async"),
    dict(code="emissions_data", display_name="Emissions data",
         purpose="Scope 1/2/3 activity data by site and category",
         allowed_mime_types=[XLSX, XLS, CSV], processing_mode="async"),
    dict(code="waste_data", display_name="Waste data",
         purpose="Site-level waste generation, disposal, and recycling records",
         allowed_mime_types=[XLSX, XLS, CSV], processing_mode="async"),
    # Evidence — never parsed for numbers (approved decision 4)
    dict(code="general_evidence", display_name="Supporting evidence",
         purpose="Bills, audit letters, certificates supporting a dataset",
         allowed_mime_types=[PDF, DOCX], processing_mode="sync"),
]


def seed_upload_types(session: Session) -> dict:
    count = 0
    for spec in UPLOAD_TYPES:
        existing = session.exec(select(UploadType).where(UploadType.code == spec["code"])).first()
        if not existing:
            session.add(UploadType(**spec))
            count += 1
    session.commit()
    return {"upload_types_added": count, "total_defined": len(UPLOAD_TYPES)}


if __name__ == "__main__":
    with Session(engine) as s:
        result = seed_upload_types(s)
        print("Seeded:", result)
