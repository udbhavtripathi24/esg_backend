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

# The standard library: the top ten filers we curate per sector. Ten is a
# deliberate figure -- percentile resolution is bounded by peer count
# (with N peers a percentile can only land on multiples of 100/N), so a
# thin library produces coarse, unconvincing rankings.
#
# A client who wants to compare against a company outside this list can
# upload that company's own BRSR filing; it then joins the comparison as
# a custom peer for them alone. See CUSTOM_PEERS below.
SECTORS = {
    "Manufacturing": [
        "Meridian Industries Ltd", "Sundaram Steelworks Ltd", "Arcadia Cements Ltd",
        "Pinnacle Auto Components Ltd", "Zenith Metals Ltd", "Vantage Textiles Ltd",
        "Corbett Engineering Ltd", "Deccan Forgings Ltd", "Marigold Packaging Ltd",
        "Ironwood Fabrication Ltd",
    ],
    "Chemicals & Pharma": [
        "Kaveri Chemicals Ltd", "Sterling Pharma Ltd", "Halcyon Specialty Chemicals Ltd",
        "Orbit Life Sciences Ltd", "Trident Agrochem Ltd", "Bluepeak Biosciences Ltd",
        "Vermilion Dyes Ltd", "Anantha Formulations Ltd", "Crestline Polymers Ltd",
        "Saffron Healthcare Ltd",
    ],
    "Energy & Utilities": [
        "Everest Power Ltd", "Nimbus Energy Ltd", "Solstice Renewables Ltd",
        "Cascade Utilities Ltd", "Aurora Grid Ltd", "Tarawind Power Ltd",
        "Blackridge Coal & Power Ltd", "Lumen Transmission Ltd", "Highvolt Utilities Ltd",
        "Greenspan Hydro Ltd",
    ],
}

# Peers a client added themselves by uploading that company's BRSR. In the
# prototype this is seeded so the flow can be demonstrated; in production
# each entry would be created by a real upload and scoped to the company
# that uploaded it, since it is their own research rather than curated
# library content.
CUSTOM_PEERS = {
    "Manufacturing": ["Kestrel Alloys Ltd (uploaded)"],
    "Chemicals & Pharma": [],
    "Energy & Utilities": [],
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
    return [YOUR_ORG] + SECTORS.get(sector, []) + CUSTOM_PEERS.get(sector, [])


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
                    f"({w['best_company']}). Closing this gap would move you ahead of "
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


# =====================================================================
# Deeper analysis layer
# =====================================================================
# Everything below is still plain deterministic maths on the same peer
# matrix. The AI modes assemble narrative from these computed facts --
# they never invent a figure, which is what keeps the "ask the AI"
# experience trustworthy rather than decorative.


def get_head_to_head(sector: str, period: str, peer: str) -> dict:
    """Direct one-to-one comparison -- answers 'where is A better than B'.

    Returns a per-KPI verdict plus a win/loss tally, using each KPI's own
    direction so 'better' always means genuinely better performance and
    not merely a larger number.
    """
    matrix = _build_matrix(sector, period)
    if peer not in matrix or peer == YOUR_ORG:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}

    rows, you_win, peer_win, ties = [], 0, 0, 0
    for kpi in BRSR_CORE_KPIS:
        mine = matrix[YOUR_ORG][kpi["code"]]
        theirs = matrix[peer][kpi["code"]]
        if mine == theirs:
            verdict, ties = "tie", ties + 1
        elif (kpi["direction"] == "lower" and mine < theirs) or (kpi["direction"] == "higher" and mine > theirs):
            verdict, you_win = "you", you_win + 1
        else:
            verdict, peer_win = "peer", peer_win + 1

        # Percentage difference framed so positive always = you ahead.
        if theirs:
            raw = (mine - theirs) / abs(theirs) * 100
            diff = round(raw if kpi["direction"] == "higher" else -raw, 1)
        else:
            diff = 0.0

        rows.append({
            "code": kpi["code"], "name": kpi["name"], "unit": kpi["unit"],
            "pillar": kpi["pillar"], "direction": kpi["direction"],
            "your_value": mine, "peer_value": theirs,
            "verdict": verdict, "advantage_pct": diff,
        })

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False,
        "peer": peer, "you_win": you_win, "peer_win": peer_win, "ties": ties,
        "rows": rows,
        "summary": (
            f"You outperform {peer} on {you_win} of {len(BRSR_CORE_KPIS)} BRSR Core KPIs, "
            f"trail on {peer_win}" + (f", and tie on {ties}" if ties else "") + "."
        ),
    }


