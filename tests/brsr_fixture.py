"""A complete BRSR answer set built from the reference filing supplied by
the engagement manager (Nuvama Wealth Management Ltd, BRSR FY 2024-25 —
a public SEBI filing).

Used by the end-to-end tests: every required question is answered, so a
response set filled with this fixture can be submitted, approved and
turned into a report, and the generated document can be compared with
the source filing.

Where the source filing contains an arithmetic error, the fixture holds
the correct raw inputs (the platform computes the percentage), e.g.
P3 Q9: the filing shows 112.19% of male employees reviewed because it
divided by permanent male employees (2,551) instead of all male employees
(2,957). Revenue from operations is derived from the filing's own energy
intensity (Total energy / intensity) and is illustrative only.
"""

from app.brsr.question_bank import QUESTIONS_BY_ID

NA = lambda reason: {"na": True, "na_reason": reason}  # noqa: E731


def _cy(v):
    return {"cy_v": v}


def _cypy(cy, py):
    return {"cy_v": cy, "py_v": py}


def answers() -> dict:
    a: dict[str, dict] = {}

    # ---- Section A ---------------------------------------------------------
    a["A.I"] = {"fields": {
        "cin": "L67110MH1993PLC344643", "name": "NUVAMA WEALTH MANAGEMENT LIMITED", "year_incorporated": 1993,
        "registered_office": "801- 804, Wing A, Building No. 3, Inspire BKC, G Block, Bandra Kurla Complex, "
                             "Bandra East, Mumbai – 400 051",
        "corporate_address": "801- 804, Wing A, Building No. 3, Inspire BKC, G Block, Bandra Kurla Complex, "
                             "Bandra East, Mumbai – 400 051",
        "email": "esg@nuvama.com", "telephone": "022-6620-3030", "website": "www.nuvama.com",
        "stock_exchanges": "National Stock Exchange of India Limited and BSE Limited",
        "paid_up_capital": 359743580, "contact_name": "Head, ESG & CSR", "contact_designation": "Head ESG & CSR",
        "contact_phone": "022-6620-3030", "contact_email": "esg@nuvama.com",
        "reporting_boundary": "Consolidated", "assurance_provider": "", "assurance_type": "Not obtained",
    }}
    a["A.16"] = {"rows": [{"main_activity": "Financial Services",
                           "business_activity": "The Company is an integrated wealth management platform providing "
                                                "clients with tailored wealth management solutions and advice.",
                           "pct_turnover": 100}]}
    a["A.17"] = {"rows": [{"product": "Wealth Management Services", "nic_code": "66190", "pct_turnover": 100}]}
    a["A.18"] = {"cells": {"national": {"plants": 0, "offices": 95}, "international": {"plants": 0, "offices": 6}}}
    a["A.19a"] = {"cells": {"national": {"number": 28}, "international": {"number": 6}}}
    a["A.19b"] = {"value": 0}
    a["A.19c"] = {"text": "Individual and family-office wealth clients, institutional investors and corporates."}
    a["A.20a"] = {"cells": {
        "employees_perm": {"total": 3414, "male": 2551, "female": 863},
        "employees_other": {"total": 489, "male": 406, "female": 83},
        "workers_perm": {"total": 0, "male": 0, "female": 0},
        "workers_other": {"total": 0, "male": 0, "female": 0}}}
    a["A.20b"] = {"cells": {
        "employees_perm": {"total": 3, "male": 3, "female": 0},
        "employees_other": {"total": 0, "male": 0, "female": 0},
        "workers_perm": {"total": 0, "male": 0, "female": 0},
        "workers_other": {"total": 0, "male": 0, "female": 0}}}
    a["A.21"] = {"cells": {"bod": {"total": 9, "female": 1}, "kmp": {"total": 4, "female": 1}}}
    a["A.22"] = {"cells": {"perm_employees": {"cy_male": 23.38, "cy_female": 26.47, "cy_total": 24.12},
                           "perm_workers": {"cy_male": 0, "cy_female": 0, "cy_total": 0}}}
    subs = [("Nuvama Clearing services Limited", "Wholly Owned Subsidiary", 100),
            ("Nuvama Wealth Finance Limited", "Wholly Owned Subsidiary", 100),
            ("Nuvama Wealth and Investment Limited", "Wholly Owned Subsidiary", 100),
            ("Nuvama Asset Management Limited", "Wholly Owned Subsidiary", 100),
            ("Pickright Technologies Private Limited", "Subsidiary", 74),
            ("Nuvama and Cushman & Wakefield Management Private Limited", "Joint Venture", 50),
            ("Nuvama Custodial Services Limited", "Associate", 49)]
    a["A.23"] = {"rows": [{"name": n, "relationship": r, "pct_shares": p, "participates": "No"} for n, r, p in subs]}
    a["A.24"] = {"fields": {"csr_applicable": "Yes", "turnover": 41693400000, "net_worth": 35000000000}}
    a["A.25"] = {"cells": {k: {"mechanism": "Yes", "cy_filed": 0, "cy_pending": 0, "py_filed": 0, "py_pending": 0}
                           for k in ("communities", "investors", "shareholders", "employees", "customers",
                                     "value_chain")} | {"other": {"mechanism": "NA"}}}
    a["A.26"] = {"rows": [
        {"issue": "Business ethics and Transparency to ensure Customer Satisfaction",
         "risk_or_opportunity": "Risk and Opportunity",
         "rationale": "Putting the customer interest first strengthens trust and loyalty.",
         "mitigation": "Customer-first philosophy, satisfaction measurement and grievance redressal policy.",
         "implication_sign": "Positive", "implication": "Higher customer retention and revenue stability."},
        {"issue": "Data Privacy and Security", "risk_or_opportunity": "Risk",
         "rationale": "Cyber threats can erode customer trust.",
         "mitigation": "Cybersecurity policies, employee training and ISO certification.",
         "implication_sign": "Positive", "implication": "Prevention of penalties and losses due to breaches."}]}

    # ---- Section B ---------------------------------------------------------
    P = [f"P{i}" for i in range(1, 10)]
    a["B.1"] = {"cells": {"covers": {p: "Yes" for p in P}, "board_approved": {p: "Yes" for p in P}},
                "span": {"web_link": "https://www.nuvama.com/investor-relations/corporate-governance/"}}
    a["B.2"] = {"cells": {"translated": {p: "Yes" for p in P}}}
    a["B.3"] = {"cells": {"value_chain": {p: "No" for p in P}}}
    a["B.4"] = {"span": {"codes": "ISO 27001:2022 for the information security management program."}}
    a["B.5"] = {"span": {"commitments": "Internal ESG goals with defined timelines, aligned to 13 SDGs."}}
    a["B.6"] = {"span": {"performance": "Tracked internally; progress published in the ESG report."}}
    a["B.7"] = {"text": "Statement by the Managing Director & CEO on ESG challenges, targets and achievements."}
    a["B.8"] = {"fields": {"name": "Mr. Ashish Kehair", "designation": "Managing Director & CEO", "din": "07789972"}}
    a["B.9"] = {"answer": "Yes", "details": "Dedicated ESG committee chaired by an independent director."}
    a["B.10"] = {"cells": {"perf_by": {p: "Committee of the Board" for p in P},
                           "perf_freq": {p: "Annually" for p in P},
                           "comp_by": {p: "Committee of the Board" for p in P},
                           "comp_freq": {p: "Annually" for p in P}}}
    a["B.11"] = {"cells": {"assessed": {p: "No" for p in P}},
                 "span": {"agency": "Internal audit; policy gap review by E&Y"}}

    # ---- Principle 1 -------------------------------------------------------
    a["P1.E1"] = {"cells": {"bod": {"programmes": 0, "pct_covered": 0},
                            "kmp": {"programmes": 1, "topics": "ICSI Annual Conference", "pct_covered": 25},
                            "employees": {"programmes": 706, "topics": "Wealth planning, data analytics, "
                                                                     "leadership and compliance programmes",
                                          "pct_covered": 21},
                            "workers": {"programmes": 0, "pct_covered": 0}}}
    a["P1.E2a"] = NA("No fines, penalties, settlements or compounding fees during the year.")
    a["P1.E2b"] = NA("No imprisonment or punishment during the year.")
    a["P1.E3"] = {"rows": [], "none": True}
    a["P1.E4"] = {"answer": "Yes", "details": "Anti Bribery, Corruption, Gifts and Entertainment policy.",
                  "link": "https://www.nuvama.com/investor-relations/corporate-governance/"}
    a["P1.E5"] = {"cells": {k: _cypy(0, 0) for k in ("directors", "kmps", "employees", "workers")}}
    a["P1.E6"] = {"cells": {"directors": {"cy_number": 0, "py_number": 0},
                            "kmps": {"cy_number": 0, "py_number": 0}}}
    a["P1.E7"] = {"text": "No such incidents have been reported."}
    a["P1.E8"] = NA("Not meaningful for a financial-services entity with no cost of goods procured.")
    a["P1.E9"] = {"cells": {k: _cypy(0, 0) for k in (
        "p_trading_pct", "p_trading_count", "p_top10_pct", "s_dealer_pct", "s_dealer_count", "s_top10_pct",
        "r_purchases", "r_sales", "r_loans", "r_investments")}}

    # ---- Principle 2 -------------------------------------------------------
    a["P2.E1"] = NA("No R&D or capex specifically targeting environmental and social impacts.")
    a["P2.E2a"] = {"answer": "No"}
    a["P2.E3"] = NA("The entity provides financial services and does not sell physical products.")
    a["P2.E4"] = {"answer": "No", "details": "EPR does not apply due to the nature of operations."}

    # ---- Principle 3 -------------------------------------------------------
    a["P3.E1a"] = {"cells": {
        "perm_male": {"total": 2551, "health": 2551, "accident": 2551, "maternity": 0, "paternity": 114, "daycare": 0},
        "perm_female": {"total": 863, "health": 863, "accident": 863, "maternity": 29, "paternity": 0, "daycare": 0},
        "other_male": {"total": 406, "health": 311, "accident": 311, "maternity": 0, "paternity": 0, "daycare": 0},
        "other_female": {"total": 83, "health": 52, "accident": 52, "maternity": 1, "paternity": 0, "daycare": 0}}}
    a["P3.E1b"] = NA("The entity has no workers; all personnel are employees.")
    a["P3.E1c"] = {"cells": {"cost": _cy(225913000)}}
    a["P3.E2"] = {"cells": {
        "pf": {"cy_emp_pct": 97, "cy_deposited": "Yes", "py_emp_pct": 96, "py_deposited": "Yes"},
        "gratuity": {"cy_emp_pct": 100, "cy_deposited": "Yes", "py_emp_pct": 100, "py_deposited": "Yes"},
        "esi": {"cy_emp_pct": 1, "cy_deposited": "Yes", "py_emp_pct": 1, "py_deposited": "Yes"},
        "others": {"cy_emp_pct": 3, "cy_deposited": "Yes", "py_emp_pct": 3, "py_deposited": "Yes"}}}
    a["P3.E3"] = {"text": "All offices are equipped with elevators for ease of movement of differently-abled "
                          "individuals."}
    a["P3.E4"] = {"answer": "Yes", "details": "Equal Opportunity Policy available to employees on the intranet."}
    a["P3.E5"] = {"cells": {"male": {"emp_took": 114, "emp_returned": 114, "emp_retained": 0},
                            "female": {"emp_took": 29, "emp_returned": 27, "emp_retained": 0}}}
    a["P3.E6"] = {"cells": {"perm_employees": {"available": "Yes", "details": "Equal Opportunity Policy and "
                                                                            "Disciplinary Mechanism"},
                            "other_employees": {"available": "Yes"},
                            "perm_workers": {"available": "NA"}, "other_workers": {"available": "NA"}}}
    a["P3.E7"] = {"cells": {"emp_male": {"cy_total": 2551, "cy_members": 0},
                            "emp_female": {"cy_total": 863, "cy_members": 0}}}
    a["P3.E8"] = {"cells": {"emp_male": {"cy_total": 2957, "cy_hs": 1940, "cy_skill": 2500},
                            "emp_female": {"cy_total": 946, "cy_hs": 646, "cy_skill": 792}}}
    # Correct denominators (the filing divided male reviews by permanent male employees -> 112%).
    a["P3.E9"] = {"cells": {"emp_male": {"cy_total": 2957, "cy_reviewed": 2862},
                            "emp_female": {"cy_total": 946, "cy_reviewed": 915}}}
    a["P3.E10a"] = {"answer": "Yes", "details": "Fire safety drills, ergonomic equipment, hygiene measures and "
                                                "wellness training."}
    a["P3.E10b"] = {"text": "Periodic safety communications, fire drills and assessment of 90% of offices."}
    a["P3.E10c"] = {"answer": "NA"}
    a["P3.E10d"] = {"answer": "Yes", "details": "Nuvama Cares: digital healthcare platforms, wellness camps and "
                                                "annual health check-ups."}
    a["P3.E11"] = {"cells": {k: _cypy(0, 0) for k in (
        "lti_emp", "lti_wrk", "recordable_emp", "recordable_wrk", "fatal_emp", "fatal_wrk", "highcons_emp",
        "highcons_wrk")} | {"hours_emp": _cypy(7806000, 7500000)}}
    a["P3.E12"] = {"text": "POSH, whistle-blower and disciplinary policies; mock fire drills; on-site doctor room."}
    a["P3.E13"] = {"cells": {k: {"cy_filed": 0, "cy_pending": 0, "py_filed": 0, "py_pending": 0}
                             for k in ("working_conditions", "health_safety")}}
    a["P3.E14"] = {"cells": {"health_safety": {"pct": 90}, "working_conditions": {"pct": 90}}}
    a["P3.E15"] = {"text": "No incidences of breach were reported."}

    # ---- Principle 4 -------------------------------------------------------
    a["P4.E1"] = {"text": "Stakeholders are identified and mapped according to their role, relevance and "
                          "influence."}
    a["P4.E2"] = {"rows": [
        {"group": "Employees", "vulnerable": "No", "channels": "Emails, meetings, surveys, town halls",
         "frequency": "Continuous", "purpose": "Career growth, learning and development"},
        {"group": "Communities", "vulnerable": "Yes", "channels": "CSR partners",
         "frequency": "As per program requirements", "purpose": "Sustainable development of the community"}]}

    # ---- Principle 5 -------------------------------------------------------
    a["P5.E1"] = {"cells": {"emp_perm": {"cy_total": 3414, "cy_covered": 2586, "py_covered": 518},
                            "emp_other": {"cy_total": 489, "cy_covered": 385, "py_covered": 78}}}
    a["P5.E2"] = {"cells": {
        "emp_perm_male": {"cy_total": 2551, "cy_equal": 0, "cy_more": 2535},
        "emp_perm_female": {"cy_total": 863, "cy_equal": 0, "cy_more": 857},
        "emp_other_male": {"cy_total": 406, "cy_equal": 0, "cy_more": 311},
        "emp_other_female": {"cy_total": 83, "cy_equal": 0, "cy_more": 52}}}
    a["P5.E3a"] = {"cells": {"bod": {"male_n": 8, "male_median": 3470000, "female_n": 1, "female_median": 3190000},
                             "kmp": {"male_n": 3, "male_median": 70014457.5, "female_n": 1, "female_median": 7823340},
                             "employees": {"male_n": 211, "male_median": 2615177.5, "female_n": 67,
                                           "female_median": 1328150.5},
                             "workers": {"male_n": 0, "female_n": 0}}}
    a["P5.E3b"] = {"cells": {"female_wages": _cy(1502063118), "total_wages": _cy(10000000000)}}
    a["P5.E4"] = {"answer": "Yes", "details": "Grievance committee"}
    a["P5.E5"] = {"text": "POSH, whistleblower, disciplinary and equal opportunity frameworks with an independent "
                          "redressal process."}
    a["P5.E6"] = {"cells": {"sexual_harassment": {"cy_filed": 1, "cy_pending": 1, "py_filed": 2, "py_pending": 0}}
                  | {k: {"cy_filed": 0, "cy_pending": 0, "py_filed": 0, "py_pending": 0}
                     for k in ("discrimination", "child_labour", "forced_labour", "wages", "other")}}
    a["P5.E7"] = {"cells": {"total": _cypy(1, 2), "female_headcount": _cypy(946, 900), "upheld": _cypy(1, 2)}}
    a["P5.E8"] = {"text": "No-retaliation policy; role or manager change options and close pulse checks."}
    a["P5.E9"] = {"answer": "No", "details": "Human-rights risks are not material to the business operations."}
    a["P5.E10"] = {"cells": {k: {"pct": 0} for k in ("child_labour", "forced_labour", "sexual_harassment",
                                                      "discrimination", "wages", "others")}}
    a["P5.E11"] = {"text": "No assessments raised significant risks."}

    # ---- Principle 6 -------------------------------------------------------
    a["P6.DEN"] = {"cells": {"revenue": _cypy(41693400000, 38000000000), "ppp_factor": _cypy(20.66, 20.3)},
                   "note": "PPP factor: IMF World Economic Outlook implied PPP conversion rate (illustrative)."}
    a["P6.E1"] = {"cells": {"ren_elec": _cypy(4192242.84, 2695950), "ren_fuel": _cypy(0, 0),
                            "ren_other": _cypy(0, 0), "non_elec": _cypy(5795394.76, 7667530),
                            "non_fuel": _cypy(0, 0), "non_other": _cypy(0, 0)}}
    a["P6.E2"] = {"answer": "No", "details": "Operations are outside the scope of the PAT scheme."}
    a["P6.E3"] = {"cells": {"surface": _cypy(0, 0), "ground": _cypy(0, 0), "third_party": _cypy(0, 0),
                            "sea": _cypy(0, 0), "others": _cypy(20222.02, 44188),
                            "consumption": _cypy(20222.02, 44188)}}
    a["P6.E4"] = NA("Water is discharged to municipal sewers through building management; not separately metered.")
    a["P6.E5"] = {"answer": "NA"}
    a["P6.E6"] = NA("No process air emissions from office operations.")
    a["P6.E7"] = {"cells": {"scope1": _cypy(0, 0), "scope2": _cypy(1170.35, 1548.42)}}
    a["P6.E8"] = {"answer": "No", "details": "Indirect reductions through efficient lighting and renewable power."}
    a["P6.E9"] = {"cells": {"plastic": _cypy(0, 0), "ewaste": _cypy(0.722, 0.34), "biomedical": _cypy(0, 0),
                            "cnd": _cypy(0, 0), "battery": _cypy(0, 0), "radioactive": _cypy(0, 0),
                            "other_haz": _cypy(0, 0), "other_nonhaz": _cypy(217.729, 298),
                            "recycled": _cypy(0.722, 0), "reused": _cypy(0, 0), "other_recovery": _cypy(0, 0),
                            "incineration": _cypy(0, 0), "landfilling": _cypy(0, 0),
                            "other_disposal": _cypy(0, 0)}}
    a["P6.E10"] = {"text": "Waste segregation and recycling protocols; responsible e-waste disposal."}
    a["P6.E11"] = {"rows": [], "none": True}
    a["P6.E12"] = {"rows": [], "none": True}
    a["P6.E13"] = {"answer": "Yes"}

    # ---- Principle 7 -------------------------------------------------------
    a["P7.E1a"] = {"value": 8}
    a["P7.E1b"] = {"rows": [{"name": n, "reach": "National"} for n in (
        "Bombay Stock Exchange Brokers' Forum (BBF)", "Association Of National Exchanges Members of India (ANMI)",
        "Commodity Participants Association of India (CPAI)", "Association of Portfolio Managers in India (APMI)")]
        + [{"name": "Asia Securities Industry & Financial Markets Association (ASIFMA)", "reach": "International"}]}
    a["P7.E2"] = {"rows": [], "none": True}

    # ---- Principle 8 -------------------------------------------------------
    a["P8.E1"] = {"rows": [], "none": True}
    a["P8.E2"] = {"rows": [], "none": True}
    a["P8.E3"] = {"text": "Community grievances are channelled through CSR partners with established protocols."}
    a["P8.E4"] = {"cells": {"total_inputs": _cy(1000000000), "msme_inputs": _cy(462252685),
                            "india_inputs": _cy(999176902)}}
    a["P8.E5"] = {"cells": {"rural": _cy(0), "semi_urban": _cy(0), "urban": _cy(919120261),
                            "metro": _cy(9080879739)}}

    # ---- Principle 9 -------------------------------------------------------
    a["P9.E1"] = {"text": "Phone, e-mail, chat, branches, SEBI SCORES and an escalation matrix."}
    a["P9.E2"] = NA("Financial services; product labelling parameters do not apply.")
    a["P9.E3"] = {"cells": {k: {"cy_received": 0, "cy_pending": 0, "py_received": 0, "py_pending": 0}
                            for k in ("data_privacy", "advertising", "cyber_security", "essential_services",
                                      "restrictive", "unfair")}
                  | {"other": {"cy_received": 0, "cy_pending": 0, "py_received": 1155, "py_pending": 14}}}
    a["P9.E4"] = NA("No physical products; recalls do not apply.")
    a["P9.E5"] = {"answer": "Yes", "details": "Cybersecurity and data privacy policies in place.",
                  "link": "https://www.nuvama.com/wp-content/uploads/2023/09/Nuvama-Privacy-Statement-Revised-2023-vs-2.pdf"}
    a["P9.E6"] = {"text": "N/A"}
    a["P9.E7"] = {"cells": {"breaches": {"value": 0}, "pii_breaches": {"value": 0}}}
    a["P9.E7c"] = {"text": "No data breaches occurred."}

    # A few leadership indicators (voluntary)
    a["P6.L5"] = {"text": "Board-approved Business Continuity Plan and Disaster Recovery Plan with regular testing."}
    a["P9.L1"] = {"text": "Company website."}

    # ---- Bank v3: figures come from the data sheets ------------------------
    # Questions built entirely from the sheets are not typed at all; in mixed
    # tables only the typed cells are kept (the engine drops the rest).
    for qid in list(a):
        if QUESTIONS_BY_ID[qid].get("source") == "data":
            a.pop(qid)
    a["A.25"] = {"cells": {k: {"mechanism": "Yes"} for k in ("communities", "investors", "shareholders",
                                                                 "employees", "customers", "value_chain")}
                 | {"other": {"mechanism": "NA"}}}
    a["P3.E2"] = {"cells": {k: {"cy_deposited": "Yes", "py_deposited": "Yes"} for k in ("pf", "gratuity", "esi",
                                                                                         "others")}}
    a["P5.E7"] = {"cells": {"upheld": _cypy(1, 2)}}
    a["P6.E6"] = {"cells": {k: {"unit": u} for k, u in (("nox", "ppm"), ("sox", "ppm"), ("pm", "mg/m3"),
                                                        ("pop", "mg/m3"), ("voc", "mg/m3"), ("hap", "mg/m3"))}}
    return a


