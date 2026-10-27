"""Annual data — the short quantitative form behind the BRSR.

Why this exists
---------------
SEBI's BRSR asks for ~40 large quantitative tables (headcount by gender,
benefits coverage, training, wages, complaints, energy, water, waste…),
each with a current-year and previous-year column. Typing those tables
directly means entering the same headcount eight times and re-entering
last year's figures every year.

Instead, each department fills a short **annual form** — one number per
line, entered once — and the platform builds every quantitative BRSR
table from it:

* the same headcount feeds A.20, P3.1, P3.7, P3.8, P3.9, P5.1, P5.2, P5.7;
* the previous-year column comes from last year's approved annual data
  (carried forward automatically), so it is never typed twice;
* percentages, totals and intensities are calculated (app/brsr/formula.py).

Areas are owned by departments, following the engagement's
department → KPI mapping (Admin: energy/water/waste/health & safety;
HR: diversity/well-being/training/wages/complaints; Finance: finance;
Secretarial: board; Legal / client-experience teams: grievances).

How derivation works
--------------------
``DERIVE`` maps BRSR table cells to expressions over metric keys:
    "A.20a": {"employees_perm.male": "emp_perm_m", ...}
The key is ``<row>.<column base>``. For financial-year columns
(``cy_x`` / ``py_x``) the base is ``x`` and the expression is evaluated
against that year's annual data; columns without a year use the current
year. Cells not listed stay as ordinary inputs (remarks, Yes/No columns,
the "prior to previous year" column), so a table can be partly derived.
"""
from __future__ import annotations

from typing import Any

YESNO = ["Yes", "No"]


def m(key: str, label: str, kind: str = "count", *, unit: str | None = None, required: bool = True,
      lte: str | None = None, help: str | None = None, options: list[str] | None = None) -> dict:
    f: dict[str, Any] = {"key": key, "label": label, "kind": kind, "required": required}
    for k, v in (("unit", unit), ("lte", lte), ("help", help), ("options", options)):
        if v is not None:
            f[k] = v
    return f


def group(title: str, fields: list[dict], *, show_if: str | None = None, note: str | None = None) -> dict:
    g: dict[str, Any] = {"title": title, "fields": fields}
    if show_if:
        g["show_if"] = show_if
    if note:
        g["note"] = note
    return g


# Expressions reused below
EMP_M = "emp_perm_m + emp_oth_m"
EMP_F = "emp_perm_f + emp_oth_f"
EMP_ALL = "emp_perm_m + emp_perm_f + emp_oth_m + emp_oth_f"
WRK_M = "wrk_perm_m + wrk_oth_m"
WRK_F = "wrk_perm_f + wrk_oth_f"
WRK_ALL = "wrk_perm_m + wrk_perm_f + wrk_oth_m + wrk_oth_f"
HAS_WORKERS = WRK_ALL  # groups about workers are shown only when the entity has workers

