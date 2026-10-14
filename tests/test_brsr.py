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

def test_bank_covers_every_sebi_section():
    assert SECTION_CODES == ["A", "B"] + [f"P{i}" for i in range(1, 10)]
    for code in SECTION_CODES:
        assert any(q["section"] == code for q in QUESTIONS), code
    # Spot-check SEBI items that must exist
    for qid in ("A.I", "A.20a", "A.25", "B.1", "B.10", "P1.E9", "P3.E9", "P5.E7", "P6.E1", "P6.E7", "P6.E9",
                "P8.E5", "P9.E7"):
        assert qid in QUESTIONS_BY_ID
    # Leadership indicators are voluntary under SEBI's format
    assert all(not q["required"] for q in QUESTIONS if q.get("indicator") == "leadership")


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


def test_computed_cells_are_never_stored_even_if_sent():
    v = engine.normalize_answer("P3.E9", {"cells": {"emp_male": {"cy_total": 10, "cy_reviewed": 5, "cy_pct": 99},
                                                    "emp_total": {"cy_total": 999}}})
    assert v["cells"] == {"emp_male": {"cy_total": 10, "cy_reviewed": 5}}


def test_the_112_percent_error_in_the_reference_filing_is_impossible():
    # The filing divided male reviews (2,862) by permanent male employees (2,551).
    a = {"P3.E9": engine.normalize_answer("P3.E9", {"cells": {"emp_male": {"cy_total": 2551, "cy_reviewed": 2862}}})}
    issues = engine.validate_answer("P3.E9", a["P3.E9"], a)
    assert any(i["level"] == "error" and "cannot exceed" in i["message"] for i in issues)
    a = {"P3.E9": engine.normalize_answer("P3.E9", {"cells": {"emp_male": {"cy_total": 2957, "cy_reviewed": 2862}}})}
    assert engine.validate_answer("P3.E9", a["P3.E9"], a) == []
    assert round(engine.compute_table("P3.E9", a)["emp_male"]["cy_pct"], 2) == 96.79


def test_totals_rows_are_computed_and_constraints_apply_to_them():
    a = {"A.20a": engine.normalize_answer("A.20a", {"cells": {
        "employees_perm": {"total": 10, "male": 6, "female": 4},
        "employees_other": {"total": 5, "male": 2, "female": 3}}})}
    g = engine.compute_table("A.20a", a)
    assert g["employees_total"] == {"total": 15, "male": 8, "male_pct": pytest.approx(53.333, 0.01),
                                    "female": 7, "female_pct": pytest.approx(46.667, 0.01)}


def test_intensities_use_the_single_revenue_figure_and_ppp():
    a = {
        "P6.DEN": engine.normalize_answer("P6.DEN", {"cells": {"revenue": {"cy_v": 1_000_000}, "ppp_factor": {"cy_v": 20}}}),
        "P6.E1": engine.normalize_answer("P6.E1", {"cells": {"ren_elec": {"cy_v": 300}, "non_elec": {"cy_v": 700}}}),
    }
    g = engine.compute_table("P6.E1", a)
    assert g["total"]["cy_v"] == 1000
    assert g["int_rupee"]["cy_v"] == pytest.approx(0.001)
    assert g["int_ppp"]["cy_v"] == pytest.approx(0.02)
    assert g["int_rupee"]["py_v"] is None  # no previous-year revenue -> blank, never 0


def test_not_applicable_needs_a_reason():
    a = {"P6.E4": engine.normalize_answer("P6.E4", {"na": True, "na_reason": ""})}
    assert any(i["level"] == "error" for i in engine.validate_answer("P6.E4", a["P6.E4"], a))
    assert not engine.is_answered(QUESTIONS_BY_ID["P6.E4"], a["P6.E4"])


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
    a = {"A.19b": engine.normalize_answer("A.19b", {"value": "120"})}
    assert any("100%" in i["message"] for i in engine.validate_answer("A.19b", a["A.19b"], a))


