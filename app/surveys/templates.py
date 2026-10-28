"""Built-in survey and assessment templates.

Kept in code (not the database) so they are versioned with the platform and
every company sees the same, reviewed wording. Creating a survey from a
template copies the definition; the copy is then the company's own to edit.
Companies can also save any of their surveys as a company template
(``surveys.is_template``).
"""
from __future__ import annotations

import copy
from typing import Any, Optional

from app.surveys.schema import normalize_definition


def o(label: str, score: Optional[float] = None, *, oid: str | None = None, na: bool = False,
      exclusive: bool = False) -> dict:
    d: dict[str, Any] = {"id": oid, "label": label}
    if score is not None:
        d["score"] = score
    if na:
        d["na"] = True
    if exclusive:
        d["exclusive"] = True
    return d


def q(qid: str, qtype: str, title: str, *, help: str = "", required: bool = True, **kw) -> dict:
    d = {"id": qid, "type": qtype, "title": title, "help": help, "required": required, **kw}
    for part in ("options", "rows", "columns"):
        for i, item in enumerate(d.get(part, []), 1):
            item["id"] = item.get("id") or f"{part[0]}{i}"
    return d


def when(question: str, op: str, value: Any = None) -> dict:
    rule = {"question": question, "op": op}
    if value is not None:
        rule["value"] = value
    return {"match": "all", "rules": [rule]}


YES_NO = [o("Yes", oid="yes"), o("No", oid="no")]


def yes_no(qid, title, yes=4, no=0, **kw):
    return q(qid, "yes_no", title, options=[o("Yes", yes, oid="yes"), o("No", no, oid="no")], **kw)


AGREE = [o("Strongly disagree", 1), o("Disagree", 2), o("Neutral", 3), o("Agree", 4), o("Strongly agree", 5)]


# ---------------------------------------------------------------------------