AREAS: list[dict] = [
    {"code": "WF", "title": "Workforce & diversity", "departments": ["HR"],
     "intro": "Headcount as at the last day of the financial year, including differently abled people. "
              "Entered once here, it feeds every BRSR table that needs a headcount.",
     "groups": [
         group("Employees", [
             m("emp_perm_m", "Permanent employees — male"), m("emp_perm_f", "Permanent employees — female"),
             m("emp_oth_m", "Other than permanent employees — male"),
             m("emp_oth_f", "Other than permanent employees — female"),
         ]),
         group("Workers", [
             m("wrk_perm_m", "Permanent workers — male"), m("wrk_perm_f", "Permanent workers — female"),
             m("wrk_oth_m", "Other than permanent workers — male"),
             m("wrk_oth_f", "Other than permanent workers — female"),
         ], note="Enter 0 if the entity has no workers. Worker questions elsewhere then disappear."),
         group("Differently abled employees (included in the numbers above)", [
             m("da_emp_perm_m", "Permanent — male", lte="emp_perm_m"),
             m("da_emp_perm_f", "Permanent — female", lte="emp_perm_f"),
             m("da_emp_oth_m", "Other than permanent — male", lte="emp_oth_m"),
             m("da_emp_oth_f", "Other than permanent — female", lte="emp_oth_f"),
         ]),
         group("Differently abled workers (included in the numbers above)", [
             m("da_wrk_perm_m", "Permanent — male", lte="wrk_perm_m"),
             m("da_wrk_perm_f", "Permanent — female", lte="wrk_perm_f"),
             m("da_wrk_oth_m", "Other than permanent — male", lte="wrk_oth_m"),
             m("da_wrk_oth_f", "Other than permanent — female", lte="wrk_oth_f"),
         ], show_if=HAS_WORKERS),
         group("Permanent employees — for the turnover rate", [
             m("emp_perm_m_start", "Male at the start of the year"),
             m("emp_perm_f_start", "Female at the start of the year"),
             m("left_emp_perm_m", "Male who left during the year"),
             m("left_emp_perm_f", "Female who left during the year"),
         ], note="Turnover rate = leavers ÷ average of start and end headcount × 100."),
         group("Permanent workers — for the turnover rate", [
             m("wrk_perm_m_start", "Male at the start of the year"),
             m("wrk_perm_f_start", "Female at the start of the year"),
             m("left_wrk_perm_m", "Male who left during the year"),
             m("left_wrk_perm_f", "Female who left during the year"),
         ], show_if=HAS_WORKERS),
     ]},

    {"code": "BD", "title": "Board & key managerial personnel", "departments": ["Secretarial"],
     "intro": "Composition as at the last day of the financial year.",
     "groups": [
         group("Board of Directors", [m("bod_m", "Male directors"), m("bod_f", "Female directors")]),
         group("Key Managerial Personnel", [m("kmp_m", "Male KMPs"), m("kmp_f", "Female KMPs")]),
     ]},

    {"code": "WB", "title": "Well-being & benefits", "departments": ["HR"],
     "intro": "Number of people covered at year end. Percentages are calculated against the headcount "
              "in Workforce & diversity.",
     "groups": [
         group("Permanent employees covered by", [
             m("wb_health_emp_perm_m", "Health insurance — male", lte="emp_perm_m"),
             m("wb_health_emp_perm_f", "Health insurance — female", lte="emp_perm_f"),
             m("wb_accident_emp_perm_m", "Accident insurance — male", lte="emp_perm_m"),
             m("wb_accident_emp_perm_f", "Accident insurance — female", lte="emp_perm_f"),
             m("wb_maternity_emp_perm_f", "Maternity benefits — female", lte="emp_perm_f"),
             m("wb_paternity_emp_perm_m", "Paternity benefits — male", lte="emp_perm_m"),
             m("wb_daycare_emp_perm_m", "Day care facilities — male", lte="emp_perm_m"),
             m("wb_daycare_emp_perm_f", "Day care facilities — female", lte="emp_perm_f"),
         ]),
         group("Other than permanent employees covered by", [
             m("wb_health_emp_oth_m", "Health insurance — male", lte="emp_oth_m"),
             m("wb_health_emp_oth_f", "Health insurance — female", lte="emp_oth_f"),
             m("wb_accident_emp_oth_m", "Accident insurance — male", lte="emp_oth_m"),
             m("wb_accident_emp_oth_f", "Accident insurance — female", lte="emp_oth_f"),
             m("wb_maternity_emp_oth_f", "Maternity benefits — female", lte="emp_oth_f"),
             m("wb_paternity_emp_oth_m", "Paternity benefits — male", lte="emp_oth_m"),
             m("wb_daycare_emp_oth_m", "Day care facilities — male", lte="emp_oth_m"),
             m("wb_daycare_emp_oth_f", "Day care facilities — female", lte="emp_oth_f"),
         ]),
         group("Permanent workers covered by", [
             m("wb_health_wrk_perm_m", "Health insurance — male", lte="wrk_perm_m"),
             m("wb_health_wrk_perm_f", "Health insurance — female", lte="wrk_perm_f"),
             m("wb_accident_wrk_perm_m", "Accident insurance — male", lte="wrk_perm_m"),
             m("wb_accident_wrk_perm_f", "Accident insurance — female", lte="wrk_perm_f"),
             m("wb_maternity_wrk_perm_f", "Maternity benefits — female", lte="wrk_perm_f"),
             m("wb_paternity_wrk_perm_m", "Paternity benefits — male", lte="wrk_perm_m"),
             m("wb_daycare_wrk_perm_m", "Day care facilities — male", lte="wrk_perm_m"),
             m("wb_daycare_wrk_perm_f", "Day care facilities — female", lte="wrk_perm_f"),
         ], show_if=HAS_WORKERS),
         group("Other than permanent workers covered by", [
             m("wb_health_wrk_oth_m", "Health insurance — male", lte="wrk_oth_m"),
             m("wb_health_wrk_oth_f", "Health insurance — female", lte="wrk_oth_f"),
             m("wb_accident_wrk_oth_m", "Accident insurance — male", lte="wrk_oth_m"),
             m("wb_accident_wrk_oth_f", "Accident insurance — female", lte="wrk_oth_f"),
             m("wb_maternity_wrk_oth_f", "Maternity benefits — female", lte="wrk_oth_f"),
             m("wb_paternity_wrk_oth_m", "Paternity benefits — male", lte="wrk_oth_m"),
             m("wb_daycare_wrk_oth_m", "Day care facilities — male", lte="wrk_oth_m"),
             m("wb_daycare_wrk_oth_f", "Day care facilities — female", lte="wrk_oth_f"),
         ], show_if=HAS_WORKERS),
         group("Spend on well-being", [
             m("wb_cost", "Cost incurred on well-being measures (employees and workers)", "amount", unit="INR"),
         ]),
         group("Retirement benefits — employees covered", [
             m("ret_pf_emp", "Provident Fund", lte=EMP_ALL), m("ret_gratuity_emp", "Gratuity", lte=EMP_ALL),
             m("ret_esi_emp", "ESI", lte=EMP_ALL), m("ret_other_emp", "Other retirement benefits", lte=EMP_ALL),
         ]),
         group("Retirement benefits — workers covered", [
             m("ret_pf_wrk", "Provident Fund", lte=WRK_ALL), m("ret_gratuity_wrk", "Gratuity", lte=WRK_ALL),
             m("ret_esi_wrk", "ESI", lte=WRK_ALL), m("ret_other_wrk", "Other retirement benefits", lte=WRK_ALL),
         ], show_if=HAS_WORKERS),
         group("Parental leave — permanent employees", [
             m("pl_emp_took_m", "Male who took parental leave"),
             m("pl_emp_returned_m", "…of whom returned to work", lte="pl_emp_took_m"),
             m("pl_emp_retained_m", "…still employed 12 months after returning", lte="pl_emp_returned_m"),
             m("pl_emp_took_f", "Female who took parental leave"),
             m("pl_emp_returned_f", "…of whom returned to work", lte="pl_emp_took_f"),
             m("pl_emp_retained_f", "…still employed 12 months after returning", lte="pl_emp_returned_f"),
         ]),
         group("Parental leave — permanent workers", [
             m("pl_wrk_took_m", "Male who took parental leave"),
             m("pl_wrk_returned_m", "…of whom returned to work", lte="pl_wrk_took_m"),
             m("pl_wrk_retained_m", "…still employed 12 months after returning", lte="pl_wrk_returned_m"),
             m("pl_wrk_took_f", "Female who took parental leave"),
             m("pl_wrk_returned_f", "…of whom returned to work", lte="pl_wrk_took_f"),
             m("pl_wrk_retained_f", "…still employed 12 months after returning", lte="pl_wrk_returned_f"),
         ], show_if=HAS_WORKERS),
         group("Membership of associations or unions — permanent employees", [
             m("union_emp_perm_m", "Male members", lte="emp_perm_m"),
             m("union_emp_perm_f", "Female members", lte="emp_perm_f"),
         ]),
         group("Membership of associations or unions — permanent workers", [
             m("union_wrk_perm_m", "Male members", lte="wrk_perm_m"),
             m("union_wrk_perm_f", "Female members", lte="wrk_perm_f"),
         ], show_if=HAS_WORKERS),
     ]},

    {"code": "TR", "title": "Training & performance reviews", "departments": ["HR"],
     "intro": "Number of people (not sessions) during the financial year.",
     "groups": [
         group("Employees trained", [
             m("tr_hs_emp_m", "Health & safety — male", lte=EMP_M),
             m("tr_hs_emp_f", "Health & safety — female", lte=EMP_F),
             m("tr_skill_emp_m", "Skill upgradation — male", lte=EMP_M),
             m("tr_skill_emp_f", "Skill upgradation — female", lte=EMP_F),
             m("tr_hr_emp_perm", "Human rights — permanent", lte="emp_perm_m + emp_perm_f"),
             m("tr_hr_emp_oth", "Human rights — other than permanent", lte="emp_oth_m + emp_oth_f"),
         ]),
         group("Workers trained", [
             m("tr_hs_wrk_m", "Health & safety — male", lte=WRK_M),
             m("tr_hs_wrk_f", "Health & safety — female", lte=WRK_F),
             m("tr_skill_wrk_m", "Skill upgradation — male", lte=WRK_M),
             m("tr_skill_wrk_f", "Skill upgradation — female", lte=WRK_F),
             m("tr_hr_wrk_perm", "Human rights — permanent", lte="wrk_perm_m + wrk_perm_f"),
             m("tr_hr_wrk_oth", "Human rights — other than permanent", lte="wrk_oth_m + wrk_oth_f"),
         ], show_if=HAS_WORKERS),
         group("Performance and career development reviews", [
             m("rev_emp_m", "Employees reviewed — male", lte=EMP_M),
             m("rev_emp_f", "Employees reviewed — female", lte=EMP_F),
         ]),
         group("Performance and career development reviews — workers", [
             m("rev_wrk_m", "Workers reviewed — male", lte=WRK_M),
             m("rev_wrk_f", "Workers reviewed — female", lte=WRK_F),
         ], show_if=HAS_WORKERS),
     ]},

    {"code": "WG", "title": "Wages & remuneration", "departments": ["HR"],
     "intro": "Annual figures. Headcount totals come from Workforce & diversity.",
     "groups": [
         group("Employees paid equal to minimum wage", [
             m("mw_eq_emp_perm_m", "Permanent — male", lte="emp_perm_m"),
             m("mw_eq_emp_perm_f", "Permanent — female", lte="emp_perm_f"),
             m("mw_eq_emp_oth_m", "Other than permanent — male", lte="emp_oth_m"),
             m("mw_eq_emp_oth_f", "Other than permanent — female", lte="emp_oth_f"),
         ]),
         group("Employees paid more than minimum wage", [
             m("mw_more_emp_perm_m", "Permanent — male", lte="emp_perm_m - mw_eq_emp_perm_m"),
             m("mw_more_emp_perm_f", "Permanent — female", lte="emp_perm_f - mw_eq_emp_perm_f"),
             m("mw_more_emp_oth_m", "Other than permanent — male", lte="emp_oth_m - mw_eq_emp_oth_m"),
             m("mw_more_emp_oth_f", "Other than permanent — female", lte="emp_oth_f - mw_eq_emp_oth_f"),
         ]),
         group("Workers paid equal to minimum wage", [
             m("mw_eq_wrk_perm_m", "Permanent — male", lte="wrk_perm_m"),
             m("mw_eq_wrk_perm_f", "Permanent — female", lte="wrk_perm_f"),
             m("mw_eq_wrk_oth_m", "Other than permanent — male", lte="wrk_oth_m"),
             m("mw_eq_wrk_oth_f", "Other than permanent — female", lte="wrk_oth_f"),
         ], show_if=HAS_WORKERS),
         group("Workers paid more than minimum wage", [
             m("mw_more_wrk_perm_m", "Permanent — male", lte="wrk_perm_m - mw_eq_wrk_perm_m"),
             m("mw_more_wrk_perm_f", "Permanent — female", lte="wrk_perm_f - mw_eq_wrk_perm_f"),
             m("mw_more_wrk_oth_m", "Other than permanent — male", lte="wrk_oth_m - mw_eq_wrk_oth_m"),
             m("mw_more_wrk_oth_f", "Other than permanent — female", lte="wrk_oth_f - mw_eq_wrk_oth_f"),
         ], show_if=HAS_WORKERS),
         group("Median annual remuneration", [
             m("med_bod_m", "Board of Directors — male", "amount", unit="INR"),
             m("med_bod_f", "Board of Directors — female", "amount", unit="INR"),
             m("med_kmp_m", "Key Managerial Personnel — male", "amount", unit="INR"),
             m("med_kmp_f", "Key Managerial Personnel — female", "amount", unit="INR"),
             m("med_emp_n_m", "Employees other than BoD and KMP considered — male"),
             m("med_emp_m", "Employees other than BoD and KMP — male", "amount", unit="INR"),
             m("med_emp_n_f", "Employees other than BoD and KMP considered — female"),
             m("med_emp_f", "Employees other than BoD and KMP — female", "amount", unit="INR"),
         ]),
         group("Median annual remuneration — workers", [
             m("med_wrk_m", "Workers — male", "amount", unit="INR"),
             m("med_wrk_f", "Workers — female", "amount", unit="INR"),
         ], show_if=HAS_WORKERS),
         group("Gross wages", [
             m("wages_total", "Total gross wages paid", "amount", unit="INR"),
             m("wages_female", "Gross wages paid to females", "amount", unit="INR", lte="wages_total"),
         ]),
         group("Wages by place of employment", [
             m("wages_rural", "Rural", "amount", unit="INR"),
             m("wages_semi_urban", "Semi-urban", "amount", unit="INR"),
             m("wages_urban", "Urban", "amount", unit="INR"),
             m("wages_metro", "Metropolitan", "amount", unit="INR"),
         ], note="RBI place classification. Shares of the total are calculated for Principle 8."),
     ]},

    {"code": "HS", "title": "Health & safety", "departments": ["HR", "Admin"],
     "intro": "Incidents during the financial year.",
     "groups": [
         group("Employees", [
             m("lti_emp", "Lost-time injuries"),
             m("hours_emp", "Person-hours worked", "number"),
             m("recordable_emp", "Total recordable work-related injuries"),
             m("fatal_emp", "Fatalities"),
             m("highcons_emp", "High-consequence injuries or ill-health (excluding fatalities)"),
         ], note="LTIFR is calculated per one million person-hours."),
         group("Workers", [
             m("lti_wrk", "Lost-time injuries"),
             m("hours_wrk", "Person-hours worked", "number"),
             m("recordable_wrk", "Total recordable work-related injuries"),
             m("fatal_wrk", "Fatalities"),
             m("highcons_wrk", "High-consequence injuries or ill-health (excluding fatalities)"),
         ], show_if=HAS_WORKERS),
     ]},

    {"code": "HC", "title": "Workplace complaints", "departments": ["HR"],
     "intro": "Complaints made by employees and workers: filed during the year, and still pending at year end.",
     "groups": [
         group("Working conditions and health & safety", [
             m("cmp_wc_filed", "Working conditions — filed"),
             m("cmp_wc_pending", "Working conditions — pending"),
             m("cmp_hs_filed", "Health & safety — filed"),
             m("cmp_hs_pending", "Health & safety — pending"),
         ]),
         group("Human rights", [
             m("cmp_sh_filed", "Sexual harassment (POSH) — filed"),
             m("cmp_sh_pending", "Sexual harassment (POSH) — pending"),
             m("cmp_sh_upheld", "Sexual harassment (POSH) — upheld", lte="cmp_sh_filed"),
             m("cmp_disc_filed", "Discrimination at workplace — filed"),
             m("cmp_disc_pending", "Discrimination at workplace — pending"),
             m("cmp_child_filed", "Child labour — filed"),
             m("cmp_child_pending", "Child labour — pending"),
             m("cmp_forced_filed", "Forced / involuntary labour — filed"),
             m("cmp_forced_pending", "Forced / involuntary labour — pending"),
             m("cmp_wages_filed", "Wages — filed"),
             m("cmp_wages_pending", "Wages — pending"),
             m("cmp_otherhr_filed", "Other human rights issues — filed"),
             m("cmp_otherhr_pending", "Other human rights issues — pending"),
         ]),
     ]},

    {"code": "GR", "title": "Grievances, consumer complaints & ethics",
     "departments": ["Legal", "Client experience and Governance", "ISG", "CLC", "Compliance"],
     "intro": "Complaints received during the year and pending at year end.",
     "groups": [
         group("Grievances on the NGRBC principles, by stakeholder group", [
             m("ngrbc_comm_filed", "Communities — filed"), m("ngrbc_comm_pending", "Communities — pending"),
             m("ngrbc_inv_filed", "Investors (other than shareholders) — filed"),
             m("ngrbc_inv_pending", "Investors (other than shareholders) — pending"),
             m("ngrbc_sh_filed", "Shareholders — filed"), m("ngrbc_sh_pending", "Shareholders — pending"),
             m("ngrbc_emp_filed", "Employees and workers — filed"),
             m("ngrbc_emp_pending", "Employees and workers — pending"),
             m("ngrbc_cust_filed", "Customers — filed"), m("ngrbc_cust_pending", "Customers — pending"),
             m("ngrbc_vc_filed", "Value chain partners — filed"),
             m("ngrbc_vc_pending", "Value chain partners — pending"),
             m("ngrbc_other_filed", "Other — filed"), m("ngrbc_other_pending", "Other — pending"),
         ]),
         group("Consumer complaints", [
             m("cc_privacy_rec", "Data privacy — received"), m("cc_privacy_pending", "Data privacy — pending"),
             m("cc_adv_rec", "Advertising — received"), m("cc_adv_pending", "Advertising — pending"),
             m("cc_cyber_rec", "Cyber-security — received"), m("cc_cyber_pending", "Cyber-security — pending"),
             m("cc_ess_rec", "Delivery of essential services — received"),
             m("cc_ess_pending", "Delivery of essential services — pending"),
             m("cc_rtp_rec", "Restrictive trade practices — received"),
             m("cc_rtp_pending", "Restrictive trade practices — pending"),
             m("cc_utp_rec", "Unfair trade practices — received"),
             m("cc_utp_pending", "Unfair trade practices — pending"),
             m("cc_other_rec", "Other — received"), m("cc_other_pending", "Other — pending"),
         ]),
         group("Ethics", [
             m("coi_directors", "Conflict-of-interest complaints concerning Directors"),
             m("coi_kmps", "Conflict-of-interest complaints concerning KMPs"),
             m("disc_directors", "Directors facing disciplinary action for bribery / corruption"),
             m("disc_kmps", "KMPs facing disciplinary action for bribery / corruption"),
             m("disc_employees", "Employees facing disciplinary action for bribery / corruption"),
             m("disc_workers", "Workers facing disciplinary action for bribery / corruption"),
         ]),
         group("Data breaches", [
             m("breaches", "Instances of data breaches"),
             m("pii_breaches", "…of which involved customers' personally identifiable information",
               lte="breaches"),
         ]),
     ]},

    {"code": "EN", "title": "Energy & emissions", "departments": ["Admin", "Tech"],
     "intro": "Totals for the financial year across all locations. Electricity is entered in kWh as it "
              "appears on bills and converted to gigajoules (1 kWh = 0.0036 GJ).",
     "groups": [
         group("Electricity", [
             m("elec_ren_kwh", "Electricity from renewable sources", "number", unit="kWh"),
             m("elec_nonren_kwh", "Electricity from non-renewable sources (e.g. grid)", "number", unit="kWh"),
         ]),
         group("Fuel and other energy", [
             m("fuel_ren_gj", "Fuel from renewable sources", "number", unit="GJ"),
             m("fuel_nonren_gj", "Fuel from non-renewable sources (diesel, petrol, gas…)", "number", unit="GJ"),
             m("energy_other_ren_gj", "Other renewable energy (purchased heat, steam, cooling)", "number", unit="GJ"),
             m("energy_other_nonren_gj", "Other non-renewable energy (purchased heat, steam, cooling)", "number",
               unit="GJ"),
         ]),
         group("Greenhouse gas emissions", [
             m("scope1", "Scope 1 emissions", "number", unit="tCO2e"),
             m("scope2", "Scope 2 emissions", "number", unit="tCO2e"),
             m("scope3", "Scope 3 emissions (optional — leadership indicator)", "number", unit="tCO2e",
               required=False),
         ]),
     ]},

    {"code": "WA", "title": "Water", "departments": ["Admin"],
     "intro": "Volumes for the financial year, in kilolitres.",
     "groups": [
         group("Water withdrawn, by source", [
             m("w_surface", "Surface water", "number", unit="kL"),
             m("w_ground", "Groundwater", "number", unit="kL"),
             m("w_third_party", "Third-party water (e.g. municipal supply, tankers)", "number", unit="kL"),
             m("w_sea", "Seawater / desalinated water", "number", unit="kL"),
             m("w_other", "Other sources", "number", unit="kL"),
         ]),
         group("Water consumed", [
             m("w_consumption", "Total water consumption", "number", unit="kL",
               lte="w_surface + w_ground + w_third_party + w_sea + w_other"),
         ]),
         group("Water discharged", [
             m("wd_surface_none", "To surface water — no treatment", "number", unit="kL"),
             m("wd_surface_treated", "To surface water — treated", "number", unit="kL"),
             m("wd_ground_none", "To groundwater — no treatment", "number", unit="kL"),
             m("wd_ground_treated", "To groundwater — treated", "number", unit="kL"),
             m("wd_sea_none", "To seawater — no treatment", "number", unit="kL"),
             m("wd_sea_treated", "To seawater — treated", "number", unit="kL"),
             m("wd_third_none", "Sent to third parties — no treatment", "number", unit="kL"),
             m("wd_third_treated", "Sent to third parties — treated", "number", unit="kL"),
             m("wd_other_none", "Other — no treatment", "number", unit="kL"),
             m("wd_other_treated", "Other — treated", "number", unit="kL"),
         ], note="Enter 0 where there is no discharge to that destination."),
     ]},

    {"code": "WS", "title": "Waste", "departments": ["Admin", "Tech"],
     "intro": "Quantities for the financial year, in metric tonnes.",
     "groups": [
         group("Waste generated", [
             m("ws_plastic", "Plastic waste", "number", unit="MT"),
             m("ws_ewaste", "E-waste", "number", unit="MT"),
             m("ws_biomedical", "Bio-medical waste", "number", unit="MT"),
             m("ws_cnd", "Construction and demolition waste", "number", unit="MT"),
             m("ws_battery", "Battery waste", "number", unit="MT"),
             m("ws_radioactive", "Radioactive waste", "number", unit="MT"),
             m("ws_other_haz", "Other hazardous waste", "number", unit="MT"),
             m("ws_other_nonhaz", "Other non-hazardous waste", "number", unit="MT"),
         ]),
         group("Waste recovered", [
             m("ws_recycled", "Recycled", "number", unit="MT"),
             m("ws_reused", "Re-used", "number", unit="MT"),
             m("ws_other_recovery", "Other recovery operations", "number", unit="MT"),
         ]),
         group("Waste disposed", [
             m("ws_incineration", "Incineration", "number", unit="MT"),
             m("ws_landfill", "Landfilling", "number", unit="MT"),
             m("ws_other_disposal", "Other disposal operations", "number", unit="MT"),
         ]),
     ]},

    {"code": "FN", "title": "Finance", "departments": ["Finance"],
     "intro": "From the audited financial statements for the year.",
     "groups": [
         group("Revenue and size", [
             m("revenue", "Revenue from operations", "amount", unit="INR",
               help="Used for every 'per rupee of turnover' intensity in the report."),
             m("turnover", "Turnover", "amount", unit="INR"),
             m("net_worth", "Net worth", "amount", unit="INR"),
             m("exports", "Export revenue", "amount", unit="INR", lte="turnover"),
             m("csr_applicable", "Is CSR applicable under section 135 of the Companies Act, 2013?", "yesno",
               options=YESNO),
         ]),
         group("Intensity factors", [
             m("ppp_factor", "PPP conversion factor (IMF implied PPP rate for the year)", "number",
               help="IMF World Economic Outlook, 'implied PPP conversion rate' for India."),
             m("physical_output", "Physical output (optional, for manufacturing entities)", "number",
               required=False),
         ]),
         group("Payables and sourcing", [
             m("payables", "Accounts payable", "amount", unit="INR"),
             m("cogs", "Cost of goods / services procured", "amount", unit="INR"),
             m("inputs_total", "Total input material procured", "amount", unit="INR"),
             m("inputs_msme", "…sourced directly from MSMEs / small producers", "amount", unit="INR",
               lte="inputs_total"),
             m("inputs_india", "…sourced directly from within India", "amount", unit="INR", lte="inputs_total"),
         ]),
     ]},

    {"code": "OP", "title": "Operations & markets", "departments": ["Admin"],
     "intro": "Locations and markets as at the end of the financial year.",
     "groups": [
         group("Plants and offices", [
             m("plants_national", "Plants in India"), m("offices_national", "Offices in India"),
             m("plants_international", "Plants outside India"), m("offices_international", "Offices outside India"),
         ]),
         group("Markets served", [
             m("states_served", "Number of Indian states served"),
             m("countries_served", "Number of countries served"),
         ]),
     ]},
]

