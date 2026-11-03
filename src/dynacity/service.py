"""FastAPI boundary for the forecasting engine.

A thin adapter, intentionally. All validation lives in `contracts.py` and all
behaviour in `engine.py`, so the HTTP surface and the CLI cannot drift apart —
the same request JSON produces the same result either way.

`create_app` takes an already-loaded engine rather than a model path so the
model is loaded once at startup, not per request, and so tests can inject an
engine without touching disk.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field

from .contracts import ForecastRequest, ForecastResult, UrbanStateSnapshot
from .comparison import compare_forecast
from .engine import ForecastEngine
from .evidence import EvidenceBundle, verify_bundle
from .limits import RequestBudget
from .map_query import ask_map
from .planning import PlanningRequest, PlanningResult, plan
from .providers import DraftProvider, resolve_provider
from .scenario_compiler import CompilationResult, compile_scenario
from .terrain import GroundModel
from .traffic import TrafficModel, road_events, traffic_impact
from .viewer import forecast_summary


class ScenarioCompileRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    snapshot: UrbanStateSnapshot
    text: str = Field(min_length=1, max_length=4000)
    evidence: EvidenceBundle | None = None


class ViewerScenarioRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str = Field(min_length=1, max_length=4000)
    # Hour of day (Beirut time) the road impact is measured at.
    hour: int = Field(default=8, ge=0, le=23)


# Road segments one request may close by hand or by prose (a long street is
# 50-100 segments; the Beirut network has about 11,000).
MAX_CLOSED_ROADS = 1000


class ViewerTrafficRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # Road indices closed by hand, and crash sites, from the page's road list.
    closed: list[int] = Field(default_factory=list, max_length=MAX_CLOSED_ROADS)
    crashes: list[int] = Field(default_factory=list, max_length=20)
    hour: int = Field(default=8, ge=0, le=23)


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
    viewer_traffic: TrafficModel | None = None,
    point_cloud: Any | None = None,
    beirut001: dict | None = None,
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

    def roads_after(hour: int, closed: Sequence[int] = (), crashes: Sequence[int] = ()) -> dict | None:
        # What hand-placed or typed closures and crashes do to the street
        # network; None without a traffic model or with nothing on the roads.
        if viewer_traffic is None or not (closed or crashes):
            return None
        disruption = road_events(viewer_traffic, closed=closed, crashes=crashes)
        # Three trip samples, so the page can show the sampling range (~1 s).
        return traffic_impact(viewer_traffic, disruption, hour=hour, samples=3)

    def road_event_sites(events: list) -> tuple[list[int], list[int]]:
        # A closure shuts every segment of the named street; a crash sits on
        # its busiest segment (most probe readings), since prose names a
        # street, not a spot on it.
        closed: list[int] = []
        crashes: list[int] = []
        for event in events:
            if event.kind == "closure":
                closed.extend(r for r in event.roads if r not in closed)
            elif event.roads and viewer_traffic is not None:
                busiest = max(event.roads, key=lambda r: int(viewer_traffic.readings[r]))
                if busiest not in crashes:
                    crashes.append(busiest)
        return closed[:MAX_CLOSED_ROADS], crashes[:20]

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

    @app.post("/v1/forecast/compare")
    def forecast_compare(request: ForecastRequest) -> dict:
        try:
            return compare_forecast(engine, request)
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

    @app.get("/v1/point-cloud")
    def point_cloud_metadata():
        return {"available": point_cloud is not None,
                "metadata": point_cloud.metadata if point_cloud is not None else None}

    @app.get("/v1/point-cloud/{buffer}")
    def point_cloud_buffer(buffer: str):
        if point_cloud is None or buffer not in {"positions", "colors"}:
            raise HTTPException(status_code=404, detail="Point-cloud buffer unavailable")
        return Response(content=getattr(point_cloud, buffer), media_type="application/octet-stream",
                        headers={"Cache-Control": "private, no-store"})

    # Beirut 001 buildings from a pack (`beirut001_pack.viewer_payload`); the
    # source names no licence, so it is served privately and never cached.
    @app.get("/v1/beirut001")
    def beirut001_status():
        return {"available": beirut001 is not None}

    @app.get("/v1/beirut001/buildings")
    def beirut001_buildings():
        if beirut001 is None:
            raise HTTPException(status_code=404, detail="No Beirut 001 pack loaded")
        return JSONResponse(beirut001, headers={"Cache-Control": "private, no-store"})

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
                    road_names=viewer_traffic.name if viewer_traffic is not None else None,
                )
            except RuntimeError as exc:
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            forecast = None
            baseline = None
            comparison = None
            traffic = None
            if compilation.scenario is not None:
                try:
                    compared = compare_forecast(engine, ForecastRequest(
                        snapshot=viewer_snapshot, scenario=compilation.scenario, draws=viewer_draws
                    ))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                forecast = forecast_summary(compared["forecast"])
                baseline = forecast_summary(compared["baseline"])
                comparison = compared["comparison"]
                closed, crashes = road_event_sites(compilation.road_events)
                traffic = roads_after(request.hour, closed, crashes)
            else:
                closed, crashes = [], []
            return {
                "compilation": compilation.model_dump(mode="json"), "forecast": forecast,
                "baseline": baseline, "comparison": comparison,
                "traffic": traffic,
                # Road indices the typed closures and crashes landed on, for the page's road tools.
                "road_events": {"closed": closed, "crashes": crashes},
            }

    # Road impact alone, without re-running the forecast: for the hour slider,
    # and for closures and crashes placed on the roads by hand.
    if viewer_snapshot is not None and viewer_traffic is not None:
        from .traffic_sumo import install_routes
        install_routes(app, viewer_traffic, viewer_snapshot, viewer_ground)
        @app.post("/v1/viewer/traffic")
        def viewer_traffic_impact(request: ViewerTrafficRequest) -> dict:
            try:
                return {"traffic": roads_after(request.hour, request.closed, request.crashes)}
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc

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

    return app

