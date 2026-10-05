"""District-level and asset-level climate risk demo data.

Built to the structure of the two datasets the business intends to use:

  District-level (World Bank)  -- sector x state x agroclimatic zone x
      hazard, with five components per hazard (historical hazard, future
      hazard, vulnerability, historical risk, future climate risk).

  Asset-level -- individual asset locations plotted by latitude and
      longitude, scored per hazard across two emission scenarios and
      three time horizons.

Every figure here is generated deterministically for demonstration. The
real World Bank extract and the real asset register replace the two
generators below; the filter shape, the API contract and the entire
front end stay exactly as they are.

District names and state groupings come from the same 2011 district
boundary set used to draw the map, so the data and the map always key
off an identical list -- a district can never appear in one and not the
other.
"""
import hashlib
import json
import os

DEMO_DISCLAIMER = (
    "Illustrative demo data. Not derived from the World Bank dataset, any "
    "climate model, or a real asset register. Structure mirrors the intended "
    "datasets so the real extracts can be dropped in without UI changes."
)

_HERE = os.path.dirname(__file__)
with open(os.path.join(_HERE, "_india_districts.json"), encoding="utf-8") as fh:
    STATE_DISTRICTS: dict[str, list[str]] = json.load(fh)

STATES = sorted(STATE_DISTRICTS.keys())

SECTORS = ["Agriculture", "Water", "Health", "Forestry", "Ecosystem and Biodiversity"]
DISTRICT_HAZARDS = ["Flood", "Drought"]
COMPONENTS = [
    "Historical Hazard", "Future Hazard", "Vulnerability",
    "Historical Risk", "Future Climate Risk",
]

# The fifteen agroclimatic zones the Planning Commission defines for India.
AGROCLIMATIC_ZONES = [
    "Western Himalayan Region", "Eastern Himalayan Region",
    "Lower Gangetic Plains Region", "Middle Gangetic Plains Region",
    "Upper Gangetic Plains Region", "Trans-Gangetic Plains Region",
    "Eastern Plateau and Hills Region", "Central Plateau and Hills Region",
    "Western Plateau and Hills Region", "Southern Plateau and Hills Region",
    "East Coast Plains and Hills Region", "West Coast Plains and Ghat Region",
    "Gujarat Plains and Hills Region", "Western Dry Region", "Island Region",
]

# Which zones plausibly occur in which state. Kept explicit rather than
# derived, because a zone appearing in a state it does not belong to is
# the sort of error a domain reviewer spots immediately.
STATE_ZONES = {
    "Jammu and Kashmir": ["Western Himalayan Region"],
    "Ladakh": ["Western Himalayan Region"],
    "Himachal Pradesh": ["Western Himalayan Region"],
    "Uttarakhand": ["Western Himalayan Region"],
    "Arunachal Pradesh": ["Eastern Himalayan Region"],
    "Assam": ["Eastern Himalayan Region"],
    "Manipur": ["Eastern Himalayan Region"],
    "Meghalaya": ["Eastern Himalayan Region"],
    "Mizoram": ["Eastern Himalayan Region"],
    "Nagaland": ["Eastern Himalayan Region"],
    "Sikkim": ["Eastern Himalayan Region"],
    "Tripura": ["Eastern Himalayan Region"],
    "West Bengal": ["Lower Gangetic Plains Region"],
    "Bihar": ["Middle Gangetic Plains Region"],
    "Uttar Pradesh": ["Middle Gangetic Plains Region", "Upper Gangetic Plains Region"],
    "Punjab": ["Trans-Gangetic Plains Region"],
    "Haryana": ["Trans-Gangetic Plains Region"],
    "Chandigarh": ["Trans-Gangetic Plains Region"],
    "Delhi": ["Trans-Gangetic Plains Region"],
    "Jharkhand": ["Eastern Plateau and Hills Region"],
    "Odisha": ["Eastern Plateau and Hills Region", "East Coast Plains and Hills Region"],
    "Chhattisgarh": ["Eastern Plateau and Hills Region"],
    "Madhya Pradesh": ["Central Plateau and Hills Region"],
    "Rajasthan": ["Central Plateau and Hills Region", "Western Dry Region"],
    "Maharashtra": ["Western Plateau and Hills Region"],
    "Telangana": ["Southern Plateau and Hills Region"],
    "Andhra Pradesh": ["Southern Plateau and Hills Region", "East Coast Plains and Hills Region"],
    "Karnataka": ["Southern Plateau and Hills Region", "West Coast Plains and Ghat Region"],
    "Tamil Nadu": ["Southern Plateau and Hills Region", "East Coast Plains and Hills Region"],
    "Puducherry": ["East Coast Plains and Hills Region"],
    "Kerala": ["West Coast Plains and Ghat Region"],
    "Goa": ["West Coast Plains and Ghat Region"],
    "Gujarat": ["Gujarat Plains and Hills Region"],
    "Dadra and Nagar Haveli and Daman and Diu": ["Gujarat Plains and Hills Region"],
    "Andaman and Nicobar Islands": ["Island Region"],
    "Lakshadweep": ["Island Region"],
}

