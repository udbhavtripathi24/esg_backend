"""The BRSR question bank — SEBI's Business Responsibility and
Sustainability Report format, encoded as data.

Source: SEBI circular SEBI/HO/CFD/CFD-SEC-2/P/CIR/2023/122 (BRSR format,
Annexure I), cross-checked line by line against the reference filing
supplied by the engagement manager (BRSR_Report_FY2024-25).

Everything downstream is driven by this one definition: the client
questionnaire renders from it, validation checks against it, and the
Word report is laid out from it. Adding or correcting a question is a
change here and nowhere else.

Conventions
-----------
* Question ids are stable and must never be renumbered once a client has
  answered them (they are the storage key): ``A.20a``, ``B.1a``,
  ``P3.E9`` (Principle 3, Essential indicator 9), ``P6.L2`` (Leadership 2).
* ``P6.DEN`` is a platform helper question (``helper=True``): it collects
  the intensity denominators (revenue, PPP factor, physical output) once,
  so every "per rupee of turnover" intensity in the report is computed
  from the same figure. It is not printed as a BRSR question.
* Table columns that belong to a financial year are prefixed ``cy_``
  (current FY), ``py_`` (previous FY) or ``ppy_`` (the year before).
  Previous-year columns are pre-filled from last year's approved
  submission by matching the suffix.
* Cells with a ``formula`` are computed, never typed. Column formulas use
  column keys of the same row; row formulas use row keys of the same
  column. When both apply, the column formula wins (so a "Total" row's
  percentage is recomputed from the totals, not summed).
* Leadership indicators are voluntary under SEBI's format, so they are
  ``required=False``. Everything in Sections A and B and every Essential
  indicator is required — answerable as "Not applicable" with a reason.
"""
from __future__ import annotations

import copy
import re
from typing import Any

# ---------------------------------------------------------------------------
# Column / row / question builders
# ---------------------------------------------------------------------------

INPUT_KINDS = {"count", "number", "amount", "percent", "rate", "text", "longtext", "yesno", "choice"}
YESNO_OPTIONS = ["Yes", "No", "NA"]
PERIOD_LABEL = {"cy": "{cy} (Current FY)", "py": "{py} (Previous FY)", "ppy": "{ppy} (Prior to Previous FY)"}


def col(key: str, label: str, kind: str = "count", *, formula: str | None = None,
        lte: str | None = None, options: list[str] | None = None, unit: str | None = None) -> dict:
    c: dict[str, Any] = {"key": key, "label": label, "kind": "computed" if formula else kind}
    if formula:
        c["formula"] = formula
        c["display"] = kind  # how to format the computed value
    if lte:
        c["lte"] = lte
    if options:
        c["options"] = options
    if unit:
        c["unit"] = unit
    return c


def row(key: str, label: str, *, formula: str | None = None, header: bool = False,
        display: str | None = None) -> dict:
    r: dict[str, Any] = {"key": key, "label": label}
    if header:
        r["header"] = True
    if formula:
        r["formula"] = formula
        if display:
            r["display"] = display
    return r


def fy_cols(base: list[dict], periods: tuple[str, ...] = ("cy", "py")) -> list[dict]:
    """Repeat ``base`` columns once per financial year, prefixing keys and
    rewriting ``$name`` references in formulas/constraints."""
    out = []
    for p in periods:
        for c in base:
            c2 = copy.deepcopy(c)
            c2["key"] = f"{p}_{c['key']}"
            c2["period"] = p
            if "formula" in c2:
                c2["formula"] = c2["formula"].replace("$", f"{p}_")
            if "lte" in c2:
                c2["lte"] = c2["lte"].replace("$", f"{p}_")
            out.append(c2)
    return out


def fy_header(per_period: int, periods: tuple[str, ...] = ("cy", "py"), lead: str = "") -> list[list]:
    return [[(lead, 1)] + [(PERIOD_LABEL[p], per_period) for p in periods]]


def table(qid: str, number: str, prompt: str, rows: list[dict], columns: list[dict], *,
          headers: list[list] | None = None, row_header: str = "", required: bool = True,
          guidance: str | None = None, unit: str | None = None, helper: bool = False,
          min_one_row: bool = False) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "table",
         "rows": rows, "columns": columns, "row_header": row_header, "required": required}
    if headers:
        q["headers"] = [[{"label": l, "span": s} for (l, s) in h] for h in headers]
    if guidance:
        q["guidance"] = guidance
    if unit:
        q["unit"] = unit
    if helper:
        q["helper"] = True
    return q


def rows_q(qid: str, number: str, prompt: str, columns: list[dict], *, required: bool = True,
           min_rows: int = 1, max_rows: int | None = None, guidance: str | None = None,
           checks: list[str] | None = None) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "rows", "columns": columns,
         "required": required, "min_rows": min_rows}
    if max_rows:
        q["max_rows"] = max_rows
    if guidance:
        q["guidance"] = guidance
    if checks:
        q["checks"] = checks
    return q


def text_q(qid: str, number: str, prompt: str, *, required: bool = True, guidance: str | None = None,
           max_words: int | None = None) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "text", "required": required}
    if guidance:
        q["guidance"] = guidance
    if max_words:
        q["max_words"] = max_words
    return q


def yesno_q(qid: str, number: str, prompt: str, *, details: str | None = "Details",
            details_required_when: str | None = "Yes", required: bool = True,
            link: bool = False, guidance: str | None = None) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "yesno", "required": required,
         "details_label": details, "details_required_when": details_required_when if details else None,
         "link": link}
    if guidance:
        q["guidance"] = guidance
    return q


def number_q(qid: str, number: str, prompt: str, kind: str = "percent", *, required: bool = True,
             unit: str | None = None, guidance: str | None = None) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "number", "kind": kind, "required": required}
    if unit:
        q["unit"] = unit
    if guidance:
        q["guidance"] = guidance
    return q


def fields_q(qid: str, number: str, prompt: str, fields: list[dict], *, required: bool = True,
             numbered: bool = False, guidance: str | None = None) -> dict:
    q = {"id": qid, "number": number, "prompt": prompt, "type": "fields", "fields": fields,
         "required": required, "numbered": numbered}
    if guidance:
        q["guidance"] = guidance
    return q


def field(key: str, label: str, kind: str = "text", *, number: str | None = None, required: bool = True,
          options: list[str] | None = None, pattern: str | None = None, pattern_hint: str | None = None,
          readonly: bool = False, minv: float | None = None, maxv: float | None = None) -> dict:
    f: dict[str, Any] = {"key": key, "label": label, "kind": kind, "required": required}
    for k, v in (("number", number), ("options", options), ("pattern", pattern), ("pattern_hint", pattern_hint),
                 ("min", minv), ("max", maxv)):
        if v is not None:
            f[k] = v
    if readonly:
        f["readonly"] = True
    return f


PRINCIPLES = [f"P{i}" for i in range(1, 10)]


def grid_q(qid: str, number: str, prompt: str, grid_rows: list[dict], *, required: bool = True,
           guidance: str | None = None) -> dict:
    """Principle grid: each row is answered once per principle P1..P9.
    A row may set ``span: True`` to take a single answer covering all nine
    principles (SEBI allows one consolidated response for e.g. web links)."""
    q = {"id": qid, "number": number, "prompt": prompt, "type": "principle_grid",
         "grid_rows": grid_rows, "required": required}
    if guidance:
        q["guidance"] = guidance
    return q


def grow(key: str, label: str, kind: str = "yesno", *, options: list[str] | None = None,
         span_allowed: bool = False) -> dict:
    r: dict[str, Any] = {"key": key, "label": label, "kind": kind}
    if options:
        r["options"] = options
    if span_allowed:
        r["span_allowed"] = True
    return r


# ---------------------------------------------------------------------------
# Reusable table shapes
# ---------------------------------------------------------------------------

def gender_split_cols() -> list[dict]:
    return [
        col("total", "Total (A)"),
        col("male", "No. (B)", lte="total"),
        col("male_pct", "% (B / A)", "percent", formula="pct(male, total)"),
        col("female", "No. (C)", lte="total"),
        col("female_pct", "% (C / A)", "percent", formula="pct(female, total)"),
    ]


GENDER_SPLIT_HEADERS = [[("", 1), ("", 1), ("Male", 2), ("Female", 2)]]


def headcount_rows(kind: str) -> list[dict]:
    """Employees and workers, permanent / other-than-permanent / total."""
    noun = "employees" if kind == "employees" else "workers"
    d, e = ("D", "E") if kind == "employees" else ("F", "G")
    return [
        row(f"{kind}_perm", f"Permanent ({d})"),
        row(f"{kind}_other", f"Other than Permanent ({e})"),
        row(f"{kind}_total", f"Total {noun} ({d} + {e})", formula=f"{kind}_perm + {kind}_other"),
    ]


def mf_rows(prefix: str, title: str) -> list[dict]:
    return [
        row(f"{prefix}_hdr", title, header=True),
        row(f"{prefix}_male", "Male"),
        row(f"{prefix}_female", "Female"),
        row(f"{prefix}_total", "Total", formula=f"{prefix}_male + {prefix}_female"),
    ]


def covered_cols(letter_total: str, letter_cov: str) -> list[dict]:
    return [
        col("total", f"Total ({letter_total})"),
        col("covered", f"No. ({letter_cov})", lte="$total"),
        col("pct", f"% ({letter_cov} / {letter_total})", "percent", formula=f"pct($covered, $total)"),
    ]


def complaints_cols() -> list[dict]:
    return fy_cols([
        col("filed", "Filed during the year"),
        col("pending", "Pending resolution at the end of year"),
        col("remarks", "Remarks", "text"),
    ])


def intensity_rows(total_row: str, unit: str) -> list[dict]:
    """The four intensity lines SEBI asks for under energy, water, GHG and
    waste — all computed from P6.DEN so they are mutually consistent."""
    return [
        row("int_rupee", f"{unit} intensity per rupee of turnover (Total / Revenue from operations)",
            formula=f"div({total_row}, ref('P6.DEN', 'revenue'))", display="intensity"),
        row("int_ppp", f"{unit} intensity per rupee of turnover adjusted for Purchasing Power Parity (PPP)",
            formula=f"div({total_row} * ref('P6.DEN', 'ppp_factor'), ref('P6.DEN', 'revenue'))", display="intensity"),
        row("int_physical", f"{unit} intensity in terms of physical output",
            formula=f"div({total_row}, ref('P6.DEN', 'physical_output'))", display="intensity"),
        row("int_optional", f"{unit} intensity (optional) – the relevant metric may be selected by the entity"),
    ]


ASSURANCE_GUIDANCE = ("SEBI also asks whether an independent assessment/evaluation/assurance of this "
                      "disclosure was carried out by an external agency. Record it in the note field "
                      "below the table (agency name, or 'No').")

# ---------------------------------------------------------------------------
# Sections
# ---------------------------------------------------------------------------