ESG_MATURITY = {
    "id": "tpl_esg_maturity", "kind": "assessment", "category": "ESG maturity",
    "title": "ESG maturity self-assessment",
    "description": "Where the business stands on governance, environment, social and disclosure — "
                   "a scored baseline to plan the next steps against.",
    "welcome": "This assessment takes about 15 minutes. Answer for the business as it is today, not as planned. "
               "Where a practice exists only in part of the business, choose the answer that describes most of it.",
    "thank_you": "Thank you. Your answers have been recorded.",
    "anonymous": False,
    "definition": {
        "sections": [
            {"id": "gov", "title": "Governance and strategy", "description": "How ESG is owned and steered.",
             "questions": [
                 q("gov1", "single_choice", "Is there a sustainability / ESG policy?", options=[
                     o("No", 0), o("Being drafted", 1), o("Approved by management", 2),
                     o("Approved by the board", 3), o("Board-approved and reviewed in the last two years", 4)]),
                 q("gov2", "single_choice", "Who oversees ESG performance?", options=[
                     o("No one specifically", 0), o("A function head (e.g. HR, EHS, CSR)", 1),
                     o("A management committee", 2), o("A board committee with ESG in its charter", 4)]),
                 q("gov3", "single_choice", "Are ESG targets part of senior management's performance goals or pay?",
                   options=[o("No", 0), o("Under consideration", 1), o("Yes, for some leaders", 3),
                            o("Yes, for the CEO and the leadership team", 4)]),
                 q("gov4", "single_choice", "Have you assessed which ESG issues are material to the business?",
                   options=[o("Not yet", 0), o("Informally", 1), o("Formally, more than three years ago", 2),
                            o("Formally in the last three years, with stakeholder input", 4)],
                   help="Materiality: the issues that matter most to the business and its stakeholders "
                        "(BRSR question A.26)."),
                 q("gov5", "long_text", "In a few lines, what is the business's ESG strategy?", required=False),
             ]},
            {"id": "env", "title": "Environment", "description": "Climate, resources and environmental risk.",
             "questions": [
                 q("env1", "single_choice", "Do you measure Scope 1 and Scope 2 greenhouse-gas emissions?", options=[
                     o("No", 0), o("For some sites or sources", 1), o("Fully, every year", 3),
                     o("Fully, and verified by a third party", 4)]),
                 q("env2", "single_choice", "Do you measure Scope 3 (value-chain) emissions?", options=[
                     o("No", 0), o("A first screening has been done", 1), o("The main categories are measured", 3),
                     o("Measured and verified", 4)]),
                 q("env3", "single_choice", "Do you have an emissions-reduction target?", options=[
                     o("No", 0), o("An internal target", 2), o("A public, time-bound target", 3),
                     o("A science-based target (validated)", 4)]),
                 q("env4", "multi_choice", "Which of these do you track regularly?", options=[
                     o("Energy use", 1), o("Water withdrawal and discharge", 1), o("Waste by type and disposal", 1),
                     o("Air emissions other than GHG", 1), o("Impacts on biodiversity", 1),
                     o("None of these", 0, exclusive=True)]),
                 q("env5", "percent", "Roughly what share of your electricity comes from renewable sources?",
                   required=False, help="Leave blank if you don't know."),
                 q("env6", "single_choice", "Have you assessed physical and transition climate risks?", options=[
                     o("No", 0), o("Qualitatively", 2), o("With scenario analysis", 3),
                     o("With scenario analysis, and the results drive decisions", 4)]),
             ]},
            {"id": "soc", "title": "Social", "description": "People, human rights and communities.",
             "questions": [
                 q("soc1", "single_choice", "Is there a human-rights policy?", options=[
                     o("No", 0), o("Covering our own operations", 2),
                     o("Covering our operations and our value chain", 3),
                     o("Covering both, with due diligence carried out", 4)]),
                 q("soc2", "single_choice", "Do you track health and safety incidents (e.g. LTIFR)?", options=[
                     o("No", 0), o("For employees only", 2), o("For employees and contract workers", 3),
                     o("For both, with targets and root-cause reviews", 4)]),
                 q("soc3", "matrix", "How established are these people practices?", rows=[
                     o("Skill and career training"), o("Health and well-being benefits"),
                     o("A grievance mechanism open to all workers"), o("Equal-pay review by gender"),
                     o("Diversity targets")],
                   columns=[o("Not in place", 0), o("Partly", 1), o("Fully", 2)]),
                 q("soc4", "single_choice", "How do you run community / CSR programmes?", options=[
                     o("We don't", 0), o("Donations and ad-hoc activities", 1),
                     o("Planned programmes with outputs tracked", 3),
                     o("Planned programmes with impact assessed", 4),
                     o("Not applicable to our business", na=True)]),
             ]},
            {"id": "dis", "title": "Data and disclosure", "description": "How ESG figures are produced and reported.",
             "questions": [
                 q("dis1", "single_choice", "How is ESG data collected?", options=[
                     o("It isn't, yet", 0), o("Spreadsheets shared by e-mail", 1),
                     o("Standard templates with an owner per figure", 2),
                     o("A system with review steps and an audit trail", 4)]),
                 q("dis2", "single_choice", "What do you publish?", options=[
                     o("Nothing yet", 0), o("A section in the annual report", 1), o("A BRSR / sustainability report", 2),
                     o("…with limited assurance", 3), o("…with reasonable assurance", 4)]),
                 q("dis3", "yes_no", "Are ESG figures reviewed by someone other than the person who prepared them "
                                     "before they are published?", options=[o("Yes", 4, oid="yes"), o("No", 0, oid="no")]),
             ]},
        ],
        "bands": [
            {"min": 0, "label": "Beginning", "description": "ESG is not yet managed systematically. Start with a policy, "
                                                         "an owner and a materiality assessment."},
            {"min": 30, "label": "Developing", "description": "Foundations exist. Close the gaps in measurement and "
                                                          "extend policies to the value chain."},
            {"min": 55, "label": "Established", "description": "ESG is managed and measured. Set public targets, link "
                                                           "them to pay and seek assurance."},
            {"min": 80, "label": "Leading", "description": "ESG is embedded and assured. Keep raising ambition and "
                                                       "help the value chain follow."},
        ],
    },
}

