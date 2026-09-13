"""Tests for the demo ESG dashboard endpoints -- illustrative data only,
never real production data. See app/services/demo_esg_data.py for the
full explanation of why this exists and its boundaries. Structure
mirrors a reference document provided by the business stakeholder."""
from tests.conftest_helpers import bootstrap, make_user, auth


def _user(session):
    org = bootstrap(session)
    return make_user(session, "demo@d.com", "deloitte", "Administrator", org=org)


def test_filters_endpoint(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/filters", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert len(body["locations"]) == 5  # All + 4 real demo locations
    assert 2025 in body["years"] and 2026 in body["years"]


# ---- Environment: all 4 sub-sections ----

def test_environment_ghg(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/environment/ghg?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert set(body["scope_totals"].keys()) == {"scope1", "scope2", "scope3"}


def test_environment_energy(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/environment/energy?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert "Renewable" in body["renewable_vs_non_renewable_mj"]
    assert "Wind" in body["renewable_energy_by_source_mj_by_location"]
    assert "Bengaluru" in body["renewable_energy_percentage_by_location"]


def test_environment_water(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/environment/water?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert body["water_withdrawal_kl"] > 0
    assert "Surface" in body["water_withdrawal_breakdown_by_location"]


def test_environment_waste(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/environment/waste?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert body["waste_generated_mt"] > 0
    assert "Paper" in body["waste_generated_by_category_by_location"]


# ---- Social: all 5 sub-sections ----

def test_social_training(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/social/training?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["is_demo_data"] is True
    assert body["total_training_hours"] > 0


def test_social_diversity(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/social/diversity?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert set(body["workforce_by_gender"].keys()) == {"Female", "Male", "Others"}


def test_social_wellbeing(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/social/wellbeing?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert "Paternity" in body["paternity_maternity_benefits_by_location"]


def test_social_health_safety(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/social/health-safety?year=2026", headers=auth(user))
    assert r.status_code == 200
    assert r.json()["is_demo_data"] is True


def test_social_complaints(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/social/complaints?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert "Customers" in body["hr_complaints_received_by_type_by_location"]


# ---- Governance: both sub-sections ----

def test_governance_leadership(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/governance/leadership?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["total_board_of_directors"]["Total"] == 68
    assert body["total_employees"] == 537
    assert "Bengaluru" in body["complaints_by_category_table"]
    assert "Child Labour" in body["complaints_by_category_table"]["Bengaluru"]


def test_governance_supply_chain(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/governance/supply-chain?year=2026", headers=auth(user))
    assert r.status_code == 200
    body = r.json()
    assert body["awareness_programs_for_value_chain_partners"] > 0


# ---- Cross-cutting correctness ----

def test_all_endpoints_deterministic(client, session):
    user = _user(session)
    endpoints = [
        "/environment/ghg", "/environment/energy", "/environment/water", "/environment/waste",
        "/social/training", "/social/diversity", "/social/wellbeing", "/social/health-safety", "/social/complaints",
        "/governance/leadership", "/governance/supply-chain",
    ]
    for ep in endpoints:
        r1 = client.get(f"/api/v1/demo-esg-dashboard{ep}?year=2026", headers=auth(user))
        r2 = client.get(f"/api/v1/demo-esg-dashboard{ep}?year=2026", headers=auth(user))
        assert r1.json() == r2.json(), f"{ep} is not deterministic"


def test_location_filter_narrows_results(client, session):
    user = _user(session)
    r = client.get("/api/v1/demo-esg-dashboard/environment/water?location=Mumbai&year=2026", headers=auth(user))
    body = r.json()
    assert list(body["water_withdrawal_by_location"].keys()) == ["Mumbai"]


def test_demo_dashboard_requires_authentication(client):
    r = client.get("/api/v1/demo-esg-dashboard/environment/ghg")
    assert r.status_code == 401


def test_demo_dashboard_never_imports_real_production_models():
    """Structural safeguard: confirms the demo module's own actual code
    (not its explanatory docstring/comments) contains no real import of
    KpiValue, Dataset, or any other real production model."""
    import ast
    import inspect
    import app.services.demo_esg_data as demo_module

    source = inspect.getsource(demo_module)
    tree = ast.parse(source)
    imported_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported_names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported_names.update(alias.name for alias in node.names)

    assert "KpiValue" not in imported_names
    assert "Dataset" not in imported_names
    assert not any(name.startswith("app.models") for name in imported_names)