SECTIONS: list[dict] = [
    {"code": "A", "title": "Section A: General Disclosures"},
    {"code": "B", "title": "Section B: Management and Process Disclosures",
     "intro": "This section is aimed at helping businesses demonstrate the structures, policies and "
              "processes put in place towards adopting the NGRBC Principles and Core Elements."},
    {"code": "P1", "title": "Principle 1",
     "statement": "Businesses should conduct and govern themselves with integrity, and in a manner that is "
                  "Ethical, Transparent and Accountable."},
    {"code": "P2", "title": "Principle 2",
     "statement": "Businesses should provide goods and services in a manner that is sustainable and safe."},
    {"code": "P3", "title": "Principle 3",
     "statement": "Businesses should respect and promote the well-being of all employees, including those "
                  "in their value chains."},
    {"code": "P4", "title": "Principle 4",
     "statement": "Businesses should respect the interests of and be responsive to all its stakeholders."},
    {"code": "P5", "title": "Principle 5",
     "statement": "Businesses should respect and promote human rights."},
    {"code": "P6", "title": "Principle 6",
     "statement": "Businesses should respect and make efforts to protect and restore the environment."},
    {"code": "P7", "title": "Principle 7",
     "statement": "Businesses, when engaging in influencing public and regulatory policy, should do so in a "
                  "manner that is responsible and transparent."},
    {"code": "P8", "title": "Principle 8",
     "statement": "Businesses should promote inclusive growth and equitable development."},
    {"code": "P9", "title": "Principle 9",
     "statement": "Businesses should engage with and provide value to their consumers in a responsible manner."},
]

SECTION_CODES = [s["code"] for s in SECTIONS]

# ---------------------------------------------------------------------------
# SECTION A
# ---------------------------------------------------------------------------

CIN_PATTERN = r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$"

SECTION_A: list[dict] = [
    {"group": "I. Details of the listed entity", **fields_q("A.I", "1–15", "Details of the listed entity", [
        field("cin", "Corporate Identity Number (CIN) of the Listed Entity", number="1",
              pattern=CIN_PATTERN, pattern_hint="21 characters, e.g. L67110MH1993PLC344643"),
        field("name", "Name of the Listed Entity", number="2"),
        field("year_incorporated", "Year of incorporation", "count", number="3", minv=1800, maxv=2100),
        field("registered_office", "Registered office address", "longtext", number="4"),
        field("corporate_address", "Corporate address", "longtext", number="5"),
        field("email", "E-mail", "email", number="6"),
        field("telephone", "Telephone", number="7"),
        field("website", "Website", "url", number="8"),
        field("financial_year", "Financial year for which reporting is being done", number="9", readonly=True),
        field("stock_exchanges", "Name of the Stock Exchange(s) where shares are listed", number="10"),
        field("paid_up_capital", "Paid-up Capital (INR)", "amount", number="11"),
        field("contact_name", "Name of the person who may be contacted in case of any queries on the BRSR report",
              number="12"),
        field("contact_designation", "Designation of the contact person", number="12"),
        field("contact_phone", "Telephone of the contact person", number="12"),
        field("contact_email", "E-mail of the contact person", "email", number="12"),
        field("reporting_boundary", "Reporting boundary — are the disclosures made on a standalone or a "
              "consolidated basis", "choice", number="13", options=["Standalone", "Consolidated"]),
        field("assurance_provider", "Name of assurance provider", number="14", required=False),
        field("assurance_type", "Type of assurance obtained", "choice", number="15",
              options=["Reasonable assurance", "Limited assurance", "Not obtained"]),
    ], numbered=True)},

    {"group": "II. Products/services", **rows_q("A.16", "16",
        "Details of business activities (accounting for 90% of the turnover)", [
            col("main_activity", "Description of Main Activity", "text"),
            col("business_activity", "Description of Business Activity", "longtext"),
            col("pct_turnover", "% of Turnover of the entity", "percent"),
        ], checks=["sum_pct_turnover"],
        guidance="List activities until together they account for at least 90% of turnover.")},
    {"group": "II. Products/services", **rows_q("A.17", "17",
        "Products/Services sold by the entity (accounting for 90% of the entity's Turnover)", [
            col("product", "Product/Service", "text"),
            col("nic_code", "NIC Code", "text"),
            col("pct_turnover", "% of total Turnover contributed", "percent"),
        ], checks=["sum_pct_turnover", "nic_code"])},

    {"group": "III. Operations", **table("A.18", "18",
        "Number of locations where plants and/or operations/offices of the entity are situated",
        [row("national", "National"), row("international", "International")],
        [col("plants", "Number of plants"), col("offices", "Number of offices"),
         col("total", "Total", formula="plants + offices")], row_header="Location")},
    {"group": "III. Operations", **table("A.19a", "19(a)", "Markets served by the entity — number of locations",
        [row("national", "National (No. of States)"), row("international", "International (No. of Countries)")],
        [col("number", "Number")], row_header="Locations")},
    {"group": "III. Operations", **number_q("A.19b", "19(b)",
        "What is the contribution of exports as a percentage of the total turnover of the entity?", "percent")},
    {"group": "III. Operations", **text_q("A.19c", "19(c)", "A brief on types of customers")},

    {"group": "IV. Employees", **table("A.20a", "20(a)",
        "Details as at the end of Financial Year — employees and workers (including differently abled)",
        [row("emp_hdr", "EMPLOYEES", header=True)] + headcount_rows("employees")
        + [row("wrk_hdr", "WORKERS", header=True)] + headcount_rows("workers"),
        gender_split_cols(), headers=GENDER_SPLIT_HEADERS, row_header="Particulars")},
    {"group": "IV. Employees", **table("A.20b", "20(b)", "Differently abled employees and workers",
        [row("emp_hdr", "DIFFERENTLY ABLED EMPLOYEES", header=True)] + headcount_rows("employees")
        + [row("wrk_hdr", "DIFFERENTLY ABLED WORKERS", header=True)] + headcount_rows("workers"),
        gender_split_cols(), headers=GENDER_SPLIT_HEADERS, row_header="Particulars")},
    {"group": "IV. Employees", **table("A.21", "21", "Participation/Inclusion/Representation of women",
        [row("bod", "Board of Directors"), row("kmp", "Key Management Personnel")],
        [col("total", "Total (A)"), col("female", "No. (B)", lte="total"),
         col("female_pct", "% (B / A)", "percent", formula="pct(female, total)")],
        headers=[[("", 1), ("", 1), ("No. and percentage of Females", 2)]])},
    {"group": "IV. Employees", **table("A.22", "22", "Turnover rate for permanent employees and workers",
        [row("perm_employees", "Permanent Employees"), row("perm_workers", "Permanent Workers")],
        fy_cols([col("male", "Male", "rate"), col("female", "Female", "rate"), col("total", "Total", "rate")],
                ("cy", "py", "ppy")),
        headers=fy_header(3, ("cy", "py", "ppy")),
        guidance="Turnover rate = (people who left during the year / average headcount) × 100, per SEBI's "
                 "BRSR guidance note.")},

    {"group": "V. Holding, Subsidiary and Associate Companies (including joint ventures)", **rows_q("A.23", "23",
        "Names of holding / subsidiary / associate companies / joint ventures", [
            col("name", "Name of the holding / subsidiary / associate companies / joint ventures (A)", "text"),
            col("relationship", "Indicate whether holding/ Subsidiary/ Associate/ Joint Venture", "choice",
                options=["Holding", "Subsidiary", "Wholly Owned Subsidiary", "Associate", "Joint Venture"]),
            col("pct_shares", "% of shares held by listed entity", "percent"),
            col("participates", "Does the entity indicated at column A, participate in the Business "
                "Responsibility initiatives of the listed entity? (Yes/No)", "yesno"),
        ], min_rows=0)},

    {"group": "VI. CSR Details", **fields_q("A.24", "24", "CSR Details", [
        field("csr_applicable", "Whether CSR is applicable as per section 135 of Companies Act, 2013", "yesno",
              number="(a)"),
        field("turnover", "Turnover (in Rs.)", "amount", number="(b)"),
        field("net_worth", "Net worth (in Rs.)", "amount", number="(c)"),
    ])},

    {"group": "VII. Transparency and Disclosures Compliances", **table("A.25", "25",
        "Complaints/Grievances on any of the principles (Principles 1 to 9) under the National Guidelines on "
        "Responsible Business Conduct",
        [row("communities", "Communities"), row("investors", "Investors (other than shareholders)"),
         row("shareholders", "Shareholders"), row("employees", "Employees and workers"),
         row("customers", "Customers"), row("value_chain", "Value Chain Partners"),
         row("other", "Other (please specify)")],
        [col("mechanism", "Grievance Redressal Mechanism in Place (Yes/No)", "yesno"),
         col("policy_link", "Web-link for grievance redress policy", "text")] + complaints_cols(),
        headers=[[("", 1), ("", 2), (PERIOD_LABEL["cy"], 3), (PERIOD_LABEL["py"], 3)]],
        row_header="Stakeholder group from whom complaint is received")},
    {"group": "VII. Transparency and Disclosures Compliances", **rows_q("A.26", "26",
        "Overview of the entity's material responsible business conduct issues — material responsible business "
        "conduct and sustainability issues pertaining to environmental and social matters that present a risk or "
        "an opportunity to your business, rationale for identifying the same, approach to adapt or mitigate the "
        "risk along-with its financial implications", [
            col("issue", "Material issue identified", "text"),
            col("risk_or_opportunity", "Indicate whether risk or opportunity (R/O)", "choice",
                options=["Risk", "Opportunity", "Risk and Opportunity"]),
            col("rationale", "Rationale for identifying the risk / opportunity", "longtext"),
            col("mitigation", "In case of risk, approach to adapt or mitigate", "longtext"),
            col("implication_sign", "Financial implication (positive or negative)", "choice",
                options=["Positive", "Negative", "Positive and Negative"]),
            col("implication", "Financial implications of the risk or opportunity", "longtext"),
        ])},
]

# ---------------------------------------------------------------------------
# SECTION B
# ---------------------------------------------------------------------------

REVIEW_BY = ["Director", "Committee of the Board", "Any other Committee", "Not undertaken"]
REVIEW_FREQ = ["Annually", "Half yearly", "Quarterly", "Any other", "Not applicable"]