AREA_CODES = [a["code"] for a in AREAS]
METRICS: dict[str, dict] = {}
METRIC_AREA: dict[str, str] = {}
METRIC_GROUP_SHOW_IF: dict[str, str] = {}
for _a in AREAS:
    for _g in _a["groups"]:
        for _f in _g["fields"]:
            if _f["key"] in METRICS:
                raise ValueError(f"Duplicate metric {_f['key']}")
            METRICS[_f["key"]] = _f
            METRIC_AREA[_f["key"]] = _a["code"]
            if _g.get("show_if"):
                METRIC_GROUP_SHOW_IF[_f["key"]] = _g["show_if"]

KWH_TO_GJ = "0.0036"

# ---------------------------------------------------------------------------
# BRSR table cells built from the annual data.
# "<row>.<column base>": expression over metric keys
# ---------------------------------------------------------------------------


def _gender_rows(prefix_emp: str, prefix_wrk: str) -> dict:
    """Rows employees_perm / employees_other / workers_perm / workers_other."""
    out = {}
    for row, (m_, f_) in {
        "employees_perm": (f"{prefix_emp}emp_perm_m", f"{prefix_emp}emp_perm_f"),
        "employees_other": (f"{prefix_emp}emp_oth_m", f"{prefix_emp}emp_oth_f"),
        "workers_perm": (f"{prefix_wrk}wrk_perm_m", f"{prefix_wrk}wrk_perm_f"),
        "workers_other": (f"{prefix_wrk}wrk_oth_m", f"{prefix_wrk}wrk_oth_f"),
    }.items():
        out[f"{row}.total"] = f"{m_} + {f_}"
        out[f"{row}.male"] = m_
        out[f"{row}.female"] = f_
    return out