# Asset-level dimensions, exactly as specified.
ASSET_HAZARDS = [
    "Drought", "Riverine Flood", "Extreme Heat", "Landslide",
    "Cyclone", "Sea Level Rise",
]
SCENARIOS = ["SSP2-4.5", "SSP5-8.5"]
TIME_HORIZONS = [
    {"code": "ST", "label": "Short Term", "window": "2021-2040"},
    {"code": "MT", "label": "Medium Term", "window": "2041-2060"},
    {"code": "LT", "label": "Long Term", "window": "2081-2100"},
]

RISK_BANDS = [
    {"label": "Very Low", "max": 20, "color": "#2E7D32"},
    {"label": "Low", "max": 40, "color": "#9CCC65"},
    {"label": "Moderate", "max": 60, "color": "#FDD835"},
    {"label": "High", "max": 80, "color": "#FB8C00"},
    {"label": "Very High", "max": 101, "color": "#C62828"},
]


def _seeded(key: str, low: float, high: float) -> float:
    h = int(hashlib.sha256(key.encode()).hexdigest(), 16)
    return round(low + (h % 10_000) / 10_000 * (high - low), 1)


def band_for(score: float) -> dict:
    for b in RISK_BANDS:
        if score < b["max"]:
            return {"label": b["label"], "color": b["color"]}
    return {"label": RISK_BANDS[-1]["label"], "color": RISK_BANDS[-1]["color"]}


def get_district_filters() -> dict:
    return {
        "sectors": SECTORS,
        "states": STATES,
        "state_districts": STATE_DISTRICTS,
        "agroclimatic_zones": AGROCLIMATIC_ZONES,
        "state_zones": STATE_ZONES,
        "hazards": DISTRICT_HAZARDS,
        "components": COMPONENTS,
        "risk_bands": RISK_BANDS,
        "sectors_with_data": ["Agriculture"],  # the shared extract covers Agriculture only
    }


def get_district_scores(sector: str, hazard: str, component: str,
                        state: str | None = None, zone: str | None = None) -> dict:
    """A 0-100 score for every district matching the filters.

    Returned for the whole country even when a state is selected -- the
    map zooms to the state rather than discarding the rest, so the
    national context stays visible behind the zoom.
    """
    scores = []
    for st, districts in STATE_DISTRICTS.items():
        zones = STATE_ZONES.get(st, [])
        if zone and zone not in zones:
            continue
        for dist in districts:
            score = _seeded(f"dr-{sector}-{hazard}-{component}-{st}-{dist}", 2, 98)
            b = band_for(score)
            scores.append({
                "district": dist, "state": st, "score": score,
                "band": b["label"], "color": b["color"],
                "in_focus": (state is None or st == state),
            })

    focused = [s for s in scores if s["in_focus"]]
    ranked = sorted(focused, key=lambda s: s["score"], reverse=True)
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "sector": sector, "hazard": hazard, "component": component,
        "state": state, "zone": zone,
        "district_count": len(focused),
        "average_score": round(sum(s["score"] for s in focused) / len(focused), 1) if focused else 0,
        "highest": ranked[:5],
        "lowest": ranked[-5:][::-1] if len(ranked) >= 5 else [],
        "band_counts": [
            {"band": b["label"], "color": b["color"],
             "count": sum(1 for s in focused if s["band"] == b["label"])}
            for b in RISK_BANDS
        ],
        "scores": scores,
    }


