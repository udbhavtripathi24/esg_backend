"""Tests for the district-level and asset-level climate risk endpoints.

Illustrative demo data. The assertions that matter most are the
geographic ones: a map that places sea level risk inland, or shows a
state's districts under the wrong agroclimatic zone, is wrong in a way
that a reviewer spots instantly and that destroys confidence in
everything else on the page.
"""
from tests.conftest_helpers import bootstrap, make_user, auth


def _user(session):
    org = bootstrap(session)
    return make_user(session, "geo@d.com", "deloitte", "Administrator", org=org)


# ---- District level ----

def test_district_filters_cover_all_of_india(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/filters", headers=auth(user)).json()
    assert len(b["states"]) == 36          # 28 states + 8 union territories
    assert sum(len(v) for v in b["state_districts"].values()) == 760
    assert len(b["agroclimatic_zones"]) == 15
    assert b["hazards"] == ["Flood", "Drought"]
    assert len(b["components"]) == 5
    assert b["sectors_with_data"] == ["Agriculture"]


def test_district_scores_cover_every_district(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                   params={"sector": "Agriculture", "hazard": "Flood",
                           "component": "Future Climate Risk"}, headers=auth(user)).json()
    assert len(b["scores"]) == 760
    assert b["district_count"] == 760
    assert all(0 <= s["score"] <= 100 for s in b["scores"])


def test_state_filter_narrows_focus_but_keeps_national_context(client, session):
    """Selecting a state zooms the map; it must not discard the rest of
    the country, or the surrounding context disappears behind the zoom."""
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                   params={"state": "Kerala"}, headers=auth(user)).json()
    assert b["district_count"] < 760          # focus narrowed
    assert len(b["scores"]) == 760            # whole country still returned
    assert all(s["in_focus"] for s in b["scores"] if s["state"] == "Kerala")
    assert not any(s["in_focus"] for s in b["scores"] if s["state"] == "Punjab")


def test_agroclimatic_zone_filter_is_geographically_correct(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                   params={"zone": "Western Dry Region"}, headers=auth(user)).json()
    states = {s["state"] for s in b["scores"]}
    assert states == {"Rajasthan"}, "the Western Dry Region is Rajasthan only"

    b2 = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                    params={"zone": "Island Region"}, headers=auth(user)).json()
    assert {s["state"] for s in b2["scores"]} == {"Andaman and Nicobar Islands", "Lakshadweep"}


def test_district_scores_are_deterministic(client, session):
    user = _user(session)
    p = {"sector": "Agriculture", "hazard": "Drought", "component": "Vulnerability"}
    a = client.get("/api/v1/demo-esg-dashboard/climate/district/scores", params=p, headers=auth(user)).json()
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/scores", params=p, headers=auth(user)).json()
    assert a == b


def test_changing_component_changes_the_scores(client, session):
    user = _user(session)
    base = {"sector": "Agriculture", "hazard": "Flood"}
    a = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                   params={**base, "component": "Historical Hazard"}, headers=auth(user)).json()
    b = client.get("/api/v1/demo-esg-dashboard/climate/district/scores",
                   params={**base, "component": "Future Climate Risk"}, headers=auth(user)).json()
    assert a["scores"] != b["scores"]


# ---- Asset level ----

def test_asset_filters_match_the_specification(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/filters", headers=auth(user)).json()
    assert b["hazards"] == ["Drought", "Riverine Flood", "Extreme Heat",
                            "Landslide", "Cyclone", "Sea Level Rise"]
    assert b["scenarios"] == ["SSP2-4.5", "SSP5-8.5"]
    assert [t["code"] for t in b["time_horizons"]] == ["ST", "MT", "LT"]


def test_assets_have_plottable_coordinates(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores", headers=auth(user)).json()
    assert b["asset_count"] == 15
    for a in b["assets"]:
        # inside India's bounding box, or the point lands in the sea
        assert 6.0 <= a["lat"] <= 37.5, f"{a['name']} latitude out of range"
        assert 68.0 <= a["lon"] <= 97.5, f"{a['name']} longitude out of range"


def test_sea_level_risk_is_coastal_only(client, session):
    """The clearest sanity check on the whole asset map: a landlocked
    site must not show meaningful sea level rise exposure, even in the
    worst scenario and the longest horizon."""
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores",
                   params={"hazard": "Sea Level Rise", "scenario": "SSP5-8.5", "horizon": "LT"},
                   headers=auth(user)).json()
    by_name = {a["name"]: a["score"] for a in b["assets"]}
    coastal = by_name["Mumbai Corporate Office"]
    inland = by_name["Jaipur Solar Park"]
    assert coastal > 60 and inland < 30
    # every high-exposure asset must outrank every low-exposure one
    highs = [a["score"] for a in b["assets"] if a["exposure"] == "high"]
    lows = [a["score"] for a in b["assets"] if a["exposure"] == "low"]
    assert min(highs) > max(lows)


def test_landslide_risk_is_highest_in_the_himalaya(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores",
                   params={"hazard": "Landslide"}, headers=auth(user)).json()
    assert b["highest"][0]["district"] == "Dehradun"


def test_risk_rises_with_horizon_and_scenario(client, session):
    user = _user(session)

    def score(scenario, horizon):
        b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores",
                       params={"hazard": "Cyclone", "scenario": scenario, "horizon": horizon},
                       headers=auth(user)).json()
        return next(a["score"] for a in b["assets"] if a["id"] == "AST-001")

    assert score("SSP2-4.5", "ST") < score("SSP2-4.5", "MT") < score("SSP2-4.5", "LT")
    assert score("SSP2-4.5", "LT") < score("SSP5-8.5", "LT")


def test_scores_never_clip_at_one_hundred(client, session):
    """Clipping flattened the horizon trend for already-exposed assets,
    hiding the very change the chart exists to show."""
    user = _user(session)
    for hz in ["Drought", "Riverine Flood", "Extreme Heat", "Landslide", "Cyclone", "Sea Level Rise"]:
        b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores",
                       params={"hazard": hz, "scenario": "SSP5-8.5", "horizon": "LT"},
                       headers=auth(user)).json()
        assert all(a["score"] < 100 for a in b["assets"]), f"{hz} clipped at 100"


def test_asset_horizon_profile(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/profile",
                   params={"asset_id": "AST-001", "hazard": "Cyclone", "scenario": "SSP5-8.5"},
                   headers=auth(user)).json()
    assert b["not_found"] is False
    assert [p["horizon"] for p in b["points"]] == ["ST", "MT", "LT"]
    assert b["points"][0]["score"] < b["points"][-1]["score"]


def test_asset_type_filter(client, session):
    user = _user(session)
    b = client.get("/api/v1/demo-esg-dashboard/climate/asset/scores",
                   params={"asset_type": "Office"}, headers=auth(user)).json()
    assert {a["type"] for a in b["assets"]} == {"Office"}


def test_geo_endpoints_require_authentication(client):
    assert client.get("/api/v1/demo-esg-dashboard/climate/district/filters").status_code == 401
    assert client.get("/api/v1/demo-esg-dashboard/climate/asset/scores").status_code == 401
