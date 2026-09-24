"""Benchmarking demo data + peer comparison engine.

DESIGN NOTE -- why this module is structured the way it is:

The intended production flow is:
  1. A BRSR report is uploaded as XBRL (structured, machine-tagged XML).
  2. A DETERMINISTIC parser reads the tagged facts out of it. An LLM is
     deliberately NOT used for this step -- XBRL is already structured,
     so parsing it with real code gives exact, auditable numbers, while
     asking a model to read it would only introduce error.
  3. A separate, optional Sustainability Report (PDF) upload IS the
     right place for an LLM, because that content is genuinely
     unstructured narrative.
  4. All comparison maths below is plain deterministic code. Any LLM
     layer narrates these numbers; it never produces them.

Until the real XBRL ingestion pipeline exists, this module serves
deterministic DEMO peer data so the full comparison experience can be
built and reviewed. Every response carries is_demo_data: True.

PEER NAMES ARE DELIBERATELY FICTIONAL. Attaching fabricated ESG figures
to real, named listed companies would be misleading the moment anyone
screenshots the page -- so the peer set uses invented company names that
read realistically without impersonating any real filer.

KPI SET: the nine BRSR Core attributes mandated by SEBI, rather than an
invented ESG rubric. This matters because BRSR Core is a fixed,
standardised set every filer reports the same way -- which is exactly
what makes reliable extraction and honest peer comparison possible.
"""
import hashlib

DEMO_DISCLAIMER = (
    "Prototype data. Peer figures are illustrative and generated for "
    "demonstration -- they are not extracted from real BRSR filings, and the "
    "peer companies named are fictional. Replace with real XBRL ingestion "
    "before any external use."
)

# direction: "lower" means a lower value is better performance.
# This is not cosmetic -- percentile ranking inverts based on it, and
# getting it wrong would rank an efficient company as a laggard.
BRSR_CORE_KPIS = [
    {"code": "ghg_intensity", "name": "GHG Emission Intensity", "unit": "tCO2e / \u20b9 cr", "pillar": "Environment", "direction": "lower", "range": (8.0, 46.0)},
    {"code": "energy_intensity", "name": "Energy Intensity", "unit": "GJ / \u20b9 cr", "pillar": "Environment", "direction": "lower", "range": (120.0, 520.0)},
    {"code": "renewable_share", "name": "Renewable Energy Share", "unit": "%", "pillar": "Environment", "direction": "higher", "range": (6.0, 64.0)},
    {"code": "water_intensity", "name": "Water Intensity", "unit": "KL / \u20b9 cr", "pillar": "Environment", "direction": "lower", "range": (40.0, 280.0)},
    {"code": "waste_recycled", "name": "Waste Recycled", "unit": "%", "pillar": "Environment", "direction": "higher", "range": (18.0, 88.0)},
    {"code": "ltifr", "name": "Lost Time Injury Frequency Rate", "unit": "per mn hrs", "pillar": "Social", "direction": "lower", "range": (0.1, 2.4)},
    {"code": "women_workforce", "name": "Women in Workforce", "unit": "%", "pillar": "Social", "direction": "higher", "range": (9.0, 42.0)},
    {"code": "msme_sourcing", "name": "Input Sourced from MSMEs", "unit": "%", "pillar": "Social", "direction": "higher", "range": (12.0, 58.0)},
    {"code": "privacy_complaints", "name": "Data Privacy Complaints", "unit": "count", "pillar": "Governance", "direction": "lower", "range": (0.0, 14.0)},
]
KPI_BY_CODE = {k["code"]: k for k in BRSR_CORE_KPIS}

YOUR_ORG = "Your Organization"

SECTORS = {
    "Manufacturing": [
        "Meridian Industries Ltd", "Sundaram Steelworks Ltd", "Arcadia Cements Ltd",
        "Pinnacle Auto Components Ltd", "Zenith Metals Ltd", "Vantage Textiles Ltd",
    ],
    "Chemicals & Pharma": [
        "Kaveri Chemicals Ltd", "Sterling Pharma Ltd", "Halcyon Specialty Chemicals Ltd",
        "Orbit Life Sciences Ltd", "Trident Agrochem Ltd",
    ],
    "Energy & Utilities": [
        "Everest Power Ltd", "Nimbus Energy Ltd", "Solstice Renewables Ltd",
        "Cascade Utilities Ltd", "Aurora Grid Ltd",
    ],
}
PERIODS = ["FY 2024-25", "FY 2023-24", "FY 2022-23"]