SUPPLIER_DD = {
    "id": "tpl_supplier_dd", "kind": "assessment", "category": "Supplier due diligence",
    "title": "Supplier ESG due diligence",
    "description": "Screen suppliers and other value-chain partners on labour, health and safety, environment and "
                   "ethics. Supports BRSR P3.L5, P5.L4 and P6.L7 (assessment of value-chain partners).",
    "welcome": "We ask our suppliers about how they manage environmental, social and governance matters. "
               "It takes about 15 minutes. You can save and come back to it with the same link.",
    "thank_you": "Thank you for completing the questionnaire. We will be in touch if we have questions.",
    "anonymous": False,
    "definition": {
        "sections": [
            {"id": "about", "title": "About your company", "questions": [
                q("sup_name", "short_text", "Company name"),
                q("sup_type", "dropdown", "What do you mainly supply?", options=[
                    o("Raw materials or components"), o("Finished goods"), o("Services"), o("Logistics"),
                    o("Contract labour"), o("Other")]),
                q("sup_emp", "number", "Number of employees and workers", integer=True, min=0, required=False),
                q("sup_loc", "short_text", "Main operating locations", required=False),
            ]},
            {"id": "labour", "title": "Labour and human rights", "description": "BRSR P5.L4", "questions": [
                yes_no("lab1", "Do you have a written policy prohibiting child labour and forced labour?"),
                q("lab2", "single_choice", "Are all workers paid at least the applicable minimum wage?", options=[
                    o("Yes, all employees and contract workers", 4), o("Yes, for employees only", 2), o("No", 0)]),
                yes_no("lab3", "Do you keep records of working hours and overtime for all workers?"),
                yes_no("lab4", "Is there an Internal Committee under the POSH Act (or equivalent)?", required=True,
                       help="Prevention of sexual harassment at the workplace."),
                yes_no("lab5", "Can workers raise grievances anonymously?"),
            ]},
            {"id": "hs", "title": "Health and safety", "description": "BRSR P3.L5", "questions": [
                yes_no("hs1", "Do you have a health and safety policy and a person responsible for it?"),
                yes_no("hs2", "Do you record and investigate work-related injuries?"),
                q("hs3", "single_choice", "Is your health and safety management system certified (e.g. ISO 45001)?",
                  options=[o("Yes", 4), o("Certification in progress", 2), o("No", 0)]),
                q("hs4", "number", "Fatalities in the last 12 months", integer=True, min=0),
                q("hs5", "long_text", "What happened, and what has changed since?", show_if=when("hs4", "gte", 1)),
            ]},
            {"id": "env", "title": "Environment", "description": "BRSR P6.L7", "questions": [
                q("en1", "single_choice", "Do you hold all the environmental permits your operations need?", options=[
                    o("Yes, all valid", 4), o("Some renewals are pending", 2), o("No", 0),
                    o("None are needed for our operations", na=True)]),
                yes_no("en2", "Do you measure your energy use and greenhouse-gas emissions?"),
                yes_no("en3", "Is hazardous waste handled only by authorised vendors?",
                       help="Answer Yes if you produce no hazardous waste."),
                q("en4", "single_choice", "Is your environmental management system certified (e.g. ISO 14001)?",
                  options=[o("Yes", 4), o("Certification in progress", 2), o("No", 0)]),
            ]},
            {"id": "ethics", "title": "Business ethics", "questions": [
                yes_no("et1", "Do you have an anti-bribery and anti-corruption policy?"),
                yes_no("et2", "Do you have a code of conduct that applies to your own suppliers?"),
                q("et3", "yes_no", "Have there been fines, penalties or legal proceedings on environmental, labour or "
                                   "corruption matters in the last three years?", options=YES_NO),
                q("et4", "long_text", "Please describe them and how they were resolved.", show_if=when("et3", "equals", "yes")),
            ]},
            {"id": "evidence", "title": "Supporting documents and declaration", "questions": [
                q("ev1", "file", "Upload policies or certificates that support your answers", required=False, max_files=5,
                  help="PDF, Word, Excel or images, up to 25 MB each."),
                q("ev2", "statement", "By submitting, you confirm the information is accurate to the best of your "
                                      "knowledge and that you will tell us about material changes."),
                q("ev3", "short_text", "Your name and role"),
            ]},
        ],
        "bands": [
            {"min": 0, "label": "High risk", "description": "Significant gaps. Agree a corrective action plan before "
                                                         "continuing or expanding business."},
            {"min": 50, "label": "Medium risk", "description": "Basic controls exist. Follow up on the gaps and re-assess "
                                                           "within a year."},
            {"min": 75, "label": "Low risk", "description": "Good practices in place. Re-assess on the normal cycle."},
        ],
    },
}

