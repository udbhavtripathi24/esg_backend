"""Where every quantitative BRSR figure comes from.

Each metric (the keys used by the BRSR derivations, app/brsr/annual_data.py
``DERIVE``) is computed from the approved data sheets for the financial year
by one rule:

    sheet     the data sheet (app/datasheets/catalog.py)
    cols      column(s), summed per row; a (column, factor) pair converts
              the column first (e.g. kWh × 0.0036 → GJ)
    how       "sum"     every monthly row in the year            (flows:
                        energy, water, waste, incidents, complaints received)
              "latest"  each location's latest month in the year (stocks:
                        headcount, coverage, board, pending complaints)
              "first"   each location's earliest month           (start-of-
                        year headcount)
              "mean"    average of the monthly values            (air-emission
                        concentrations — a ppm or mg/m³ cannot be added up)
    across    how locations combine for latest/first: "sum" (default),
              "mean" (a rate such as the PPP factor) or "max" (a count of
              countries, which locations would otherwise double)
    cat       only rows of this Category (Employee / Worker)
    where     only rows with these text values (e.g. Water_Stress_Zone = Yes)
    scale     multiplier after aggregation (INR million → INR = 1e6)

A metric with several ``Part``s adds them (e.g. inputs from the admin and
the tech supply-chain sheets). ``COMPUTED`` metrics are worked out from other
metrics (e.g. leavers = start + hires − end).

Blank cells in an uploaded sheet count as 0. A metric is blank (None) only
when its sheet has no approved rows for the year at all — the BRSR cell then
shows empty with a coverage warning, never a silent zero.

Metrics without a source (turnover, net worth, Scope 1/2/3, worker parental
leave…) are typed by the company directly in the BRSR table.

Every factor is listed in ``FACTORS`` and appears in the formula sheet given
to the engagement manager for sign-off.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional, Union

Col = Union[str, tuple[str, float]]


@dataclass(frozen=True)
class Part:
    sheet: str
    cols: tuple[Col, ...]
    how: str = "latest"
    cat: Optional[str] = None
    where: tuple[tuple[str, str], ...] = ()
    scale: float = 1.0
    across: str = "sum"


@dataclass(frozen=True)
class Source:
    parts: tuple[Part, ...]
    note: str = ""
    deviation: str = ""  # differs from the engagement's Report Mapping — needs sign-off


def S(sheet: str, *cols: Col, how: str = "latest", cat: Optional[str] = None,
      where: Optional[dict] = None, scale: float = 1.0, across: str = "sum",
      note: str = "", deviation: str = "") -> Source:
    return Source((Part(sheet, tuple(cols), how, cat, tuple((where or {}).items()), scale, across),),
                  note, deviation)


def multi(*parts: Source, note: str = "", deviation: str = "") -> Source:
    return Source(tuple(p for s in parts for p in s.parts), note, deviation)


# ---------------------------------------------------------------------------
# Conversion factors (sign-off required)
# ---------------------------------------------------------------------------

FACTORS: dict[str, dict] = {
    "kwh_to_gj": {"value": 0.0036, "label": "1 kWh = 0.0036 GJ", "basis": "Exact (SI)", "signoff": False},
    "coal_gj_per_t": {"value": 25.8, "label": "Coal (other bituminous): 25.8 GJ per tonne",
                      "basis": "IPCC 2006 Guidelines, Vol. 2, Table 1.2 default NCV", "signoff": True},
    "gas_gj_per_kg": {"value": 0.048, "label": "Natural gas: 48.0 MJ per kg",
                      "basis": "IPCC 2006 Guidelines, Vol. 2, Table 1.2 default NCV", "signoff": True},
    "biomass_gj_per_kg": {"value": 0.0156, "label": "Wood / biomass: 15.6 MJ per kg",
                          "basis": "IPCC 2006 Guidelines, Vol. 2, Table 1.2 default NCV", "signoff": True},
    "biofuel_gj_per_kg": {"value": 0.027, "label": "Biodiesel: 27 MJ per kg (column read as kg)",
                          "basis": "IPCC 2006 Guidelines, Vol. 2, Table 1.2 default NCV", "signoff": True},
    "diesel_gj_per_l": {"value": 0.03612, "label": "Diesel (DG sets): 43.0 MJ/kg × 0.84 kg/L = 36.12 MJ per litre",
                        "basis": "IPCC 2006 default NCV; density 0.84 kg/L", "signoff": True},
    "million": {"value": 1e6, "label": "INR million → INR", "basis": "Exact", "signoff": False},
    "crore": {"value": 1e7, "label": "INR crore → INR", "basis": "Exact", "signoff": False},
}
F = {k: v["value"] for k, v in FACTORS.items()}

E, W = "Employee", "Worker"


def _people(prefix: str, cat: str, col: str) -> dict[str, Source]:
    """Headcount-style metrics by gender from a Category sheet."""
    return {f"{prefix}_m": S("diversity", col.format(g="Male"), cat=cat),
            f"{prefix}_f": S("diversity", col.format(g="Female"), cat=cat),
            f"{prefix}_o": S("diversity", col.format(g="Others"), cat=cat)}


SOURCES: dict[str, Source] = {}

# --- Workforce (Diversity sheet; Category Employee / Worker) ---------------
for _who, _cat in (("emp", E), ("wrk", W)):
    SOURCES.update(_people(f"{_who}_perm", _cat, "Permanent_{g}_including_differently_abled"))
    SOURCES.update(_people(f"{_who}_oth", _cat, "Other_than_Permanent_{g}_including_differently_abled"))
    SOURCES.update(_people(f"da_{_who}_perm", _cat, "Permanent_{g}_differently_abled"))
    SOURCES.update(_people(f"da_{_who}_oth", _cat, "Other_than_Permanent_{g}_differently_abled"))
    SOURCES[f"{_who}_perm_m_start"] = S("diversity", "Males_employed_at_the_start_of_FY_Permanent", how="first", cat=_cat)
    SOURCES[f"{_who}_perm_f_start"] = S("diversity", "Female_employed_at_the_start_of_FY_Permanent", how="first", cat=_cat)
    SOURCES[f"{_who}_perm_m_end"] = S("diversity", "Males_employed_at_the_end_of_FY_Permanent", cat=_cat)
    SOURCES[f"{_who}_perm_f_end"] = S("diversity", "Female_employed_at_the_end_of_FY_Permanent", cat=_cat)
    SOURCES[f"hires_{_who}_perm_m"] = S("diversity", "Permanent_Male_Hires", how="sum", cat=_cat)
    SOURCES[f"hires_{_who}_perm_f"] = S("diversity", "Permanent_Female_Hires", how="sum", cat=_cat)

# --- Board & KMP ------------------------------------------------------------
SOURCES.update({
    "bod_m": S("board_of_directors", "Board_of_directors_Male"),
    "bod_f": S("board_of_directors", "Board_of_directors_Female"),
    "kmp_m": S("board_of_directors", "KMPs_Male"),
    "kmp_f": S("board_of_directors", "KMPs_Female"),
    "med_bod_m": S("board_of_directors", "Median_remuneration_or_wages_Male_BoD"),
    "med_bod_f": S("board_of_directors", "Median_remuneration_or_wages_Female_BoD"),
    "med_kmp_m": S("board_of_directors", "Median_remuneration_or_wages_Male_KMPs"),
    "med_kmp_f": S("board_of_directors", "Median_remuneration_or_wages_Female_KMPs"),
    "med_emp_m": S("board_of_directors", "Median_remuneration_or_wages_Male_other_than_BoD_and_KMP"),
    "med_emp_f": S("board_of_directors", "Median_remuneration_or_wages_Female_other_than_BoD_and_KMP"),
    "med_emp_n_m": S("board_of_directors", "Employees_other_than_BoD_and_KMP_Male"),
    "med_emp_n_f": S("board_of_directors", "Employees_other_than_BoD_and_KMP_Female"),
    "coi_directors": S("board_of_directors", "Complaints_on_Conflict_of_Interest_Directors", how="sum"),
    "coi_kmps": S("board_of_directors", "Complaints_on_Conflict_of_Interest_KMPs", how="sum"),
    "disc_directors": S("board_of_directors", "Directors_with_Disciplinary_Action_for_Bribery_or_Corruption", how="sum"),
    "disc_kmps": S("board_of_directors", "KMPs_with_Disciplinary_Action_for_Bribery_or_Corruption", how="sum"),
    "disc_employees": S("board_of_directors", "Employees_with_Disciplinary_Action_for_Bribery_or_Corruption", how="sum"),
    "disc_workers": S("board_of_directors", "Workers_with_Disciplinary_Action_for_Bribery_or_Corruption", how="sum"),
})

# --- Well-being (Employee Well-being sheet; Category Employee / Worker) ----
for _who, _cat in (("emp", E), ("wrk", W)):
    for _m, _col in (("health", "Health_Insurance"), ("accident", "Accident_Insurance"),
                     ("maternity", "Maternity_Leave"), ("paternity", "Paternity_Leave"),
                     ("daycare", "Day_Care_Facilities")):
        for _perm, _pc in (("perm", "Permanent"), ("oth", "NonPermanent")):
            for _g, _gc in (("m", "Male"), ("f", "Female")):
                SOURCES[f"wb_{_m}_{_who}_{_perm}_{_g}"] = S("employee_wellbeing", f"{_col}_{_pc}_{_gc}Employees",
                                                           cat=_cat)
    SOURCES[f"ret_pf_{_who}"] = S("employee_wellbeing", "PF_Coverage_Employees", cat=_cat)
    SOURCES[f"ret_gratuity_{_who}"] = S("employee_wellbeing", "Gratuity_Coverage_Employees", cat=_cat)
    SOURCES[f"ret_esi_{_who}"] = S("employee_wellbeing", "ESI_Coverage_Employees", cat=_cat)
    SOURCES[f"ret_other_{_who}"] = S("employee_wellbeing", "Other_Retirement_Benefits_Coverage_Employees", cat=_cat)
    SOURCES[f"union_{_who}_perm_m"] = S("employee_wellbeing", "Union_Membership_Permanent_MaleEmployees", cat=_cat)
    SOURCES[f"union_{_who}_perm_f"] = S("employee_wellbeing", "Union_Membership_Permanent_FemaleEmployees", cat=_cat)
    SOURCES[f"rev_{_who}_m"] = S("employee_wellbeing", "Performance_Review_MaleEmployees", cat=_cat)
    SOURCES[f"rev_{_who}_f"] = S("employee_wellbeing", "Performance_Review_FemaleEmployees", cat=_cat)
SOURCES["wb_cost"] = S("employee_wellbeing", "Wellbeing_Spending_Total_INR_crore", scale=F["crore"],
                       note="Latest month's year-to-date spend, Employee and Worker rows added.")

# --- Parental leave (Workforce Retention sheet: employees only) -------------
for _g, _gc in (("m", "Male"), ("f", "Female")):
    _plural = "Males" if _g == "m" else "Females"
    SOURCES[f"pl_emp_took_{_g}"] = S("workforce_retention", f"{_gc}_Due_to_Return_parental_leave", how="sum")
    SOURCES[f"pl_emp_returned_{_g}"] = S("workforce_retention", f"{_gc}_who_Returned_parental_leave", how="sum")
    SOURCES[f"pl_emp_retained_{_g}"] = S("workforce_retention", f"{_plural}_Retained_12_Months_After_Return", how="sum")
    SOURCES[f"pl_emp_returned_prior_{_g}"] = S("workforce_retention",
                                               f"{_plural}_Returned_from_Parental_Leave_in_prior_period", how="sum")

# --- Training (Trainings sheet; Category Employee / Worker) -----------------
for _who, _cat in (("emp", E), ("wrk", W)):
    SOURCES[f"tr_hs_{_who}_m"] = S("trainings", "Training_on_Health_Safety_Male_Employees", cat=_cat)
    SOURCES[f"tr_hs_{_who}_f"] = S("trainings", "Training_on_Health_Safety_Female_Employees", cat=_cat)
    SOURCES[f"tr_skill_{_who}_m"] = S("trainings", "Training_on_Skill_Upgradation_Male_Employees", cat=_cat)
    SOURCES[f"tr_skill_{_who}_f"] = S("trainings", "Training_on_Skill_Upgradation_Female_Employees", cat=_cat)
    SOURCES[f"tr_hr_{_who}_perm"] = S("trainings", "Training_on_Human_Rights_Permanent_Employees", cat=_cat)
    SOURCES[f"tr_hr_{_who}_oth"] = S("trainings", "Training_on_Human_Rights_NonPermanent_Employees", cat=_cat)

# --- Wages (Wages & Remuneration sheet; Category Employee / Worker) ---------
for _who, _cat in (("emp", E), ("wrk", W)):
    for _perm, _pc in (("perm", "Permanent"), ("oth", "NonPermanent")):
        for _g, _gc in (("m", "Male"), ("f", "Female")):
            SOURCES[f"mw_eq_{_who}_{_perm}_{_g}"] = S("wages_remuneration",
                                                       f"{_pc}_{_gc}Employees_Earning_Minimum_Wages", cat=_cat)
            SOURCES[f"mw_more_{_who}_{_perm}_{_g}"] = S("wages_remuneration",
                                                         f"{_pc}_{_gc}Employees_Earning_More_Than_Minimum_Wages",
                                                         cat=_cat)
SOURCES.update({
    "wages_total": S("wages_remuneration", "Total_Salary_Wages_Paid_Reporting_FY",
                     note="Year-to-date figure: latest month, Employee and Worker rows added."),
    "wages_female": S("wages_remuneration", "Gross_Salary_Wages_Paid_to_Females"),
    "wages_rural": S("wages_remuneration", "Wages_Paid_in_Rural_Location"),
    "wages_semi_urban": S("wages_remuneration", "Wages_Paid_in_SemiUrban_Location"),
    "wages_urban": S("wages_remuneration", "Wages_Paid_in_Urban_Location"),
    "wages_metro": S("wages_remuneration", "Wages_Paid_in_Metropolitan_Location"),
})

# --- Health & safety (Safety and Health sheets) -----------------------------
for _who, _sfx in (("emp", "Employees"), ("wrk", "Workers")):
    SOURCES.update({
        f"lti_{_who}": S("safety", f"Lost_Time_Injury_{_sfx}", how="sum"),
        f"hours_{_who}": S("safety", f"Total_Man_Hours_{_sfx}", how="sum"),
        f"recordable_{_who}": S("safety", f"Total_Recordable_Work_Related_Injuries_{_sfx}", how="sum"),
        f"fatal_{_who}": S("safety", f"Fatalities_{_sfx}", how="sum"),
        f"highcons_{_who}": S("safety", f"High_Consequence_Work_Related_Injury_or_Ill_Health_Excluding_Fatalities_{_sfx}",
                              how="sum"),
        f"hc_affected_{_who}": S("health", f"{_sfx}_Suffered_High_Consequence_Injury_Ill_Health_Fatalities", how="sum"),
        f"hc_placed_{_who}": S("health", f"{_sfx}_Rehabilitated_and_Placed_in_Suitable_Employment", how="sum"),
    })

# --- Complaints -------------------------------------------------------------
_PENDING = "Pending complaints are a year-end position: the latest month is used, not a sum."
SOURCES.update({
    "cmp_wc_filed": S("safety", "Complaints_Filed_on_Working_Condition", how="sum"),
    "cmp_wc_pending": S("safety", "Complaints_Pending_on_Working_Condition_Year_End", note=_PENDING),
    "cmp_hs_filed": S("safety", "Complaints_Filed_on_Health_and_Safety", how="sum"),
    "cmp_hs_pending": S("safety", "Complaints_Pending_on_Health_and_Safety_Year_End", note=_PENDING),
})
for _k, _rec, _pen in (("sh", "Sexual_Harassment_Received", "Sexual_Harassment_Pending"),
                       ("disc", "Discrimination_at_workplace_Received", "Discrimination_at_workplace_Pending"),
                       ("child", "child_labour_Received", "child_labour_pending"),
                       ("forced", "Forced_labour_Received", "Forced_labour_pending"),
                       ("wages", "Wages_Received", "Wages_pending"),
                       ("otherhr", "Other_human_related_issues_Received", "Other_human_related_issues_Pending")):
    SOURCES[f"cmp_{_k}_filed"] = S("hr_complaints", _rec, how="sum",
                                   deviation="Report Mapping says latest value; complaints received in a month "
                                             "are added up over the year.")
    SOURCES[f"cmp_{_k}_pending"] = S("hr_complaints", _pen, note=_PENDING)
for _k, _rec, _pen in (("comm", "Complaints_Received_Communities", "Complaints_pending_Communtities"),
                       ("inv", "Complaints_Received_Investors_otherthan_Stakeholders",
                        "Complaints_pending_Investors_otherthan_Stakeholders"),
                       ("sh", "Complaints_Received_Shareholders", "Complaints_pending_Shareholders"),
                       ("emp", "Complaints_Received_Employees_and_workers", "Complaints_pending_Employees_and_workers"),
                       ("cust", "Complaints_Received_customers", "Complaints_pendingcustomers"),
                       ("vc", "Complaints_Received_value_chain_partners", "Complaints_pending_value_chain_partners"),
                       ("other", "Complaints_Received_others", "Complaints_pending_others")):
    SOURCES[f"ngrbc_{_k}_filed"] = S("ngrbc_complaints", _rec, how="sum")
    SOURCES[f"ngrbc_{_k}_pending"] = S("ngrbc_complaints", _pen, note=_PENDING)
for _k, _base in (("privacy", "Data_privacy"), ("adv", "Advertising"), ("cyber", "Cybersecurity"),
                  ("ess", "Delivery_of_essential_services"), ("rtp", "Restrictive_trade_practices"),
                  ("utp", "Unfair_trade_practices"), ("other", "Other_consumer_complaints")):
    SOURCES[f"cc_{_k}_rec"] = S("consumer_complaints", f"{_base}_received", how="sum")
    SOURCES[f"cc_{_k}_pending"] = S("consumer_complaints", f"{_base}_pending", note=_PENDING)

# --- Energy (Energy and DG Sets sheets) -------------------------------------
_K = F["kwh_to_gj"]
SOURCES.update({
    "elec_ren_kwh": S("energy", "Renewable_Electricity_Generated_from_Solar_Plant_kWh",
                      "Renewable_Electricity_Generated_from_Wind_kWh",
                      "Renewable_Electricity_Generated_from_Small_Hydro_kWh", how="sum"),
    "elec_nonren_kwh": S("energy", "Nonrenewable_Purchased_Electricity_from_Grid_kWh", "Purchase_backup_Electricity",
                         how="sum", note="Back-up electricity purchased is read as kWh."),
    "fuel_ren_gj": S("energy", ("Renewable_Biomass_Consumption_kg", F["biomass_gj_per_kg"]),
                     ("Renewable_Biofuel_Consumption_kg_L", F["biofuel_gj_per_kg"]), how="sum"),
    "fuel_nonren_gj": multi(
        S("energy", ("Nonrenewable_Coal_Consumption_MT", F["coal_gj_per_t"]),
          ("Nonrenewable_Natural_Gas_Consumption_kg", F["gas_gj_per_kg"]), how="sum"),
        S("dg_sets", ("Fuel_in_L", F["diesel_gj_per_l"]), how="sum")),
    "energy_other_ren_gj": S("energy", ("Renewable_Purchased_Heat_KWh", _K), ("Renewable_Purchased_Steam_KWh", _K),
                             ("Renewable_Purchased_Cooling_KWh", _K), how="sum"),
    "energy_other_nonren_gj": S("energy", ("Nonrenewable_Purchased_Heat_KWh", _K),
                                ("Nonrenewable_Purchased_Steam_KWh", _K),
                                ("Nonrenewable_Purchased_Cooling_KWh", _K), how="sum"),
})

# --- Air emissions (concentrations: averaged) -------------------------------
_AIR_DEV = ("Report Mapping says aggregation; concentrations (ppm, mg/m³) cannot be added, so the average "
            "of the monthly readings is reported.")
for _k, _col in (("nox", "Nox_in_PPM"), ("sox", "Sox_in_PPM"), ("pm", "Particulate_Matter_in_mg_per_m3"),
                 ("pop", "Persistent_Organic_pollutants_in_mg_per_m3"),
                 ("voc", "Volatile_Organic_Compounds_in_mg_per_m3"),
                 ("hap", "Hazardous_Air_Pollutants_in_mg_per_m3"), ("others", "Others")):
    SOURCES[f"air_{_k}"] = S("air_emissions", _col, how="mean", deviation=_AIR_DEV)

# --- Water (all sites, and sites in water-stress areas) ----------------------
_WITHDRAW = (("surface", "Surface_Water_kL"), ("ground", "Ground_Water_kL"), ("third_party", "Third_Party_Water_kL"),
             ("sea", "Seawater_or_Desalinated_Water_kL"), ("other", "Other_Water_Sources_kL"))
_DISCHARGE = (("surface", "Discharge_to_Surface_Water"), ("ground", "Discharge_to_Groundwater"),
              ("sea", "Discharge_to_Seawater"), ("third", "Sent_to_Third_Parties"),
              ("other", "Discharge_to_Other_Sources"))
for _pfx, _where in (("", None), ("stress_", {"Water_Stress_Zone": "Yes"})):
    for _k, _col in _WITHDRAW:
        SOURCES[f"{_pfx}w_{_k if _k != 'third_party' else 'third_party'}"] = S("water", _col, how="sum", where=_where)
    for _k, _col in _DISCHARGE:
        SOURCES[f"{_pfx}wd_{_k}_none"] = S("water", f"{_col}_without_Treatment_kL", how="sum", where=_where)
        SOURCES[f"{_pfx}wd_{_k}_treated"] = S("water", f"{_col}_with_Treatment_kL", how="sum", where=_where)

# --- Waste (Waste Management and E-waste sheets) ----------------------------
_WS = "waste_management"
SOURCES.update({
    "ws_plastic": S(_WS, "Plastic_Waste_Generated_MT", how="sum"),
    "ws_ewaste": S("e_waste", "E_Waste_Generated_MT", how="sum"),
    "ws_biomedical": S(_WS, "Biomedical_Waste_Generated_MT", how="sum"),
    "ws_cnd": S(_WS, "Construction_and_Demolition_Waste_Generated_MT", how="sum"),
    "ws_battery": S("e_waste", "Battery_Waste_Generated_MT", how="sum"),
    "ws_radioactive": S(_WS, "Radioactive_Waste_Generated_MT", how="sum"),
    "ws_other_haz": S(_WS, "Other_Hazardous_Waste_Generated_MT", how="sum"),
    "ws_other_nonhaz": S(_WS, "Other_Non_Hazardous_Waste_Generated_MT", how="sum"),
})
_WS_KINDS = ("Plastic_Waste", "Biomedical_Waste", "Construction_and_Demolition_Waste", "Radioactive_Waste",
             "Other_Hazardous_Waste", "Other_Non_Hazardous_Waste")
_EW_KINDS = ("E_Waste", "Battery_Waste")
for _key, _sfx in (("ws_recycled", "Recycled_MT"), ("ws_reused", "Reused_MT"),
                   ("ws_other_recovery", "Other_Recovery_Options_MT"), ("ws_incineration", "Incinerated_MT"),
                   ("ws_landfill", "Landfilled_MT"), ("ws_other_disposal", "Other_Disposal_Options_MT")):
    SOURCES[_key] = multi(S(_WS, *[f"{k}_{_sfx}" for k in _WS_KINDS], how="sum"),
                          S("e_waste", *[f"{k}_{_sfx}" for k in _EW_KINDS], how="sum"))
SOURCES.update({
    "recl_plastic_recycled": S(_WS, "For_Products_and_Packaging_Reclaimed_Total_Plastic_Waste_Recycled", how="sum"),
    "recl_plastic_reused": S(_WS, "For_Products_and_Packaging_Reclaimed_Total_Plastic_Waste_Reused", how="sum"),
    "recl_plastic_disposed": S(_WS, "For_products_and_packaging_reclaimed_Total_plastic_safely_disposed", how="sum"),
    "recl_ewaste_recycled": S("e_waste", "For_Products_and_Packaging_Reclaimed_Total_Ewaste_Waste_Recycled", how="sum"),
    "recl_ewaste_reused": S("e_waste", "For_Products_and_Packaging_Reclaimed_Total_Ewaste_Waste_Reused", how="sum"),
    "recl_ewaste_disposed": S("e_waste", "For_Products_and_Packaging_Reclaimed_Total_Ewaste_Safely_Disposed", how="sum"),
})

# --- Finance ------------------------------------------------------------------
_FIN_DEV = "Report Mapping says latest value; monthly amounts are added up over the year."
SOURCES.update({
    "revenue": S("finance", "Revenue_from_Operations_INR_million", how="sum", scale=F["million"], deviation=_FIN_DEV),
    "exports": S("finance", "Contribution_of_exports_INR_million", how="sum", scale=F["million"], deviation=_FIN_DEV),
    "ppp_factor": S("finance", "Purchase_Power_Parity_INR_USD", across="mean"),
    "payables": S("finance", "Accounts_Payable_INR", note="Year-end balance."),
    "cogs": S("finance", "Cost_of_Goods_Services_Procured_INR", how="sum", deviation=_FIN_DEV),
    "fin_purchases": S("finance", "Total_Purchases_INR", how="sum"),
    "fin_purch_trading": S("finance", "Purchases_from_Trading_Houses_INR", how="sum"),
    "fin_trading_houses": S("finance", "Number_of_Trading_Houses"),
    "fin_purch_top10": S("finance", "Purchases_from_Top_10_Trading_Houses_INR", how="sum"),
    "fin_sales": S("finance", "Total_Sales_INR", how="sum"),
    "fin_sales_dealers": S("finance", "Sales_to_Dealers_Distributors_INR", how="sum"),
    "fin_dealers": S("finance", "Number_of_Dealers_Distributors"),
    "fin_sales_top10": S("finance", "Sales_to_top_10_dealers_distributors_INR", how="sum"),
    "fin_purch_rp": S("finance", "Purchases_with_Related_Parties_INR", how="sum"),
    "fin_sales_rp": S("finance", "Sales_to_Related_Parties_INR", how="sum"),
    "fin_loans_rp": S("finance", "Loans_and_Advances_to_Related_Parties_INR"),
    "fin_loans": S("finance", "Total_Loans_and_Advances_INR"),
    "fin_inv_rp": S("finance", "Investments_in_Related_Parties_INR"),
    "fin_inv": S("finance", "Total_Investments_INR_Million", scale=F["million"]),
})

# --- Supply chain (admin + tech sheets) --------------------------------------
_SCA, _SCT = "supply_chain_admin", "supply_chain_tech"
SOURCES.update({
    "suppliers": multi(S(_SCA, "Total_Number_of_Suppliers"), S(_SCT, "Total_Number_of_Tech_Suppliers")),
    "inputs_msme": multi(S(_SCA, "Input_Material_Sourced_from_MSMEs_Small_Producers_INR"),
                         S(_SCT, "Tech_Materials_from_MSMEs_INR")),
    "inputs_india": multi(S(_SCA, "Input_Material_Sourced_Within_India_INR"),
                          S(_SCT, "Tech_Materials_sourced_within_India_INR")),
    "inputs_total": multi(S(_SCA, "Input_Material_Sourced_Within_India_INR", "Input_Material_Sourced_Outside_India_INR"),
                          S(_SCT, "Tech_Materials_sourced_within_India_INR", "Tech_Materials_sourced_Outside_India_INR"),
                          note="Total = sourced within India + outside India."),
    "vc_awareness_programmes": multi(S(_SCA, "Awareness_Programs_for_Value_Chain_Partners", how="sum"),
                                     S(_SCT, "Awareness_for_Tech_Value_Chain", how="sum")),
    "vc_awareness_covered": multi(S(_SCA, "Suppliers_Covered_Under_Awareness_Programs"),
                                  S(_SCT, "Tech_Suppliers_in_Awareness_Programs")),
    "vc_env_assessed": multi(S(_SCA, "Suppliers_Assessed_on_Environmental_Impacts"),
                             S(_SCT, "Tech_Suppliers_on_Environmental_Impacts")),
})
_VC_DEV = "SEBI asks for % by value of business; the sheets give supplier counts, so % of suppliers is reported."
for _k, _a, _t in (("hs", "Value_Chain_Assessed_on_Health_Safety_Practices", "Tech_Chain_Assessed_on_Health_Safety"),
                   ("wc", "Value_Chain_Assessed_on_Working_Conditions", "Tech_Chain_Assessed_on_Work_Conditions"),
                   ("sh", "Value_Chain_Assessed_on_Sexual_Harassment", "Tech_Chain_Assessed_on_Harassment"),
                   ("child", "Value_Chain_Assessed_on_Child_Labor", "Tech_Chain_Assessed_on_Child_Labor"),
                   ("forced", "Value_Chain_Assessed_on_Forced_Labor", "Tech_Chain_Assessed_on_Forced_Labor"),
                   ("disc", "Value_Chain_Assessed_on_Workplace_Discrimination", "Tech_Chain_on_Workplace_Discrimination"),
                   ("wages", "Value_Chain_Assessed_on_Wages", "Tech_Chain_Assessed_on_Wages"),
                   ("others", "Value_Chain_Assessed_on_Others_Specify", "Tech_Chain_Other_Assessments")):
    SOURCES[f"vc_assessed_{_k}"] = multi(S(_SCA, _a), S(_SCT, _t), deviation=_VC_DEV)

# --- Plants, offices, own-operation assessments -------------------------------
SOURCES.update({
    "plants_national": S("national_offices", "Number_of_National_plants"),
    "offices_national": S("national_offices", "Number_of_National_offices"),
    "plants_international": S("international_offices", "Number_of_International_plants"),
    "offices_international": S("international_offices", "Number_of_International_offices"),
    "countries_served": S("international_offices", "No_of_international_countries", across="max",
                          note="Largest figure reported by any location (countries are not added up)."),
})
for _k, _col in (("hs", "Health_and_safety_practices"), ("wc", "Working_Conditions"), ("child", "Child_Labour"),
                 ("forced", "Forced_Labour"), ("sh", "Sexual_Harassment"), ("disc", "Workplace_Discrimination"),
                 ("wages", "Wages"), ("others", "Others_Specify")):
    SOURCES[f"own_assessed_{_k}"] = S("human_rights_assessments", f"Plants_Offices_Assessed_on_{_col}")


# ---------------------------------------------------------------------------
# Metrics worked out from other metrics (None-safe)
# ---------------------------------------------------------------------------

def _add(*xs):
    vs = [x for x in xs if x is not None]
    return sum(vs) if vs else None


def _leavers(start, hires, end):
    if start is None or end is None:
        return None
    return max(0.0, start + (hires or 0) - end)


def _net(withdrawn: list, discharged: list):
    w, d = _add(*withdrawn), _add(*discharged)
    if w is None:
        return None
    return max(0.0, w - (d or 0))


def _computed() -> dict[str, tuple[tuple[str, ...], Callable]]:
    out: dict[str, tuple[tuple[str, ...], Callable]] = {}
    for who in ("emp", "wrk"):
        for g in ("m", "f"):
            deps = (f"{who}_perm_{g}_start", f"hires_{who}_perm_{g}", f"{who}_perm_{g}_end")
            out[f"left_{who}_perm_{g}"] = (deps, _leavers)
    for pfx in ("", "stress_"):
        wd = tuple(f"{pfx}w_{k}" for k, _ in _WITHDRAW)
        dd = tuple(f"{pfx}wd_{k}_{t}" for k, _ in _DISCHARGE for t in ("none", "treated"))
        out[f"{pfx}w_consumption"] = (wd + dd, lambda *v, n=len(wd): _net(list(v[:n]), list(v[n:])))
    return out


COMPUTED = _computed()
COMPUTED_NOTES = {
    "left_emp_perm_m": "Leavers = employed at start of year + permanent hires − employed at end of year.",
    "w_consumption": "Water consumption = total withdrawal − total discharge.",
}

SOURCED: frozenset[str] = frozenset(SOURCES) | frozenset(COMPUTED)
SHEETS_USED: frozenset[str] = frozenset(p.sheet for s in SOURCES.values() for p in s.parts)


def describe(metric: str) -> Optional[dict]:
    """Plain description of a metric's source, for the UI and the formula sheet."""
    if metric in COMPUTED:
        deps, _fn = COMPUTED[metric]
        note = COMPUTED_NOTES.get(metric) or COMPUTED_NOTES.get(metric.replace("stress_", "")) or \
            COMPUTED_NOTES.get(metric[:-2] + "_m" if metric.startswith("left_") else metric)
        return {"metric": metric, "computed_from": list(deps), "note": note or ""}
    src = SOURCES.get(metric)
    if src is None:
        return None
    return {"metric": metric, "note": src.note, "deviation": src.deviation,
            "parts": [{"sheet": p.sheet, "columns": [c if isinstance(c, str) else c[0] for c in p.cols],
                       "factors": {c[0]: c[1] for c in p.cols if not isinstance(c, str)},
                       "how": p.how, "category": p.cat, "where": dict(p.where), "scale": p.scale,
                       "across": p.across} for p in src.parts]}
