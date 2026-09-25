"""FastAPI boundary for the forecasting engine.

A thin adapter, intentionally. All validation lives in `contracts.py` and all
behaviour in `engine.py`, so the HTTP surface and the CLI cannot drift apart —
the same request JSON produces the same result either way.

`create_app` takes an already-loaded engine rather than a model path so the
model is loaded once at startup, not per request, and so tests can inject an
engine without touching disk.
"""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ForecastRequest, ForecastResult, UrbanStateSnapshot
from .engine import ForecastEngine
from .evidence import EvidenceBundle, verify_bundle
from .planning import PlanningRequest, PlanningResult, plan
from .providers import DraftProvider, resolve_provider
from .scenario_compiler import CompilationResult, compile_scenario
from .viewer import forecast_summary


class ScenarioCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UrbanStateSnapshot
    text: str = Field(min_length=1, max_length=4000)
    evidence: EvidenceBundle | None = None


class ViewerScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


def create_app(
    engine: ForecastEngine,
    scenario_client: Any | None = None,
    *,
    scenario_provider: DraftProvider | None = None,
    viewer_html: str | None = None,
    viewer_snapshot: UrbanStateSnapshot | None = None,
    evidence: EvidenceBundle | None = None,
    viewer_draws: int = 300,
) -> FastAPI:
    def provider() -> DraftProvider | None:
        # Resolved per request when none was injected, so a key added after
        # startup is picked up; a missing SDK or key becomes a clear 503.
        if scenario_provider is not None or scenario_client is not None:
            return scenario_provider
        try:
            return resolve_provider()
        except Exception as exc:  # SDKs raise different types for a missing key
            raise HTTPException(
                status_code=503,
                detail=f"no language-model provider configured ({exc}); set GEMINI_API_KEY or ANTHROPIC_API_KEY",
            ) from exc

    app = FastAPI(
        title="DynaCITY Urban-Form Forecasting",
        version="0.1.0",
        description=(
            "Probabilistic Beirut urban-state forecasts. Intervention scenarios are "
            "assumption-based and are not causal recommendations."
        ),
    )

    @app.get("/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "model_version": engine.bundle.model_version}

    # Exposes the bundle's provenance — versions, training intervals, and the
    # benchmark metrics of the model that shipped. Published deliberately: a
    # consumer should be able to see how the model scored, including when the
    # learned candidate lost the gate and a baseline is serving.
    @app.get("/v1/models")
    def model_metadata() -> dict:
        return {
            "model_version": engine.bundle.model_version,
            "data_version": engine.bundle.data_version,
            "trained_at": engine.bundle.trained_at,
            "metrics": engine.bundle.metrics,
            "training_intervals": engine.bundle.training_intervals,
        }

    @app.post("/v1/forecast", response_model=ForecastResult)
    def forecast(request: ForecastRequest) -> ForecastResult:
        # The engine raises ValueError for inputs that parse but are not
        # forecastable (empty snapshot, unsupported KPI). That is a semantic
        # problem with the request, so 422 — matching how pydantic already
        # reports schema violations — rather than a 500.
        try:
            return engine.forecast(request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Searches lever combinations for the Pareto front and the backcast. Work
    # scales with evaluations × draws × objects, bounded by PlanningRequest.
    @app.post("/v1/plan", response_model=PlanningResult)
    def plan_endpoint(request: PlanningRequest) -> PlanningResult:
        try:
            return plan(engine, request)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    # Compiles planner prose into a ScenarioSpec but never runs it: the caller
    # reviews the returned attempts and issues, then posts the scenario to
    # /v1/forecast. A rejected compile is still a 200 with `scenario: null`,
    # because the audit trail is the useful response.
    @app.post("/v1/scenarios/compile")
    def compile_endpoint(request: ScenarioCompileRequest) -> dict:
        if request.evidence is not None and not verify_bundle(request.evidence):
            raise HTTPException(status_code=422, detail="evidence bundle does not match its content hash")
        try:
            result: CompilationResult = compile_scenario(
                request.text, request.snapshot, provider=provider(),
                client=scenario_client, evidence=request.evidence,
            )
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return result.model_dump(mode="json")

    # Viewer mode: the 3D page is served from the same origin as the API, and
    # scenarios run against the snapshot held here. The browser only ever sends
    # prose, so per-object features never round-trip through the page.
    if viewer_html is not None:
        @app.get("/", response_class=HTMLResponse, include_in_schema=False)
        def viewer_page() -> str:
            return viewer_html

    if viewer_snapshot is not None:
        @app.post("/v1/viewer/scenario")
        def viewer_scenario(request: ViewerScenarioRequest) -> dict:
            try:
                compilation = compile_scenario(
                    request.text, viewer_snapshot, provider=provider(),
                    client=scenario_client, evidence=evidence,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            forecast = None
            if compilation.scenario is not None:
                result = engine.forecast(ForecastRequest(
                    snapshot=viewer_snapshot, scenario=compilation.scenario, draws=viewer_draws
                ))
                forecast = forecast_summary(result)
            return {"compilation": compilation.model_dump(mode="json"), "forecast": forecast}

    return app

