"""Tests for the Benchmarking peer-comparison endpoints.

Prototype peer data only -- see app/services/benchmarking_data.py for the
design rationale. The direction-awareness tests below matter most: a
percentile that ignores KPI direction would rank an efficient company as
a laggard, which is the single easiest way for this feature to be
confidently wrong.
"""
from tests.conftest_helpers import bootstrap, make_user, auth
from app.services.benchmarking_data import _percentile_rank


def _user(session):
    org = bootstrap(session)
    return make_user(session, "bench@d.com", "deloitte", "Administrator", org=org)


# ---- Pure logic: direction-aware percentile ----

def test_percentile_lower_is_better():
    pop = [5, 10, 20, 30]
    assert _percentile_rank(5, pop, "lower") == 100.0
    assert _percentile_rank(30, pop, "lower") == 0.0


def test_percentile_higher_is_better():
    pop = [5, 10, 20, 30]
    assert _percentile_rank(30, pop, "higher") == 100.0
    assert _percentile_rank(5, pop, "higher") == 0.0


def test_percentile_direction_actually_inverts():
    """The same value must not score the same under both directions."""
    pop = [5, 10, 20, 30]
    assert _percentile_rank(10, pop, "lower") != _percentile_rank(10, pop, "higher")


# ---- API ----

def test_filters(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/filters", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert len(body["kpis"]) == 9  # the nine BRSR Core attributes
    assert "Manufacturing" in body["sectors"]
    codes = {k["code"] for k in body["kpis"]}
    assert "ghg_intensity" in codes and "women_workforce" in codes


def test_overview_shape_and_consistency(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/overview", params={"sector": "Manufacturing", "period": "FY 2024-25"}, headers=auth(user))
    assert r.status_code == 200
    b = r.json()
    assert b["is_demo_data"] is True
    assert len(b["kpis"]) == 9
    # Overall percentile must equal this org's own leaderboard entry --
    # if these two ever disagree, one of the two calculations is wrong.
    mine = next(x for x in b["leaderboard"] if x["is_you"])
    assert mine["mean_percentile"] == b["overall_percentile"]
    assert mine["rank"] == b["overall_rank"]


def test_overview_reports_a_real_best_performer_per_kpi(client, session):
    user = _user(session)
    b = client.get("/api/v1/benchmarking/overview", headers=auth(user)).json()
    for row in b["kpis"]:
        assert row["best_company"]
        # For a lower-is-better KPI the best value must not exceed ours.
        if row["direction"] == "lower":
            assert row["best_value"] <= row["your_value"]
        else:
            assert row["best_value"] >= row["your_value"]


def test_kpi_comparison_sorted_by_direction(client, session):
    user = _user(session)
    low = client.get("/api/v1/benchmarking/kpi-comparison", params={"kpi_code": "ghg_intensity"}, headers=auth(user)).json()
    vals = [r["value"] for r in low["rows"]]
    assert vals == sorted(vals), "lower-is-better KPI should rank ascending"

    high = client.get("/api/v1/benchmarking/kpi-comparison", params={"kpi_code": "renewable_share"}, headers=auth(user)).json()
    vals2 = [r["value"] for r in high["rows"]]
    assert vals2 == sorted(vals2, reverse=True), "higher-is-better KPI should rank descending"


def test_kpi_comparison_unknown_code(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/kpi-comparison", params={"kpi_code": "not_a_kpi"}, headers=auth(user))
    assert r.status_code == 200
    assert r.json()["not_found"] is True


def test_trend_covers_all_periods(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/trend", params={"kpi_code": "ghg_intensity"}, headers=auth(user))
    body = r.json()
    assert len(body["points"]) == 3
    # oldest first, so a chart reads left-to-right chronologically
    assert body["points"][0]["period"] == "FY 2022-23"


def test_ai_insights_are_built_from_real_numbers(client, session):
    user = _user(session)
    overview = client.get("/api/v1/benchmarking/overview", headers=auth(user)).json()
    ai = client.get("/api/v1/benchmarking/ai-insights", headers=auth(user)).json()
    # The narrative must quote the same rank the overview computed --
    # this is what keeps the AI layer honest about the underlying maths.
    assert f"#{overview['overall_rank']}" in ai["headline"]
    assert len(ai["gaps"]) == 3
    assert all(g["recommendation"] for g in ai["gaps"])


def test_deterministic(client, session):
    user = _user(session)
    a = client.get("/api/v1/benchmarking/overview", headers=auth(user)).json()
    b = client.get("/api/v1/benchmarking/overview", headers=auth(user)).json()
    assert a == b


def test_requires_authentication(client):
    assert client.get("/api/v1/benchmarking/overview").status_code == 401