# ---------------------------------------------------------------------------
# The same filing's figures as data-sheet metrics (bank v3)
# ---------------------------------------------------------------------------

def sheet_metrics() -> dict:
    """{"cy": {metric: value}, "py": {...}} in the keys of
    app/brsr/sheet_sources.SOURCES. Where the filing gives only a rate, the
    inputs reproduce it (turnover 23.38%: start 2,500, hires 641, end 2,551)."""
    from app.brsr.sheet_sources import SOURCES
    cy = {k: 0 for k in SOURCES}
    cy.update({
        "emp_perm_m": 2551, "emp_perm_f": 863, "emp_oth_m": 406, "emp_oth_f": 83,
        "da_emp_perm_m": 3,
        "emp_perm_m_start": 2500, "emp_perm_f_start": 840, "emp_perm_m_end": 2551, "emp_perm_f_end": 863,
        "hires_emp_perm_m": 641, "hires_emp_perm_f": 248,
        "bod_m": 8, "bod_f": 1, "kmp_m": 3, "kmp_f": 1,
        "wb_health_emp_perm_m": 2551, "wb_health_emp_perm_f": 863, "wb_accident_emp_perm_m": 2551,
        "wb_accident_emp_perm_f": 863, "wb_maternity_emp_perm_f": 29, "wb_paternity_emp_perm_m": 114,
        "wb_health_emp_oth_m": 311, "wb_health_emp_oth_f": 52, "wb_accident_emp_oth_m": 311,
        "wb_accident_emp_oth_f": 52, "wb_maternity_emp_oth_f": 1,
        "wb_cost": 225913000, "ret_pf_emp": 3786, "ret_gratuity_emp": 3903, "ret_esi_emp": 39, "ret_other_emp": 117,
        "pl_emp_took_m": 114, "pl_emp_returned_m": 114, "pl_emp_retained_m": 108,
        "pl_emp_took_f": 29, "pl_emp_returned_f": 27, "pl_emp_retained_f": 24,
        "tr_hs_emp_m": 1940, "tr_hs_emp_f": 646, "tr_skill_emp_m": 2500, "tr_skill_emp_f": 792,
        "tr_hr_emp_perm": 2586, "tr_hr_emp_oth": 385,
        # Correct denominators: the filing divided 2,862 by 2,551 and printed 112.19%.
        "rev_emp_m": 2862, "rev_emp_f": 915,
        "mw_more_emp_perm_m": 2535, "mw_more_emp_perm_f": 857, "mw_more_emp_oth_m": 311, "mw_more_emp_oth_f": 52,
        "med_bod_m": 3470000, "med_bod_f": 3190000, "med_kmp_m": 70014457.5, "med_kmp_f": 7823340,
        "med_emp_n_m": 211, "med_emp_m": 2615177.5, "med_emp_n_f": 67, "med_emp_f": 1328150.5,
        "wages_total": 10000000000, "wages_female": 1502063118, "wages_urban": 919120261, "wages_metro": 9080879739,
        "hours_emp": 7806000, "cmp_sh_filed": 1, "cmp_sh_pending": 1,
        "elec_ren_kwh": 4192242.84, "elec_nonren_kwh": 5795394.76,
        "w_other": 20222.02,
        "ws_ewaste": 0.722, "ws_other_nonhaz": 217.729, "ws_recycled": 0.722,
        "revenue": 41693400000, "ppp_factor": 20.66, "payables": 1200000000, "cogs": 9500000000,
        "inputs_msme": 462252685, "inputs_india": 999176902, "inputs_total": 1000000000,
        "plants_national": 0, "offices_national": 95, "offices_international": 6, "countries_served": 6,
        "own_assessed_hs": 91, "own_assessed_wc": 91, "suppliers": 40, "vc_awareness_programmes": 2,
        "vc_awareness_covered": 20,
    })
    py = {k: 0 for k in SOURCES}
    py.update({"emp_perm_m": 2500, "emp_perm_f": 840, "emp_oth_m": 380, "emp_oth_f": 80,
               "emp_perm_m_start": 2400, "emp_perm_f_start": 800, "emp_perm_m_end": 2500, "emp_perm_f_end": 840,
               "hours_emp": 7500000, "cmp_sh_filed": 2, "cc_other_rec": 1155, "cc_other_pending": 14,
               "elec_ren_kwh": 2695950, "elec_nonren_kwh": 7667530, "w_other": 44188,
               "ws_ewaste": 0.34, "ws_other_nonhaz": 298, "revenue": 38000000000, "ppp_factor": 20.3,
               "offices_national": 90, "offices_international": 6, "countries_served": 6})
    return {"cy": cy, "py": py}


