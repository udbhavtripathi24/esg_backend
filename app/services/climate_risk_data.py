"""Climate Risk Assessment demo data -- new addition to the ESG Demo
Dashboard, following the exact same conventions as
app/services/demo_esg_data.py (deterministic, seeded, clearly labeled
as demo data, is_demo_data: True on every response).

Reference: a business-provided Excel/Figma mockup showing two
independent sections -- Trend Analysis and Scenario Analysis -- each
with their OWN separate State/City/Scenario/Year filters (confirmed by
comparing two reference screenshots where each side showed a
genuinely different selected state/city at the same time).

Real constraint from the reference: City options must always be
scoped to the selected State (Chennai/Coimbatore only appear under
Tamil Nadu, Bangalore/Narsapura only under Karnataka, Pune only under
Maharashtra) -- never mixed across states.
"""
import hashlib

DEMO_DISCLAIMER = (
    "Illustrative demo data only -- not derived from real climate models, "
    "real emission scenarios, or approved methodology. For demonstration of "
    "platform capability only."
)

STATE_CITIES = {
    "Tamil Nadu": ["Chennai", "Coimbatore"],
    "Karnataka": ["Bangalore", "Narsapura"],
    "Maharashtra": ["Pune"],
}
STATES = list(STATE_CITIES.keys())
SCENARIOS = ["SSP2-4.5", "SSP5-8.5"]
INDICATORS = ["Average Maximum Temp", "Five-day Max Rainfall", "Maximum of One-day Maximum Temp", "One-day Max Rainfall"]
TREND_YEARS = list(range(2026, 2051))  # 2026-2050 inclusive, matching the reference chart's x-axis
SCENARIO_YEARS = [2026, 2030, 2050]

# Distinct starting baseline and growth-rate ranges per indicator, so
# the four lines in the trend chart look visually distinct the same
# way they do in the reference (one clearly higher/steeper line --
# Five-day Max Rainfall -- and three closer together lower on the
# chart), rather than four indistinguishable overlapping lines.
INDICATOR_RANGES = {
    "Average Maximum Temp": (28, 32, 0.35),
    "Five-day Max Rainfall": (100, 108, 0.9),
    "Maximum of One-day Maximum Temp": (35, 39, 0.15),
    "One-day Max Rainfall": (42, 46, 0.3),
}


def _seeded_value(key: str, low: float, high: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    fraction = (h % 10_000) / 10_000
    return round(low + fraction * (high - low), 2)


def _increase_label(pct: float) -> str:
    """Thresholds reverse-engineered from four real values shown in the
    reference mockup: 20.7%->High, 14.3%->Moderate, 3.8%->Low, 6.4%->Moderate."""
    if pct < 5:
        return "Low Increase"
    if pct < 20:
        return "Moderate Increase"
    return "High Increase"


def get_climate_filter_options() -> dict:
    return {
        "states": STATES,
        "state_cities": STATE_CITIES,
        "scenarios": SCENARIOS,
        "indicators": INDICATORS,
        "years": SCENARIO_YEARS,
    }


def get_climate_trend_data(state: str, city: str, scenario: str, indicator: str) -> dict:
    """Powers the Trend Analysis section: a 2026-2050 line for each of
    the 4 indicators (all shown together on one chart, matching the
    reference), plus two headline KPIs (% change from 2026 baseline to
    2030 and to 2050) computed specifically for the selected indicator.

    Returns {"no_data": True} if city does not genuinely belong to
    state -- defensive check matching the real requirement, even though
    the real UI should never be able to produce this combination since
    the city dropdown is always scoped to the selected state.
    """
    if city not in STATE_CITIES.get(state, []):
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "no_data": True}

    series_by_indicator = {}
    for ind, (low, high, growth) in INDICATOR_RANGES.items():
        base = _seeded_value(f"climstart-{state}-{city}-{scenario}-{ind}", low, high)
        series = []
        for i, year in enumerate(TREND_YEARS):
            # Gentle, monotonic real-looking growth curve: fast early,
            # flattening later -- matching the reference chart's shape
            # (steep rise 2026-2032, near-flat 2040-2050) -- via a
            # square-root-shaped progression rather than a straight line.
            progress = (i / (len(TREND_YEARS) - 1)) ** 0.55
            noise = _seeded_value(f"climnoise-{state}-{city}-{scenario}-{ind}-{year}", -0.6, 0.6)
            value = round(base + growth * progress * len(TREND_YEARS) * 0.6 + noise, 2)
            series.append({"year": year, "value": value})
        series_by_indicator[ind] = series

    selected_series = series_by_indicator[indicator]
    baseline_value = selected_series[0]["value"]
    value_2030 = next(p["value"] for p in selected_series if p["year"] == 2030)
    value_2050 = next(p["value"] for p in selected_series if p["year"] == 2050)
    pct_2030 = round((value_2030 - baseline_value) / baseline_value * 100, 1)
    pct_2050 = round((value_2050 - baseline_value) / baseline_value * 100, 1)

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "no_data": False,
        "series_by_indicator": series_by_indicator,
        "baseline_to_2030_pct": pct_2030,
        "baseline_to_2030_label": _increase_label(pct_2030),
        "baseline_to_2050_pct": pct_2050,
        "baseline_to_2050_label": _increase_label(pct_2050),
    }


def get_climate_scenario_data(state: str, scenario: str, years: list[int] | None = None) -> dict:
    """Powers the Scenario Analysis section: a grouped bar chart, one
    group per (city, year) combination, for every city belonging to the
    selected state -- matching the reference exactly (Tamil Nadu, with
    2 cities, produced 2 cities x 3 years = 6 groups; Maharashtra, with
    only 1 city, produced 1 city x 1 selected year = 1 group)."""
    cities = STATE_CITIES.get(state, [])
    selected_years = years or SCENARIO_YEARS

    groups = []
    for city in cities:
        for year in selected_years:
            values = {}
            for ind, (low, high, _growth) in INDICATOR_RANGES.items():
                values[ind] = _seeded_value(f"climscen-{state}-{city}-{scenario}-{ind}-{year}", low, high * 1.6)
            groups.append({"city": city, "year": year, "values": values})

    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "cities": cities, "groups": groups,
    }