def _seeded(key: str, low: float, high: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return round(low + (h % 10_000) / 10_000 * (high - low), 2)


def _percentile_rank(value: float, population: list[float], direction: str) -> float:
    """Share of the peer population this value outperforms, 0-100.

    Direction-aware: for a "lower is better" KPI, beating a peer means
    having a SMALLER value. Computed as a plain count of peers
    outperformed -- no weighting, no proprietary rubric, so the number
    is fully explainable to anyone who asks how it was derived.
    """
    others = [v for v in population if v != value]
    if not others:
        return 100.0
    if direction == "lower":
        beaten = sum(1 for v in others if value < v)
    else:
        beaten = sum(1 for v in others if value > v)
    return round(beaten / len(others) * 100, 1)


def _companies_for(sector: str) -> list[str]:
    return [YOUR_ORG] + SECTORS.get(sector, [])


def _value_for(company: str, kpi: dict, sector: str, period: str) -> float:
    low, high = kpi["range"]
    return _seeded(f"bm-{company}-{kpi['code']}-{sector}-{period}", low, high)


def get_benchmark_filters() -> dict:
    return {"sectors": list(SECTORS.keys()), "periods": PERIODS, "kpis": BRSR_CORE_KPIS}


def _build_matrix(sector: str, period: str) -> dict:
    """company -> kpi_code -> value, for every company in the sector."""
    companies = _companies_for(sector)
    return {
        c: {k["code"]: _value_for(c, k, sector, period) for k in BRSR_CORE_KPIS}
        for c in companies
    }


def get_benchmark_overview(sector: str, period: str) -> dict:
    """Headline cards + per-KPI comparison table for the whole peer set."""
    matrix = _build_matrix(sector, period)
    companies = list(matrix.keys())
    peer_count = len(companies) - 1

    kpi_rows = []
    for kpi in BRSR_CORE_KPIS:
        population = [matrix[c][kpi["code"]] for c in companies]
        mine = matrix[YOUR_ORG][kpi["code"]]
        peers_only = [matrix[c][kpi["code"]] for c in companies if c != YOUR_ORG]
        best = min(peers_only + [mine]) if kpi["direction"] == "lower" else max(peers_only + [mine])
        best_company = next(c for c in companies if matrix[c][kpi["code"]] == best)
        pct = _percentile_rank(mine, population, kpi["direction"])
        avg = round(sum(peers_only) / len(peers_only), 2)
        # Gap expressed so a positive number always means "room to improve"
        gap = round(abs(mine - best), 2)
        kpi_rows.append({
            "code": kpi["code"], "name": kpi["name"], "unit": kpi["unit"],
            "pillar": kpi["pillar"], "direction": kpi["direction"],
            "your_value": mine, "industry_average": avg, "best_value": best,
            "best_company": best_company, "percentile": pct, "gap_to_best": gap,
        })

    overall_percentile = round(sum(r["percentile"] for r in kpi_rows) / len(kpi_rows), 1)
    ahead_count = sum(1 for r in kpi_rows if r["percentile"] >= 50)

    # Overall rank = position by mean percentile across all KPIs.
    mean_by_company = {}
    for c in companies:
        pcts = [
            _percentile_rank(matrix[c][k["code"]], [matrix[x][k["code"]] for x in companies], k["direction"])
            for k in BRSR_CORE_KPIS
        ]
        mean_by_company[c] = round(sum(pcts) / len(pcts), 1)
    ordered = sorted(mean_by_company.items(), key=lambda kv: kv[1], reverse=True)
    your_rank = next(i + 1 for i, (c, _) in enumerate(ordered) if c == YOUR_ORG)

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "sector": sector, "period": period,
        "peer_count": peer_count,
        "overall_percentile": overall_percentile,
        "overall_rank": your_rank,
        "total_companies": len(companies),
        "kpis_ahead_of_median": ahead_count,
        "kpis_total": len(kpi_rows),
        "methodology": (
            "Percentile = share of peers outperformed on that KPI, direction-aware "
            "(lower is better for intensity and incident metrics). Overall percentile is the "
            "unweighted mean across all nine BRSR Core KPIs -- no proprietary weighting applied."
        ),
        "kpis": kpi_rows,
        "leaderboard": [
            {"company": c, "mean_percentile": p, "rank": i + 1, "is_you": c == YOUR_ORG}
            for i, (c, p) in enumerate(ordered)
        ],
    }


