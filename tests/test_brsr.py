"""BRSR questionnaire + report tests.

Two layers:
1. Engine units — the question bank, the formula evaluator, computation
   and validation, independent of HTTP.
2. API journeys — a client fills the full BRSR (the reference filing in
   tests/brsr_fixture.py) through the real endpoints, submits every
   section, a Deloitte reviewer approves, and the real report worker
   produces the Word file, which is then opened and checked.
"""
import io
import shutil

import pytest
from docx import Document
from sqlmodel import select

from app.brsr import engine
from app.brsr import formula as F
from app.brsr.question_bank import QUESTIONS, QUESTIONS_BY_ID, SECTION_CODES, financial_year_labels
from app.models.brsr import BrsrAnswerHistory
from app.models.consultant_assignment import ConsultantAssignment
from tests import brsr_fixture
from tests.conftest_helpers import auth, bootstrap, make_company, make_user
from tests.test_layer1_kpi import _set_storage

API = "/api/v1/brsr"


# ===========================================================================
# Engine units
# ===========================================================================

from app.brsr.sheet_metrics import SHEET_KEY


def _with(cy=None, py=None, **answers):
    """Answers plus sheet metrics, as the service injects them."""
    return {**answers, SHEET_KEY: {"cy": dict(cy or {}), "py": dict(py or {})}}


def test_bank_covers_every_sebi_section():
    # Bank v3: the SEBI sections only; figures come from the data sheets
    assert SECTION_CODES == ["A", "B"] + [f"P{i}" for i in range(1, 10)]
    for code in SECTION_CODES:
        assert any(q["section"] == code for q in QUESTIONS), code
    for qid in ("A.I", "A.20a", "A.25", "B.1", "B.10", "P1.E9", "P3.E9", "P5.E7", "P6.E1", "P6.E7", "P6.E9",
                "P8.E5", "P9.E7"):
        assert qid in QUESTIONS_BY_ID
    assert not any(q["type"] == "metrics" for q in QUESTIONS)  # the annual form is retired
    assert all(not q["required"] for q in QUESTIONS if q.get("indicator") == "leadership")
    assert all(q.get("departments") for q in QUESTIONS if not q.get("helper")), \
        [q["id"] for q in QUESTIONS if not q.get("helper") and not q.get("departments")]


def test_quantitative_tables_come_from_the_data_sheets():
    from app.brsr.sheet_sources import SOURCED
    for qid in ("A.20a", "A.22", "P3.E1a", "P3.E9", "P3.E14", "P5.E10", "P6.E1", "P6.E9", "P5.E3b", "P6.L1b",
                "P1.E9", "P6.L7"):
        q = QUESTIONS_BY_ID[qid]
        assert q["derive"] and q["derived_from"], qid
        assert all(F.names_in(e) <= SOURCED for e in q["derive"].values()), qid
    assert QUESTIONS_BY_ID["P8.L6"]["derive_rows"]["table"] == "csr_projects"
    # Figures without a sheet column are typed in the BRSR table itself
    for qid in ("A.24", "P6.E7", "P9.E7", "P7.E1a"):
        assert not QUESTIONS_BY_ID[qid].get("derive") and QUESTIONS_BY_ID[qid]["source"] == "qualitative", qid
    # Mixed tables keep their typed columns
    assert QUESTIONS_BY_ID["A.25"]["source"] == "both" and QUESTIONS_BY_ID["P3.E9"]["source"] == "data"
    assert "Diversity" in QUESTIONS_BY_ID["A.20a"]["derived_from_titles"]
    assert "HR" in QUESTIONS_BY_ID["A.20a"]["departments"]


def test_hidden_worker_groups_count_as_zero_and_tables_still_build():
    # No workers in the Diversity sheet: worker figures are 0, not blank
    a = _with(cy={"emp_perm_m": 6, "wrk_perm_m": 0, "wrk_perm_f": 0, "wrk_perm_o": 0, "wrk_oth_m": 0,
                  "wrk_oth_f": 0, "wrk_oth_o": 0})
    vals = engine.metric_values(a)["cy"]
    assert vals["da_wrk_perm_m"] == 0 and vals["emp_perm_m"] == 6
    # Before the headcount is known nothing is assumed
    assert engine.metric_values(_with(cy={"emp_perm_m": 6}))["cy"].get("da_wrk_perm_m") is None


@pytest.mark.parametrize("expr", ["__import__('os')", "a.b", "(lambda: 1)()", "a[0]", "open('x')", "a if b else c"])
def test_formula_evaluator_rejects_anything_outside_the_whitelist(expr):
    with pytest.raises(F.FormulaError):
        F.parse(expr)


def test_formula_missing_values_and_division_by_zero():
    assert F.evaluate("pct(a, b)", {"a": 1, "b": 0}.get) is None
    assert F.evaluate("pct(a, b)", {"a": None, "b": 4}.get) is None
    assert F.evaluate("a + b", {"a": 2, "b": None}.get) == 2
    assert F.evaluate("a - b", {"a": 2, "b": None}.get) is None
    assert F.evaluate("div(a * 365, b)", {"a": 10, "b": 365}.get) == 10


def test_derived_cells_are_never_stored_even_if_sent():
    # Current-year cells built from the sheets: nothing the client sends is kept
    v = engine.normalize_answer("P3.E9", {"cells": {"emp_male": {"cy_total": 10, "cy_reviewed": 5, "cy_pct": 99}},
                                          "na": True, "na_reason": "x"})
    assert v == {"cells": {}}
    # A.25 is mixed: the mechanism column stays the client's, the counts are built
    v = engine.normalize_answer("A.25", {"cells": {"investors": {"mechanism": "Yes", "cy_filed": 5}}})
    assert v["cells"] == {"investors": {"mechanism": "Yes"}}


