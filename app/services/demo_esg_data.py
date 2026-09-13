"""Demo ESG dashboard data generator.

CRITICAL, non-negotiable boundary: this module produces ILLUSTRATIVE
DEMO DATA ONLY, generated deterministically from fixed seed values --
never real uploaded/approved data, never touching KpiValue, Dataset,
or any other real production table. This exists specifically to
demonstrate real, interactive charting capability to stakeholders
before real emission-factor methodology, Scope 1/2/3 classification
rules, or financial (turnover) data collection have been approved --
none of which exist anywhere in this platform's real data model today
(confirmed: docs/decisions/emission-factors.md is still status
BLOCKED).

Every function returns a dict including "is_demo_data": True and a
disclaimer string. Structure, chart types, and metric labels are drawn
directly from a reference document provided by the business
stakeholder (an ESG Dashboard mockup) -- this module implements
exactly that structure, adding no invented metrics beyond what the
reference specifies.
"""
import hashlib

DEMO_DISCLAIMER = (
    "Illustrative demo data only -- not derived from real uploads, real "
    "emission factors, or approved ESG methodology. For demonstration of "
    "platform capability only."
)

LOCATIONS = ["Bengaluru", "Chennai", "Delhi", "Mumbai"]
YEARS = [2025, 2026]
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


def _seeded_value(key: str, low: float, high: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    fraction = (h % 10_000) / 10_000
    return round(low + fraction * (high - low), 2)


def _seeded_int(key: str, low: int, high: int) -> int:
    return round(_seeded_value(key, low, high))


def _filtered_locations(location: str | None) -> list[str]:
    if location and location != "All":
        return [location] if location in LOCATIONS else []
    return LOCATIONS


def _by_location(locs: list[str], key_prefix: str, period_key: str, low: float, high: float) -> dict:
    return {loc: _seeded_value(f"{key_prefix}-{loc}-{period_key}", low, high) for loc in locs}


def _by_location_and_category(locs: list[str], key_prefix: str, period_key: str, categories: list[str], low: float, high: float) -> dict:
    return {
        cat: {loc: _seeded_value(f"{key_prefix}-{cat}-{loc}-{period_key}", low, high) for loc in locs}
        for cat in categories
    }


# ============================== ENVIRONMENT ==============================

def get_ghg_environment_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    scope1_by_location = _by_location(locs, "s1", period_key, 150, 1300)
    scope2_by_location = _by_location(locs, "s2", period_key, 150, 450)
    scope3_by_location = _by_location(locs, "s3", period_key, 150, 1300)
    scope1_by_category = _by_location_and_category(locs, "s1cat", period_key, ["Company owned cars", "DG sets", "HVAC"], 150, 700)
    scope3_by_category = _by_location_and_category(locs, "s3cat", period_key, ["Leased HVAC", "Leased DG sets", "Company leased cars"], 150, 900)
    energy_by_location = {loc: round(_seeded_value(f"energy-{loc}-{period_key}", 400000, 900000)) for loc in locs}

    total_scope1 = round(sum(scope1_by_location.values()), 1)
    total_scope2 = round(sum(scope2_by_location.values()), 1)
    total_scope3 = round(sum(scope3_by_location.values()), 1)

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "total_ghg_emissions_tco2e": round(total_scope1 + total_scope2 + total_scope3, 1),
        "scope_totals": {"scope1": total_scope1, "scope2": total_scope2, "scope3": total_scope3},
        "emissions_by_location": {
            loc: {"scope1": scope1_by_location[loc], "scope2": scope2_by_location[loc], "scope3": scope3_by_location[loc]}
            for loc in locs
        },
        "scope1_by_category": scope1_by_category,
        "scope3_by_category": scope3_by_category,
        "emission_intensity_tco2e_per_rupee_turnover": _seeded_value(f"ei-{period_key}", 0.15, 0.35),
        "renewable_energy_percentage": _seeded_value(f"renew-{period_key}", 15, 45),
        "total_energy_consumption_mj": sum(energy_by_location.values()),
        "energy_by_location_mj": energy_by_location,
        "energy_intensity_mj_per_rupee_turnover": _seeded_value(f"eneri-{period_key}", 60, 100),
    }