def get_kpi_peer_comparison(sector: str, period: str, kpi_code: str) -> dict:
    """Every company's value for one KPI, sorted best-to-worst."""
    kpi = KPI_BY_CODE.get(kpi_code)
    if not kpi:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}
    matrix = _build_matrix(sector, period)
    rows = [{"company": c, "value": v[kpi_code], "is_you": c == YOUR_ORG} for c, v in matrix.items()]
    rows.sort(key=lambda r: r["value"], reverse=(kpi["direction"] == "higher"))
    for i, r in enumerate(rows):
        r["rank"] = i + 1
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False,
        "kpi": kpi, "rows": rows,
    }


def get_pillar_summary(sector: str, period: str) -> dict:
    """Mean percentile per pillar -- your org vs the peer average."""
    overview = get_benchmark_overview(sector, period)
    pillars = {}
    for row in overview["kpis"]:
        pillars.setdefault(row["pillar"], []).append(row["percentile"])
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "pillars": [
            {"pillar": p, "your_percentile": round(sum(v) / len(v), 1), "peer_baseline": 50.0}
            for p, v in pillars.items()
        ],
    }


def get_trend(sector: str, kpi_code: str) -> dict:
    """Your value vs the peer average for one KPI, across all periods."""
    kpi = KPI_BY_CODE.get(kpi_code)
    if not kpi:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}
    points = []
    for period in reversed(PERIODS):
        matrix = _build_matrix(sector, period)
        peers = [v[kpi_code] for c, v in matrix.items() if c != YOUR_ORG]
        points.append({
            "period": period,
            "your_value": matrix[YOUR_ORG][kpi_code],
            "industry_average": round(sum(peers) / len(peers), 2),
        })
    return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False, "kpi": kpi, "points": points}


def get_ai_insights(sector: str, period: str) -> dict:
    """Narrative built from the real computed numbers above.

    Deliberately assembled in code for this prototype rather than called
    out to a model: it keeps the demo deterministic and offline, and it
    documents exactly which computed facts a real LLM call should be
    handed as context. Swapping this for a real model means passing the
    same overview payload as the prompt -- the numbers do not change.
    """
    o = get_benchmark_overview(sector, period)
    strengths = sorted(o["kpis"], key=lambda r: r["percentile"], reverse=True)[:3]
    weaknesses = sorted(o["kpis"], key=lambda r: r["percentile"])[:3]

    headline = (
        f"Against {o['peer_count']} peers in {sector} for {period}, you rank "
        f"#{o['overall_rank']} of {o['total_companies']} with a mean percentile of "
        f"{o['overall_percentile']}, ahead of the median on {o['kpis_ahead_of_median']} "
        f"of {o['kpis_total']} BRSR Core KPIs."
    )
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "headline": headline,
        "strengths": [
            {"name": s["name"], "detail": f"{s['your_value']} {s['unit']} \u2014 ahead of {s['percentile']}% of peers."}
            for s in strengths
        ],
        "gaps": [
            {
                "name": w["name"],
                "detail": (
                    f"{w['your_value']} {w['unit']} vs best-in-peer {w['best_value']} {w['unit']} "
                    f"({w['best_company']}). Closing this gap would move you past "
                    f"{round(100 - w['percentile'], 1)}% of the peer set."
                ),
                "recommendation": _recommendation_for(w["code"]),
            }
            for w in weaknesses
        ],
        "suggested_questions": [
            "Where am I doing better than the industry leader?",
            f"Why is my {weaknesses[0]['name']} behind peers?",
            "What should I prioritise to improve my overall rank?",
        ],
    }


def _recommendation_for(code: str) -> str:
    return {
        "ghg_intensity": "Target Scope 2 first \u2014 shifting purchased electricity to renewable contracts is usually the fastest intensity reduction available.",
        "energy_intensity": "Run an energy audit on the highest-consuming sites; process heat recovery typically yields the largest early gains.",
        "renewable_share": "Evaluate open-access or captive renewable PPAs; peers ahead of you on this KPI are largely using contracted renewables rather than on-site generation.",
        "water_intensity": "Prioritise closed-loop cooling and treated-water reuse at the highest-withdrawal sites.",
        "waste_recycled": "Segregate at source and formalise recycler contracts \u2014 unsegregated streams are the most common cause of a low recycling rate.",
        "ltifr": "Review near-miss reporting: peers with low LTIFR generally report more near-misses, not fewer, because leading indicators are being captured.",
        "women_workforce": "Look at retention rather than hiring alone; mid-career attrition is usually where the gap opens.",
        "msme_sourcing": "Map current suppliers against MSME registration \u2014 a share of existing vendors often already qualify but are not classified.",
        "privacy_complaints": "Review the complaint intake and closure workflow; unresolved ageing complaints weigh more heavily than volume alone.",
    }.get(code, "Review this KPI against the best-performing peer to identify the practical driver of the gap.")