def _inverse_rows(metrics: dict, start, end) -> dict:
    """One row per sheet whose aggregation gives back ``metrics`` (each value
    goes into the first column of its source, divided by its scale)."""
    from app.brsr.sheet_sources import SOURCES
    rows: dict[str, dict] = {}

    def put(sheet: str, cat, col: str, value: float):
        key = (sheet, cat)
        r = rows.setdefault(key, {"sheet": sheet, "start": start, "end": end, "location": "Mumbai",
                                  "attrs": {"location": "Mumbai", **({"Category": cat} if cat else {})},
                                  "values": {}, "version_id": 1, "row": 2 + len(rows)})
        r["values"][col] = r["values"].get(col, 0) + value

    shared = {"inputs_total", "inputs_india"}  # share the within-India columns
    for key, src in SOURCES.items():
        v = metrics.get(key) or 0
        if key in shared:
            continue
        p = src.parts[0]
        col = p.cols[0]
        name, factor = (col, 1.0) if isinstance(col, str) else col
        val = v / p.scale / factor if v else 0
        put(p.sheet, p.cat, name, val)
        for other in src.parts[1:]:  # make sure every sheet a metric reads exists
            c = other.cols[0]
            put(other.sheet, other.cat, c if isinstance(c, str) else c[0], 0)
    put("supply_chain_admin", None, "Input_Material_Sourced_Within_India_INR", metrics.get("inputs_india") or 0)
    put("supply_chain_admin", None, "Input_Material_Sourced_Outside_India_INR",
        (metrics.get("inputs_total") or 0) - (metrics.get("inputs_india") or 0))
    out: dict[str, list] = {}
    for (sheet, _cat), r in rows.items():
        out.setdefault(sheet, []).append(r)
    return out