SECTION_B: list[dict] = [
    {"group": "Policy and management processes", **grid_q("B.1", "1", "Policy coverage", [
        grow("covers", "(a) Whether your entity's policy/policies cover each principle and its core elements "
             "of the NGRBCs. (Yes/No)"),
        grow("board_approved", "(b) Has the policy been approved by the Board? (Yes/No)"),
        grow("web_link", "(c) Web Link of the Policies, if available", "longtext", span_allowed=True),
    ])},
    {"group": "Policy and management processes", **grid_q("B.2", "2",
        "Whether the entity has translated the policy into procedures. (Yes / No)",
        [grow("translated", "Translated into procedures")])},
    {"group": "Policy and management processes", **grid_q("B.3", "3",
        "Do the enlisted policies extend to your value chain partners? (Yes/No)",
        [grow("value_chain", "Extends to value chain partners")])},
    {"group": "Policy and management processes", **grid_q("B.4", "4",
        "Name of the national and international codes/certifications/labels/standards (e.g. Forest Stewardship "
        "Council, Fairtrade, Rainforest Alliance, Trustea) standards (e.g. SA 8000, OHSAS, ISO, BIS) adopted by "
        "your entity and mapped to each principle.",
        [grow("codes", "Codes / certifications / standards", "longtext", span_allowed=True)])},
    {"group": "Policy and management processes", **grid_q("B.5", "5",
        "Specific commitments, goals and targets set by the entity with defined timelines, if any.",
        [grow("commitments", "Commitments, goals and targets", "longtext", span_allowed=True)])},
    {"group": "Policy and management processes", **grid_q("B.6", "6",
        "Performance of the entity against the specific commitments, goals and targets along-with reasons in "
        "case the same are not met.",
        [grow("performance", "Performance against commitments", "longtext", span_allowed=True)])},

    {"group": "Governance, leadership and oversight", **text_q("B.7", "7",
        "Statement by director responsible for the business responsibility report, highlighting ESG related "
        "challenges, targets and achievements (listed entity has flexibility regarding the placement of this "
        "disclosure)")},
    {"group": "Governance, leadership and oversight", **fields_q("B.8", "8",
        "Details of the highest authority responsible for implementation and oversight of the Business "
        "Responsibility policy (ies).", [
            field("name", "Name"), field("designation", "Designation"),
            field("din", "DIN (if a director)", required=False, pattern=r"^[0-9]{8}$",
                  pattern_hint="8 digits"),
        ])},
    {"group": "Governance, leadership and oversight", **yesno_q("B.9", "9",
        "Does the entity have a specified Committee of the Board/ Director responsible for decision making on "
        "sustainability related issues? (Yes / No). If yes, provide details.")},
    {"group": "Governance, leadership and oversight", **grid_q("B.10", "10",
        "Details of Review of NGRBCs by the Company", [
            grow("perf_by", "Performance against above policies and follow up action — review undertaken by",
                 "choice", options=REVIEW_BY),
            grow("perf_freq", "Performance against above policies and follow up action — frequency",
                 "choice", options=REVIEW_FREQ),
            grow("comp_by", "Compliance with statutory requirements of relevance to the principles, and "
                 "rectification of any non-compliances — review undertaken by", "choice", options=REVIEW_BY),
            grow("comp_freq", "Compliance with statutory requirements of relevance to the principles, and "
                 "rectification of any non-compliances — frequency", "choice", options=REVIEW_FREQ),
            grow("details", "Additional details (optional)", "longtext", span_allowed=True),
        ])},
    {"group": "Governance, leadership and oversight", **grid_q("B.11", "11",
        "Has the entity carried out independent assessment/ evaluation of the working of its policies by an "
        "external agency? (Yes/No). If yes, provide name of the agency.", [
            grow("assessed", "Independent assessment carried out"),
            grow("agency", "Name of the agency", "text", span_allowed=True),
        ])},
    {"group": "Governance, leadership and oversight", **grid_q("B.12", "12",
        "If answer to question (1) above is \"No\" i.e. not all Principles are covered by a policy, reasons to "
        "be stated", [
            grow("not_material", "The entity does not consider the Principles material to its business (Yes/No)"),
            grow("not_ready", "The entity is not at a stage where it is in a position to formulate and implement "
                 "the policies on specified principles (Yes/No)"),
            grow("no_resources", "The entity does not have the financial or/human and technical resources "
                 "available for the task (Yes/No)"),
            grow("next_year", "It is planned to be done in the next financial year (Yes/No)"),
            grow("other", "Any other reason (please specify)", "text", span_allowed=True),
        ], required=False,
        guidance="Required only for principles answered 'No' in question 1(a).")},
]

# ---------------------------------------------------------------------------
# SECTION C — Principle-wise performance
# ---------------------------------------------------------------------------

E, L = "Essential Indicators", "Leadership Indicators"


def _ess(items: list[dict]) -> list[dict]:
    return [{"group": E, "indicator": "essential", **q} for q in items]


def _lead(items: list[dict]) -> list[dict]:
    return [{"group": L, "indicator": "leadership", **{**q, "required": False}} for q in items]


CY_PY = fy_cols([col("value", "", "number")])
CY_PY_HEADERS = None  # plain two-column FY tables use the period labels directly


def cy_py(kind: str = "number") -> list[dict]:
    return [dict(c, kind=kind, label=PERIOD_LABEL[c["period"]]) for c in fy_cols([col("v", "", kind)])]


# --- Principle 1 ------------------------------------------------------------
P1 = _ess([
    table("P1.E1", "1", "Percentage coverage by training and awareness programmes on any of the Principles during "
          "the financial year",
          [row("bod", "Board of Directors"), row("kmp", "Key Managerial Personnel"),
           row("employees", "Employees other than BoD and KMPs"), row("workers", "Workers")],
          [col("programmes", "Total number of training and awareness programmes held"),
           col("topics", "Topics / principles covered under the training and its impact", "longtext"),
           col("pct_covered", "%age of persons in respective category covered by the awareness programmes",
               "percent")], row_header="Segment"),
    table("P1.E2a", "2", "Details of fines / penalties / punishment / award / compounding fees / settlement amount "
          "paid in proceedings (by the entity or by directors / KMPs) with regulators / law enforcement agencies / "
          "judicial institutions, in the financial year — Monetary",
          [row("penalty", "Penalty/ Fine"), row("settlement", "Settlement"), row("compounding", "Compounding fee")],
          [col("principle", "NGRBC Principle", "text"),
           col("agency", "Name of the regulatory/ enforcement agencies/ judicial institutions", "text"),
           col("amount", "Amount (In INR)", "amount"), col("brief", "Brief of the Case", "longtext"),
           col("appeal", "Has an appeal been preferred? (Yes/No)", "yesno")],
          guidance="Disclose on the basis of materiality as specified in Regulation 30 of SEBI (LODR) Regulations, "
                   "2015. Leave a row blank if there were no such proceedings."),
    table("P1.E2b", "2", "Non-Monetary",
          [row("imprisonment", "Imprisonment"), row("punishment", "Punishment")],
          [col("principle", "NGRBC Principle", "text"),
           col("agency", "Name of the regulatory/ enforcement agencies/ judicial institutions", "text"),
           col("brief", "Brief of the Case", "longtext"),
           col("appeal", "Has an appeal been preferred? (Yes/No)", "yesno")]),
    rows_q("P1.E3", "3", "Of the instances disclosed in Question 2 above, details of the Appeal/ Revision preferred "
           "in cases where monetary or non-monetary action has been appealed.",
           [col("case", "Case Details", "longtext"),
            col("agency", "Name of the regulatory/ enforcement agencies/ judicial institutions", "text")],
           min_rows=0),
    yesno_q("P1.E4", "4", "Does the entity have an anti-corruption or anti-bribery policy? If yes, provide details "
            "in brief and if available, provide a web-link to the policy.", link=True),
    table("P1.E5", "5", "Number of Directors/KMPs/employees/workers against whom disciplinary action was taken by "
          "any law enforcement agency for the charges of bribery/ corruption",
          [row("directors", "Directors"), row("kmps", "KMPs"), row("employees", "Employees"),
           row("workers", "Workers")], cy_py("count")),
    table("P1.E6", "6", "Details of complaints with regard to conflict of interest",
          [row("directors", "Number of complaints received in relation to issues of Conflict of Interest of the "
               "Directors"),
           row("kmps", "Number of complaints received in relation to issues of Conflict of Interest of the KMPs")],
          fy_cols([col("number", "Number"), col("remarks", "Remarks", "text")]), headers=fy_header(2)),
    text_q("P1.E7", "7", "Provide details of any corrective action taken or underway on issues related to fines / "
           "penalties / action taken by regulators/ law enforcement agencies/ judicial institutions, on cases of "
           "corruption and conflicts of interest."),
    table("P1.E8", "8", "Number of days of accounts payables ((Accounts payable × 365) / Cost of goods/services "
          "procured)",
          [row("payables", "Accounts payable (INR)"),
           row("cogs", "Cost of goods/services procured (INR)"),
           row("days", "Number of days of accounts payables", formula="div(payables * 365, cogs)",
               display="number")], cy_py("amount")),
    table("P1.E9", "9", "Open-ness of business: concentration of purchases and sales with trading houses, dealers, "
          "and related parties along-with loans and advances & investments, with related parties",
          [row("p_hdr", "Concentration of Purchases", header=True),
           row("p_trading_pct", "a. Purchases from trading houses as % of total purchases"),
           row("p_trading_count", "b. Number of trading houses where purchases are made from"),
           row("p_top10_pct", "c. Purchases from top 10 trading houses as % of total purchases from trading houses"),
           row("s_hdr", "Concentration of Sales", header=True),
           row("s_dealer_pct", "a. Sales to dealers / distributors as % of total sales"),
           row("s_dealer_count", "b. Number of dealers / distributors to whom sales are made"),
           row("s_top10_pct", "c. Sales to top 10 dealers / distributors as % of total sales to dealers / distributors"),
           row("r_hdr", "Share of RPTs in", header=True),
           row("r_purchases", "a. Purchases (Purchases with related parties / Total Purchases)"),
           row("r_sales", "b. Sales (Sales to related parties / Total Sales)"),
           row("r_loans", "c. Loans & advances (Loans & advances given to related parties / Total loans & advances)"),
           row("r_investments", "d. Investments (Investments in related parties / Total Investments made)")],
          cy_py("rate"), row_header="Parameter / Metrics",
          guidance="Enter percentages (0–100) for the % rows and plain counts for rows (b)."),
]) + _lead([
    rows_q("P1.L1", "1", "Awareness programmes conducted for value chain partners on any of the Principles during "
           "the financial year", [
               col("programmes", "Total number of awareness programmes held"),
               col("topics", "Topics / principles covered under the training", "longtext"),
               col("pct_covered", "%age of value chain partners covered (by value of business done with such "
                   "partners) under the awareness programmes", "percent")], min_rows=0),
    yesno_q("P1.L2", "2", "Does the entity have processes in place to avoid/ manage conflict of interests "
            "involving members of the Board? (Yes/No) If Yes, provide details of the same."),
])

# --- Principle 2 ------------------------------------------------------------
P2 = _ess([
    table("P2.E1", "1", "Percentage of R&D and capital expenditure (capex) investments in specific technologies to "
          "improve the environmental and social impacts of product and processes to total R&D and capex "
          "investments made by the entity, respectively.",
          [row("rnd", "R&D"), row("capex", "Capex")],
          [col("cy", "Current Financial Year (%)", "percent"), col("py", "Previous Financial Year (%)", "percent"),
           col("details", "Details of improvements in environmental and social impacts", "longtext")]),
    yesno_q("P2.E2a", "2(a)", "Does the entity have procedures in place for sustainable sourcing? (Yes/No)",
            details=None),
    number_q("P2.E2b", "2(b)", "If yes, what percentage of inputs were sourced sustainably?", "percent",
             required=False),
    fields_q("P2.E3", "3", "Describe the processes in place to safely reclaim your products for reusing, recycling "
             "and disposing at the end of life", [
                 field("plastics", "(a) Plastics (including packaging)", "longtext"),
                 field("ewaste", "(b) E-waste", "longtext"),
                 field("hazardous", "(c) Hazardous waste", "longtext"),
                 field("other", "(d) Other waste", "longtext")]),
    yesno_q("P2.E4", "4", "Whether Extended Producer Responsibility (EPR) is applicable to the entity's activities "
            "(Yes / No). If yes, whether the waste collection plan is in line with the Extended Producer "
            "Responsibility (EPR) plan submitted to Pollution Control Boards? If not, provide steps taken to "
            "address the same", details_required_when=None),
]) + _lead([
    rows_q("P2.L1", "1", "Has the entity conducted Life Cycle Perspective / Assessments (LCA) for any of its "
           "products (for manufacturing industry) or for its services (for service industry)? If yes, provide "
           "details", [
               col("nic", "NIC Code", "text"), col("product", "Name of Product /Service", "text"),
               col("pct_turnover", "% of total Turnover contributed", "percent"),
               col("boundary", "Boundary for which the Life Cycle Perspective / Assessment was conducted", "text"),
               col("independent", "Whether conducted by independent external agency (Yes/No)", "yesno"),
               col("public", "Results communicated in public domain (Yes/No) If yes, provide the web-link.",
                   "text")], min_rows=0),
    rows_q("P2.L2", "2", "If there are any significant social or environmental concerns and/or risks arising from "
           "production or disposal of your products / services, as identified in the Life Cycle Perspective / "
           "Assessments (LCA) or through any other means, briefly describe the same along-with action taken to "
           "mitigate the same.", [
               col("product", "Name of Product / Service", "text"),
               col("risk", "Description of the risk / concern", "longtext"),
               col("action", "Action Taken", "longtext")], min_rows=0),
    rows_q("P2.L3", "3", "Percentage of recycled or reused input material to total material (by value) used in "
           "production (for manufacturing industry) or providing services (for service industry).", [
               col("material", "Indicate input material", "text"),
               col("cy", "Current FY (%)", "percent"), col("py", "Previous FY (%)", "percent")], min_rows=0),
    table("P2.L4", "4", "Of the products and packaging reclaimed at end of life of products, amount (in metric "
          "tonnes) reused, recycled, and safely disposed",
          [row("plastics", "Plastics (including packaging)"), row("ewaste", "E-waste"),
           row("hazardous", "Hazardous waste"), row("other", "Other waste")],
          fy_cols([col("reused", "Re-Used", "number"), col("recycled", "Recycled", "number"),
                   col("disposed", "Safely Disposed", "number")]),
          headers=fy_header(3), unit="metric tonnes"),
    rows_q("P2.L5", "5", "Reclaimed products and their packaging materials (as percentage of products sold) for "
           "each product category.", [
               col("category", "Indicate product category", "text"),
               col("pct", "Reclaimed products and their packaging materials as % of total products sold in "
                   "respective category", "percent")], min_rows=0),
])