def get_energy_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "energy_intensity_mj_per_rupee_turnover_by_location": _by_location(locs, "enloc", period_key, 70, 90),
        "energy_intensity_mj_per_rupee_turnover_ppp_by_location": _by_location(locs, "enppp", period_key, 1.0, 2.0),
        "renewable_energy_percentage_by_location": _by_location(locs, "renpctloc", period_key, 15, 45),
        "renewable_vs_non_renewable_mj": {
            "Renewable": round(_seeded_value(f"renmj-{period_key}", 2_000_000, 3_500_000)),
            "Non Renewable": round(_seeded_value(f"nonrenmj-{period_key}", 4_000_000, 6_000_000)),
        },
        "other_renewable_sources_mj_by_location": _by_location_and_category(locs, "othren", period_key, ["Cooling", "Heat", "Steam"], 18000, 50000),
        "renewable_energy_by_source_mj_by_location": _by_location_and_category(
            locs, "renbysrc", period_key, ["Wind", "Solar", "Small Hydro", "Biomass", "Biofuel", "Steam", "Heat", "Cooling"], 1_800_000, 2_900_000
        ),
        "electricity_from_renewable_by_location": _by_location_and_category(locs, "elecren", period_key, ["Solar Plant", "Wind", "Small Hydro"], 1_800_000, 2_900_000),
        "renewable_fuels_by_location": _by_location_and_category(locs, "renfuel", period_key, ["Renewable Biomass", "Renewable Biofuel"], 5000, 18600),
    }


def get_water_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    withdrawal = round(_seeded_value(f"wwithdraw-{period_key}", 300, 450))
    recycled = round(_seeded_value(f"wrecycled-{period_key}", 40, 70))
    reused = round(_seeded_value(f"wreused-{period_key}", 40, 70))
    consumed = round(_seeded_value(f"wconsumed-{period_key}", 300, 450))

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "water_consumed_kl": consumed,
        "water_intensity_kl_per_rupee_turnover": _seeded_value(f"wintensity-{period_key}", 0.005, 0.009),
        "water_reused_kl": reused,
        "water_recycled_kl": recycled,
        "water_withdrawal_kl": withdrawal,
        "water_withdrawal_by_location": _by_location(locs, "wwloc", period_key, 7, 13),
        "water_consumption_by_location": _by_location(locs, "wcloc", period_key, 40, 80),
        "water_recycled_reused_by_location": {
            "Water Reused KL": _by_location(locs, "wruse", period_key, 3, 15),
            "Water Recycled KL": _by_location(locs, "wrcyc", period_key, 5, 15),
        },
        "water_intensity_by_location": _by_location(locs, "wintloc", period_key, 0.0062, 0.0082),
        "water_withdrawal_breakdown_by_location": _by_location_and_category(
            locs, "wwbreak", period_key, ["Surface", "Ground", "Third Party", "Seawater or Desalinated", "Other"], 4, 12
        ),
    }


def get_waste_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    generated = round(_seeded_value(f"wastegen-{period_key}", 600, 900))
    disposed = round(_seeded_value(f"wastedisp-{period_key}", 100, 200))
    recovered = round(_seeded_value(f"wasterec-{period_key}", 100, 200))

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "waste_generated_mt": generated,
        "waste_disposed_mt": disposed,
        "waste_recovered_mt": recovered,
        "waste_intensity_mt_per_rupee_turnover": _seeded_value(f"wasteint-{period_key}", 0.01, 0.03),
        "waste_generated_by_category_by_location": _by_location_and_category(
            locs, "wastecat", period_key,
            ["Paper", "Biomedical", "Construction & Demolition", "Battery", "Radioactive", "Other Hazardous", "Other Non Hazardous"],
            3, 12,
        ),
        "waste_recovery_by_location": {
            "Recycled MT": _by_location(locs, "wrecycled2", period_key, 6, 12),
            "Reused MT": _by_location(locs, "wreused2", period_key, 6, 12),
            "Recovery Options MT": _by_location(locs, "wrecopt", period_key, 6, 12),
        },
        "waste_disposal_by_location": {
            "Incinerated MT": _by_location(locs, "wincin", period_key, 2, 8),
            "Landfilled MT": _by_location(locs, "wlandfill", period_key, 5, 13),
            "Other Disposal Options MT": _by_location(locs, "wotherdisp", period_key, 5, 11),
        },
    }