def sheet_rows(fy: str = "2024-25") -> dict:
    """{sheet: [row]} for the current and previous financial year."""
    from datetime import date
    y = int(fy[:4])
    m = sheet_metrics()
    cy = _inverse_rows(m["cy"], date(y + 1, 3, 1), date(y + 1, 3, 31))
    py = _inverse_rows(m["py"], date(y, 3, 1), date(y, 3, 31))
    for sheet, rs in py.items():
        cy.setdefault(sheet, []).extend(rs)
    for rs in cy.values():  # one file per sheet: row numbers must be unique in it
        for i, r in enumerate(rs, start=2):
            r["row"] = i
    return cy


def seed_sheet_data(session, company, user, fy: str = "2024-25") -> list:
    """Approved data-sheet datasets for ``company`` with the fixture figures,
    as if uploaded, reviewed and extracted. Returns the dataset ids."""
    from datetime import date
    from sqlmodel import select
    from app.datasheets.seed import ensure_datasheet_types
    from app.datasheets.service import upload_type_code
    from app.models.dataset import Dataset, DatasetFile, DatasetVersion
    from app.models.kpi import KpiValue
    from app.models.upload_type import UploadType
    ensure_datasheet_types(session)
    types = {t.code: t for t in session.exec(select(UploadType)).all()}
    ids = []
    for sheet, rows in sheet_rows(fy).items():
        ds = Dataset(company_id=company.id, upload_type_id=types[upload_type_code(sheet)].id,
                     reporting_period_start=date(int(fy[:4]) - 1, 4, 1), reporting_period_end=date(int(fy[:4]) + 1, 3, 31),
                     reporting_frequency="monthly", status="approved", created_by=user.id)
        session.add(ds)
        session.flush()
        v = DatasetVersion(dataset_id=ds.id, version_number=1, status="approved", uploaded_by=user.id)
        session.add(v)
        session.flush()
        f = DatasetFile(dataset_version_id=v.id, role="data", storage_key=f"test/{sheet}.xlsx",
                        original_filename=f"{sheet}.xlsx", mime_type="application/octet-stream", size_bytes=1,
                        sha256_checksum="0" * 64, uploaded_by=user.id)
        session.add(f)
        session.flush()
        ds.current_version_id = v.id
        for r in rows:
            for col, val in r["values"].items():
                session.add(KpiValue(dataset_id=ds.id, dataset_version_id=v.id, source_file_id=f.id,
                                     source_row_number=r["row"], company_id=company.id,
                                     kpi_code=f"{sheet}.{col}", value=val, unit="", attributes=r["attrs"],
                                     reporting_period_start=r["start"], reporting_period_end=r["end"]))
        ids.append(ds.id)
    session.commit()
    return ids


def with_sheet_metrics(answers: dict, fy: str = "2024-25") -> dict:
    """``answers`` plus the sheet metrics the service would inject, computed
    by the real aggregation from the fixture rows."""
    from app.brsr.sheet_metrics import SHEET_KEY, coverage_for, fy_bounds, metrics_for, tables_for
    rows = sheet_rows(fy)
    s, e = fy_bounds(fy)
    cy = {k: [r for r in v if r["start"] >= s] for k, v in rows.items()}
    py = {k: [r for r in v if r["end"] < s] for k, v in rows.items()}
    out = dict(answers)
    out[SHEET_KEY] = {"fy": fy, "cy": metrics_for(cy), "py": metrics_for(py), "cy_tables": tables_for(cy),
                      "py_tables": tables_for(py), "cy_coverage": coverage_for(cy, s, e),
                      "py_coverage": coverage_for(py, s, e), "basis": [], "warnings": []}
    return out