def test_cross_question_consistency_warning():
    a = {
        "A.20a": engine.normalize_answer("A.20a", {"cells": {"employees_perm": {"total": 10, "male": 6, "female": 4}}}),
        "P3.E1a": engine.normalize_answer("P3.E1a", {"cells": {"perm_male": {"total": 7}}}),
    }
    w = engine.consistency_warnings(a)
    assert any("P3 1(a)" in x["message"] and "7 vs 6" in x["message"] for x in w)


def test_structurally_invalid_payloads_are_rejected():
    for qid, raw in [("P3.E9", {"cells": {"nope": {}}}), ("P3.E9", {"cells": {"emp_male": {"nope": 1}}}),
                     ("A.I", {"fields": {"hacker": 1}}), ("B.1", {"cells": {"covers": {"P10": "Yes"}}}),
                     ("B.2", {"span": {"translated": "Yes"}}), ("A.16", {"rows": "x"}), ("NOPE", {})]:
        with pytest.raises(engine.PayloadError):
            engine.normalize_answer(qid, raw)


def test_fixture_is_complete_and_clean():
    norm = {q: engine.normalize_answer(q, v) for q, v in brsr_fixture.answers().items()}
    norm["A.I"]["fields"]["financial_year"] = "2024-25"
    for code in SECTION_CODES:
        r = engine.section_report(code, norm)
        assert r["ready_to_submit"], (code, r["missing"], r["errors"])
        assert r["warnings"] == [], (code, r["warnings"])


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


def _submit_all(client, user, set_id):
    for code in SECTION_CODES:
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
    assert [s["status"] for s in ov["sections"]] == ["draft"] * 11
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
    r = _save(client, w["uploader"], set_id, {"P3.E9": {"cells": {"emp_male": {"cy_total": 2551, "cy_reviewed": 2862}}}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["saved"] == ["P3.E9"]
    a = body["answers"]["P3.E9"]
    assert a["revision"] == 1
    assert round(a["computed"]["emp_male"]["cy_pct"], 2) == 112.19
    assert any("cannot exceed" in i["message"] for i in a["issues"])
    assert body["sections"]["P3"]["ready_to_submit"] is False


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
    _submit_all(client, w["uploader"], set_id)

    # Locked while under review
    r = _save(client, w["uploader"], set_id, {"A.19c": {"text": "edit"}}, {"A.19c": 1})
    assert r.status_code == 409 and r.json()["error"]["code"] == "section_locked"

    # Review queue shows the 11 submitted sections to the reviewer
    q = client.get(f"{API}/review-queue", headers=auth(w["reviewer"])).json()
    assert len(q) == 11 and {x["section"] for x in q} == set(SECTION_CODES)

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
    assert _save(client, w["uploader"], set_id, {"P3.E15": {"text": "No incidents. HR register attached."}},
                 {"P3.E15": rev}).status_code == 200
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
    _submit_all(client, w["uploader"], set_id)
    _approve_all(client, w["reviewer"], set_id)

    new_id = _create(client, w["uploader"], "2025-26")
    p6 = client.get(f"{API}/response-sets/{new_id}/sections/P6", headers=auth(w["uploader"])).json()
    cells = p6["answers"]["P6.E1"]["value"]["cells"]
    assert cells["ren_elec"] == {"py_v": 4192242.84}  # last year's current FY -> this year's previous FY
    assert "cy_v" not in cells["ren_elec"]
    a = client.get(f"{API}/response-sets/{new_id}/sections/A", headers=auth(w["uploader"])).json()
    assert a["answers"]["A.I"]["value"]["fields"]["cin"] == "L67110MH1993PLC344643"
    assert a["answers"]["A.I"]["value"]["fields"]["financial_year"] == "2025-26"
    assert a["answers"]["A.22"]["value"]["cells"]["perm_employees"] == {"py_male": 23.38, "py_female": 26.47,
                                                                        "py_total": 24.12}
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