# ================================ SOCIAL ================================

def get_social_training_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    hours_by_location = _by_location(locs, "trainhrs", period_key, 18000, 30000)
    per_employee_by_location = _by_location(locs, "trainperemp", period_key, 51, 54.5)

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "total_training_hours": round(sum(hours_by_location.values())),
        "training_hours_per_employee_avg": round(sum(per_employee_by_location.values()) / max(len(locs), 1), 2),
        "training_hours_by_location": hours_by_location,
        "training_hours_per_employee_by_location": per_employee_by_location,
        "human_rights_training_by_location": _by_location_and_category(locs, "hrtrain", period_key, ["Permanent Employees", "Non Permanent Employees"], 1300, 7300),
        "skill_upgradation_training_by_location": _by_location_and_category(locs, "skilltrain", period_key, ["Female Employees", "Male Employees"], 1200, 6900),
        "health_safety_training_attendance_by_location": _by_location_and_category(locs, "hstrain", period_key, ["Female Employees", "Male Employees"], 1600, 6900),
    }


def get_social_diversity_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "workforce_by_gender": {
            "Female": _seeded_int(f"wff-{period_key}", 1200, 1800),
            "Male": _seeded_int(f"wfm-{period_key}", 5200, 5600),
            "Others": _seeded_int(f"wfo-{period_key}", 1600, 1800),
        },
        "permanent_work_type_by_location": _by_location_and_category(locs, "permwt", period_key, ["Permanent Male", "Permanent Female", "Permanent Others"], 1100, 5600),
        "non_permanent_work_type_by_location": _by_location_and_category(locs, "nonpermwt", period_key, ["Other than Permanent Male", "Other than Permanent Female", "Other than Permanent Others"], 300, 1400),
        "differently_abled_permanent_by_location": _by_location_and_category(locs, "dapermwt", period_key, ["Permanent Male", "Permanent Female"], 10, 91),
        "differently_abled_non_permanent_by_location": _by_location_and_category(locs, "danonpermwt", period_key, ["Other than Permanent Male", "Other than Permanent Female"], 4, 20),
    }


def get_social_wellbeing_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "performance_review_by_gender_by_location": _by_location_and_category(locs, "perfrev", period_key, ["Female Employees", "Male Employees", "Other Employees"], 1700, 6900),
        "accident_insurance_by_workforce_type_by_location": _by_location_and_category(
            locs, "accins", period_key, ["Female Permanent", "Female Non Permanent", "Male Permanent", "Male Non Permanent", "Other Permanent", "Other Non Permanent"], 900, 9000
        ),
        "day_care_facilities_by_workforce_type_by_location": _by_location_and_category(
            locs, "daycare", period_key, ["Female Permanent", "Female Non Permanent", "Male Permanent", "Male Non Permanent", "Other Permanent", "Other Non Permanent"], 200, 430
        ),
        "health_insurance_by_workforce_type_by_location": _by_location_and_category(
            locs, "healthins", period_key, ["Female Permanent", "Female Non Permanent", "Male Permanent", "Male Non Permanent", "Other Permanent", "Other Non Permanent"], 900, 9000
        ),
        "paternity_maternity_benefits_by_location": _by_location_and_category(locs, "patmat", period_key, ["Paternity", "Maternity", "Paternity Benefits"], 50, 115),
        "min_wage_by_category_by_location": _by_location_and_category(
            locs, "minwage", period_key, ["Female BOD", "Male BOD", "Female KMPs", "Male KMPs", "Female Other Than BOD & KMP", "Male Other Than BOD & KMPs"], 2_000_000, 96_000_000
        ),
    }


