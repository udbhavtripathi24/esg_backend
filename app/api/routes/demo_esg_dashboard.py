"""Demo ESG Dashboard API.

Serves the illustrative demo dataset from app/services/demo_esg_data.py
-- see that module's docstring for the full explanation of why this
exists and what it deliberately is NOT (real data, real emission
factors, real financial figures).

Deliberately a SEPARATE router/prefix from the real /analytics
endpoints. Requires authentication but no company-scoping -- the demo
dataset is not tied to any real company's data, so there's nothing to
tenant-isolate.

Structure mirrors the reference document's own organization exactly:
Environment (GHG / Energy / Water / Waste), Social (Training /
Diversity / Wellbeing / Health & Safety / Complaints), Governance
(Leadership Diversity / Supply Chain Management).
"""
from typing import Optional, Annotated
from fastapi import APIRouter, Depends, Query
from app.api.deps import get_current_user
from app.models.user import User
from app.services.demo_esg_data import (
    get_ghg_environment_data, get_energy_data, get_water_data, get_waste_data,
    get_social_training_data, get_social_diversity_data, get_social_wellbeing_data,
    get_social_health_safety_data, get_social_complaints_data,
    get_governance_leadership_data, get_governance_supply_chain_data,
    get_filter_options,
)
from app.services.climate_risk_data import (
    get_climate_filter_options, get_climate_trend_data, get_climate_scenario_data,
)

router = APIRouter(prefix="/demo-esg-dashboard", tags=["demo-esg-dashboard"])


def _common_params(location: Optional[str], year: Optional[int], month: Optional[str]) -> dict:
    return {"location": location, "year": year, "month": month}


@router.get("/filters")
def demo_filters(actor: User = Depends(get_current_user)):
    return get_filter_options()


# ---- Environment ----

@router.get("/environment/ghg")
def demo_ghg(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_ghg_environment_data(**_common_params(location, year, month))


@router.get("/environment/energy")
def demo_energy(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_energy_data(**_common_params(location, year, month))


@router.get("/environment/water")
def demo_water(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_water_data(**_common_params(location, year, month))


@router.get("/environment/waste")
def demo_waste(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_waste_data(**_common_params(location, year, month))


# ---- Social ----

@router.get("/social/training")
def demo_social_training(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_social_training_data(**_common_params(location, year, month))


@router.get("/social/diversity")
def demo_social_diversity(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_social_diversity_data(**_common_params(location, year, month))


@router.get("/social/wellbeing")
def demo_social_wellbeing(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_social_wellbeing_data(**_common_params(location, year, month))


@router.get("/social/health-safety")
def demo_social_health_safety(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_social_health_safety_data(**_common_params(location, year, month))


@router.get("/social/complaints")
def demo_social_complaints(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_social_complaints_data(**_common_params(location, year, month))


# ---- Governance ----

@router.get("/governance/leadership")
def demo_governance_leadership(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_governance_leadership_data(**_common_params(location, year, month))


@router.get("/governance/supply-chain")
def demo_governance_supply_chain(location: Optional[str] = Query(None), year: Optional[int] = Query(None), month: Optional[str] = Query(None), actor: User = Depends(get_current_user)):
    return get_governance_supply_chain_data(**_common_params(location, year, month))


# ---- Climate Risk Assessment ----

@router.get("/climate/filters")
def demo_climate_filters(actor: User = Depends(get_current_user)):
    return get_climate_filter_options()


@router.get("/climate/trend")
def demo_climate_trend(
    state: str = Query(...),
    city: str = Query(...),
    scenario: str = Query(...),
    indicator: str = Query(...),
    actor: User = Depends(get_current_user),
):
    return get_climate_trend_data(state=state, city=city, scenario=scenario, indicator=indicator)


@router.get("/climate/scenario")
def demo_climate_scenario(
    state: str = Query(...),
    scenario: str = Query(...),
    years: str | None = Query(default=None),
    actor: User = Depends(get_current_user),
):
    # Manually parse years if provided as comma-separated string or repeated params
    years_list = None
    if years:
        try:
            # Try to parse as comma-separated: "2026,2030,2050"
            if ',' in years:
                years_list = [int(y.strip()) for y in years.split(',')]
            else:
                years_list = [int(years)]
        except (ValueError, AttributeError):
            years_list = None
    return get_climate_scenario_data(state=state, scenario=scenario, years=years_list)