def test_previous_year_is_typed_until_the_sheets_cover_it():
    # First year on the platform: no previous-year sheets, the client types last year's figure
    stored = engine.normalize_answer("P3.E11", {"cells": {"hours_emp": {"cy_v": 1, "py_v": 7500000}}})
    assert stored == {"cells": {"hours_emp": {"py_v": 7500000}}}  # cy dropped, py kept
    a = _with(cy={"hours_emp": 7806000}, **{"P3.E11": stored})
    g = engine.compute_table("P3.E11", a)
    assert g["hours_emp"] == {"cy_v": 7806000, "py_v": 7500000}
    assert engine.derived_py_cells(QUESTIONS_BY_ID["P3.E11"], a) == []  # typed, so editable
    # Once last year's sheets are approved they win, and the cell becomes read-only
    a = _with(cy={"hours_emp": 7806000}, py={"hours_emp": 7400000}, **{"P3.E11": stored})
    assert engine.compute_table("P3.E11", a)["hours_emp"]["py_v"] == 7400000
    assert ["hours_emp", "py_v"] in engine.derived_py_cells(QUESTIONS_BY_ID["P3.E11"], a)
    # A current-year cell with no sheet data stays blank — never a typed or stale number
    assert engine.compute_table("P3.E11", _with(**{"P3.E11": stored}))["hours_emp"]["cy_v"] is None


def test_missing_sheet_data_is_a_visible_warning_not_a_silent_zero():
    a = _with(cy={})
    r = engine.section_report("P3", a)
    msgs = [w["message"] for w in r["warnings"] if w["question_id"] == "P3.E9"]
    assert msgs and "Waiting for approved data" in msgs[0]


def test_the_112_percent_error_in_the_reference_filing_is_impossible():
    # The filing divided male reviews (2,862) by permanent male employees only (2,551).
    a = _with(cy={"emp_perm_m": 2551, "emp_oth_m": 0, "emp_perm_o": 0, "emp_oth_o": 0, "rev_emp_m": 2862})
    issues = engine.section_report("P3", a)["warnings"]
    assert any(i["question_id"] == "P3.E9" and "cannot exceed" in i["message"] and "2,862" in i["message"]
               for i in issues)
    # With contract staff counted the review count fits, and the percentage is built, not typed
    a = _with(cy={"emp_perm_m": 2551, "emp_oth_m": 406, "emp_perm_o": 0, "emp_oth_o": 0, "rev_emp_m": 2862})
    assert round(engine.compute_table("P3.E9", a)["emp_male"]["cy_pct"], 2) == 96.79
    assert not any(i["question_id"] == "P3.E9" and "cannot exceed" in i["message"]
                   for i in engine.section_report("P3", a)["warnings"])


def test_electricity_unit_mistake_is_flagged_for_office_entities():
    from app.brsr.sheet_metrics import plausibility_warnings
    m = {"emp_perm_m": 2000, "emp_perm_f": 1000, "plants_national": 0, "plants_international": 0,
         "elec_ren_kwh": 4192242.84 / 0.0036}  # the filing's kWh typed as GJ and converted
    w = plausibility_warnings(m, "2024-25")
    assert len(w) == 1 and "Check the unit" in w[0]["message"]
    assert plausibility_warnings({**m, "elec_ren_kwh": 4192242.84}, "2024-25") == []
    assert plausibility_warnings({**m, "plants_national": 3}, "2024-25") == []  # a manufacturer may


def test_percentages_over_100_from_inconsistent_sheets_are_flagged():
    # Top-10 dealers cannot sell more than all dealers together
    a = _with(cy={"fin_sales": 1000, "fin_sales_dealers": 100, "fin_sales_top10": 430})
    w = [i for i in engine.section_report("P1", a)["warnings"] if i["question_id"] == "P1.E9"]
    assert any("430% is over 100%" in i["message"] for i in w)


def test_tables_and_totals_are_built_from_headcount():
    a = _with(cy={"emp_perm_m": 6, "emp_perm_f": 4, "emp_perm_o": 0, "emp_oth_m": 2, "emp_oth_f": 3,
                  "emp_oth_o": 0})
    g = engine.compute_table("A.20a", a)
    assert g["employees_total"] == {"total": 15, "male": 8, "male_pct": pytest.approx(53.333, 0.01),
                                    "female": 7, "female_pct": pytest.approx(46.667, 0.01)}
    d = engine.apply_derivations(a)
    assert d["P3.E1a"]["cells"]["perm_male"]["total"] == 6
    assert d["A.20a"]["cells"]["employees_perm"]["male"] == 6
    # Other genders count in the totals
    a = _with(cy={"emp_perm_m": 6, "emp_perm_f": 4, "emp_perm_o": 1, "emp_oth_m": 0, "emp_oth_f": 0,
                  "emp_oth_o": 0})
    assert engine.apply_derivations(a)["A.20a"]["cells"]["employees_perm"]["total"] == 11


def test_intensities_use_the_single_revenue_figure_and_ppp():
    a = _with(cy={"revenue": 1_000_000, "ppp_factor": 20, "elec_ren_kwh": 300 / 0.0036,
                  "elec_nonren_kwh": 700 / 0.0036, "fuel_ren_gj": 0, "fuel_nonren_gj": 0,
                  "energy_other_ren_gj": 0, "energy_other_nonren_gj": 0})
    g = engine.compute_table("P6.E1", a)
    assert g["total"]["cy_v"] == pytest.approx(1000)
    assert g["int_rupee"]["cy_v"] == pytest.approx(0.001)
    assert g["int_ppp"]["cy_v"] == pytest.approx(0.02)
    assert g["int_rupee"]["py_v"] is None  # no previous-year revenue -> blank, never 0


def test_not_applicable_needs_a_reason():
    a = {"P2.E1": engine.normalize_answer("P2.E1", {"na": True, "na_reason": ""})}
    assert any(i["level"] == "error" for i in engine.validate_answer("P2.E1", a["P2.E1"], a))
    assert not engine.is_answered(QUESTIONS_BY_ID["P2.E1"], a["P2.E1"])
    a = {"P2.E1": engine.normalize_answer("P2.E1", {"na": True, "na_reason": "No such R&D in our offices."})}
    assert engine.is_answered(QUESTIONS_BY_ID["P2.E1"], a["P2.E1"])


def test_conditional_requirement():
    assert "P6.E13b" not in engine.required_ids({"P6.E13": {"answer": "Yes"}})
    assert "P6.E13b" in engine.required_ids({"P6.E13": {"answer": "No"}})
    assert "P2.E2b" in engine.required_ids({"P2.E2a": {"answer": "Yes"}})


