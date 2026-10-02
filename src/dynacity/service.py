"""FastAPI boundary for the forecasting engine.

A thin adapter, intentionally. All validation lives in `contracts.py` and all
behaviour in `engine.py`, so the HTTP surface and the CLI cannot drift apart —
the same request JSON produces the same result either way.

`create_app` takes an already-loaded engine rather than a model path so the
model is loaded once at startup, not per request, and so tests can inject an
engine without touching disk.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import ForecastRequest, ForecastResult, HazardEvent, HazardKind, ScenarioSpec, UrbanStateSnapshot
from .engine import ForecastEngine
from .hazards import PROFILES
from .evidence import EvidenceBundle, verify_bundle
from .google_tiles import GoogleTilesProxy, TilesError
from .limits import RequestBudget
from .map_query import ask_map
from .planning import PlanningRequest, PlanningResult, plan
from .providers import DraftProvider, resolve_provider
from .scenario_compiler import CompilationResult, compile_scenario
from .terrain import GroundModel
from .viewer import drop_to_metres, forecast_summary, hazard_focus


class ScenarioCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UrbanStateSnapshot
    text: str = Field(min_length=1, max_length=4000)
    evidence: EvidenceBundle | None = None


class ViewerScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)


class DroppedHazard(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: HazardKind
    # [longitude, latitude]: one point drops the hazard there; two or more
    # are a tornado's track, in the order it was drawn.
    points: list[tuple[float, float]] = Field(min_length=1, max_length=64)
    severity: Literal["minor", "moderate", "severe", "extreme"] = "moderate"
    # Footprint radius (half-width for a track); omitted, the severity implies it.
    radius_m: float | None = Field(default=None, gt=0, le=5000)


class ViewerHazardRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    hazards: list[DroppedHazard] = Field(min_length=1, max_length=8)


class ViewerAskRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1, max_length=1000)


def create_app(
    engine: ForecastEngine,
    scenario_client: Any | None = None,
    *,
    scenario_provider: DraftProvider | None = None,
    viewer_html: str | None = None,
    viewer_snapshot: UrbanStateSnapshot | None = None,
    evidence: EvidenceBundle | None = None,
    viewer_draws: int = 300,
    viewer_forecast: ForecastResult | None = None,
    viewer_ground: GroundModel | None = None,
    llm_budget: RequestBudget | None = None,
    tiles_proxy: GoogleTilesProxy | None = None,
) -> FastAPI:
    def spend_llm_call() -> None:
        # Checked before any model call; a refused request costs nothing.
        refused = llm_budget.take() if llm_budget is not None else None
        if refused:
            raise HTTPException(status_code=429, detail=refused)

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
        spend_llm_call()
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
            spend_llm_call()
            try:
                compilation = compile_scenario(
                    request.text, viewer_snapshot, provider=provider(),
                    client=scenario_client, evidence=evidence,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            forecast = None
            hazards: list[dict] = []
            if compilation.scenario is not None:
                result = engine.forecast(ForecastRequest(
                    snapshot=viewer_snapshot, scenario=compilation.scenario, draws=viewer_draws
                ))
                forecast = forecast_summary(result)
                # Lets the page fly to where each disaster strikes.
                hazards = hazard_focus(compilation.scenario.hazards, viewer_snapshot, ground=viewer_ground)
            return {"compilation": compilation.model_dump(mode="json"), "forecast": forecast, "hazards": hazards}

    # Disasters dragged onto the map: placed by the page, so no language model
    # is involved and the drop runs straight through the engine. They all
    # strike on the first step, and each drop re-runs the whole set.
    if viewer_snapshot is not None:
        horizon = max((k.year_offset for k in viewer_forecast.kpis), default=6) if viewer_forecast else 6
        located = any(o.centroid_x_m is not None for o in viewer_snapshot.objects)

        @app.post("/v1/viewer/hazard")
        def viewer_hazard(request: ViewerHazardRequest) -> dict:
            if not located:
                raise HTTPException(
                    status_code=422,
                    detail="the served snapshot has no building centroids, so a dropped hazard cannot reach any "
                    "building; serve a snapshot built with centroids",
                )
            hazards = []
            try:
                for index, drop in enumerate(request.hazards, start=1):
                    points = drop_to_metres(drop.points, viewer_snapshot.crs)
                    where = {"path_m": points} if len(points) > 1 else {
                        "center_x_m": points[0][0], "center_y_m": points[0][1]
                    }
                    hazards.append(HazardEvent(
                        hazard_id=f"{drop.kind.value}-{index}", kind=drop.kind,
                        magnitude=PROFILES[drop.kind].severity_magnitudes[drop.severity],
                        radius_m=drop.radius_m, **where,
                    ))
                scenario = ScenarioSpec(
                    scenario_id="dropped-disasters", baseline_snapshot_id=viewer_snapshot.snapshot_id,
                    horizon_years=horizon, hazards=hazards,
                )
            except ValidationError as exc:
                raise HTTPException(status_code=422, detail=exc.errors()[0]["msg"]) from exc
            result = engine.forecast(ForecastRequest(snapshot=viewer_snapshot, scenario=scenario, draws=viewer_draws))
            return {
                "scenario": scenario.model_dump(mode="json"),
                "forecast": forecast_summary(result),
                "hazards": hazard_focus(hazards, viewer_snapshot, ground=viewer_ground),
            }

    # Questions about the map are answered by code from the snapshot and the
    # business-as-usual forecast; the model only turns the question into a
    # checked query (see map_query). A rejected question is still a 200 with
    # `answer: null`, because the audit trail is the useful response.
    if viewer_snapshot is not None and viewer_forecast is not None:
        @app.post("/v1/viewer/ask")
        def viewer_ask(request: ViewerAskRequest) -> dict:
            spend_llm_call()
            try:
                result = ask_map(request.question, viewer_snapshot, viewer_forecast, provider=provider())
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            return result.model_dump(mode="json")

    # Photorealistic basemap: Google 3D Tiles fetched through this server so
    # the Maps key never reaches the browser (see google_tiles).
    if tiles_proxy is not None:
        @app.get("/v1/3dtiles/{path:path}", include_in_schema=False)
        def google_3d_tiles(path: str, request: Request) -> Response:
            try:
                tile = tiles_proxy.fetch(f"v1/3dtiles/{path}", dict(request.query_params))
            except TilesError as exc:
                raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
            return Response(content=tile.body, media_type=tile.content_type, headers={"Cache-Control": "no-store"})

    return app