# ---------------------------------------------------------------------
# Asset-level
# ---------------------------------------------------------------------

# Demo asset register. Real coordinates for real Indian industrial
# locations so the map reads correctly; the companies and the risk
# scores attached to them are invented.
ASSETS = [
    {"id": "AST-001", "name": "Chennai Manufacturing Plant", "type": "Manufacturing", "state": "Tamil Nadu", "district": "Chennai", "lat": 13.0827, "lon": 80.2707},
    {"id": "AST-002", "name": "Coimbatore Textile Unit", "type": "Manufacturing", "state": "Tamil Nadu", "district": "Coimbatore", "lat": 11.0168, "lon": 76.9558},
    {"id": "AST-003", "name": "Bengaluru Technology Campus", "type": "Office", "state": "Karnataka", "district": "Bangalore", "lat": 12.9716, "lon": 77.5946},
    {"id": "AST-004", "name": "Narsapura Components Facility", "type": "Manufacturing", "state": "Karnataka", "district": "Kolar", "lat": 13.0667, "lon": 78.1333},
    {"id": "AST-005", "name": "Pune Assembly Plant", "type": "Manufacturing", "state": "Maharashtra", "district": "Pune", "lat": 18.5204, "lon": 73.8567},
    {"id": "AST-006", "name": "Mumbai Corporate Office", "type": "Office", "state": "Maharashtra", "district": "Mumbai", "lat": 19.0760, "lon": 72.8777},
    {"id": "AST-007", "name": "Mundra Port Warehouse", "type": "Warehouse", "state": "Gujarat", "district": "Kachchh", "lat": 22.8394, "lon": 69.7219},
    {"id": "AST-008", "name": "Ahmedabad Distribution Hub", "type": "Warehouse", "state": "Gujarat", "district": "Ahmadabad", "lat": 23.0225, "lon": 72.5714},
    {"id": "AST-009", "name": "Haldia Chemical Works", "type": "Manufacturing", "state": "West Bengal", "district": "Purba Medinipur", "lat": 22.0667, "lon": 88.0698},
    {"id": "AST-010", "name": "Kolkata Regional Office", "type": "Office", "state": "West Bengal", "district": "Kolkata", "lat": 22.5726, "lon": 88.3639},
    {"id": "AST-011", "name": "Visakhapatnam Steel Facility", "type": "Manufacturing", "state": "Andhra Pradesh", "district": "Visakhapatnam", "lat": 17.6868, "lon": 83.2185},
    {"id": "AST-012", "name": "Jaipur Solar Park", "type": "Energy", "state": "Rajasthan", "district": "Jaipur", "lat": 26.9124, "lon": 75.7873},
    {"id": "AST-013", "name": "Dehradun Hydro Station", "type": "Energy", "state": "Uttarakhand", "district": "Dehradun", "lat": 30.3165, "lon": 78.0322},
    {"id": "AST-014", "name": "Guwahati Logistics Centre", "type": "Warehouse", "state": "Assam", "district": "Kamrup", "lat": 26.1445, "lon": 91.7362},
    {"id": "AST-015", "name": "Kochi Marine Terminal", "type": "Warehouse", "state": "Kerala", "district": "Ernakulam", "lat": 9.9312, "lon": 76.2673},
]

ASSET_TYPES = sorted({a["type"] for a in ASSETS})

# Hazards are not uniform across India, so exposure is decided by where
# the asset actually is before any score is generated. Multiplying a
# random base by a weight was not enough: a Himalayan site could still
# draw a low landslide base and end up looking safe. Exposure now selects
# the range the score is drawn FROM, so geography always dominates.
COASTAL = {"Chennai", "Mumbai", "Kachchh", "Purba Medinipur", "Kolkata",
           "Visakhapatnam", "Ernakulam"}