CLIMATE_RISK = {
    "id": "tpl_climate_risk", "kind": "assessment", "category": "Climate risk",
    "title": "Climate risk readiness",
    "description": "How well climate-related risks are identified, governed and managed, following the TCFD / "
                   "ISSB structure: governance, strategy, risk management, metrics and targets.",
    "welcome": "Answer for the business as a whole. It takes about 10 minutes.",
    "thank_you": "Thank you. Your answers have been recorded.",
    "anonymous": False,
    "definition": {
        "sections": [
            {"id": "gov", "title": "Governance", "questions": [
                q("g1", "single_choice", "How often does the board discuss climate-related risks?", options=[
                    o("Never", 0), o("When an issue arises", 1), o("Once a year", 2), o("Every quarter or more", 4)]),
                yes_no("g2", "Is a named executive responsible for climate risk?"),
            ]},
            {"id": "strat", "title": "Strategy", "questions": [
                q("s1", "matrix", "How exposed are your operations to these physical hazards?",
                  help="For information — this question is not scored.", required=False,
                  rows=[o("Flooding"), o("Extreme heat"), o("Water scarcity"), o("Cyclones and storms"),
                        o("Sea-level rise")],
                  columns=[o("Not exposed"), o("Low"), o("Medium"), o("High"), o("Don't know")]),
                q("s2", "single_choice", "Have you used climate scenarios to test the business?", options=[
                    o("No", 0), o("Qualitative scenarios", 2), o("Quantified for a 1.5–2 °C and a 3 °C+ world", 4)]),
                q("s3", "single_choice", "Have you assessed transition risks (carbon prices, regulation, demand shifts)?",
                  options=[o("No", 0), o("Informally", 1), o("Yes, for the main products", 3),
                           o("Yes, with financial impact estimated", 4)]),
            ]},
            {"id": "risk", "title": "Risk management", "questions": [
                q("r1", "single_choice", "Are climate risks part of the enterprise risk register?", options=[
                    o("No", 0), o("As a single line item", 2), o("Broken down by risk, each with an owner", 4)]),
                q("r2", "single_choice", "Do new investments consider climate risk?", options=[
                    o("No", 0), o("Sometimes", 2), o("Always, with an internal carbon price", 4)]),
            ]},
            {"id": "metrics", "title": "Metrics and targets", "questions": [
                q("m1", "single_choice", "Which emissions do you measure?", options=[
                    o("None", 0), o("Scope 1 and 2", 2), o("Scope 1, 2 and the main Scope 3 categories", 4)]),
                q("m2", "single_choice", "Do you have a net-zero or interim emissions target?", options=[
                    o("No", 0), o("An internal target", 2), o("A public target with interim milestones", 4)]),
                q("m3", "short_text", "If you have a target, what is it?", required=False,
                  show_if={"match": "any", "rules": [{"question": "m2", "op": "not_equals", "value": "o1"}]}),
            ]},
        ],
        "bands": [
            {"min": 0, "label": "Early", "description": "Climate risk is not yet managed in a structured way."},
            {"min": 40, "label": "Developing", "description": "Climate risk is recognised; quantify it and integrate "
                                                          "it into decisions."},
            {"min": 70, "label": "Advanced", "description": "Climate risk is governed, quantified and managed."},
        ],
    },
}

ISSUES = ["Climate change and emissions", "Energy", "Water", "Waste and circularity", "Biodiversity",
          "Employee health and safety", "Diversity and inclusion", "Human rights", "Data privacy and cyber security",
          "Customer welfare and product responsibility", "Business ethics and anti-corruption", "Community development"]

MATERIALITY = {
    "id": "tpl_materiality", "kind": "survey", "category": "Materiality",
    "title": "Stakeholder materiality survey",
    "description": "Ask stakeholders which ESG issues matter most and how well they are managed — the input for the "
                   "materiality assessment (BRSR A.26) and stakeholder engagement (P4).",
    "welcome": "We'd like to know which environmental, social and governance issues matter most to you. "
               "It takes about 5 minutes.",
    "thank_you": "Thank you for sharing your views.",
    "anonymous": False,
    "definition": {
        "sections": [
            {"id": "you", "title": "About you", "questions": [
                q("who", "single_choice", "Which group best describes you?", allow_other=True, options=[
                    o("Employee"), o("Customer"), o("Investor or shareholder"), o("Supplier or business partner"),
                    o("Local community"), o("Regulator or government"), o("NGO or civil society")]),
            ]},
            {"id": "issues", "title": "Issues", "questions": [
                q("imp", "matrix", "How important is each issue to you?",
                  rows=[o(i) for i in ISSUES],
                  columns=[o("Not important", 1), o("Slightly", 2), o("Moderately", 3), o("Very", 4), o("Critical", 5)]),
                q("perf", "matrix", "How well do you think we manage each issue?", required=False,
                  help="Leave a row blank if you don't know.",
                  rows=[o(i) for i in ISSUES],
                  columns=[o("Poorly", 1), o("Below expectations", 2), o("Adequately", 3), o("Well", 4),
                           o("Very well", 5)]),
                q("other", "long_text", "Is there an issue we've missed, or anything else we should know?", required=False),
            ]},
            {"id": "engage", "title": "Staying in touch", "questions": [
                q("freq", "single_choice", "How often would you like to hear from us on these topics?", required=False,
                  options=[o("Quarterly"), o("Twice a year"), o("Once a year"), o("Only when something important changes")]),
            ]},
        ],
        "bands": [],
    },
}

