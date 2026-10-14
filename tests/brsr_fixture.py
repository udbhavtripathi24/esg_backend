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
        "paid_up_capital": 359743580, "contact_name": "Rashmi Nagori", "contact_designation": "Head ESG & CSR",
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
    return a
