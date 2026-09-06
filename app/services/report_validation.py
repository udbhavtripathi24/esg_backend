"""Technical validation for a generated report artifact.

Deliberately NOT business/framework/assurance validation — see module
docstring in report_docx_generator.py for the same boundary. This module
only checks: did the document actually get built correctly, does it
contain what the snapshot said it should, is the artifact itself sound.
"""
from io import BytesIO
from docx import Document


REQUIRED_HEADINGS = [
    "Report Metadata", "Data Coverage", "KPI Summary",
    "Data Quality / Limitations", "Evidence Register", "Methodology / Technical Notes",
]


def validate_generated_docx(doc_bytes: bytes, expected_kpi_value_count: int) -> dict:
    """Returns {"valid": bool, "errors": [...]}. Never raises for an
    ordinary validation failure — only for a genuinely unreadable file,
    which is itself reported as an error, not an exception the caller
    must catch."""
    errors: list[str] = []

    if not doc_bytes or len(doc_bytes) == 0:
        return {"valid": False, "errors": ["Generated artifact is empty."]}

    try:
        doc = Document(BytesIO(doc_bytes))
    except Exception as e:
        return {"valid": False, "errors": [f"Generated file could not be opened as a valid DOCX: {e}"]}

    heading_texts = {p.text for p in doc.paragraphs if p.style.name.startswith("Heading")}
    for required in REQUIRED_HEADINGS:
        if required not in heading_texts:
            errors.append(f"Required section missing: {required}")

    # Count actual KPI rows rendered across all tables (subtract header rows).
    # This is a structural check -- it does NOT re-validate the business
    # correctness of the values themselves, only that the expected number
    # of rows made it into the document at all.
    kpi_table_row_count = 0
    for table in doc.tables:
        if table.rows and table.rows[0].cells[0].text == "KPI":
            kpi_table_row_count += len(table.rows) - 1
    if expected_kpi_value_count > 0 and kpi_table_row_count == 0:
        errors.append("Snapshot references KPI values but none appear in the generated document.")

    return {"valid": len(errors) == 0, "errors": errors}
