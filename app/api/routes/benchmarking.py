"""Benchmarking API.

Serves the peer-comparison engine in app/services/benchmarking_data.py --
see that module's docstring for the full design rationale, in particular
why XBRL extraction is deliberately deterministic rather than LLM-driven.

Requires authentication. No company scoping is applied because the peer
dataset is prototype data not tied to any real company's records; that
changes the moment real BRSR ingestion lands, at which point the
uploading company's own filings become tenant-scoped like every other
dataset in this platform.
"""
from typing import Optional
from fastapi import APIRouter, Depends, Query
from app.api.deps import get_current_user
from app.models.user import User
from app.services.benchmarking_data import (
    get_benchmark_filters, get_benchmark_overview, get_kpi_peer_comparison,
    get_pillar_summary, get_trend, get_ai_insights,
    get_head_to_head, get_positioning_scatter, simulate_improvement,
    get_ai_analysis, get_analysis_modes, get_peer_list, get_library,
)

router = APIRouter(prefix="/benchmarking", tags=["benchmarking"])

DEFAULT_SECTOR = "Manufacturing"
DEFAULT_PERIOD = "FY 2024-25"


@router.get("/filters")
def benchmark_filters(actor: User = Depends(get_current_user)):
    return get_benchmark_filters()


@router.get("/overview")
def benchmark_overview(
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_benchmark_overview(sector=sector, period=period)


@router.get("/kpi-comparison")
def benchmark_kpi_comparison(
    kpi_code: str = Query(...),
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_kpi_peer_comparison(sector=sector, period=period, kpi_code=kpi_code)


@router.get("/pillars")
def benchmark_pillars(
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_pillar_summary(sector=sector, period=period)


@router.get("/trend")
def benchmark_trend(
    kpi_code: str = Query(...),
    sector: str = Query(DEFAULT_SECTOR),
    actor: User = Depends(get_current_user),
):
    return get_trend(sector=sector, kpi_code=kpi_code)


@router.get("/ai-insights")
def benchmark_ai_insights(
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_ai_insights(sector=sector, period=period)


@router.get("/peers")
def benchmark_peers(sector: str = Query(DEFAULT_SECTOR), actor: User = Depends(get_current_user)):
    return {"peers": get_peer_list(sector)}


@router.get("/head-to-head")
def benchmark_head_to_head(
    peer: str = Query(...),
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_head_to_head(sector=sector, period=period, peer=peer)


@router.get("/scatter")
def benchmark_scatter(
    x_kpi: str = Query(...),
    y_kpi: str = Query(...),
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_positioning_scatter(sector=sector, period=period, x_kpi=x_kpi, y_kpi=y_kpi)


@router.get("/simulate")
def benchmark_simulate(
    kpi_code: str = Query(...),
    improvement_pct: float = Query(10.0),
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return simulate_improvement(sector=sector, period=period, kpi_code=kpi_code, improvement_pct=improvement_pct)


@router.get("/analysis-modes")
def benchmark_analysis_modes(actor: User = Depends(get_current_user)):
    return {"modes": get_analysis_modes()}


@router.get("/analysis")
def benchmark_analysis(
    mode: str = Query("executive"),
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_ai_analysis(sector=sector, period=period, mode=mode)


@router.get("/library")
def benchmark_library(
    sector: str = Query(DEFAULT_SECTOR),
    period: str = Query(DEFAULT_PERIOD),
    actor: User = Depends(get_current_user),
):
    return get_library(sector=sector, period=period)