# --- Principle 3 ------------------------------------------------------------

def _wellbeing_cols() -> list[dict]:
    cols = [col("total", "Total (A)")]
    for key, letter in (("health", "B"), ("accident", "C"), ("maternity", "D"), ("paternity", "E"),
                        ("daycare", "F")):
        cols.append(col(key, f"Number ({letter})", lte="total"))
        cols.append(col(f"{key}_pct", f"% ({letter} / A)", "percent", formula=f"pct({key}, total)"))
    return cols


WELLBEING_HEADERS = [[("", 1), ("", 1), ("Health insurance", 2), ("Accident insurance", 2),
                      ("Maternity benefits", 2), ("Paternity Benefits", 2), ("Day Care facilities", 2)]]


def _parental_cols() -> list[dict]:
    out = []
    for grp, title in (("emp", "Permanent employees"), ("wrk", "Permanent workers")):
        out += [
            col(f"{grp}_took", f"{title}: took parental leave"),
            col(f"{grp}_returned", f"{title}: returned to work after leave", lte=f"{grp}_took"),
            col(f"{grp}_retained", f"{title}: still employed 12 months after return", lte=f"{grp}_returned"),
            col(f"{grp}_rtw", "Return to work rate", "percent", formula=f"pct({grp}_returned, {grp}_took)"),
            col(f"{grp}_ret", "Retention rate", "percent", formula=f"pct({grp}_retained, {grp}_returned)"),
        ]
    return out


def _training_cols() -> list[dict]:
    return fy_cols([
        col("total", "Total"),
        col("hs", "Health & safety: No.", lte="$total"),
        col("hs_pct", "Health & safety: %", "percent", formula="pct($hs, $total)"),
        col("skill", "Skill upgradation: No.", lte="$total"),
        col("skill_pct", "Skill upgradation: %", "percent", formula="pct($skill, $total)"),
    ])


P3 = _ess([
    table("P3.E1a", "1(a)", "Details of measures for the well-being of employees",
          mf_rows("perm", "Permanent employees") + mf_rows("other", "Other than Permanent employees"),
          _wellbeing_cols(), headers=WELLBEING_HEADERS, row_header="Category"),
    table("P3.E1b", "1(b)", "Details of measures for the well-being of workers",
          mf_rows("perm", "Permanent workers") + mf_rows("other", "Other than Permanent workers"),
          _wellbeing_cols(), headers=WELLBEING_HEADERS, row_header="Category"),
    table("P3.E1c", "1(c)", "Spending on measures towards well-being of employees and workers (including permanent "
          "and other than permanent)",
          [row("cost", "Cost incurred on well-being measures (INR)"),
           row("pct_revenue", "Cost incurred on well-being measures as a % of total revenue of the company",
               formula="pct(cost, ref('P6.DEN', 'revenue'))", display="percent")],
          cy_py("amount"), guidance="The percentage uses the revenue entered once in Principle 6 → "
                                    "'Intensity denominators'."),
    table("P3.E2", "2", "Details of retirement benefits, for Current FY and Previous Financial Year",
          [row("pf", "PF"), row("gratuity", "Gratuity"), row("esi", "ESI"),
           row("others", "Others – please specify")],
          fy_cols([col("emp_pct", "No. of employees covered as a % of total employees", "percent"),
                   col("wrk_pct", "No. of workers covered as a % of total workers", "percent"),
                   col("deposited", "Deducted and deposited with the authority (Y/N/N.A.)", "yesno")]),
          headers=fy_header(3), row_header="Benefits"),
    text_q("P3.E3", "3", "Accessibility of workplaces: Are the premises / offices of the entity accessible to "
           "differently abled employees and workers, as per the requirements of the Rights of Persons with "
           "Disabilities Act, 2016? If not, whether any steps are being taken by the entity in this regard."),
    yesno_q("P3.E4", "4", "Does the entity have an equal opportunity policy as per the Rights of Persons with "
            "Disabilities Act, 2016? If so, provide a web-link to the policy.", link=True),
    table("P3.E5", "5", "Return to work and Retention rates of permanent employees and workers that took parental "
          "leave.",
          [row("male", "Male"), row("female", "Female"), row("total", "Total", formula="male + female")],
          _parental_cols(), row_header="Gender",
          guidance="Enter how many took leave, returned and stayed 12 months — the rates are calculated."),
    table("P3.E6", "6", "Is there a mechanism available to receive and redress grievances for the following "
          "categories of employees and worker? If yes, give details of the mechanism in brief.",
          [row("perm_workers", "Permanent Workers"), row("other_workers", "Other than Permanent Workers"),
           row("perm_employees", "Permanent Employees"), row("other_employees", "Other than Permanent Employees")],
          [col("available", "Yes/No", "yesno"), col("details", "Details of the mechanism in brief", "longtext")]),
    table("P3.E7", "7", "Membership of employees and worker in association(s) or Unions recognised by the listed "
          "entity",
          [row("emp_total", "Total Permanent Employees", formula="emp_male + emp_female"),
           row("emp_male", "Male"), row("emp_female", "Female"),
           row("wrk_total", "Total Permanent Workers", formula="wrk_male + wrk_female"),
           row("wrk_male", "Male"), row("wrk_female", "Female")],
          fy_cols([col("total", "Total employees / workers in respective category"),
                   col("members", "No. who are part of association(s) or Union", lte="$total"),
                   col("pct", "%", "percent", formula="pct($members, $total)")]),
          headers=fy_header(3), row_header="Category"),
    table("P3.E8", "8", "Details of training given to employees and workers",
          mf_rows("emp", "Employees") + mf_rows("wrk", "Workers"), _training_cols(),
          headers=fy_header(5), row_header="Category"),
    table("P3.E9", "9", "Details of performance and career development reviews of employees and worker",
          mf_rows("emp", "Employees") + mf_rows("wrk", "Workers"),
          fy_cols([col("total", "Total"), col("reviewed", "No. reviewed", lte="$total"),
                   col("pct", "%", "percent", formula="pct($reviewed, $total)")]),
          headers=fy_header(3), row_header="Category"),
    yesno_q("P3.E10a", "10(a)", "Whether an occupational health and safety management system has been implemented "
            "by the entity? (Yes/ No). If yes, the coverage such system?"),
    text_q("P3.E10b", "10(b)", "What are the processes used to identify work-related hazards and assess risks on a "
           "routine and non-routine basis by the entity?"),
    yesno_q("P3.E10c", "10(c)", "Whether you have processes for workers to report the work related hazards and to "
            "remove themselves from such risks. (Y/N)"),
    yesno_q("P3.E10d", "10(d)", "Do the employees/ worker of the entity have access to non-occupational medical and "
            "healthcare services? (Yes/ No)"),
    table("P3.E11", "11", "Details of safety related incidents",
          [row("lti_emp", "Lost time injuries — Employees (number)"),
           row("lti_wrk", "Lost time injuries — Workers (number)"),
           row("hours_emp", "Person-hours worked — Employees"),
           row("hours_wrk", "Person-hours worked — Workers"),
           row("ltifr_emp", "Lost Time Injury Frequency Rate (LTIFR) (per one million-person hours worked) — "
               "Employees", formula="div(lti_emp * 1000000, hours_emp)", display="number"),
           row("ltifr_wrk", "Lost Time Injury Frequency Rate (LTIFR) (per one million-person hours worked) — "
               "Workers", formula="div(lti_wrk * 1000000, hours_wrk)", display="number"),
           row("recordable_emp", "Total recordable work-related injuries — Employees"),
           row("recordable_wrk", "Total recordable work-related injuries — Workers"),
           row("fatal_emp", "No. of fatalities — Employees"),
           row("fatal_wrk", "No. of fatalities — Workers"),
           row("highcons_emp", "High consequence work-related injury or ill-health (excluding fatalities) — "
               "Employees"),
           row("highcons_wrk", "High consequence work-related injury or ill-health (excluding fatalities) — "
               "Workers")],
          cy_py("number"), row_header="Safety Incident/Number",
          guidance="Enter lost-time injuries and person-hours; LTIFR is calculated."),
    text_q("P3.E12", "12", "Describe the measures taken by the entity to ensure a safe and healthy work place."),
    table("P3.E13", "13", "Number of Complaints on the following made by employees and workers",
          [row("working_conditions", "Working Conditions"), row("health_safety", "Health & Safety")],
          complaints_cols(), headers=fy_header(3)),
    table("P3.E14", "14", "Assessments for the year",
          [row("health_safety", "Health and safety practices"), row("working_conditions", "Working Conditions")],
          [col("pct", "% of your plants and offices that were assessed (by entity or statutory authorities or "
               "third parties)", "percent")]),
    text_q("P3.E15", "15", "Provide details of any corrective action taken or underway to address safety-related "
           "incidents (if any) and on significant risks / concerns arising from assessments of health & safety "
           "practices and working conditions."),
]) + _lead([
    fields_q("P3.L1", "1", "Does the entity extend any life insurance or any compensatory package in the event of "
             "death of (A) Employees (Y/N) (B) Workers (Y/N).", [
                 field("employees", "(A) Employees", "yesno"), field("workers", "(B) Workers", "yesno"),
                 field("details", "Details", "longtext", required=False)]),
    text_q("P3.L2", "2", "Provide the measures undertaken by the entity to ensure that statutory dues have been "
           "deducted and deposited by the value chain partners."),
    table("P3.L3", "3", "Provide the number of employees / workers having suffered high consequence work-related "
          "injury / ill-health / fatalities (as reported in Q11 of Essential Indicators above), who have been are "
          "rehabilitated and placed in suitable employment or whose family members have been placed in suitable "
          "employment",
          [row("employees", "Employees"), row("workers", "Workers")],
          [dict(col("cy_affected", "Total affected — " + PERIOD_LABEL["cy"]), period="cy"),
           dict(col("py_affected", "Total affected — " + PERIOD_LABEL["py"]), period="py"),
           dict(col("cy_placed", "Rehabilitated / placed — " + PERIOD_LABEL["cy"], lte="cy_affected"), period="cy"),
           dict(col("py_placed", "Rehabilitated / placed — " + PERIOD_LABEL["py"], lte="py_affected"), period="py")]),
    yesno_q("P3.L4", "4", "Does the entity provide transition assistance programs to facilitate continued "
            "employability and the management of career endings resulting from retirement or termination of "
            "employment? (Yes/ No)"),
    table("P3.L5", "5", "Details on assessment of value chain partners",
          [row("health_safety", "Health and safety practices"), row("working_conditions", "Working Conditions")],
          [col("pct", "% of value chain partners (by value of business done with such partners) that were "
               "assessed", "percent")]),
    text_q("P3.L6", "6", "Provide details of any corrective actions taken or underway to address significant risks "
           "/ concerns arising from assessments of health and safety practices and working conditions of value "
           "chain partners."),
])