def get_social_health_safety_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "health_safety_complaints_status_by_location": _by_location(locs, "hscomplaint", period_key, 9, 13.1),
    }


def get_social_complaints_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "working_conditions_complaints_by_location": _by_location(locs, "wccomplaint", period_key, 4, 16),
        "health_safety_complaints_by_location": _by_location(locs, "hscomplaint2", period_key, 9, 13.1),
        "hr_complaints_received_by_type_by_location": _by_location_and_category(locs, "hrrecv", period_key, ["Customers", "Communities", "Employees and Workers", "Investors"], 20, 42),
        "hr_complaints_pending_by_type_by_location": _by_location_and_category(locs, "hrpend", period_key, ["Investors", "NonShareholders", "Employees and Workers", "Working Condition", "Year End"], 4, 16),
        "ngrbc_complaints_received_by_stakeholder_by_location": _by_location_and_category(locs, "ngrbcrecv", period_key, ["Wages Received", "Unfair Trade Practices"], 1, 6),
        "ngrbc_complaints_pending_by_stakeholder_by_location": _by_location_and_category(locs, "ngrbcpend", period_key, ["Data Privacy", "Cybersecurity", "Chain Partners", "Customers"], 2, 8),
    }


# =============================== GOVERNANCE ===============================

def get_governance_leadership_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    bod_female, bod_male = 17, 51
    kmp_female, kmp_male = 17, 34
    indep_male, indep_female = 17, 34
    indep_total = indep_male + indep_female

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "total_board_of_directors": {"Female": bod_female, "Male": bod_male, "Total": bod_female + bod_male},
        "total_key_managerial_personnel": {"Female": kmp_female, "Male": kmp_male, "Total": kmp_female + kmp_male},
        "independent_directors": {
            "Male": indep_male, "Female": indep_female,
            "Male Percentage": round(indep_male / indep_total * 100, 2),
            "Female Percentage": round(indep_female / indep_total * 100, 2),
        },
        "total_employees": 537,
        "conflict_of_interest_complaints_by_location": _by_location_and_category(locs, "coi", period_key, ["KMPs", "Directors", "Employees"], 0, 3),
        "bribery_corruption_disciplinary_action_by_location": _by_location_and_category(locs, "bribery", period_key, ["KMPs", "Directors", "Employees"], 0, 3),
        "complaints_by_category_table": {
            loc: {
                cat: _seeded_int(f"govtable-{cat}-{loc}", 19, 29)
                for cat in ["Child Labour", "Forced Labour", "Sexual Harassment", "Workplace Discrimination", "Wages", "Health & Safety Practices", "Working Conditions"]
            }
            for loc in locs
        },
        "independent_directors_percentage_by_location": _by_location_and_category(locs, "indeploc", period_key, ["Female", "Male"], 33.33, 66.67),
        "bod_kmp_other_by_location": _by_location_and_category(locs, "bodkmp", period_key, ["BOD", "KMPs", "Other"], 2, 9),
    }


def get_governance_supply_chain_data(location: str | None = None, year: int | None = None, month: str | None = None) -> dict:
    locs = _filtered_locations(location)
    period_key = f"{year or 'all'}-{month or 'all'}"

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "awareness_programs_for_value_chain_partners": _seeded_int(f"awprog-{period_key}", 25, 40),
        "chain_partners_covered_under_awareness_program_percentage": _seeded_value(f"chaincov-{period_key}", 60, 100),
        "input_material_sourced_by_location": _by_location_and_category(locs, "inputmat", period_key, ["Within India", "MSMEs"], 19, 58),
        "value_chain_hr_assessment_by_location": _by_location_and_category(locs, "vchr", period_key, ["Workplace Discrimination", "Health Safety Practices"], 49, 84),
        "overall_suppliers_assessment_by_location": _by_location(locs, "suppassess", period_key, 80, 130),
        "new_suppliers_assessment_by_location": _by_location(locs, "newsupp", period_key, 1, 4),
    }


def get_filter_options() -> dict:
    return {"locations": ["All"] + LOCATIONS, "years": YEARS, "months": ["All"] + MONTHS}
