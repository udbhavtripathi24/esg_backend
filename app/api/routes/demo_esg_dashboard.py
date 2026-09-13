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
from typing import Optional
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