# --- Principle 4 ------------------------------------------------------------
P4 = _ess([
    text_q("P4.E1", "1", "Describe the processes for identifying key stakeholder groups of the entity."),
    rows_q("P4.E2", "2", "List stakeholder groups identified as key for your entity and the frequency of engagement "
           "with each stakeholder group.", [
               col("group", "Stakeholder Group", "text"),
               col("vulnerable", "Whether identified as Vulnerable & Marginalized Group (Yes/No)", "yesno"),
               col("channels", "Channels of communication (Email, SMS, Newspaper, Pamphlets, Advertisement, "
                   "Community Meetings, Notice Board, Website), Other", "longtext"),
               col("frequency", "Frequency of engagement (Annually/ Half yearly/ Quarterly / others – please "
                   "specify)", "text"),
               col("purpose", "Purpose and scope of engagement including key topics and concerns raised during "
                   "such engagement", "longtext")]),
]) + _lead([
    text_q("P4.L1", "1", "Provide the processes for consultation between stakeholders and the Board on economic, "
           "environmental, and social topics or if consultation is delegated, how is feedback from such "
           "consultations provided to the Board."),
    yesno_q("P4.L2", "2", "Whether stakeholder consultation is used to support the identification and management of "
            "environmental, and social topics (Yes / No). If so, provide details of instances as to how the inputs "
            "received from stakeholders on these topics were incorporated into policies and activities of the "
            "entity."),
    text_q("P4.L3", "3", "Provide details of instances of engagement with, and actions taken to, address the "
           "concerns of vulnerable/ marginalized stakeholder groups."),
])

# --- Principle 5 ------------------------------------------------------------

def _min_wage_rows(prefix: str, title: str) -> list[dict]:
    return [
        row(f"{prefix}_hdr", title, header=True),
        row(f"{prefix}_perm", "Permanent", formula=f"{prefix}_perm_male + {prefix}_perm_female"),
        row(f"{prefix}_perm_male", "Male"), row(f"{prefix}_perm_female", "Female"),
        row(f"{prefix}_other", "Other than Permanent", formula=f"{prefix}_other_male + {prefix}_other_female"),
        row(f"{prefix}_other_male", "Male"), row(f"{prefix}_other_female", "Female"),
    ]


P5 = _ess([
    table("P5.E1", "1", "Employees and workers who have been provided training on human rights issues and "
          "policy(ies) of the entity",
          [row("emp_hdr", "Employees", header=True), row("emp_perm", "Permanent"),
           row("emp_other", "Other than permanent"),
           row("emp_total", "Total Employees", formula="emp_perm + emp_other"),
           row("wrk_hdr", "Workers", header=True), row("wrk_perm", "Permanent"),
           row("wrk_other", "Other than permanent"),
           row("wrk_total", "Total Workers", formula="wrk_perm + wrk_other")],
          fy_cols([col("total", "Total"), col("covered", "No. covered", lte="$total"),
                   col("pct", "%", "percent", formula="pct($covered, $total)")]),
          headers=fy_header(3), row_header="Category"),
    table("P5.E2", "2", "Details of minimum wages paid to employees and workers",
          _min_wage_rows("emp", "Employees") + _min_wage_rows("wrk", "Workers"),
          fy_cols([col("total", "Total"),
                   col("equal", "Equal to Minimum Wage: No.", lte="$total"),
                   col("equal_pct", "Equal to Minimum Wage: %", "percent", formula="pct($equal, $total)"),
                   col("more", "More than Minimum Wage: No.", lte="$total"),
                   col("more_pct", "More than Minimum Wage: %", "percent", formula="pct($more, $total)")]),
          headers=fy_header(5), row_header="Category"),
    table("P5.E3a", "3(a)", "Details of remuneration/salary/wages — median remuneration / wages",
          [row("bod", "Board of Directors (BoD)"), row("kmp", "Key Managerial Personnel"),
           row("employees", "Employees other than BoD and KMP"), row("workers", "Workers")],
          [col("male_n", "Number"), col("male_median", "Median remuneration/ salary/ wages", "amount"),
           col("female_n", "Number"), col("female_median", "Median remuneration/ salary/ wages", "amount")],
          headers=[[("", 1), ("Male", 2), ("Female", 2)]], unit="INR"),
    table("P5.E3b", "3(b)", "Gross wages paid to females as % of total wages paid by the entity",
          [row("female_wages", "Gross wages paid to females (INR)"),
           row("total_wages", "Total wages paid (INR)"),
           row("pct", "Gross wages paid to females as % of total wages",
               formula="pct(female_wages, total_wages)", display="percent")], cy_py("amount")),
    yesno_q("P5.E4", "4", "Do you have a focal point (Individual/ Committee) responsible for addressing human rights "
            "impacts or issues caused or contributed to by the business? (Yes/No)"),
    text_q("P5.E5", "5", "Describe the internal mechanisms in place to redress grievances related to human rights "
           "issues."),
    table("P5.E6", "6", "Number of Complaints on the following made by employees and workers",
          [row("sexual_harassment", "Sexual Harassment"), row("discrimination", "Discrimination at workplace"),
           row("child_labour", "Child Labour"), row("forced_labour", "Forced Labour/Involuntary Labour"),
           row("wages", "Wages"), row("other", "Other human rights related issues")],
          complaints_cols(), headers=fy_header(3)),
    table("P5.E7", "7", "Complaints filed under the Sexual Harassment of Women at Workplace (Prevention, "
          "Prohibition and Redressal) Act, 2013 (POSH)",
          [row("total", "Total Complaints reported under POSH"),
           row("female_headcount", "Female employees / workers (headcount used for the % below)"),
           row("pct_female", "Complaints on POSH as a % of female employees / workers",
               formula="pct(total, female_headcount)", display="percent"),
           row("upheld", "Complaints on POSH upheld (number)"),
           row("upheld_pct", "Complaints on POSH upheld (%)", formula="pct(upheld, total)", display="percent")],
          cy_py("count")),
    text_q("P5.E8", "8", "Mechanisms to prevent adverse consequences to the complainant in discrimination and "
           "harassment cases."),
    yesno_q("P5.E9", "9", "Do human rights requirements form part of your business agreements and contracts? "
            "(Yes/No)", details_required_when=None),
    table("P5.E10", "10", "Assessments for the year",
          [row("child_labour", "Child labour"), row("forced_labour", "Forced/involuntary labour"),
           row("sexual_harassment", "Sexual harassment"), row("discrimination", "Discrimination at workplace"),
           row("wages", "Wages"), row("others", "Others – please specify")],
          [col("pct", "% of your plants and offices that were assessed (by entity or statutory authorities or "
               "third parties)", "percent")]),
    text_q("P5.E11", "11", "Provide details of any corrective actions taken or underway to address significant "
           "risks / concerns arising from the assessments at Question 10 above."),
]) + _lead([
    text_q("P5.L1", "1", "Details of a business process being modified / introduced as a result of addressing human "
           "rights grievances/complaints."),
    text_q("P5.L2", "2", "Details of the scope and coverage of any Human rights due-diligence conducted."),
    yesno_q("P5.L3", "3", "Is the premise/office of the entity accessible to differently abled visitors, as per the "
            "requirements of the Rights of Persons with Disabilities Act, 2016?"),
    table("P5.L4", "4", "Details on assessment of value chain partners",
          [row("sexual_harassment", "Sexual Harassment"), row("discrimination", "Discrimination at workplace"),
           row("child_labour", "Child Labour"), row("forced_labour", "Forced Labour/Involuntary Labour"),
           row("wages", "Wages"), row("others", "Others – please specify")],
          [col("pct", "% of value chain partners (by value of business done with such partners) that were "
               "assessed", "percent")]),
    text_q("P5.L5", "5", "Provide details of any corrective actions taken or underway to address significant risks "
           "/ concerns arising from the assessments at Question 4 above."),
])

# --- Principle 6 ------------------------------------------------------------

def _water_rows() -> list[dict]:
    return [
        row("w_hdr", "Water withdrawal by source (in kilolitres)", header=True),
        row("surface", "(i) Surface water"), row("ground", "(ii) Groundwater"),
        row("third_party", "(iii) Third party water"), row("sea", "(iv) Seawater / desalinated water"),
        row("others", "(v) Others"),
        row("withdrawal_total", "Total volume of water withdrawal (in kilolitres) (i + ii + iii + iv + v)",
            formula="surface + ground + third_party + sea + others"),
        row("consumption", "Total volume of water consumption (in kilolitres)"),
    ]


def _discharge_rows(prefix: str = "") -> list[dict]:
    out = [row(f"{prefix}d_hdr", "Water discharge by destination and level of treatment (in kilolitres)",
               header=True)]
    parts = []
    for key, label in (("surface", "(i) To Surface water"), ("ground", "(ii) To Groundwater"),
                       ("sea", "(iii) To Seawater"), ("third_party", "(iv) Sent to third-parties"),
                       ("others", "(v) Others")):
        out += [row(f"{prefix}{key}_hdr", label, header=True),
                row(f"{prefix}{key}_none", "- No treatment"),
                row(f"{prefix}{key}_treated", "- With treatment – please specify level of treatment")]
        parts += [f"{prefix}{key}_none", f"{prefix}{key}_treated"]
    out.append(row(f"{prefix}discharge_total", "Total water discharged (in kilolitres)", formula=" + ".join(parts)))
    return out