def _wellbeing(kind: str) -> dict:
    out = {}
    for row, cat in (("perm_male", f"{kind}_perm"), ("perm_female", f"{kind}_perm"),
                     ("other_male", f"{kind}_oth"), ("other_female", f"{kind}_oth")):
        g = "m" if row.endswith("male") and not row.endswith("female") else "f"
        out[f"{row}.total"] = f"{cat}_{g}"
        out[f"{row}.health"] = f"wb_health_{cat}_{g}"
        out[f"{row}.accident"] = f"wb_accident_{cat}_{g}"
        out[f"{row}.maternity"] = f"wb_maternity_{cat}_f" if g == "f" else "0"
        out[f"{row}.paternity"] = f"wb_paternity_{cat}_m" if g == "m" else "0"
        out[f"{row}.daycare"] = f"wb_daycare_{cat}_{g}"
    return out


def _turnover(kind: str) -> dict:
    s_m, s_f = f"{kind}_perm_m_start", f"{kind}_perm_f_start"
    e_m, e_f = f"{kind}_perm_m", f"{kind}_perm_f"
    l_m, l_f = f"left_{kind}_perm_m", f"left_{kind}_perm_f"
    row = "perm_employees" if kind == "emp" else "perm_workers"
    return {
        f"{row}.male": f"pct({l_m}, ({s_m} + {e_m}) / 2)",
        f"{row}.female": f"pct({l_f}, ({s_f} + {e_f}) / 2)",
        f"{row}.total": f"pct({l_m} + {l_f}, ({s_m} + {s_f} + {e_m} + {e_f}) / 2)",
    }