def test_turnover_percentages_and_nic_codes():
    a = {"A.17": engine.normalize_answer("A.17", {"rows": [{"product": "x", "nic_code": "6619A", "pct_turnover": 70},
                                                           {"product": "y", "nic_code": "66190", "pct_turnover": 40}]})}
    msgs = [i["message"] for i in engine.validate_answer("A.17", a["A.17"], a)]
    assert any("cannot exceed 100%" in m for m in msgs)
    assert any("NIC code" in m for m in msgs)
    a = {"A.17": engine.normalize_answer("A.17", {"rows": [{"product": "x", "nic_code": "66190", "pct_turnover": 60}]})}
    issues = engine.validate_answer("A.17", a["A.17"], a)
    assert [i["level"] for i in issues] == ["warning"]  # below the 90% SEBI asks for


def test_cin_email_and_percent_formats():
    a = {"A.I": engine.normalize_answer("A.I", {"fields": {"cin": "L123", "email": "nope", "paid_up_capital": -5}})}
    msgs = " | ".join(i["message"] for i in engine.validate_answer("A.I", a["A.I"], a))
    assert "invalid format" in msgs and "e-mail" in msgs and "negative" in msgs
    # Export share is built from the Finance sheet, as a share of revenue from operations
    a = _with(cy={"exports": 25, "revenue": 100})
    assert engine.apply_derivations(a)["A.19b"]["value"] == 25


def test_structurally_invalid_payloads_are_rejected():
    for qid, raw in [("P3.E9", {"cells": {"nope": {}}}), ("P3.E9", {"cells": {"emp_male": {"nope": 1}}}),
                     ("A.I", {"fields": {"hacker": 1}}), ("B.1", {"cells": {"covers": {"P10": "Yes"}}}),
                     ("B.2", {"span": {"translated": "Yes"}}), ("A.16", {"rows": "x"}), ("NOPE", {}),
                     ("D.WF", {"values": {}})]:
        with pytest.raises(engine.PayloadError):
            engine.normalize_answer(qid, raw)


def test_fixture_is_complete_and_clean():
    norm = {q: engine.normalize_answer(q, v) for q, v in brsr_fixture.answers().items()}
    norm["A.I"]["fields"]["financial_year"] = "2024-25"
    norm = brsr_fixture.with_sheet_metrics(norm)
    for code in SECTION_CODES:
        r = engine.section_report(code, norm)
        assert r["ready_to_submit"], (code, r["missing"], r["errors"])
        assert r["warnings"] == [], (code, r["warnings"])


def test_sheet_rows_aggregate_by_the_engagement_rules():
    from datetime import date
    from app.brsr.sheet_metrics import metrics_for
    def row(sheet, m, vals, loc="Mumbai", cat=None, **attrs):
        return {"sheet": sheet, "start": date(2024, m if m >= 4 else m, 1), "end": date(2024, m, 28),
                "location": loc, "attrs": {"location": loc, **({"Category": cat} if cat else {}), **attrs},
                "values": vals, "version_id": 1, "row": m}
    rows = {
        # stock: latest month per location, summed across locations
        "diversity": [row("diversity", 5, {"Permanent_Male_including_differently_abled": 10}, cat="Employee"),
                      row("diversity", 6, {"Permanent_Male_including_differently_abled": 12}, cat="Employee"),
                      row("diversity", 6, {"Permanent_Male_including_differently_abled": 5}, loc="Pune",
                          cat="Employee")],
        # flow: every month added up; kWh converted to GJ for heat
        "energy": [row("energy", 5, {"Nonrenewable_Purchased_Electricity_from_Grid_kWh": 100,
                                     "Nonrenewable_Purchased_Heat_KWh": 1000}),
                   row("energy", 6, {"Nonrenewable_Purchased_Electricity_from_Grid_kWh": 50})],
        # filter: only water-stress sites; concentrations averaged
        "water": [row("water", 5, {"Surface_Water_kL": 7}, Water_Stress_Zone="Yes"),
                  row("water", 6, {"Surface_Water_kL": 3}, Water_Stress_Zone="No")],
        "air_emissions": [row("air_emissions", 5, {"Nox_in_PPM": 20}), row("air_emissions", 6, {"Nox_in_PPM": 30})],
        "finance": [row("finance", 5, {"Revenue_from_Operations_INR_million": 2.5})],
    }
    m = metrics_for(rows)
    assert m["emp_perm_m"] == 17                          # 12 (Mumbai, latest) + 5 (Pune)
    assert m["wrk_perm_m"] == 0                           # sheet reported, no Worker rows
    assert m["elec_nonren_kwh"] == 150
    assert m["energy_other_nonren_gj"] == pytest.approx(3.6)
    assert m["w_surface"] == 10 and m["stress_w_surface"] == 7
    assert m["air_nox"] == 25
    assert m["revenue"] == 2_500_000                      # INR million -> INR
    assert m["bod_m"] is None                             # Board sheet not reported: blank, not 0


def test_financial_year_labels():
    assert financial_year_labels("2024-25") == {"cy": "FY 2024-25", "py": "FY 2023-24", "ppy": "FY 2022-23"}


# ===========================================================================
# API journeys
# ===========================================================================

def _world(session):
    org = bootstrap(session)
    co = make_company(session, org, "Nuvama Wealth")
    other = make_company(session, org, "Other Co")
    uploader = make_user(session, "up@client.com", "client", "Client Uploader", company=co)
    client_admin = make_user(session, "admin@client.com", "client", "Client Administrator", company=co)
    outsider = make_user(session, "x@other.com", "client", "Client Administrator", company=other)
    reviewer = make_user(session, "rev@deloitte.com", "deloitte", "Consultant", org=org)
    session.add(ConsultantAssignment(company_id=co.id, consultant_user_id=reviewer.id, is_active=True))
    session.commit()
    brsr_fixture.seed_sheet_data(session, co, uploader)
    return {"org": org, "co": co, "uploader": uploader, "admin": client_admin, "outsider": outsider,
            "reviewer": reviewer}


def _create(client, user, fy="2024-25"):
    r = client.post(f"{API}/response-sets", json={"financial_year": fy}, headers=auth(user))
    assert r.status_code == 201, r.text
    return r.json()["public_id"]


def _save(client, user, set_id, answers, revisions=None):
    revisions = revisions or {}
    body = {"answers": {q: {"value": v, "revision": revisions.get(q)} for q, v in answers.items()}}
    return client.put(f"{API}/response-sets/{set_id}/answers", json=body, headers=auth(user))


def _fill_all(client, user, set_id):
    data = brsr_fixture.answers()
    for code in SECTION_CODES:
        part = {q: v for q, v in data.items() if QUESTIONS_BY_ID[q]["section"] == code}
        r = _save(client, user, set_id, part)
        assert r.status_code == 200, (code, r.text)


