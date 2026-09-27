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


# ---- Deeper analysis layer ----

def test_head_to_head_direction_aware_verdicts(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/head-to-head",
                   params={"peer": "Meridian Industries Ltd"}, headers=auth(user))
    assert r.status_code == 200
    b = r.json()
    assert b["you_win"] + b["peer_win"] + b["ties"] == 9
    for row in b["rows"]:
        # A verdict must agree with the KPI's own direction -- this is the
        # check that catches "better" being computed backwards.
        if row["verdict"] == "you":
            if row["direction"] == "lower":
                assert row["your_value"] < row["peer_value"]
            else:
                assert row["your_value"] > row["peer_value"]


def test_head_to_head_rejects_unknown_peer(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/head-to-head", params={"peer": "Nonexistent Ltd"}, headers=auth(user))
    assert r.json()["not_found"] is True


def test_scatter_includes_every_company_and_medians(client, session):
    user = _user(session)
    r = client.get("/api/v1/benchmarking/scatter",
                   params={"x_kpi": "ghg_intensity", "y_kpi": "energy_intensity"}, headers=auth(user))
    b = r.json()
    # Derived, not hardcoded: the peer set grows as the library grows, and
    # a fixed number here would go stale the moment it does.
    peers = client.get("/api/v1/benchmarking/peers", params={"sector": "Manufacturing"}, headers=auth(user)).json()["peers"]
    assert len(b["points"]) == len(peers) + 1  # peers plus your own organisation
    assert sum(1 for p in b["points"] if p["is_you"]) == 1
    assert b["x_median"] > 0 and b["y_median"] > 0


def test_simulation_improves_in_the_correct_direction(client, session):
    user = _user(session)
    low = client.get("/api/v1/benchmarking/simulate",
                     params={"kpi_code": "ghg_intensity", "improvement_pct": 30}, headers=auth(user)).json()
    # lower-is-better: improving must DECREASE the value
    assert low["new_value"] < low["current_value"]
    assert low["kpi_percentile_after"] >= low["kpi_percentile_before"]

    high = client.get("/api/v1/benchmarking/simulate",
                      params={"kpi_code": "renewable_share", "improvement_pct": 30}, headers=auth(user)).json()
    # higher-is-better: improving must INCREASE the value
    assert high["new_value"] > high["current_value"]


def test_simulation_zero_improvement_is_a_no_op(client, session):
    user = _user(session)
    b = client.get("/api/v1/benchmarking/simulate",
                   params={"kpi_code": "ghg_intensity", "improvement_pct": 0}, headers=auth(user)).json()
    assert b["rank_before"] == b["rank_after"]
    assert b["rank_change"] == 0


def test_all_analysis_modes_return_real_content(client, session):
    user = _user(session)
    modes = client.get("/api/v1/benchmarking/analysis-modes", headers=auth(user)).json()["modes"]
    assert {m["key"] for m in modes} == {"executive", "positioning", "roadmap", "risk"}
    for m in modes:
        b = client.get("/api/v1/benchmarking/analysis", params={"mode": m["key"]}, headers=auth(user)).json()
        assert b["mode"] == m["key"]
        assert len(b["body"]) > 40
        assert len(b["points"]) >= 1


def test_analysis_modes_are_actually_different(client, session):
    user = _user(session)
    bodies = {
        m: client.get("/api/v1/benchmarking/analysis", params={"mode": m}, headers=auth(user)).json()["body"]
        for m in ["executive", "positioning", "roadmap", "risk"]
    }
    assert len(set(bodies.values())) == 4, "each mode must give a genuinely different read"


def test_peers_list_scoped_to_sector(client, session):
    user = _user(session)
    mfg = client.get("/api/v1/benchmarking/peers", params={"sector": "Manufacturing"}, headers=auth(user)).json()["peers"]
    energy = client.get("/api/v1/benchmarking/peers", params={"sector": "Energy & Utilities"}, headers=auth(user)).json()["peers"]
    assert "Meridian Industries Ltd" in mfg
    assert "Everest Power Ltd" in energy
    assert not set(mfg) & set(energy)


# ---- Peer library: curated entries plus client-uploaded custom peers ----

def test_library_has_ten_curated_companies_per_sector(client, session):
    user = _user(session)
    for sector in ["Manufacturing", "Chemicals & Pharma", "Energy & Utilities"]:
        b = client.get("/api/v1/benchmarking/library", params={"sector": sector}, headers=auth(user)).json()
        assert b["curated_count"] == 10, f"{sector} should curate ten filers"


def test_library_separates_curated_from_uploaded(client, session):
    """Provenance must stay visible -- a curated entry is one we stand
    behind, an uploaded one is the client's own research."""
    user = _user(session)
    b = client.get("/api/v1/benchmarking/library", params={"sector": "Manufacturing"}, headers=auth(user)).json()
    sources = {e["source"] for e in b["entries"]}
    assert sources == {"Standard library", "Uploaded by you"}
    assert len(b["entries"]) == b["curated_count"] + b["custom_count"]


def test_custom_peers_are_included_in_the_comparison(client, session):
    user = _user(session)
    peers = client.get("/api/v1/benchmarking/peers", params={"sector": "Manufacturing"}, headers=auth(user)).json()["peers"]
    assert any("(uploaded)" in p for p in peers)
    o = client.get("/api/v1/benchmarking/overview", params={"sector": "Manufacturing"}, headers=auth(user)).json()
    assert any("(uploaded)" in r["company"] for r in o["leaderboard"])


def test_roadmap_wording_is_plain_language(client, session):
    """'Headroom' was jargon nobody outside the build understood. The
    roadmap must say what it means in words a reader can act on."""
    user = _user(session)
    b = client.get("/api/v1/benchmarking/analysis", params={"mode": "roadmap"}, headers=auth(user)).json()
    assert "headroom" not in b["body"].lower()
    for p in b["points"]:
        assert "headroom" not in p["value"].lower()
        assert "move you ahead of" in p["value"]