HIMALAYAN = {"Dehradun"}
ARID = {"Jaipur", "Kachchh", "Ahmadabad"}
FLOOD_PLAIN = {"Purba Medinipur", "Kamrup", "Kolkata", "Ernakulam"}

# (low, high) score range per exposure level.
EXPOSURE_RANGES = {"high": (58.0, 92.0), "medium": (28.0, 58.0), "low": (3.0, 18.0)}


def _exposure(asset: dict, hazard: str) -> str:
    d = asset["district"]
    if hazard == "Sea Level Rise":
        return "high" if d in COASTAL else "low"
    if hazard == "Cyclone":
        return "high" if d in COASTAL else "low"
    if hazard == "Landslide":
        return "high" if d in HIMALAYAN else "low"
    if hazard == "Drought":
        return "high" if d in ARID else "medium"
    if hazard == "Riverine Flood":
        return "high" if d in FLOOD_PLAIN else "medium"
    return "medium"  # Extreme Heat applies broadly across India


def get_asset_filters() -> dict:
    return {
        "hazards": ASSET_HAZARDS,
        "scenarios": SCENARIOS,
        "time_horizons": TIME_HORIZONS,
        "asset_types": ASSET_TYPES,
        "risk_bands": RISK_BANDS,
    }


def get_asset_scores(hazard: str, scenario: str, horizon: str,
                     asset_type: str | None = None) -> dict:
    """Risk score per asset for one hazard / scenario / horizon."""
    # Risk rises with time and under the high-emission pathway. Applied as
    # a share of the remaining headroom rather than a multiplier, so a
    # score approaches 100 without ever clipping at it -- clipping made
    # the horizon trend flat for already-exposed assets, which hid the
    # very change the chart exists to show.
    horizon_uplift = {"ST": 0.0, "MT": 0.14, "LT": 0.28}.get(horizon, 0.0)
    scenario_uplift = 0.0 if scenario == "SSP2-4.5" else 0.12
    uplift = horizon_uplift + scenario_uplift

    rows = []
    for a in ASSETS:
        if asset_type and a["type"] != asset_type:
            continue
        low, high = EXPOSURE_RANGES[_exposure(a, hazard)]
        base = _seeded(f"as-{a['id']}-{hazard}", low, high)
        # Uplift is scaled by existing exposure as well as headroom. A
        # flat headroom uplift pushed every asset toward 100, which left a
        # landlocked site showing moderate sea level risk by 2100 -- the
        # scores rose, but they stopped meaning anything.
        score = round(base + (100 - base) * uplift * (base / 100), 1)
        b = band_for(score)
        rows.append({**a, "score": score, "band": b["label"], "color": b["color"],
                     "exposure": _exposure(a, hazard)})

    ranked = sorted(rows, key=lambda r: r["score"], reverse=True)
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER,
        "hazard": hazard, "scenario": scenario, "horizon": horizon,
        "asset_count": len(rows),
        "average_score": round(sum(r["score"] for r in rows) / len(rows), 1) if rows else 0,
        "highest": ranked[:5],
        "band_counts": [
            {"band": b["label"], "color": b["color"],
             "count": sum(1 for r in rows if r["band"] == b["label"])}
            for b in RISK_BANDS
        ],
        "assets": rows,
    }


def get_asset_horizon_profile(asset_id: str, hazard: str, scenario: str) -> dict:
    """One asset's trajectory across the three horizons -- the clearest way
    to show how exposure changes over time for a specific site."""
    asset = next((a for a in ASSETS if a["id"] == asset_id), None)
    if not asset:
        return {"is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": True}
    points = []
    for h in TIME_HORIZONS:
        s = get_asset_scores(hazard, scenario, h["code"])
        row = next(r for r in s["assets"] if r["id"] == asset_id)
        points.append({"horizon": h["code"], "label": h["label"], "window": h["window"], "score": row["score"]})
    return {
        "is_demo_data": True, "disclaimer": DEMO_DISCLAIMER, "not_found": False,
        "asset": asset, "hazard": hazard, "scenario": scenario, "points": points,
    }