def _approve_answers(client, spoc, reviewer, set_id, code):
    """SPOC sends the section's answers for review; the department reviewer approves them."""
    pending = client.get(f"{API}/response-sets/{set_id}/sections/{code}", headers=auth(spoc)).json()["pending_reviews"]
    if not pending:
        return []
    r = client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": pending},
                    headers=auth(spoc))
    assert r.status_code == 200, (code, r.text)
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": pending, "decision": "approved"}, headers=auth(reviewer))
    assert r.status_code == 200, (code, r.text)
    return pending


def _submit_all(client, user, set_id, reviewer=None, session=None):
    for code in SECTION_CODES:
        if reviewer is not None:
            _approve_answers(client, user, reviewer, set_id, code)
        r = client.post(f"{API}/response-sets/{set_id}/sections/{code}/submit", headers=auth(user))
        assert r.status_code == 200, (code, r.text)


def _approve_all(client, reviewer, set_id):
    for code in SECTION_CODES:
        r = client.post(f"{API}/response-sets/{set_id}/sections/{code}/decide",
                        json={"decision": "approved", "note": "Checked against source documents."},
                        headers=auth(reviewer))
        assert r.status_code == 200, (code, r.text)


def test_question_bank_endpoint(client, session):
    w = _world(session)
    r = client.get(f"{API}/question-bank", headers=auth(w["uploader"]))
    assert r.status_code == 200
    assert len(r.json()["questions"]) == len(QUESTIONS)
    assert client.get(f"{API}/question-bank").status_code == 401