DERIVE: dict[str, dict[str, str]] = {
    "A.18": {"national.plants": "plants_national", "national.offices": "offices_national",
             "international.plants": "plants_international", "international.offices": "offices_international"},
    "A.19a": {"national.number": "states_served", "international.number": "countries_served"},
    "A.19b": {"value": "pct(exports, turnover)"},
    "A.20a": _gender_rows("", ""),
    "A.20b": _gender_rows("da_", "da_"),
    "A.21": {"bod.total": "bod_m + bod_f", "bod.female": "bod_f",
             "kmp.total": "kmp_m + kmp_f", "kmp.female": "kmp_f"},
    "A.22": {**_turnover("emp"), **_turnover("wrk")},
    "A.24": {"fields.csr_applicable": "csr_applicable", "fields.turnover": "turnover",
             "fields.net_worth": "net_worth"},
    "A.25": {f"{row}.{c}": f"ngrbc_{k}_{c}" for row, k in (
        ("communities", "comm"), ("investors", "inv"), ("shareholders", "sh"), ("employees", "emp"),
        ("customers", "cust"), ("value_chain", "vc"), ("other", "other")) for c in ("filed", "pending")},
    "P1.E5": {"directors.v": "disc_directors", "kmps.v": "disc_kmps", "employees.v": "disc_employees",
              "workers.v": "disc_workers"},
    "P1.E6": {"directors.number": "coi_directors", "kmps.number": "coi_kmps"},
    "P1.E8": {"payables.v": "payables", "cogs.v": "cogs"},
    "P3.E1a": _wellbeing("emp"),
    "P3.E1b": _wellbeing("wrk"),
    "P3.E1c": {"cost.v": "wb_cost"},
    "P3.E2": {f"{row}.{who}_pct": f"pct(ret_{key}_{who}, {total})"
              for row, key in (("pf", "pf"), ("gratuity", "gratuity"), ("esi", "esi"), ("others", "other"))
              for who, total in (("emp", EMP_ALL), ("wrk", WRK_ALL))},
    "P3.E5": {f"{g}.{who}_{s}": f"pl_{who}_{s}_{g[0]}"
              for g in ("male", "female") for who in ("emp", "wrk") for s in ("took", "returned", "retained")},
    "P3.E7": {"emp_male.total": "emp_perm_m", "emp_male.members": "union_emp_perm_m",
              "emp_female.total": "emp_perm_f", "emp_female.members": "union_emp_perm_f",
              "wrk_male.total": "wrk_perm_m", "wrk_male.members": "union_wrk_perm_m",
              "wrk_female.total": "wrk_perm_f", "wrk_female.members": "union_wrk_perm_f"},
    "P3.E8": {"emp_male.total": EMP_M, "emp_male.hs": "tr_hs_emp_m", "emp_male.skill": "tr_skill_emp_m",
              "emp_female.total": EMP_F, "emp_female.hs": "tr_hs_emp_f", "emp_female.skill": "tr_skill_emp_f",
              "wrk_male.total": WRK_M, "wrk_male.hs": "tr_hs_wrk_m", "wrk_male.skill": "tr_skill_wrk_m",
              "wrk_female.total": WRK_F, "wrk_female.hs": "tr_hs_wrk_f", "wrk_female.skill": "tr_skill_wrk_f"},
    "P3.E9": {"emp_male.total": EMP_M, "emp_male.reviewed": "rev_emp_m",
              "emp_female.total": EMP_F, "emp_female.reviewed": "rev_emp_f",
              "wrk_male.total": WRK_M, "wrk_male.reviewed": "rev_wrk_m",
              "wrk_female.total": WRK_F, "wrk_female.reviewed": "rev_wrk_f"},
    "P3.E11": {f"{k}.v": k for k in ("lti_emp", "lti_wrk", "hours_emp", "hours_wrk", "recordable_emp",
                                     "recordable_wrk", "fatal_emp", "fatal_wrk", "highcons_emp", "highcons_wrk")},
    "P3.E13": {"working_conditions.filed": "cmp_wc_filed", "working_conditions.pending": "cmp_wc_pending",
               "health_safety.filed": "cmp_hs_filed", "health_safety.pending": "cmp_hs_pending"},
    "P5.E1": {"emp_perm.total": "emp_perm_m + emp_perm_f", "emp_perm.covered": "tr_hr_emp_perm",
              "emp_other.total": "emp_oth_m + emp_oth_f", "emp_other.covered": "tr_hr_emp_oth",
              "wrk_perm.total": "wrk_perm_m + wrk_perm_f", "wrk_perm.covered": "tr_hr_wrk_perm",
              "wrk_other.total": "wrk_oth_m + wrk_oth_f", "wrk_other.covered": "tr_hr_wrk_oth"},
    "P5.E2": {f"{who}_{cat}_{g}.{c}": expr
              for who in ("emp", "wrk") for cat, mc in (("perm", "perm"), ("other", "oth")) for g, gs in
              (("male", "m"), ("female", "f"))
              for c, expr in (("total", f"{who}_{mc}_{gs}"), ("equal", f"mw_eq_{who}_{mc}_{gs}"),
                              ("more", f"mw_more_{who}_{mc}_{gs}"))},
    "P5.E3a": {"bod.male_n": "bod_m", "bod.male_median": "med_bod_m", "bod.female_n": "bod_f",
               "bod.female_median": "med_bod_f", "kmp.male_n": "kmp_m", "kmp.male_median": "med_kmp_m",
               "kmp.female_n": "kmp_f", "kmp.female_median": "med_kmp_f",
               "employees.male_n": "med_emp_n_m", "employees.male_median": "med_emp_m",
               "employees.female_n": "med_emp_n_f", "employees.female_median": "med_emp_f",
               "workers.male_n": WRK_M, "workers.male_median": "med_wrk_m",
               "workers.female_n": WRK_F, "workers.female_median": "med_wrk_f"},
    "P5.E3b": {"female_wages.v": "wages_female", "total_wages.v": "wages_total"},
    "P5.E6": {f"{row}.{c}": f"cmp_{k}_{c}" for row, k in (
        ("sexual_harassment", "sh"), ("discrimination", "disc"), ("child_labour", "child"),
        ("forced_labour", "forced"), ("wages", "wages"), ("other", "otherhr")) for c in ("filed", "pending")},
    "P5.E7": {"total.v": "cmp_sh_filed", "female_headcount.v": f"{EMP_F} + {WRK_F}", "upheld.v": "cmp_sh_upheld"},
    "P6.DEN": {"revenue.v": "revenue", "ppp_factor.v": "ppp_factor", "physical_output.v": "physical_output"},
    "P6.E1": {"ren_elec.v": f"elec_ren_kwh * {KWH_TO_GJ}", "ren_fuel.v": "fuel_ren_gj",
              "ren_other.v": "energy_other_ren_gj", "non_elec.v": f"elec_nonren_kwh * {KWH_TO_GJ}",
              "non_fuel.v": "fuel_nonren_gj", "non_other.v": "energy_other_nonren_gj"},
    "P6.E3": {"surface.v": "w_surface", "ground.v": "w_ground", "third_party.v": "w_third_party",
              "sea.v": "w_sea", "others.v": "w_other", "consumption.v": "w_consumption"},
    "P6.E4": {f"{dest}_{t}.v": f"wd_{src}_{t}" for dest, src in (
        ("surface", "surface"), ("ground", "ground"), ("sea", "sea"), ("third_party", "third"), ("others", "other"))
        for t in ("none", "treated")},
    "P6.E7": {"scope1.v": "scope1", "scope2.v": "scope2"},
    "P6.E9": {"plastic.v": "ws_plastic", "ewaste.v": "ws_ewaste", "biomedical.v": "ws_biomedical",
              "cnd.v": "ws_cnd", "battery.v": "ws_battery", "radioactive.v": "ws_radioactive",
              "other_haz.v": "ws_other_haz", "other_nonhaz.v": "ws_other_nonhaz", "recycled.v": "ws_recycled",
              "reused.v": "ws_reused", "other_recovery.v": "ws_other_recovery",
              "incineration.v": "ws_incineration", "landfilling.v": "ws_landfill",
              "other_disposal.v": "ws_other_disposal"},
    "P6.L2": {"scope3.v": "scope3"},
    "P8.E4": {"total_inputs.v": "inputs_total", "msme_inputs.v": "inputs_msme", "india_inputs.v": "inputs_india"},
    "P8.E5": {"rural.v": "wages_rural", "semi_urban.v": "wages_semi_urban", "urban.v": "wages_urban",
              "metro.v": "wages_metro"},
    "P9.E3": {f"{row}.{c}": f"cc_{k}_{'rec' if c == 'received' else 'pending'}" for row, k in (
        ("data_privacy", "privacy"), ("advertising", "adv"), ("cyber_security", "cyber"),
        ("essential_services", "ess"), ("restrictive", "rtp"), ("unfair", "utp"), ("other", "other"))
        for c in ("received", "pending")},
    "P9.E7": {"breaches.value": "breaches", "pii_breaches.value": "pii_breaches"},
}

# Derived questions that still need some manual input from the client
# (a Yes/No or link column SEBI asks for alongside the numbers).
DERIVED_STILL_REQUIRED = {"A.25", "P3.E2"}

# Plausibility checks on the annual data — warnings, never blockers. Each is
# deliberately narrow so it does not fire on legitimate data.
#   total:   sum of these figures ...
#   per:     ... divided by this (skipped when blank or 0) ...
#   max:     ... above this looks wrong ...
#   unless:  ... unless this is non-zero (the check does not apply)
PLAUSIBILITY: list[dict] = [
    {
        "fields": ["elec_ren_kwh", "elec_nonren_kwh"],
        "per": f"{EMP_ALL} + {WRK_ALL}",
        "max": 100_000,
        "unless": "plants_national + plants_international",
        "message": "Electricity of {value} kWh per person looks very high for an entity with no plants "
                   "(offices typically use a few thousand). Check the unit: enter kWh, not GJ or Wh.",
    },
]


def areas_for_department(dept: str) -> list[str]:
    return [a["code"] for a in AREAS if dept in a["departments"]]
