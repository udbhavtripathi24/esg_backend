"""Report DOCX generator — a PURE function. Takes already-resolved data
in, returns document bytes out. Deliberately has zero database or
storage access inside it, so it can be unit-tested completely
independently of ProcessingJob/session/storage concerns, and so the
worker handler (which DOES need that safety discipline) stays a thin,
separately-testable wrapper around this.

BOUNDARY, enforced structurally: this function never computes or writes
an ESG score, CO2e, Scope 1/2/3 figure, emission factor, framework
compliance statement, target progress, or benchmarking comparison. Every
number it writes traces directly to a real `value` field on a real,
already-approved `KpiValue` row passed in via `kpi_rows`. Where a
concept is genuinely unsupported, the document says so explicitly
("Not calculated in current MVP") rather than omitting the section
silently or inventing a placeholder number.
"""
from datetime import datetime, date
from io import BytesIO
from docx import Document
from docx.shared import Pt, RGBColor

GENERATOR_VERSION = "report-generator-v1"

_DOMAIN_LABELS = {
    "energy_data": "Energy", "water_data": "Water",
    "waste_data": "Waste", "emissions_data": "Emissions",
}


def build_report_docx(
    *,
    company_name: str,
    report_type: str,
    period_start: date,
    period_end: date,
    version_number: int,
    generated_at: datetime,
    template_name: str,
    kpi_rows: list[dict],        # [{kpi_code, display_name, value, unit, site_id, attributes}, ...]
    domain_completeness: dict,   # the real, existing domain_completeness() result
    evidence_files: list[dict],  # [{original_filename, public_id}, ...]
    excluded_unrecognized_units: list[str],
) -> bytes:
    doc = Document()

    # ---- Cover ----
    title = doc.add_heading("Deloitte Vista", level=0)
    title.runs[0].font.color.rgb = RGBColor(0x64, 0xBC, 0x44)
    doc.add_heading(f"{report_type.replace('_', ' ').title()}", level=1)
    p = doc.add_paragraph()
    p.add_run(f"{company_name}\n").bold = True
    p.add_run(f"Reporting period: {period_start.isoformat()} to {period_end.isoformat()}\n")
    p.add_run(f"Report version: v{version_number}\n")
    p.add_run(f"Generated: {generated_at.strftime('%Y-%m-%d %H:%M UTC')}\n")
    doc.add_page_break()

    # ---- Report Metadata ----
    doc.add_heading("Report Metadata", level=1)
    meta_table = doc.add_table(rows=0, cols=2)
    for label, value in [
        ("Reporting period", f"{period_start.isoformat()} — {period_end.isoformat()}"),
        ("Report version", f"v{version_number}"),
        ("Generated", generated_at.strftime("%Y-%m-%d %H:%M UTC")),
        ("Template", template_name),
        ("Generator version", GENERATOR_VERSION),
    ]:
        row = meta_table.add_row()
        row.cells[0].text = label
        row.cells[1].text = str(value)

    # ---- Data Coverage ----
    doc.add_heading("Data Coverage", level=1)
    doc.add_paragraph(
        f"{domain_completeness['approved_count']} of {domain_completeness['total']} "
        f"MVP domains have authoritative approved data for this reporting period."
    )
    cov_table = doc.add_table(rows=1, cols=2)
    cov_table.rows[0].cells[0].text = "Domain"
    cov_table.rows[0].cells[1].text = "Status"
    for code, approved in domain_completeness["domains"].items():
        row = cov_table.add_row()
        row.cells[0].text = _DOMAIN_LABELS.get(code, code)
        row.cells[1].text = "Approved data available" if approved else "No approved data for this period"

    # ---- KPI Summary + Domain Sections ----
    doc.add_heading("KPI Summary", level=1)
    if not kpi_rows:
        doc.add_paragraph("No approved KPI data is available for this reporting period.")
    else:
        by_domain: dict[str, list[dict]] = {}
        for r in kpi_rows:
            domain = r["kpi_code"].split(".")[0]
            by_domain.setdefault(domain, []).append(r)

        for domain, rows in by_domain.items():
            doc.add_heading(domain.replace("_", " ").title(), level=2)
            table = doc.add_table(rows=1, cols=4)
            hdr = table.rows[0].cells
            hdr[0].text, hdr[1].text, hdr[2].text, hdr[3].text = "KPI", "Value", "Unit", "Site"
            for r in rows:
                row = table.add_row().cells
                row[0].text = r["display_name"]
                row[1].text = f"{r['value']:,}"
                row[2].text = r["unit"]
                row[3].text = f"Site #{r['site_id']}" if r["site_id"] else "Unassigned"

    # ---- Data Quality / Limitations ----
    doc.add_heading("Data Quality / Limitations", level=1)
    missing_domains = [_DOMAIN_LABELS.get(c, c) for c, ok in domain_completeness["domains"].items() if not ok]
    if missing_domains:
        doc.add_paragraph(f"No approved data available for: {', '.join(missing_domains)}.")
    if excluded_unrecognized_units:
        doc.add_paragraph(
            f"Values reported in unrecognized units ({', '.join(excluded_unrecognized_units)}) "
            f"were excluded from any converted totals shown in this report."
        )
    doc.add_paragraph(
        "The following are NOT calculated or included in this report: ESG scores, "
        "carbon dioxide equivalent (CO2e) figures, Scope 1/2/3 emissions, benchmarking "
        "comparisons, framework compliance assessments (e.g. GRI, SASB, CSRD), and "
        "assurance conclusions. These require methodology approvals not yet available."
    )

    # ---- Evidence Register ----
    doc.add_heading("Evidence Register", level=1)
    if not evidence_files:
        doc.add_paragraph("No evidence files are referenced by this report's underlying data.")
    else:
        table = doc.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "File"
        table.rows[0].cells[1].text = "Reference ID"
        for f in evidence_files:
            row = table.add_row().cells
            row[0].text = f["original_filename"]
            row[1].text = f["public_id"]

    # ---- Methodology / Technical Notes ----
    doc.add_heading("Methodology / Technical Notes", level=1)
    doc.add_paragraph(
        "This report includes only KPI values from dataset versions that have completed "
        "this platform's full review and approval workflow. Values are shown exactly as "
        "reported, in their originally submitted units, with no conversion, scoring, or "
        "derived calculation applied unless explicitly stated."
    )

    # ---- Appendix ----
    doc.add_heading("Appendix", level=1)
    doc.add_paragraph(f"Generator version: {GENERATOR_VERSION}")
    doc.add_paragraph(f"Template: {template_name}")

    buf = BytesIO()
    doc.save(buf)
    return buf.getvalue()