def get_positioning_scatter(sector: str, period: str, x_kpi: str, y_kpi: str) -> dict:
    """Two-dimensional positioning of every company in the peer set.

    A ranking table tells you the order; a scatter tells you the shape of
    the field -- who is clustered, who is an outlier, and whether the two
    KPIs actually move together.
    """
    xk, yk = KPI_BY_CODE.get(x_kpi), KPI_BY_CODE.get(y_kpi)
    if not xk or not yk:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}

    matrix = _build_matrix(sector, period)
    points = [
        {"company": c, "x": v[x_kpi], "y": v[y_kpi], "is_you": c == YOUR_ORG}
        for c, v in matrix.items()
    ]
    xs = [p["x"] for p in points]
    ys = [p["y"] for p in points]
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False,
        "x_kpi": xk, "y_kpi": yk, "points": points,
        "x_median": round(sorted(xs)[len(xs) // 2], 2),
        "y_median": round(sorted(ys)[len(ys) // 2], 2),
    }


def _mean_percentiles(matrix: dict) -> dict:
    companies = list(matrix.keys())
    out = {}
    for c in companies:
        pcts = [
            _percentile_rank(matrix[c][k["code"]], [matrix[x][k["code"]] for x in companies], k["direction"])
            for k in BRSR_CORE_KPIS
        ]
        out[c] = round(sum(pcts) / len(pcts), 1)
    return out


def simulate_improvement(sector: str, period: str, kpi_code: str, improvement_pct: float) -> dict:
    """What-if: improve one KPI by N% and recompute the real standing.

    The whole matrix is re-ranked with the new value substituted, so the
    resulting rank is computed exactly the same way as the live one --
    not estimated. This is deliberately deterministic: a model guessing
    at 'you would probably move up' would be worthless next to actually
    recomputing it.
    """
    kpi = KPI_BY_CODE.get(kpi_code)
    if not kpi:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}

    matrix = _build_matrix(sector, period)
    companies = list(matrix.keys())
    before_means = _mean_percentiles(matrix)
    before_order = sorted(before_means.items(), key=lambda kv: kv[1], reverse=True)
    before_rank = next(i + 1 for i, (c, _) in enumerate(before_order) if c == YOUR_ORG)

    current = matrix[YOUR_ORG][kpi_code]
    # "Improve" means move in the direction that is actually better.
    factor = 1 - improvement_pct / 100 if kpi["direction"] == "lower" else 1 + improvement_pct / 100
    new_value = round(max(current * factor, 0), 2)

    sim = {c: dict(v) for c, v in matrix.items()}
    sim[YOUR_ORG][kpi_code] = new_value
    after_means = _mean_percentiles(sim)
    after_order = sorted(after_means.items(), key=lambda kv: kv[1], reverse=True)
    after_rank = next(i + 1 for i, (c, _) in enumerate(after_order) if c == YOUR_ORG)

    pop_before = [matrix[c][kpi_code] for c in companies]
    pop_after = [sim[c][kpi_code] for c in companies]
    kpi_pct_before = _percentile_rank(current, pop_before, kpi["direction"])
    kpi_pct_after = _percentile_rank(new_value, pop_after, kpi["direction"])

    overtaken = [
        c for c in companies
        if c != YOUR_ORG and before_means[c] > before_means[YOUR_ORG] and after_means[c] < after_means[YOUR_ORG]
    ]

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False,
        "kpi": kpi, "improvement_pct": improvement_pct,
        "current_value": current, "new_value": new_value,
        "kpi_percentile_before": kpi_pct_before, "kpi_percentile_after": kpi_pct_after,
        "overall_percentile_before": before_means[YOUR_ORG],
        "overall_percentile_after": after_means[YOUR_ORG],
        "rank_before": before_rank, "rank_after": after_rank,
        "rank_change": before_rank - after_rank,
        "companies_overtaken": overtaken,
        "total_companies": len(companies),
    }


def get_ai_analysis(sector: str, period: str, mode: str) -> dict:
    """Several genuinely different reads of the same computed numbers.

    One generic paragraph is not much of an 'AI analysis' -- an executive
    wants a different answer than someone planning next quarter's work.
    Each mode below is assembled from the real figures; when a live model
    is wired in, this same payload becomes its prompt context.
    """
    o = get_benchmark_overview(sector, period)
    ranked = sorted(o["kpis"], key=lambda r: r["percentile"], reverse=True)
    leader = o["leaderboard"][0]["company"]

    if mode == "positioning":
        h2h = get_head_to_head(sector, period, leader) if leader != YOUR_ORG else None
        if h2h and not h2h.get("not_found"):
            beat = [r["name"] for r in h2h["rows"] if r["verdict"] == "you"]
            lose = [r["name"] for r in h2h["rows"] if r["verdict"] == "peer"]
            body = (
                f"{leader} leads the sector on mean percentile. You already beat them on "
                f"{len(beat)} of {len(h2h['rows'])} KPIs"
                + (f" \u2014 notably {', '.join(beat[:3])}." if beat else ".")
                + (f" They stay ahead on {', '.join(lose[:3])}"
                   f"{' and others' if len(lose) > 3 else ''}, which is where the rank gap actually comes from."
                   if lose else "")
            )
        else:
            body = "You currently lead the sector on mean percentile across the nine BRSR Core KPIs."
        points = [
            {"label": "Sector leader", "value": leader},
            {"label": "Your rank", "value": f"#{o['overall_rank']} of {o['total_companies']}"},
            {"label": "Mean percentile", "value": f"{o['overall_percentile']}%"},
        ]

    elif mode == "roadmap":
        worst = sorted(o["kpis"], key=lambda r: r["percentile"])[:4]
        body = (
            "Ordered by how much ground you stand to gain. The percentage on each row is how many "
            "peers you would move ahead of if you matched the best performer on that KPI."
        )
        points = [
            {
                "label": f"{i + 1}. {w['name']}",
                "value": (
                    f"Would move you ahead of {round(100 - w['percentile'], 1)}% of peers \u00b7 "
                    f"gap of {w['gap_to_best']} {w['unit']} to {w['best_company']}"
                ),
                "detail": _recommendation_for(w["code"]),
            }
            for i, w in enumerate(worst)
        ]

    elif mode == "risk":
        bottom = [r for r in o["kpis"] if r["percentile"] < 25]
        below = [r for r in o["kpis"] if 25 <= r["percentile"] < 50]
        body = (
            f"{len(bottom)} KPI(s) sit in the bottom quartile of the peer set and "
            f"{len(below)} more are below the median. In a disclosure cycle these are the "
            "figures most likely to attract questions, because peers in the same sector are "
            "reporting visibly better numbers on the same standardised metric."
        )
        points = ([{"label": r["name"], "value": f"bottom quartile \u00b7 {r['percentile']}%",
                    "detail": f"Peer best is {r['best_value']} {r['unit']} ({r['best_company']})."} for r in bottom]
                  + [{"label": r["name"], "value": f"below median \u00b7 {r['percentile']}%"} for r in below])
        if not points:
            points = [{"label": "No KPIs below the peer median", "value": "\u2014"}]

    else:  # executive
        mode = "executive"
        top, bottom = ranked[0], ranked[-1]
        body = (
            f"Across {o['peer_count']} peers in {sector} for {period} you rank #{o['overall_rank']} "
            f"of {o['total_companies']}, ahead of the median on {o['kpis_ahead_of_median']} of "
            f"{o['kpis_total']} BRSR Core KPIs. Your strongest position is {top['name']} "
            f"({top['percentile']}% of peers outperformed); the weakest is {bottom['name']} "
            f"({bottom['percentile']}%), where {bottom['best_company']} sets the peer benchmark at "
            f"{bottom['best_value']} {bottom['unit']}."
        )
        points = [
            {"label": "Overall percentile", "value": f"{o['overall_percentile']}%"},
            {"label": "KPIs above median", "value": f"{o['kpis_ahead_of_median']} of {o['kpis_total']}"},
            {"label": "Strongest KPI", "value": top["name"]},
            {"label": "Weakest KPI", "value": bottom["name"]},
        ]

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "mode": mode, "body": body, "points": points,
    }