P6 = [
    {"group": "Intensity denominators", "indicator": "helper", **table(
        "P6.DEN", "—", "Intensity denominators (used to calculate every intensity in this Principle and the "
        "well-being spend in Principle 3)",
        [row("revenue", "Revenue from operations (INR)"),
         row("ppp_factor", "PPP conversion factor (IMF, for the relevant year)"),
         row("physical_output", "Physical output (optional — enter the unit in the note)")],
        cy_py("number"), helper=True,
        guidance="Revenue is required. The PPP factor is published by the IMF World Economic Outlook "
                 "(\"implied PPP conversion rate\"); SEBI's guidance note asks companies to use the latest "
                 "available. Physical output is optional and only needed by manufacturing entities.")},
] + _ess([
    table("P6.E1", "1", "Details of total energy consumption (in Joules or multiples) and energy intensity",
          [row("r_hdr", "From renewable sources", header=True),
           row("ren_elec", "Total electricity consumption (A)"), row("ren_fuel", "Total fuel consumption (B)"),
           row("ren_other", "Energy consumption through other sources (C)"),
           row("ren_total", "Total energy consumed from renewable sources (A+B+C)",
               formula="ren_elec + ren_fuel + ren_other"),
           row("n_hdr", "From non-renewable sources", header=True),
           row("non_elec", "Total electricity consumption (D)"), row("non_fuel", "Total fuel consumption (E)"),
           row("non_other", "Energy consumption through other sources (F)"),
           row("non_total", "Total energy consumed from non-renewable sources (D+E+F)",
               formula="non_elec + non_fuel + non_other"),
           row("total", "Total energy consumed (A+B+C+D+E+F)", formula="ren_total + non_total")]
          + intensity_rows("total", "Energy"),
          cy_py("number"), row_header="Parameter", unit="GJ", guidance=ASSURANCE_GUIDANCE),
    yesno_q("P6.E2", "2", "Does the entity have any sites / facilities identified as designated consumers (DCs) under "
            "the Performance, Achieve and Trade (PAT) Scheme of the Government of India? (Y/N) If yes, disclose "
            "whether targets set under the PAT scheme have been achieved. In case targets have not been achieved, "
            "provide the remedial action taken, if any."),
    table("P6.E3", "3", "Provide details of the following disclosures related to water",
          _water_rows() + intensity_rows("consumption", "Water"),
          cy_py("number"), row_header="Parameter", unit="kilolitres", guidance=ASSURANCE_GUIDANCE),
    table("P6.E4", "4", "Provide the following details related to water discharged", _discharge_rows(),
          cy_py("number"), row_header="Parameter", unit="kilolitres", guidance=ASSURANCE_GUIDANCE),
    yesno_q("P6.E5", "5", "Has the entity implemented a mechanism for Zero Liquid Discharge? If yes, provide details "
            "of its coverage and implementation."),
    table("P6.E6", "6", "Please provide details of air emissions (other than GHG emissions) by the entity",
          [row("nox", "NOx"), row("sox", "SOx"), row("pm", "Particulate matter (PM)"),
           row("pop", "Persistent organic pollutants (POP)"), row("voc", "Volatile organic compounds (VOC)"),
           row("hap", "Hazardous air pollutants (HAP)"), row("others", "Others – please specify")],
          [col("unit", "Please specify unit", "text")] + cy_py("number"), row_header="Parameter",
          guidance=ASSURANCE_GUIDANCE),
    table("P6.E7", "7", "Provide details of greenhouse gas emissions (Scope 1 and Scope 2 emissions) & its intensity",
          [row("scope1", "Total Scope 1 emissions (Metric tonnes of CO2 equivalent) (Break-up of the GHG into CO2, "
               "CH4, N2O, HFCs, PFCs, SF6, NF3, if available)"),
           row("scope2", "Total Scope 2 emissions (Metric tonnes of CO2 equivalent) (Break-up of the GHG into CO2, "
               "CH4, N2O, HFCs, PFCs, SF6, NF3, if available)"),
           row("total", "Total Scope 1 and Scope 2 emissions (tCO2e)", formula="scope1 + scope2")]
          + intensity_rows("total", "Total Scope 1 and Scope 2 emission"),
          cy_py("number"), row_header="Parameter", unit="tCO2e", guidance=ASSURANCE_GUIDANCE),
    yesno_q("P6.E8", "8", "Does the entity have any project related to reducing Green House Gas emission? If Yes, "
            "then provide details."),
    table("P6.E9", "9", "Provide details related to waste management by the entity",
          [row("g_hdr", "Total Waste generated (in metric tonnes)", header=True),
           row("plastic", "Plastic waste (A)"), row("ewaste", "E-waste (B)"), row("biomedical", "Bio-medical waste (C)"),
           row("cnd", "Construction and demolition waste (D)"), row("battery", "Battery waste (E)"),
           row("radioactive", "Radioactive waste (F)"),
           row("other_haz", "Other Hazardous waste. Please specify, if any. (G)"),
           row("other_nonhaz", "Other Non-hazardous waste generated (H). Please specify, if any. (Break-up by "
               "composition i.e. by materials relevant to the sector)"),
           row("total", "Total (A + B + C + D + E + F + G + H)",
               formula="plastic + ewaste + biomedical + cnd + battery + radioactive + other_haz + other_nonhaz")]
          + intensity_rows("total", "Waste")
          + [row("rec_hdr", "For each category of waste generated, total waste recovered through recycling, "
                 "re-using or other recovery operations (in metric tonnes)", header=True),
             row("recycled", "(i) Recycled"), row("reused", "(ii) Re-used"),
             row("other_recovery", "(iii) Other recovery operations"),
             row("recovered_total", "Total", formula="recycled + reused + other_recovery"),
             row("dis_hdr", "For each category of waste generated, total waste disposed by nature of disposal method "
                 "(in metric tonnes)", header=True),
             row("incineration", "(i) Incineration"), row("landfilling", "(ii) Landfilling"),
             row("other_disposal", "(iii) Other disposal operations"),
             row("disposed_total", "Total", formula="incineration + landfilling + other_disposal")],
          cy_py("number"), row_header="Parameter", unit="metric tonnes", guidance=ASSURANCE_GUIDANCE),
    text_q("P6.E10", "10", "Briefly describe the waste management practices adopted in your establishments. "
           "Describe the strategy adopted by your company to reduce usage of hazardous and toxic chemicals in your "
           "products and processes and the practices adopted to manage such wastes."),
    rows_q("P6.E11", "11", "If the entity has operations/offices in/around ecologically sensitive areas (such as "
           "national parks, wildlife sanctuaries, biosphere reserves, wetlands, biodiversity hotspots, forests, "
           "coastal regulation zones etc.) where environmental approvals / clearances are required, please specify "
           "details", [
               col("location", "Location of operations/offices", "text"),
               col("type", "Type of operations", "text"),
               col("complied", "Whether the conditions of environmental approval / clearance are being complied "
                   "with? (Y/N)", "yesno"),
               col("reasons", "If no, the reasons thereof and corrective action taken, if any", "longtext")],
           min_rows=0),
    rows_q("P6.E12", "12", "Details of environmental impact assessments of projects undertaken by the entity based on "
           "applicable laws, in the current financial year", [
               col("project", "Name and brief details of project", "longtext"),
               col("eia_no", "EIA Notification No.", "text"), col("date", "Date", "text"),
               col("independent", "Whether conducted by independent external agency (Yes / No)", "yesno"),
               col("public", "Results communicated in public domain (Yes / No)", "yesno"),
               col("link", "Relevant Web link", "text")], min_rows=0),
    yesno_q("P6.E13", "13", "Is the entity compliant with the applicable environmental law/ regulations/ guidelines "
            "in India; such as the Water (Prevention and Control of Pollution) Act, Air (Prevention and Control of "
            "Pollution) Act, Environment protection act and rules thereunder (Y/N).", details=None),
    rows_q("P6.E13b", "13", "If not, provide details of all such non-compliances", [
        col("law", "Specify the law / regulation / guidelines which was not complied with", "text"),
        col("details", "Provide details of the non-compliance", "longtext"),
        col("action_by_regulator", "Any fines / penalties / action taken by regulatory agencies such as pollution "
            "control boards or by courts", "longtext"),
        col("corrective", "Corrective action taken, if any", "longtext")], min_rows=0),
]) + _lead([
    fields_q("P6.L1a", "1", "Water withdrawal, consumption and discharge in areas of water stress (in kilolitres) — "
             "for each facility / plant located in areas of water stress", [
                 field("area", "(a) Name of the area", "text"),
                 field("operations", "(b) Nature of operations", "text")]),
    table("P6.L1b", "1(c)", "Water withdrawal, consumption and discharge in areas of water stress",
          _water_rows() + [row("int_rupee", "Water intensity per rupee of turnover (Water consumed / turnover)",
                               formula="div(consumption, ref('P6.DEN', 'revenue'))", display="intensity"),
                           row("int_optional", "Water intensity (optional) – the relevant metric may be selected by "
                               "the entity")]
          + _discharge_rows("x_"),
          cy_py("number"), row_header="Parameter", unit="kilolitres"),
    table("P6.L2", "2", "Please provide details of total Scope 3 emissions & its intensity",
          [row("scope3", "Total Scope 3 emissions (Metric tonnes of CO2 equivalent) (Break-up of the GHG into CO2, "
               "CH4, N2O, HFCs, PFCs, SF6, NF3, if available)"),
           row("int_rupee", "Total Scope 3 emissions per rupee of turnover",
               formula="div(scope3, ref('P6.DEN', 'revenue'))", display="intensity"),
           row("int_optional", "Total Scope 3 emission intensity (optional) – the relevant metric may be selected by "
               "the entity")],
          cy_py("number"), row_header="Parameter", unit="tCO2e"),
    text_q("P6.L3", "3", "With respect to the ecologically sensitive areas reported at Question 11 of Essential "
           "Indicators above, provide details of significant direct & indirect impact of the entity on "
           "biodiversity in such areas along-with prevention and remediation activities."),
    rows_q("P6.L4", "4", "If the entity has undertaken any specific initiatives or used innovative technology or "
           "solutions to improve resource efficiency, or reduce impact due to emissions / effluent discharge / waste "
           "generated, please provide details of the same as well as outcome of such initiatives", [
               col("initiative", "Initiative undertaken", "text"),
               col("details", "Details of the initiative (Web-link, if any, may be provided along-with summary)",
                   "longtext"),
               col("outcome", "Outcome of the initiative", "longtext")], min_rows=0),
    text_q("P6.L5", "5", "Does the entity have a business continuity and disaster management plan? Give details in "
           "100 words/ web link.", max_words=100),
    text_q("P6.L6", "6", "Disclose any significant adverse impact to the environment, arising from the value chain "
           "of the entity. What mitigation or adaptation measures have been taken by the entity in this regard."),
    number_q("P6.L7", "7", "Percentage of value chain partners (by value of business done with such partners) that "
             "were assessed for environmental impacts.", "percent"),
    fields_q("P6.L8", "8", "How many Green Credits have been generated or procured?", [
        field("entity", "(a) By the listed entity", "longtext"),
        field("value_chain", "(b) By the top ten (in terms of value of purchases and sales, respectively) value "
              "chain partners", "longtext")]),
])

# --- Principle 7 ------------------------------------------------------------
P7 = _ess([
    number_q("P7.E1a", "1(a)", "Number of affiliations with trade and industry chambers/ associations.", "count"),
    rows_q("P7.E1b", "1(b)", "List the top 10 trade and industry chambers/ associations (determined based on the "
           "total members of such body) the entity is a member of/ affiliated to.", [
               col("name", "Name of the trade and industry chambers/ associations", "text"),
               col("reach", "Reach of trade and industry chambers/ associations (State/National)", "choice",
                   options=["State", "National", "International"])], max_rows=10, min_rows=0),
    rows_q("P7.E2", "2", "Provide details of corrective action taken or underway on any issues related to "
           "anti-competitive conduct by the entity, based on adverse orders from regulatory authorities.", [
               col("authority", "Name of authority", "text"), col("brief", "Brief of the case", "longtext"),
               col("action", "Corrective action taken", "longtext")], min_rows=0),
]) + _lead([
    rows_q("P7.L1", "1", "Details of public policy positions advocated by the entity", [
        col("policy", "Public policy advocated", "longtext"),
        col("method", "Method resorted for such advocacy", "text"),
        col("public", "Whether information available in public domain? (Yes/No)", "yesno"),
        col("frequency", "Frequency of Review by Board (Annually/ Half yearly/ Quarterly / Others – please specify)",
            "text"),
        col("link", "Web Link, if available", "text")], min_rows=0),
])

