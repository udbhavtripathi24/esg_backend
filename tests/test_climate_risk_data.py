"""Tests for the Climate Risk Assessment demo endpoints -- illustrative
data only. Follows the exact same conventions as
test_demo_esg_dashboard.py. Add this as a new file:
tests/test_climate_risk_data.py"""
from tests.conftest_helpers import bootstrap, make_user, auth


def _user(session):
    org = bootstrap(session)
    return make_user(session, "climate@d.com", "deloitte", "Administrator", org=org)


def test_climate_filters(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/climate/filters", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["states"] == ["Tamil Nadu", "Karnataka", "Maharashtra"]
    assert body["state_cities"]["Tamil Nadu"] == ["Chennai", "Coimbatore"]
    assert body["state_cities"]["Maharashtra"] == ["Pune"]


def test_climate_trend_valid_combination(client, session):
    user = _user(session)
    r = client.get(
        "/api/v1/demo-esg-dashboard/climate/trend",
        params={"state": "Tamil Nadu", "city": "Coimbatore", "scenario": "SSP5-8.5", "indicator": "Five-day Max Rainfall"},
        headers=auth(user),
    )
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert body["no_data"] is False
    assert len(body["series_by_indicator"]["Five-day Max Rainfall"]) == 25  # 2026-2050 inclusive
    assert "baseline_to_2030_label" in body
    assert "baseline_to_2050_label" in body


def test_climate_trend_rejects_mismatched_state_city(client, session):
    """The real, explicit requirement: a city that does not belong to
    the given state must show no data, never incorrect data."""
    user = _user(session)
    r = client.get(
        "/api/v1/demo-esg-dashboard/climate/trend",
        params={"state": "Tamil Nadu", "city": "Pune", "scenario": "SSP2-4.5", "indicator": "Average Maximum Temp"},
        headers=auth(user),
    )
    assert r.status_code == 200
    assert r.json()["no_data"] is True


def test_climate_trend_deterministic(client, session):
    user = _user(session)
    params = {"state": "Karnataka", "city": "Bangalore", "scenario": "SSP2-4.5", "indicator": "Average Maximum Temp"}
    r1 = client.get("/api/v1/demo-esg-dashboard/climate/trend", params=params, headers=auth(user))
    r2 = client.get("/api/v1/demo-esg-dashboard/climate/trend", params=params, headers=auth(user))
    assert r1.json() == r2.json()


def test_climate_scenario_group_count_matches_cities_and_years(client, session):
    """Years are sent as a comma-separated string ("2026,2030,2050"),
    not repeated query params -- that is the real contract the frontend
    uses (see getClimateScenario in src/api/demoEsgDashboard.js).

    Real behavior confirmed from the reference: a state with 2
    cities and 3 selected years produces 6 groups; a state with 1 city
    produces 1 group per selected year."""
    user = _user(session)
    r = client.get(
        "/api/v1/demo-esg-dashboard/climate/scenario",
        params={"state": "Tamil Nadu", "scenario": "SSP2-4.5", "years": "2026,2030,2050"},
        headers=auth(user),
    )
    assert r.status_code == 200
    body = r.json()
    assert len(body["groups"]) == 6
    assert set(body["cities"]) == {"Chennai", "Coimbatore"}

    r2 = client.get(
        "/api/v1/demo-esg-dashboard/climate/scenario",
        params={"state": "Maharashtra", "scenario": "SSP2-4.5", "years": "2026"},
        headers=auth(user),
    )
    assert len(r2.json()["groups"]) == 1
    assert r2.json()["cities"] == ["Pune"]


def test_climate_endpoints_require_authentication(client):
    r = client.get("/api/v1/demo-esg-dashboard/climate/filters")
    assert r.status_code == 401