def get_analysis_modes() -> list[dict]:
    return [
        {"key": "executive", "label": "Executive summary", "description": "Where you stand, in one read"},
        {"key": "positioning", "label": "Competitive positioning", "description": "How you compare to the sector leader"},
        {"key": "roadmap", "label": "Improvement roadmap", "description": "Where you stand to gain the most ground"},
        {"key": "risk", "label": "Disclosure risk", "description": "KPIs most likely to draw scrutiny"},
    ]


def get_peer_list(sector: str) -> list[str]:
    return SECTORS.get(sector, []) + CUSTOM_PEERS.get(sector, [])


def get_library(sector: str, period: str) -> dict:
    """The peer library for a sector, separating curated entries from any
    the client added themselves.

    Kept distinct on purpose: a curated entry is one we stand behind, a
    custom one is the client's own upload. Blurring the two would make it
    impossible to say where a given figure came from.
    """
    curated = SECTORS.get(sector, [])
    custom = CUSTOM_PEERS.get(sector, [])
    matrix = _build_matrix(sector, period)

    def _entry(name: str, source: str) -> dict:
        vals = matrix.get(name, {})
        return {
            "company": name,
            "source": source,
            "kpis_available": len(vals),
            "kpis_total": len(BRSR_CORE_KPIS),
            "period": period,
        }

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "sector": sector, "period": period,
        "curated_count": len(curated),
        "custom_count": len(custom),
        "entries": [_entry(c, "Standard library") for c in curated]
                   + [_entry(c, "Uploaded by you") for c in custom],
    }