def test_create_set_rules(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    r = client.post(f"{API}/response-sets", json={"financial_year": "2024-25"}, headers=auth(w["uploader"]))
    assert r.status_code == 409
    for bad in ("2024", "2024-26", "24-25", "1999-00"):
        r = client.post(f"{API}/response-sets", json={"financial_year": bad}, headers=auth(w["uploader"]))
        assert r.status_code == 422, bad
    ov = client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()
    assert [s["status"] for s in ov["sections"]] == ["draft"] * len(SECTION_CODES)
    assert [s["code"] for s in ov["sections"]] == SECTION_CODES
    assert [s["kind"] for s in ov["sections"]] == ["disclosure"] * 11
    assert ov["data_sheets"]["coverage"]["diversity"]["rows"] >= 1 and ov["data_changed_sections"] == []
    assert ov["fy_labels"]["cy"] == "FY 2024-25"


def test_other_companies_cannot_see_or_edit(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    h = auth(w["outsider"])
    assert client.get(f"{API}/response-sets/{set_id}", headers=h).status_code == 404
    assert client.get(f"{API}/response-sets/{set_id}/sections/A", headers=h).status_code == 404
    assert _save(client, w["outsider"], set_id, {"A.19c": {"text": "x"}}).status_code == 404
    assert client.get(f"{API}/response-sets", headers=h).json() == []
    # An unassigned Deloitte consultant cannot see it either
    stranger = make_user(session, "s@deloitte.com", "deloitte", "Consultant", org=w["org"])
    assert client.get(f"{API}/response-sets/{set_id}", headers=auth(stranger)).status_code == 404


def test_save_returns_computed_values_and_issues(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    r = _save(client, w["uploader"], set_id, {"A.19c": {"text": "Wealth clients."},
                                              "P6.E7": {"cells": {"scope2": {"cy_v": 1170.35}}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert sorted(body["saved"]) == ["A.19c", "P6.E7"]
    assert body["answers"]["A.19c"]["revision"] == 1
    assert body["workflow"]["A.19c"]["status"] == "open"
    # Scope 2 is typed; its intensity uses the revenue from the approved Finance sheet
    assert body["answers"]["P6.E7"]["computed"]["int_rupee"]["cy_v"] == pytest.approx(1170.35 / 41693400000)
    # Tables built from the sheets are there before anyone types anything
    p3 = client.get(f"{API}/response-sets/{set_id}/sections/P3", headers=auth(w["uploader"])).json()
    assert round(p3["answers"]["P3.E9"]["computed"]["emp_male"]["cy_pct"], 2) == 96.79
    assert "diversity" in p3["data_sheets"]["sheets"] and p3["data_sheets"]["coverage"]["diversity"]["rows"]
    assert "workflow" not in p3["answers"]["P3.E9"]          # sheet figures are not reviewed per question
    assert p3["answers"]["P3.E15"]["workflow"]["status"] == "open"


def test_unknown_question_and_bad_shape_rejected(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    assert _save(client, w["uploader"], set_id, {"Z.99": {"text": "x"}}).status_code == 422
    r = _save(client, w["uploader"], set_id, {"A.20a": {"cells": {"employees_perm": {"bogus": 1}}}})
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "invalid_answer"


def test_concurrent_edit_is_a_conflict_not_an_overwrite(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    assert _save(client, w["uploader"], set_id, {"A.19c": {"text": "first"}}).json()["answers"]["A.19c"]["revision"] == 1
    # Second user edits based on revision 1
    assert _save(client, w["admin"], set_id, {"A.19c": {"text": "second"}}, {"A.19c": 1}).status_code == 200
    # First user still thinks it is revision 1 -> conflict, nothing written
    r = _save(client, w["uploader"], set_id, {"A.19c": {"text": "third"}, "A.19b": {"value": 5}}, {"A.19c": 1})
    assert r.status_code == 409
    assert r.json()["conflicts"][0]["current_value"]["text"] == "second"
    sec = client.get(f"{API}/response-sets/{set_id}/sections/A", headers=auth(w["uploader"])).json()
    assert sec["answers"]["A.19c"]["value"]["text"] == "second"
    assert sec["answers"]["A.19b"]["value"] is None  # the batch was all-or-nothing


def test_unchanged_save_does_not_bump_revision_or_history(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _save(client, w["uploader"], set_id, {"A.19c": {"text": "same"}})
    r = _save(client, w["uploader"], set_id, {"A.19c": {"text": "same "}}, {"A.19c": 1})
    assert r.json()["saved"] == [] and r.json()["answers"]["A.19c"]["revision"] == 1
    hist = client.get(f"{API}/response-sets/{set_id}/answers/A.19c/history", headers=auth(w["uploader"])).json()
    assert len(hist) == 1 and hist[0]["changed_by"] == w["uploader"].name


def test_incomplete_section_cannot_be_submitted(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    r = client.post(f"{API}/response-sets/{set_id}/sections/P4/submit", headers=auth(w["uploader"]))
    assert r.status_code == 422
    assert r.json()["error"]["code"] == "section_incomplete"


def test_full_journey_review_and_brsr_report(client, session, tmp_path):
    _set_storage(tmp_path)
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _fill_all(client, w["uploader"], set_id)
    _submit_all(client, w["uploader"], set_id, w["reviewer"])

    # Locked while under review
    r = _save(client, w["uploader"], set_id, {"A.19c": {"text": "edit"}}, {"A.19c": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "section_locked"

    # Review queue shows every submitted section to the reviewer
    q = client.get(f"{API}/review-queue", headers=auth(w["reviewer"])).json()
    assert len(q) == len(SECTION_CODES) and {x["section"] for x in q} == set(SECTION_CODES)

    # Segregation of duties: the submitter cannot approve (a client approver role could otherwise)
    approver = make_user(session, "appr@client.com", "client", "Client Approver", company=w["co"])
    r = client.post(f"{API}/response-sets/{set_id}/sections/A/decide",
                    json={"decision": "approved", "note": "ok"}, headers=auth(w["uploader"]))
    assert r.status_code == 403  # uploader lacks dataset:review entirely

    # Request changes on P3 with a question-level comment
    r = client.post(f"{API}/response-sets/{set_id}/sections/P3/decide", json={
        "decision": "changes_requested", "note": "Please attach the HR register.",
        "question_comments": [{"question_id": "P3.E9", "body": "Confirm the denominator includes contract staff."}]},
        headers=auth(approver))
    assert r.status_code == 200, r.text
    detail = r.json()
    assert detail["status"] == "changes_requested" and detail["editable"] is True
    assert any(c["question_id"] == "P3.E9" for c in detail["comments"])

    # Report cannot be generated until everything is approved
    r = client.post("/api/v1/reports", json={"reporting_period_start": "2024-04-01",
                                             "reporting_period_end": "2025-03-31", "report_type": "brsr"},
                    headers=auth(w["admin"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "brsr_not_ready"

    # Client fixes and resubmits P3; everything gets approved
    rev = detail["answers"]["P3.E15"]["revision"]
    assert detail["answers"]["P3.E15"]["workflow"]["status"] == "approved"
    assert _save(client, w["uploader"], set_id, {"P3.E15": {"text": "No incidents. HR register attached."}},
                 {"P3.E15": rev}).status_code == 200
    # The edited answer needs its approval again before P3 can go back to Deloitte
    r = client.post(f"{API}/response-sets/{set_id}/sections/P3/submit", headers=auth(w["uploader"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "answers_not_approved" and "P3.E15" in r.text
    # Now a client approver exists, so the answer is approved on the client side (not by Deloitte)
    r = client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": ["P3.E15"]},
                    headers=auth(w["uploader"]))
    assert r.status_code == 200, r.text
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": ["P3.E15"], "decision": "approved"}, headers=auth(w["reviewer"]))
    assert r.status_code == 403
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": ["P3.E15"], "decision": "approved"}, headers=auth(approver))
    assert r.status_code == 200 and r.json()["P3.E15"]["status"] == "approved"
    assert client.post(f"{API}/response-sets/{set_id}/sections/P3/submit", headers=auth(w["uploader"])).status_code == 200
    _approve_all(client, w["reviewer"], set_id)
    assert client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()["all_approved"] is True

    # Wrong period for a BRSR report
    r = client.post("/api/v1/reports", json={"reporting_period_start": "2024-04-01",
                                             "reporting_period_end": "2024-06-30", "report_type": "brsr"},
                    headers=auth(w["admin"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "invalid_period"

    r = client.post("/api/v1/reports", json={"reporting_period_start": "2024-04-01",
                                             "reporting_period_end": "2025-03-31", "report_type": "brsr"},
                    headers=auth(w["admin"]))
    assert r.status_code == 201, r.text
    report_pid = r.json()["public_id"]
    version_pid = r.json()["current_version"]["public_id"]

    from app.models.processing_job import ProcessingJob
    from app.workers import _HANDLERS
    job = session.exec(select(ProcessingJob).where(ProcessingJob.job_type == "generate_report")).first()
    _HANDLERS["generate_report"](session, job)

    v = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}", headers=auth(w["admin"])).json()
    assert v["status"] == "generated", v
    assert "docx" in v["formats"] and v["content_hash"]

    url = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}/download",
                     headers=auth(w["admin"])).json()["url"]
    doc = Document(io.BytesIO(client.get(url, headers=auth(w["admin"])).content))
    text = "\n".join(p.text for p in doc.paragraphs)
    cells = "\n".join(c.text for t in doc.tables for row in t.rows for c in row.cells)
    assert "SECTION A: GENERAL DISCLOSURES" in text and "SECTION C" in text and "Principle 9" in text
    assert "NUVAMA WEALTH MANAGEMENT LIMITED" in cells          # entity name from A.I
    assert "35,97,43,580" in cells                              # Indian grouping
    assert "96.79" in cells and "112.19" not in cells            # correct, computed percentage
    assert "FY 2024-25 (Current FY)" in cells and "FY ____" not in text + cells
    assert "Please attach the HR register" not in text + cells  # review comment never printed
    assert "Confirm the denominator" not in text + cells
    assert "DRAFT" not in text
    assert "ANNEXURE: DATA SOURCES" in text and "Diversity" in cells and "Employee Well-being" in cells

    if shutil.which("soffice"):
        r = client.get(f"/api/v1/reports/{report_pid}/versions/{version_pid}/download?format=pdf",
                       headers=auth(w["admin"]))
        assert r.status_code == 200
        pdf = client.get(r.json()["url"], headers=auth(w["admin"])).content
        assert pdf[:4] == b"%PDF"

    # Snapshot immutability: reopen and edit after generation; the existing version is unaffected
    r = client.post(f"{API}/response-sets/{set_id}/sections/A/reopen", json={"reason": "Typo in address"},
                    headers=auth(w["uploader"]))
    assert r.status_code == 200 and r.json()["status"] == "draft"
    rev = r.json()["answers"]["A.I"]["revision"]
    fields = dict(brsr_fixture.answers()["A.I"]["fields"], name="CHANGED NAME LTD")
    assert _save(client, w["uploader"], set_id, {"A.I": {"fields": fields}}, {"A.I": rev}).status_code == 200
    doc2 = Document(io.BytesIO(client.get(url, headers=auth(w["admin"])).content))
    cells2 = "\n".join(c.text for t in doc2.tables for row in t.rows for c in row.cells)
    assert "NUVAMA WEALTH MANAGEMENT LIMITED" in cells2 and "CHANGED NAME LTD" not in cells2
    # ...and a new version cannot be cut from unapproved answers
    r = client.post(f"/api/v1/reports/{report_pid}/versions/{version_pid}/regenerate", headers=auth(w["admin"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "brsr_not_ready"


def test_submitter_cannot_review_own_section(client, session):
    w = _world(session)
    # A consultant both fills (dataset:create/submit) and reviews (dataset:review)
    set_id = client.post(f"{API}/response-sets", json={"financial_year": "2024-25", "company_id": w["co"].id},
                         headers=auth(w["reviewer"])).json()["public_id"]
    data = brsr_fixture.answers()
    _save(client, w["reviewer"], set_id, {q: v for q, v in data.items() if q.startswith("P4.")})
    approver = make_user(session, "appr@client.com", "client", "Client Approver", company=w["co"])
    _approve_answers(client, w["reviewer"], approver, set_id, "P4")
    assert client.post(f"{API}/response-sets/{set_id}/sections/P4/submit", headers=auth(w["reviewer"])).status_code == 200
    r = client.post(f"{API}/response-sets/{set_id}/sections/P4/decide",
                    json={"decision": "approved", "note": "self"}, headers=auth(w["reviewer"]))
    assert r.status_code == 403 and r.json()["error"]["code"] == "segregation_of_duties"
    q = client.get(f"{API}/review-queue", headers=auth(w["reviewer"])).json()
    assert q[0]["own_submission"] is True


def test_withdraw_and_decision_rules(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _save(client, w["uploader"], set_id, {q: v for q, v in brsr_fixture.answers().items() if q.startswith("P4.")})
    _approve_answers(client, w["uploader"], w["reviewer"], set_id, "P4")
    base = f"{API}/response-sets/{set_id}/sections/P4"
    assert client.post(f"{base}/submit", headers=auth(w["uploader"])).status_code == 200
    assert client.post(f"{base}/submit", headers=auth(w["uploader"])).status_code == 409
    assert client.post(f"{base}/withdraw", headers=auth(w["uploader"])).json()["status"] == "draft"
    assert client.post(f"{base}/decide", json={"decision": "approved", "note": "x"},
                       headers=auth(w["reviewer"])).status_code == 409  # not submitted
    client.post(f"{base}/submit", headers=auth(w["uploader"]))
    assert client.post(f"{base}/decide", json={"decision": "approved", "note": " "},
                       headers=auth(w["reviewer"])).status_code == 422
    assert client.post(f"{base}/decide", json={"decision": "rejected", "note": "x"},
                       headers=auth(w["reviewer"])).status_code == 422
    assert client.post(f"{base}/decide", json={"decision": "changes_requested", "note": "x",
                                               "question_comments": [{"question_id": "A.19c", "body": "x"}]},
                       headers=auth(w["reviewer"])).status_code == 422  # comment on another section's question
    assert client.post(f"{base}/reopen", json={"reason": "x"}, headers=auth(w["uploader"])).status_code == 409
    assert client.post(f"{base}/decide", json={"decision": "approved", "note": "fine"},
                       headers=auth(w["reviewer"])).status_code == 200
    assert client.post(f"{base}/reopen", json={"reason": ""}, headers=auth(w["uploader"])).status_code == 422


def test_previous_year_is_carried_forward_from_approved_answers(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"], "2024-25")
    _fill_all(client, w["uploader"], set_id)
    _submit_all(client, w["uploader"], set_id, w["reviewer"])
    _approve_all(client, w["reviewer"], set_id)

    new_id = _create(client, w["uploader"], "2025-26")
    p6 = client.get(f"{API}/response-sets/{new_id}/sections/P6", headers=auth(w["uploader"])).json()
    # Sheet figures: last year's approved sheets fill the previous-year column; this year waits for data
    assert p6["answers"]["P6.E1"]["computed"]["ren_elec"]["py_v"] == pytest.approx(4192242.84 * 0.0036)
    assert p6["answers"]["P6.E1"]["computed"]["ren_elec"].get("cy_v") is None
    # Typed figures: last year's approved current-year column becomes this year's previous year
    assert p6["answers"]["P6.E7"]["value"]["cells"]["scope2"]["py_v"] == 1170.35
    a = client.get(f"{API}/response-sets/{new_id}/sections/A", headers=auth(w["uploader"])).json()
    assert a["answers"]["A.I"]["value"]["fields"]["cin"] == "L67110MH1993PLC344643"
    assert a["answers"]["A.I"]["value"]["fields"]["financial_year"] == "2025-26"
    assert a["answers"]["A.19c"]["value"] is None  # narrative is not copied
    hist = session.exec(select(BrsrAnswerHistory).where(BrsrAnswerHistory.source == "carry_forward")).all()
    assert hist


def test_draft_preview_is_watermarked(client, session):
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _save(client, w["uploader"], set_id, {"A.19c": {"text": "Retail and institutional clients."}})
    r = client.get(f"{API}/response-sets/{set_id}/preview", headers=auth(w["uploader"]))
    assert r.status_code == 200
    doc = Document(io.BytesIO(r.content))
    header = "\n".join(p.text for p in doc.sections[0].header.paragraphs)
    body = "\n".join(p.text for p in doc.paragraphs)
    assert "DRAFT" in header and "Not for filing" in header
    assert "Retail and institutional clients." in body and "[Not yet answered]" in body


def test_v2_response_set_carries_its_annual_figures_into_v3(client, session):
    """A set started on bank v2 holds figures in the retired annual form. Those
    with no sheet source move into the cells v3 leaves typed; previous-year
    figures move into the previous-year columns."""
    from app.brsr.upgrade import upgrade_response_set
    from app.models.brsr import BrsrAnswer, BrsrResponseSet

    w = _world(session)
    set_id = _create(client, w["uploader"])
    rs = session.exec(select(BrsrResponseSet).where(BrsrResponseSet.public_id == set_id)).one()
    v2 = {"D.EN": {"values": {"scope1": 0, "scope2": 1170.35, "elec_ren_kwh": 1}, "py": {"scope2": 1548.42,
                                                                                         "elec_ren_kwh": 2695950}},
          "D.FN": {"values": {"turnover": 41693400000, "net_worth": 35000000000, "csr_applicable": "Yes"}},
          "D.OP": {"values": {"states_served": 28}}}
    for qid, value in v2.items():
        session.add(BrsrAnswer(response_set_id=rs.id, section_code=qid[2:], question_id=qid, value=value,
                               revision=1, updated_by=w["uploader"].id))
    rs.bank_version = "sebi-2023-122.v2-annual-data"
    session.add(rs)
    session.commit()
    ov = client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()
    assert ov["upgrade_pending"] is True  # old annual answers are tolerated, not an error

    dry = upgrade_response_set(session, rs, dry_run=True)
    assert dry["figures_filled"] >= 7 and rs.bank_version == "sebi-2023-122.v2-annual-data"
    report = upgrade_response_set(session, rs)
    assert report["figures_filled"] == dry["figures_filled"]

    a = client.get(f"{API}/response-sets/{set_id}/sections/A", headers=auth(w["uploader"])).json()["answers"]
    assert a["A.24"]["value"]["fields"] == {"turnover": 41693400000, "net_worth": 35000000000, "csr_applicable": "Yes"}
    assert a["A.19a"]["value"]["cells"]["national"]["number"] == 28
    p6 = client.get(f"{API}/response-sets/{set_id}/sections/P6", headers=auth(w["uploader"])).json()["answers"]
    assert p6["P6.E7"]["value"]["cells"]["scope2"] == {"cy_v": 1170.35, "py_v": 1548.42}
    # Electricity is built from the sheets now; the v2 current-year figure is not copied
    assert p6["P6.E1"]["computed"]["ren_elec"]["cy_v"] == pytest.approx(4192242.84 * 0.0036)
    hist = session.exec(select(BrsrAnswerHistory).where(BrsrAnswerHistory.source == "bank_upgrade")).all()
    assert {h.question_id for h in hist} >= {"A.24", "P6.E7"}
    # Idempotent and never overwrites later edits
    rs.bank_version = "sebi-2023-122.v2-annual-data"
    assert upgrade_response_set(session, rs)["figures_filled"] == 0


def test_v2_approvals_are_not_carried_over_to_different_sheet_figures(client, session, tmp_path):
    """A section approved on v2's annual figures is flagged when the data
    sheets give different figures, and left alone where they agree."""
    from app.brsr.upgrade import upgrade_response_set
    from app.models.brsr import BrsrAnswer, BrsrResponseSet, BrsrSection
    _set_storage(tmp_path)
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _fill_all(client, w["uploader"], set_id)
    _submit_all(client, w["uploader"], set_id, w["reviewer"])
    _approve_all(client, w["reviewer"], set_id)
    rs = session.exec(select(BrsrResponseSet).where(BrsrResponseSet.public_id == set_id)).one()
    m = brsr_fixture.sheet_metrics()
    # v2 annual figures: the same as the sheets except the female headcount (P3, A, P5 use it)
    cy = {k: v for k, v in m["cy"].items()}
    cy["emp_perm_f"] = 800
    session.add(BrsrAnswer(response_set_id=rs.id, section_code="WF", question_id="D.WF",
                           value={"values": cy, "py": m["py"]}, revision=1, updated_by=w["uploader"].id))
    for sec in session.exec(select(BrsrSection).where(BrsrSection.response_set_id == rs.id)).all():
        sec.approved_data_hash = None  # as approved before this release
        session.add(sec)
    rs.bank_version = "sebi-2023-122.v2-annual-data"
    session.add(rs)
    session.commit()
    report = upgrade_response_set(session, rs)
    assert {"A", "P3", "P6"} <= set(report["approvals_fingerprinted"])
    changed = client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()["data_changed_sections"]
    assert {"A", "P3"} <= set(changed)   # headcount differs from what was approved
    assert "P6" not in changed            # energy and water agree


# ===========================================================================
# Per-question workflow: SPOCs answer, department reviewers approve
# ===========================================================================

def _hr_question(section="P3"):
    return next(q["id"] for q in QUESTIONS if q["section"] == section and q["source"] == "qualitative"
                and q["type"] == "text" and "HR" in q["departments"])


def test_department_roster_and_question_review(client, session):
    w = _world(session)
    approver = make_user(session, "appr@client.com", "client", "Client Approver", company=w["co"])
    other_spoc = make_user(session, "u2@client.com", "client", "Client Uploader", company=w["co"])
    set_id = _create(client, w["uploader"])
    qid = _hr_question()
    roster = {"entries": [{"department": "HR", "user_id": w["uploader"].id, "role": "spoc"},
                          {"department": "HR", "user_id": approver.id, "role": "reviewer"}]}
    # Only user administrators manage the roster; it holds this company's people only
    assert client.put(f"{API}/roster", json=roster, headers=auth(w["uploader"])).status_code == 403
    bad = {"entries": roster["entries"] + [{"department": "HR", "user_id": w["outsider"].id, "role": "spoc"}]}
    assert client.put(f"{API}/roster", json=bad, headers=auth(w["admin"])).status_code == 422
    both = {"entries": [{"department": "HR", "user_id": approver.id, "role": "spoc"},
                        {"department": "HR", "user_id": approver.id, "role": "reviewer"}]}
    r = client.put(f"{API}/roster", json=both, headers=auth(w["admin"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "segregation_of_duties"
    r = client.put(f"{API}/roster", json=roster, headers=auth(w["admin"]))
    assert r.status_code == 200, r.text
    hr = next(d for d in r.json()["departments"] if d["department"] == "HR")
    assert [p["user_id"] for p in hr["spocs"]] == [w["uploader"].id] and hr["questions"] > 0

    # Only HR's SPOC answers HR questions
    assert _save(client, other_spoc, set_id, {qid: {"text": "x"}}).status_code == 403
    r = _save(client, w["uploader"], set_id, {qid: {"text": "Policy and practice described."}})
    assert r.status_code == 200 and r.json()["workflow"][qid]["can_answer"] is True
    # Sent for review; only HR's named reviewer can decide (not Deloitte, not the SPOC)
    r = client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": [qid]},
                    headers=auth(w["uploader"]))
    assert r.status_code == 200 and r.json()[qid]["status"] == "submitted"
    queue = client.get(f"{API}/question-queue", headers=auth(approver)).json()
    assert [x["question_id"] for x in queue] == [qid]
    assert client.get(f"{API}/question-queue", headers=auth(w["reviewer"])).json() == []
    for who in (w["uploader"], w["reviewer"]):
        r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                        json={"question_ids": [qid], "decision": "approved"}, headers=auth(who))
        assert r.status_code == 403, who.email
    # Returned with a note -> a comment the SPOC sees; editing reopens it
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": [qid], "decision": "returned"}, headers=auth(approver))
    assert r.status_code == 422  # a note is required
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": [qid], "decision": "returned", "note": "Name the policy."},
                    headers=auth(approver))
    assert r.status_code == 200 and r.json()[qid]["status"] == "returned"
    sec = client.get(f"{API}/response-sets/{set_id}/sections/P3", headers=auth(w["uploader"])).json()
    assert any(c["question_id"] == qid and c["body"] == "Name the policy." for c in sec["comments"])
    rev = sec["answers"][qid]["revision"]
    r = _save(client, w["uploader"], set_id, {qid: {"text": "The POSH policy is described."}}, {qid: rev})
    assert r.json()["workflow"][qid]["status"] == "open"
    client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": [qid]},
                headers=auth(w["uploader"]))
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": [qid], "decision": "approved"}, headers=auth(approver))
    assert r.status_code == 200 and r.json()[qid]["status"] == "approved"
    # An approval covers that revision only: any later edit needs approving again
    rev = client.get(f"{API}/response-sets/{set_id}/sections/P3", headers=auth(w["uploader"])).json()[
        "answers"][qid]["revision"]
    r = _save(client, w["uploader"], set_id, {qid: {"text": "Changed after approval."}}, {qid: rev})
    assert r.json()["workflow"][qid]["status"] == "open"
    # Questions built from the data sheets are not reviewed one by one
    r = client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": ["P3.E9"]},
                    headers=auth(w["uploader"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "not_reviewed_per_question"


def test_reviewer_cannot_approve_an_answer_they_wrote(client, session):
    w = _world(session)
    approver = make_user(session, "appr@client.com", "client", "Client Approver", company=w["co"])
    set_id = _create(client, w["uploader"])
    # The approver edits the answer the SPOC submitted: four eyes means they cannot approve it now
    _save(client, w["uploader"], set_id, {"A.19c": {"text": "Wealth clients."}})
    client.post(f"{API}/response-sets/{set_id}/questions/submit", json={"question_ids": ["A.19c"]},
                headers=auth(w["uploader"]))
    other = make_user(session, "u3@client.com", "client", "Client Uploader", company=w["co"])
    assert _save(client, other, set_id, {"A.19c": {"text": "Edited."}}, {"A.19c": 1}).status_code == 200
    r = client.post(f"{API}/response-sets/{set_id}/questions/review",
                    json={"question_ids": ["A.19c"], "decision": "approved"}, headers=auth(approver))
    assert r.status_code == 409  # edited after submission -> it is open again, not awaiting review


def test_sheet_data_changed_after_approval_holds_the_report(client, session, tmp_path):
    from datetime import date
    from app.models.dataset import Dataset, DatasetVersion
    from app.models.kpi import KpiValue
    from app.models.upload_type import UploadType
    _set_storage(tmp_path)
    w = _world(session)
    set_id = _create(client, w["uploader"])
    _fill_all(client, w["uploader"], set_id)
    _submit_all(client, w["uploader"], set_id, w["reviewer"])
    _approve_all(client, w["reviewer"], set_id)
    body = {"reporting_period_start": "2024-04-01", "reporting_period_end": "2025-03-31", "report_type": "brsr"}

    # A corrected Diversity sheet is approved after the BRSR sections were approved
    ut = session.exec(select(UploadType).where(UploadType.code == "sheet_diversity")).one()
    ds = session.exec(select(Dataset).where(Dataset.upload_type_id == ut.id)).one()
    old = session.exec(select(KpiValue).where(KpiValue.dataset_id == ds.id)).all()
    v2 = DatasetVersion(dataset_id=ds.id, version_number=2, status="approved", uploaded_by=w["uploader"].id)
    session.add(v2)
    session.flush()
    for kv in old:
        val = kv.value + 10 if kv.kpi_code.endswith("Permanent_Male_including_differently_abled") and \
            kv.reporting_period_start >= date(2024, 4, 1) else kv.value
        session.add(KpiValue(dataset_id=ds.id, dataset_version_id=v2.id, source_file_id=kv.source_file_id,
                             source_row_number=kv.source_row_number, company_id=kv.company_id,
                             kpi_code=kv.kpi_code, value=val, unit=kv.unit, attributes=kv.attributes,
                             reporting_period_start=kv.reporting_period_start,
                             reporting_period_end=kv.reporting_period_end))
    session.commit()

    ov = client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()
    changed = ov["data_changed_sections"]
    assert {"A", "P3", "P5"} <= set(changed) and "B" not in changed
    r = client.post("/api/v1/reports", json=body, headers=auth(w["admin"]))
    assert r.status_code == 422 and r.json()["error"]["code"] == "brsr_data_changed"
    # Reopen and re-approve: answers are unchanged, so no new question approvals are needed
    for code in changed:
        assert client.post(f"{API}/response-sets/{set_id}/sections/{code}/reopen", json={"reason": "Sheet corrected"},
                           headers=auth(w["uploader"])).status_code == 200
        assert client.post(f"{API}/response-sets/{set_id}/sections/{code}/submit",
                           headers=auth(w["uploader"])).status_code == 200
        r = client.post(f"{API}/response-sets/{set_id}/sections/{code}/decide",
                        json={"decision": "approved", "note": "Re-checked with the corrected sheet."},
                        headers=auth(w["reviewer"]))
        assert r.status_code == 200 and r.json()["data_changed"] is False
    assert client.get(f"{API}/response-sets/{set_id}", headers=auth(w["uploader"])).json()["data_changed_sections"] == []
    p3 = client.get(f"{API}/response-sets/{set_id}/sections/P3", headers=auth(w["uploader"])).json()
    assert p3["answers"]["P3.E1a"]["computed"]["perm_male"]["total"] == 2561
    assert client.post("/api/v1/reports", json=body, headers=auth(w["admin"])).status_code == 201
