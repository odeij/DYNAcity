"""Like-for-like scenario/BAU forecasts; marginal bands are not delta bands."""

from .contracts import ForecastRequest, ScenarioSpec
from .engine import ForecastEngine


def compare_forecast(engine: ForecastEngine, request: ForecastRequest) -> dict:
    scenario = engine.forecast(request)
    baseline_request = request.model_copy(update={"scenario": ScenarioSpec(
        scenario_id="business-as-usual",
        baseline_snapshot_id=request.snapshot.snapshot_id,
        horizon_years=request.scenario.horizon_years,
        requested_kpis=request.scenario.requested_kpis,
    )})
    baseline = engine.forecast(baseline_request)
    bases = {(k.year_offset, k.kpi): k for k in baseline.kpis}
    return {
        "forecast": scenario,
        "baseline": baseline,
        "comparison": {
            "draws": request.draws, "seed": request.seed,
            "horizon_years": request.scenario.horizon_years,
            "uncertainty_note": "Bands show the 5th–95th percentiles of each forecast's simulated outcomes. "
            "Changes are differences of medians, without a delta uncertainty interval or significance claim. "
            "The bands do not include uncertainty in assumed effect sizes or model calibration.",
            "kpis": [{
                "year_offset": k.year_offset, "kpi": k.kpi, "unit": k.unit,
                "median_change": k.p50 - bases[(k.year_offset, k.kpi)].p50,
            } for k in scenario.kpis],
        },
    }