# --- Principle 8 ------------------------------------------------------------
P8 = _ess([
    rows_q("P8.E1", "1", "Details of Social Impact Assessments (SIA) of projects undertaken by the entity based on "
           "applicable laws, in the current financial year.", [
               col("project", "Name and brief details of project", "longtext"),
               col("sia_no", "SIA Notification No.", "text"), col("date", "Date of notification", "text"),
               col("independent", "Whether conducted by independent external agency (Yes / No)", "yesno"),
               col("public", "Results communicated in public domain (Yes / No)", "yesno"),
               col("link", "Relevant Web link", "text")], min_rows=0),
    rows_q("P8.E2", "2", "Provide information on project(s) for which ongoing Rehabilitation and Resettlement (R&R) "
           "is being undertaken by your entity", [
               col("project", "Name of Project for which R&R is ongoing", "text"),
               col("state", "State", "text"), col("district", "District", "text"),
               col("pafs", "No. of Project Affected Families (PAFs)"),
               col("pct_covered", "% of PAFs covered by R&R", "percent"),
               col("amount", "Amounts paid to PAFs in the FY (In INR)", "amount")], min_rows=0),
    text_q("P8.E3", "3", "Describe the mechanisms to receive and redress grievances of the community."),
    table("P8.E4", "4", "Percentage of input material (inputs to total inputs by value) sourced from suppliers",
          [row("total_inputs", "Total input material procured (INR)"),
           row("msme_inputs", "Input material sourced directly from MSMEs/ small producers (INR)"),
           row("india_inputs", "Input material sourced directly from within India (INR)"),
           row("msme_pct", "Directly sourced from MSMEs/ small producers (%)",
               formula="pct(msme_inputs, total_inputs)", display="percent"),
           row("india_pct", "Directly from within India (%)",
               formula="pct(india_inputs, total_inputs)", display="percent")], cy_py("amount")),
    table("P8.E5", "5", "Job creation in smaller towns – Disclose wages paid to persons employed (including employees "
          "or workers employed on a permanent or non-permanent / on contract basis) in the following locations, as "
          "% of total wage cost",
          [row("rural", "Wages paid — Rural (INR)"), row("semi_urban", "Wages paid — Semi-urban (INR)"),
           row("urban", "Wages paid — Urban (INR)"), row("metro", "Wages paid — Metropolitan (INR)"),
           row("total", "Total wage cost (INR)", formula="rural + semi_urban + urban + metro"),
           row("rural_pct", "Rural (%)", formula="pct(rural, total)", display="percent"),
           row("semi_urban_pct", "Semi-urban (%)", formula="pct(semi_urban, total)", display="percent"),
           row("urban_pct", "Urban (%)", formula="pct(urban, total)", display="percent"),
           row("metro_pct", "Metropolitan (%)", formula="pct(metro, total)", display="percent")],
          cy_py("amount"), row_header="Location",
          guidance="Place categorisation as per RBI: Rural, Semi-urban, Urban, Metropolitan."),
]) + _lead([
    rows_q("P8.L1", "1", "Provide details of actions taken to mitigate any negative social impacts identified in the "
           "Social Impact Assessments (Reference: Question 1 of Essential Indicators above)", [
               col("impact", "Details of negative social impact identified", "longtext"),
               col("action", "Corrective action taken", "longtext")], min_rows=0),
    rows_q("P8.L2", "2", "Provide the following information on CSR projects undertaken by your entity in designated "
           "aspirational districts as identified by government bodies", [
               col("state", "State", "text"), col("district", "Aspirational District", "text"),
               col("amount", "Amount spent (In INR)", "amount")], min_rows=0),
    yesno_q("P8.L3a", "3(a)", "Do you have a preferential procurement policy where you give preference to purchase "
            "from suppliers comprising marginalized /vulnerable groups? (Yes/No)", details_required_when=None),
    text_q("P8.L3b", "3(b)", "From which marginalized /vulnerable groups do you procure?"),
    number_q("P8.L3c", "3(c)", "What percentage of total procurement (by value) does it constitute?", "percent"),
    rows_q("P8.L4", "4", "Details of the benefits derived and shared from the intellectual properties owned or "
           "acquired by your entity (in the current financial year), based on traditional knowledge", [
               col("ip", "Intellectual Property based on traditional knowledge", "text"),
               col("owned", "Owned/ Acquired (Yes/No)", "yesno"),
               col("shared", "Benefit shared (Yes / No)", "yesno"),
               col("basis", "Basis of calculating benefit share", "longtext")], min_rows=0),
    rows_q("P8.L5", "5", "Details of corrective actions taken or underway, based on any adverse order in intellectual "
           "property related disputes wherein usage of traditional knowledge is involved.", [
               col("authority", "Name of authority", "text"), col("brief", "Brief of the Case", "longtext"),
               col("action", "Corrective action taken", "longtext")], min_rows=0),
    rows_q("P8.L6", "6", "Details of beneficiaries of CSR Projects", [
        col("project", "CSR Project", "text"),
        col("beneficiaries", "No. of persons benefitted from CSR Projects"),
        col("pct_vulnerable", "% of beneficiaries from vulnerable and marginalized groups", "percent")], min_rows=0),
])

# --- Principle 9 ------------------------------------------------------------
P9 = _ess([
    text_q("P9.E1", "1", "Describe the mechanisms in place to receive and respond to consumer complaints and "
           "feedback."),
    table("P9.E2", "2", "Turnover of products and/ services as a percentage of turnover from all products/service "
          "that carry information about",
          [row("env_social", "Environmental and social parameters relevant to the product"),
           row("safe_usage", "Safe and responsible usage"), row("recycling", "Recycling and/or safe disposal")],
          [col("pct", "As a percentage to total turnover", "percent")]),
    table("P9.E3", "3", "Number of consumer complaints in respect of the following",
          [row("data_privacy", "Data privacy"), row("advertising", "Advertising"),
           row("cyber_security", "Cyber-security"), row("essential_services", "Delivery of essential services"),
           row("restrictive", "Restrictive Trade Practices"), row("unfair", "Unfair Trade Practices"),
           row("other", "Other")],
          fy_cols([col("received", "Received during the year"),
                   col("pending", "Pending resolution at end of year"),
                   col("remarks", "Remarks", "text")]), headers=fy_header(3)),
    table("P9.E4", "4", "Details of instances of product recalls on account of safety issues",
          [row("voluntary", "Voluntary recalls"), row("forced", "Forced recalls")],
          [col("number", "Number"), col("reasons", "Reasons for recall", "longtext")]),
    yesno_q("P9.E5", "5", "Does the entity have a framework/ policy on cyber security and risks related to data "
            "privacy? (Yes/No) If available, provide a web-link of the policy.", link=True),
    text_q("P9.E6", "6", "Provide details of any corrective actions taken or underway on issues relating to "
           "advertising, and delivery of essential services; cyber security and data privacy of customers; "
           "re-occurrence of instances of product recalls; penalty / action taken by regulatory authorities on "
           "safety of products / services."),
    table("P9.E7", "7", "Provide the following information relating to data breaches",
          [row("breaches", "a. Number of instances of data breaches"),
           row("pii_breaches", "Number of data breaches involving personally identifiable information of customers"),
           row("pii_pct", "b. Percentage of data breaches involving personally identifiable information of customers",
               formula="pct(pii_breaches, breaches)", display="percent")],
          [col("value", PERIOD_LABEL["cy"])]),
    text_q("P9.E7c", "7(c)", "Impact, if any, of the data breaches."),
]) + _lead([
    text_q("P9.L1", "1", "Channels / platforms where information on products and services of the entity can be "
           "accessed (provide web link, if available)."),
    text_q("P9.L2", "2", "Steps taken to inform and educate consumers about safe and responsible usage of products "
           "and/or services."),
    text_q("P9.L3", "3", "Mechanisms in place to inform consumers of any risk of disruption/discontinuation of "
           "essential services."),
    yesno_q("P9.L4a", "4", "Does the entity display product information on the product over and above what is "
            "mandated as per local laws? (Yes/No/Not Applicable) If yes, provide details in brief."),
    yesno_q("P9.L4b", "4", "Did your entity carry out any survey with regard to consumer satisfaction relating to "
            "the major products / services of the entity, significant locations of operation of the entity or the "
            "entity as a whole? (Yes/No)"),
])


def _tag(section: str, items: list[dict]) -> list[dict]:
    return [{**q, "section": section} for q in items]


# ---------------------------------------------------------------------------
# Departments (engagement's department → question mapping). Questions built
# from annual data inherit the departments of the areas they draw on.
# ---------------------------------------------------------------------------

DEPARTMENTS = ["Admin", "Secretarial", "ESG & CSR", "HR", "Finance", "ISG", "Client experience and Governance",
               "CLC", "Legal", "Compliance", "Tech", "AP"]

QUESTION_DEPARTMENTS: dict[str, list[str]] = {
    'A.16': ['Finance'],
    'A.17': ['Finance'],
    'A.19a': ['Admin', 'Compliance'],
    'A.19c': ['Finance'],
    'A.23': ['Secretarial'],
    'A.25': ['Client experience and Governance'],
    'A.26': ['ESG & CSR'],
    'A.I': ['ESG & CSR', 'Secretarial'],
    'B.1': ['ESG & CSR', 'Secretarial'],
    'B.10': ['ESG & CSR'],
    'B.11': ['Secretarial'],
    'B.12': ['ESG & CSR'],
    'B.2': ['Secretarial'],
    'B.3': ['Secretarial'],
    'B.4': ['ESG & CSR'],
    'B.5': ['ESG & CSR'],
    'B.6': ['ESG & CSR'],
    'B.7': ['ESG & CSR'],
    'B.8': ['Secretarial'],
    'B.9': ['Secretarial'],
    'P1.E1': ['HR'],
    'P1.E2a': ['Finance'],
    'P1.E2b': ['Finance'],
    'P1.E3': ['Legal'],
    'P1.E4': ['HR'],
    'P1.E7': ['Legal'],
    'P1.E9': ['Finance'],
    'P1.L1': ['HR'],
    'P1.L2': ['Secretarial'],
    'P2.E1': ['Tech'],
    'P2.E2a': ['AP'],
    'P2.E2b': ['AP'],
    'P2.E3': ['Admin'],
    'P2.E4': ['Admin'],
    'P2.L1': ['ESG & CSR'],
    'P2.L2': ['ESG & CSR'],
    'P2.L3': ['Admin'],
    'P2.L4': ['Admin'],
    'P2.L5': ['Admin', 'Tech'],
    'P3.E10a': ['Admin', 'HR'],
    'P3.E10b': ['Admin', 'HR'],
    'P3.E10c': ['HR'],
    'P3.E10d': ['HR'],
    'P3.E12': ['Admin', 'HR'],
    'P3.E14': ['Admin', 'HR'],
    'P3.E15': ['Admin', 'HR'],
    'P3.E2': ['HR'],
    'P3.E3': ['HR'],
    'P3.E4': ['HR'],
    'P3.E6': ['HR'],
    'P3.L1': ['HR'],
    'P3.L2': ['HR'],
    'P3.L3': ['HR'],
    'P3.L4': ['HR'],
    'P3.L5': ['Admin'],
    'P3.L6': ['Admin', 'HR'],
    'P4.E1': ['ESG & CSR'],
    'P4.E2': ['ESG & CSR'],
    'P4.L1': ['ESG & CSR', 'Secretarial'],
    'P4.L2': ['ESG & CSR'],
    'P4.L3': ['ESG & CSR'],
    'P5.E10': ['Compliance'],
    'P5.E11': ['Compliance'],
    'P5.E4': ['HR'],
    'P5.E5': ['HR'],
    'P5.E7': ['HR'],
    'P5.E8': ['HR'],
    'P5.E9': ['HR', 'Legal'],
    'P5.L1': ['HR'],
    'P5.L2': ['HR'],
    'P5.L3': ['Admin', 'HR'],
    'P5.L4': ['Compliance'],
    'P5.L5': ['Admin', 'HR'],
    'P6.E10': ['Admin'],
    'P6.E11': ['ESG & CSR'],
    'P6.E12': ['Admin'],
    'P6.E13': ['ESG & CSR'],
    'P6.E13b': ['ESG & CSR'],
    'P6.E2': ['ESG & CSR'],
    'P6.E5': ['Admin'],
    'P6.E6': ['Admin'],
    'P6.E8': ['ESG & CSR'],
    'P6.L1a': ['Admin'],
    'P6.L1b': ['Admin'],
    'P6.L3': ['ESG & CSR'],
    'P6.L4': ['Tech'],
    'P6.L5': ['Tech'],
    'P6.L6': ['Admin'],
    'P6.L7': ['ESG & CSR'],
    'P6.L8': ['ESG & CSR'],
    'P7.E1a': ['Compliance'],
    'P7.E1b': ['Compliance'],
    'P7.E2': ['HR'],
    'P7.L1': ['Secretarial'],
    'P8.E1': ['ESG & CSR'],
    'P8.E2': ['ESG & CSR'],
    'P8.E3': ['ESG & CSR'],
    'P8.L1': ['ESG & CSR'],
    'P8.L2': ['ESG & CSR'],
    'P8.L3a': ['ESG & CSR'],
    'P8.L3b': ['ESG & CSR'],
    'P8.L3c': ['ESG & CSR'],
    'P8.L4': ['ESG & CSR'],
    'P8.L5': ['Admin'],
    'P8.L6': ['ESG & CSR'],
    'P9.E1': ['Client experience and Governance'],
    'P9.E2': ['Finance'],
    'P9.E4': ['Client experience and Governance'],
    'P9.E5': ['Finance', 'ISG'],
    'P9.E6': ['ISG'],
    'P9.E7': ['ISG'],
    'P9.E7c': ['ISG'],
    'P9.L1': ['Client experience and Governance'],
    'P9.L2': ['Client experience and Governance'],
    'P9.L3': ['Client experience and Governance'],
    'P9.L4a': ['Client experience and Governance'],
    'P9.L4b': ['Client experience and Governance'],
}