WELLBEING = {
    "id": "tpl_wellbeing", "kind": "survey", "category": "Employee",
    "title": "Employee well-being pulse",
    "description": "A short, anonymous pulse on safety, workload, fairness and growth, with an employee Net Promoter "
                   "Score. Results by group appear only for groups of five or more.",
    "welcome": "This survey is anonymous: your name is never stored with your answers, and results are shown only "
               "for groups of five or more people. It takes about 3 minutes.",
    "thank_you": "Thank you — your answers are anonymous and will help us improve.",
    "anonymous": True,
    "definition": {
        "sections": [
            {"id": "pulse", "title": "How things are", "questions": [
                q("enps", "nps", "How likely are you to recommend this company as a place to work?"),
                q("role", "single_choice", "Which best describes your role?", options=[
                    o("Permanent employee"), o("Other-than-permanent employee"), o("Worker"), o("Contract worker")]),
                q("agree", "matrix", "How much do you agree?", rows=[
                    o("I feel safe at work"), o("My workload is manageable"),
                    o("I can get well-being support when I need it"),
                    o("I am treated fairly regardless of gender, age or background"),
                    o("I have had useful training in the last year"),
                    o("I know how to raise a concern, and trust it will be handled")],
                  columns=copy.deepcopy(AGREE)),
                q("better", "long_text", "What is one thing we could do better?", required=False),
            ]},
        ],
        "bands": [],
    },
}

CUSTOMER = {
    "id": "tpl_customer", "kind": "survey", "category": "Customer",
    "title": "Customer satisfaction",
    "description": "Net Promoter Score, satisfaction and complaint handling — the input for consumer responsibility "
                   "(BRSR Principle 9).",
    "welcome": "Tell us about your experience. It takes about 3 minutes.",
    "thank_you": "Thank you for your feedback.",
    "anonymous": False,
    "definition": {
        "sections": [
            {"id": "exp", "title": "Your experience", "questions": [
                q("nps", "nps", "How likely are you to recommend us to a friend or colleague?"),
                q("sat", "matrix", "How satisfied are you with…", rows=[
                    o("Quality of our products or services"), o("Value for money"), o("Customer service"),
                    o("Clarity of product and safety information"), o("How we protect your data")],
                  columns=[o("Very dissatisfied", 1), o("Dissatisfied", 2), o("Neutral", 3), o("Satisfied", 4),
                           o("Very satisfied", 5)]),
                q("cmp", "yes_no", "Have you raised a complaint with us in the last 12 months?", options=YES_NO),
                q("cmp_ok", "scale", "How satisfied were you with how it was handled?", min=1, max=5,
                  min_label="Not at all", max_label="Completely", show_if=when("cmp", "equals", "yes")),
                q("why", "long_text", "What is the main reason for your score?", required=False),
            ]},
        ],
        "bands": [],
    },
}

BUILT_IN = [ESG_MATURITY, SUPPLIER_DD, CLIMATE_RISK, MATERIALITY, WELLBEING, CUSTOMER]
BY_ID = {t["id"]: t for t in BUILT_IN}


def built_in(template_id: str) -> Optional[dict]:
    t = BY_ID.get(template_id)
    if not t:
        return None
    t = copy.deepcopy(t)
    t["definition"] = normalize_definition(t["definition"])
    return t


def summary(t: dict) -> dict:
    defn = t["definition"]
    return {"id": t["id"], "built_in": True, "kind": t["kind"], "category": t["category"], "title": t["title"],
            "description": t["description"], "anonymous": t.get("anonymous", False),
            "sections": len(defn["sections"]), "questions": question_count(defn),
            "minutes": estimated_minutes(defn)}


def question_count(defn: dict) -> int:
    return sum(1 for s in defn["sections"] for qq in s["questions"] if qq["type"] != "statement")


def estimated_minutes(defn: dict) -> int:
    """Rough time to complete: ~20 s per choice, a minute per paragraph, ~10 s per matrix row."""
    secs = 0
    for s in defn["sections"]:
        for qq in s["questions"]:
            t = qq["type"]
            secs += 60 if t == "long_text" else 10 * len(qq.get("rows", [])) + 10 if t == "matrix" else \
                45 if t == "file" else 5 if t == "statement" else 20
    return max(1, round(secs / 60))