QUESTIONS: list[dict] = (
    _tag("A", SECTION_A) + _tag("B", SECTION_B) + _tag("P1", P1) + _tag("P2", P2) + _tag("P3", P3)
    + _tag("P4", P4) + _tag("P5", P5) + _tag("P6", P6) + _tag("P7", P7) + _tag("P8", P8) + _tag("P9", P9)
)

# --- Quantitative cells built from the approved data sheets (bank v3) -----
# A cell is built from the sheets only when every figure in its expression has
# a sheet source; any other cell is typed by the company in the BRSR table
# itself (e.g. turnover, net worth, Scope 1/2 emissions).
from app.brsr import formula as _F  # noqa: E402
from app.brsr.annual_data import DERIVE, DERIVED_STILL_REQUIRED, ROW_DERIVE  # noqa: E402
from app.brsr.sheet_sources import COMPUTED as _COMPUTED, SOURCED, SOURCES as _SOURCES  # noqa: E402
from app.datasheets.catalog import SHEETS as _SHEETS  # noqa: E402

for _s in SECTIONS:
    _s.setdefault("kind", "disclosure")
SECTION_CODES[:] = [s["code"] for s in SECTIONS]


def metric_sheets(name: str) -> set[str]:
    if name in _SOURCES:
        return {p.sheet for p in _SOURCES[name].parts}
    if name in _COMPUTED:
        return set().union(*(metric_sheets(d) for d in _COMPUTED[name][0]))
    return set()


def _typed_cells(q: dict, spec: dict) -> int:
    """Current-year input cells not built from the sheets."""
    if q["type"] == "number":
        return 0 if "value" in spec else 1
    if q["type"] == "fields":
        return sum(1 for f in q["fields"] if f"fields.{f['key']}" not in spec and not f.get("readonly"))
    n = 0
    for r in q["rows"]:
        if r.get("header") or r.get("formula"):
            continue
        for c in q["columns"]:
            if c["kind"] == "computed" or (c.get("period") and c["period"] != "cy"):
                continue
            base = c["key"][len(c["period"]) + 1:] if c.get("period") else c["key"]
            if f"{r['key']}.{base}" not in spec:
                n += 1
    return n


for _q in QUESTIONS:
    _deps = set(QUESTION_DEPARTMENTS.get(_q["id"], ["ESG & CSR"]))
    _spec = {cell: expr for cell, expr in DERIVE.get(_q["id"], {}).items() if _F.names_in(expr) <= SOURCED}
    _sheets: set[str] = set()
    for _expr in _spec.values():
        for _n in _F.names_in(_expr):
            _sheets |= metric_sheets(_n)
    _rows = ROW_DERIVE.get(_q["id"])
    if _rows:
        _q["derive_rows"] = _rows
        _sheets |= set(_rows["sheets"])
    if _spec:
        _q["derive"] = _spec
        _q["required"] = _q["id"] in DERIVED_STILL_REQUIRED
    if _sheets:
        _q["derived_from"] = sorted(_sheets)
        _q["derived_from_titles"] = [_SHEETS[x]["title"] for x in sorted(_sheets)]
        for _x in _sheets:
            _deps |= set(_SHEETS[_x]["departments"])
    # Where the answer comes from: the questionnaire, the data sheets, or both.
    if (_spec and not _typed_cells(_q, _spec)) or (_rows and "table" in _rows):
        _q["source"] = "data"
    elif _spec or _rows:
        _q["source"] = "both"
    else:
        _q["source"] = "qualitative"
    _q["departments"] = sorted(_deps)

QUESTIONS_BY_ID: dict[str, dict] = {q["id"]: q for q in QUESTIONS}

# Conditional requirements: (question, depends_on, answer_that_makes_it_required)
CONDITIONAL_REQUIRED: list[tuple[str, str, str]] = [
    ("P2.E2b", "P2.E2a", "Yes"),
    ("P6.E13b", "P6.E13", "No"),
]

# Cross-question consistency checks. Retired: every table that repeats a
# headcount or complaint count is now built from the same annual figure, so
# they cannot disagree. Kept as an (empty) extension point.
CONSISTENCY_CHECKS: list[tuple[str, tuple[str, str, str], Any]] = []


# ---------------------------------------------------------------------------
# Self-check: run at import time so a malformed bank fails loudly in tests
# and at app startup rather than in a client's report.
# ---------------------------------------------------------------------------

def _self_check() -> None:
    from app.brsr import formula as F

    seen: set[str] = set()
    for q in QUESTIONS:
        qid = q["id"]
        if qid in seen:
            raise ValueError(f"Duplicate question id {qid}")
        seen.add(qid)
        if q["section"] not in SECTION_CODES:
            raise ValueError(f"{qid}: unknown section {q['section']}")
        if q["type"] == "table":
            rkeys = [r["key"] for r in q["rows"]]
            ckeys = [c["key"] for c in q["columns"]]
            if len(set(rkeys)) != len(rkeys):
                raise ValueError(f"{qid}: duplicate row keys")
            if len(set(ckeys)) != len(ckeys):
                raise ValueError(f"{qid}: duplicate column keys")
            for c in q["columns"]:
                if c["kind"] not in INPUT_KINDS | {"computed"}:
                    raise ValueError(f"{qid}.{c['key']}: unknown kind {c['kind']}")
                if "formula" in c:
                    unknown = F.names_in(c["formula"]) - set(ckeys)
                    if unknown:
                        raise ValueError(f"{qid}.{c['key']}: formula reads unknown columns {unknown}")
                if "lte" in c and c["lte"] not in ckeys:
                    raise ValueError(f"{qid}.{c['key']}: lte references unknown column {c['lte']}")
            for r in q["rows"]:
                if "formula" in r:
                    unknown = F.names_in(r["formula"]) - set(rkeys)
                    if unknown:
                        raise ValueError(f"{qid}.{r['key']}: formula reads unknown rows {unknown}")
                    for (rq, rr) in F.refs_in(r["formula"]):
                        target = QUESTIONS_BY_ID.get(rq)
                        if not target or rr not in [x["key"] for x in target.get("rows", [])]:
                            raise ValueError(f"{qid}.{r['key']}: ref({rq!r}, {rr!r}) does not exist")
            if q.get("headers"):
                width = len(q["columns"]) + 1
                for h in q["headers"]:
                    if sum(cell["span"] for cell in h) != width:
                        raise ValueError(f"{qid}: header spans {sum(c['span'] for c in h)} != {width}")
        if q["type"] == "rows":
            for c in q["columns"]:
                if c["kind"] not in INPUT_KINDS:
                    raise ValueError(f"{qid}.{c['key']}: rows tables take inputs only")
    for q in QUESTIONS:
        if not q.get("derive"):
            continue
        for cell, expr in q["derive"].items():
            unknown = F.names_in(expr) - SOURCED
            if unknown:
                raise ValueError(f"{q['id']} {cell}: unknown metrics {unknown}")
            if q["type"] == "number":
                if cell != "value":
                    raise ValueError(f"{q['id']}: number questions derive only 'value', got {cell}")
                continue
            target, base = cell.split(".", 1)
            if q["type"] == "fields":
                if target != "fields" or base not in [f["key"] for f in q["fields"]]:
                    raise ValueError(f"{q['id']}: derive targets unknown field {cell}")
                continue
            rows = {r["key"]: r for r in q["rows"]}
            if target not in rows or rows[target].get("header") or rows[target].get("formula"):
                raise ValueError(f"{q['id']}: derive targets unknown/non-input row {target}")
            if not any(c["key"] == base or (c.get("period") and c["key"] == f"{c['period']}_{base}")
                       for c in q["columns"] if c["kind"] != "computed"):
                raise ValueError(f"{q['id']}: derive targets unknown column {base}")
    for code in SECTION_CODES:
        if not any(q["section"] == code for q in QUESTIONS):
            raise ValueError(f"Section {code} has no questions")
    for q in QUESTIONS:
        for dept in q.get("departments", []):
            if dept not in DEPARTMENTS:
                raise ValueError(f"{q['id']}: unknown department {dept!r}")
    for (a, b, _ans) in CONDITIONAL_REQUIRED:
        if a not in QUESTIONS_BY_ID or b not in QUESTIONS_BY_ID:
            raise ValueError(f"Conditional requirement references unknown question {a}/{b}")
    for (label, left, right) in CONSISTENCY_CHECKS:
        cells = [left] + (right if isinstance(right, list) else [right])
        for (qid, r, c) in cells:
            q = QUESTIONS_BY_ID.get(qid)
            if not q or r not in [x["key"] for x in q["rows"]] or c not in [x["key"] for x in q["columns"]]:
                raise ValueError(f"Consistency check {label!r} references unknown cell {qid}/{r}/{c}")


_self_check()


def questions_for_section(code: str) -> list[dict]:
    return [q for q in QUESTIONS if q["section"] == code]


def financial_year_labels(fy: str) -> dict[str, str]:
    """'2024-25' -> {'cy': 'FY 2024-25', 'py': 'FY 2023-24', 'ppy': 'FY 2022-23'}."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", fy)
    if not m:
        raise ValueError(f"Invalid financial year {fy!r}")
    start = int(m.group(1))

    def lab(s: int) -> str:
        return f"FY {s}-{str(s + 1)[-2:]}"

    return {"cy": lab(start), "py": lab(start - 1), "ppy": lab(start - 2)}


def public_bank() -> dict:
    """The bank as served to the frontend."""
    return {"sections": SECTIONS, "questions": QUESTIONS, "version": BANK_VERSION, "departments": DEPARTMENTS}


BANK_VERSION = "sebi-2023-122.v3-datasheets"
