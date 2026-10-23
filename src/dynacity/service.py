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
from typing import Any, Literal
from pathlib import Path
import re

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, Response, FileResponse
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .contracts import ForecastRequest, ForecastResult, HazardEvent, HazardKind, ScenarioSpec, UrbanStateSnapshot
from .comparison import compare_forecast
from .engine import ForecastEngine
from .hazards import PROFILES
from .flood_pilot import (
    PilotBundle, PlanningRunBundle, summarize_pilot, require_documented_pilot,
    evaluate_planning_bundle, evaluate_observation_payload, pilot_map_geometry,
)
from .evidence import EvidenceBundle, verify_bundle
from .limits import RequestBudget
from .map_query import ask_map
from .planning import PlanningRequest, PlanningResult, plan
from .providers import DraftProvider, resolve_provider
from .scenario_compiler import CompilationResult, compile_scenario
from .terrain import GroundModel
from .traffic import TrafficModel, combine_disruptions, road_disruption, road_events, traffic_impact
from .viewer import drop_to_metres, forecast_summary, hazard_focus


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
    hour: int = Field(default=8, ge=0, le=23)


# Road segments one request may close by hand or by prose (a long street is
# 50-100 segments; the Beirut network has about 11,000).
MAX_CLOSED_ROADS = 1000


class ViewerTrafficRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # The disasters already on the map, as the server returned them (snapshot metres).
    hazards: list[HazardEvent] = Field(default_factory=list, max_length=8)
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
    flood_pilot: PilotBundle | None = None,
    flood_context: dict | None = None,
    flood_jobs: Any | None = None,
    point_cloud: Any | None = None,
    beirut_overlay_dir: Path | None = None,
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

    def roads_after(
        hazards: list[HazardEvent], hour: int, closed: Sequence[int] = (), crashes: Sequence[int] = ()
    ) -> dict | None:
        # What the disasters (and any hand-placed closures or crashes) do to the
        # street network; None without a traffic model or with nothing on the roads.
        if viewer_traffic is None or not (hazards or closed or crashes):
            return None
        parts = [road_events(viewer_traffic, closed=closed, crashes=crashes)]
        if hazards:
            parts.append(road_disruption(hazards, viewer_snapshot, viewer_traffic, ground=viewer_ground))
        # Three trip samples, so the page can show the sampling range (~1 s).
        return traffic_impact(viewer_traffic, combine_disruptions(*parts), hour=hour, samples=3)

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

    @app.get("/flood", response_class=HTMLResponse, include_in_schema=False)
    def flood_page() -> str:
        from importlib.resources import files
        return files("dynacity").joinpath("flood_pilot_template.html").read_text(encoding="utf-8")

    def pilot_response(bundle: PilotBundle) -> dict:
        try:
            require_documented_pilot(bundle)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return {"bundle": bundle.model_dump(mode="json"), "summary": summarize_pilot(bundle)}

    @app.get("/v1/flood/pilot")
    def flood_status() -> dict:
        if flood_pilot is None:
            return {"status": "unavailable", "mode": "model_output_pilot", "bundle": None,
                    "reason": "No documented neighborhood comparison outputs loaded."}
        return pilot_response(flood_pilot)

    @app.get("/v1/flood/context")
    def flood_survey_context(request: Request) -> dict:
        if flood_context is None:
            return {"status": "unavailable", "mode": "survey_context", "simulation_ready": False,
                    "reason": "No verified survey package configured. Load it with serve --flood-context."}
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Survey package is configured for local research only.")
        return flood_context

    @app.post("/v1/flood/validation")
    def flood_validation(payload: dict) -> dict:
        try:
            return evaluate_observation_payload(payload)
        except (ValueError, TypeError, KeyError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/flood/planning/compare")
    def flood_planning_compare(bundle: PlanningRunBundle) -> dict:
        try:
            return evaluate_planning_bundle(bundle)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.get("/v1/flood/runtime")
    def flood_runtime() -> dict:
        from .flood_surface import solver_runtime_status
        from .flood_drainage import detect_swmm
        return {"surface": solver_runtime_status(), "drainage": {"available": detect_swmm() is not None},
                "coupling": {"available": False, "reason": "Surveyed inlet geometry and a surface coupling runner are unavailable."}}

    def local_flood_jobs(request: Request):
        if request.client is None or request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(status_code=403, detail="Flood execution is available only in the local session.")
        if flood_jobs is None:
            raise HTTPException(status_code=503, detail="Flood execution storage is not configured for this server.")
        return flood_jobs

    @app.get("/v1/flood/solver/inputs")
    def flood_input_status() -> dict:
        from .flood_solver import RunManifest
        from .flood_coupling import runtime_status
        return {"execution_enabled": flood_jobs is not None, "package_loaded": False,
                "dynamic_drainage":runtime_status(),
                "schema": RunManifest.model_json_schema(),
                "missing_inputs": (flood_context or {}).get("model_inputs", {}),
                "description": "ZIP containing manifest.json, numerical mesh/forcing arrays and all referenced source files. No physical values are supplied automatically."}

    @app.post("/v1/flood/solver/packages")
    async def flood_package(request: Request) -> dict:
        from .flood_jobs import MAX_UPLOAD_BYTES
        jobs = local_flood_jobs(request)
        payload = bytearray()
        async for chunk in request.stream():
            payload.extend(chunk)
            if len(payload) > MAX_UPLOAD_BYTES:
                raise HTTPException(status_code=413, detail="Input package exceeds 50 MB.")
        from starlette.concurrency import run_in_threadpool
        try:
            return await run_in_threadpool(jobs.prepare, bytes(payload))
        except Exception as exc:
            import zipfile
            if isinstance(exc, ValidationError):
                messages = [f"{'.'.join(map(str,e['loc']))}: {e['msg']}" for e in exc.errors()]
                raise HTTPException(status_code=422, detail={"message":"Input manifest is incomplete or invalid.", "issues":messages}) from exc
            if isinstance(exc, (ValueError, KeyError, OSError, zipfile.BadZipFile, ImportError, RuntimeError)):
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            raise

    def flood_job_call(request: Request, method: str, job_id: str, *args):
        jobs = local_flood_jobs(request)
        try:
            return getattr(jobs, method)(job_id, *args)
        except KeyError as exc:
            raise HTTPException(status_code=404, detail="Flood run not found in this session.") from exc
        except (ValueError, OSError, RuntimeError) as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

    @app.post("/v1/flood/solver/jobs/{job_id}/start")
    def flood_start(job_id: str, request: Request):
        return flood_job_call(request, "start", job_id)

    @app.get("/v1/flood/solver/jobs/{job_id}")
    def flood_job_status(job_id: str, request: Request):
        return flood_job_call(request, "status", job_id)

    @app.post("/v1/flood/solver/jobs/{job_id}/cancel")
    def flood_cancel(job_id: str, request: Request):
        return flood_job_call(request, "cancel", job_id)

    @app.get("/v1/flood/solver/jobs/{job_id}/geometry")
    def flood_geometry(job_id: str, request: Request):
        return flood_job_call(request, "geometry", job_id)

    @app.get("/v1/flood/solver/jobs/{job_id}/frames/{index}")
    def flood_frame(job_id: str, index: int, request: Request):
        return flood_job_call(request, "frame", job_id, index)

    if flood_jobs is not None:
        app.router.add_event_handler("shutdown", flood_jobs.close)

    @app.get("/v1/flood/pilot/demo")
    def flood_demo() -> dict:
        raise HTTPException(status_code=410, detail="Invented flood demos are disabled. Documented real inputs required.")

    @app.post("/v1/flood/pilot/map")
    def flood_map(bundle: PilotBundle) -> dict:
        return {**pilot_response(bundle), "map": pilot_map_geometry(bundle)}

    @app.post("/v1/flood/pilot/compare")
    def flood_compare(bundle: PilotBundle) -> dict:
        return pilot_response(bundle)

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

    @app.get("/v1/beirut-overlay")
    def beirut_overlay_status():
        return {"available": beirut_overlay_dir is not None}

    @app.get("/v1/beirut-overlay/{asset}")
    def beirut_overlay_asset(asset: str):
        allowed = {"data.json", "indices.bin", "map-positions.bin", "map-normals.bin", "map-picking.bin"}
        if beirut_overlay_dir is None or asset not in allowed:
            raise HTTPException(status_code=404, detail="Overlay asset unavailable")
        path = Path(beirut_overlay_dir) / asset
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Overlay asset unavailable")
        return FileResponse(path, media_type="application/json" if asset.endswith('.json') else "application/octet-stream", headers={"Cache-Control": "private, no-store"})

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
            if re.search(r'\bflood(?:s|ing|ed)?\b|فيضان|فيضانات',request.text,re.IGNORECASE):
                raise HTTPException(status_code=422,detail='Flood simulation is not running. Use the Flooding panel with a documented terrain, storm and drainage input package. Scenario prompts cannot supply missing hydraulic inputs.')
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
            hazards: list[dict] = []
            traffic = None
            if compilation.scenario is not None:
                if any(h.kind is HazardKind.FLOOD for h in compilation.scenario.hazards):
                    raise HTTPException(status_code=422, detail="Illustrative flood calculations are disabled; documented hydraulic outputs are required.")
                try:
                    compared = compare_forecast(engine, ForecastRequest(
                        snapshot=viewer_snapshot, scenario=compilation.scenario, draws=viewer_draws
                    ))
                except ValueError as exc:
                    raise HTTPException(status_code=422, detail=str(exc)) from exc
                forecast = forecast_summary(compared["forecast"])
                baseline = forecast_summary(compared["baseline"])
                comparison = compared["comparison"]
                # Lets the page fly to where each disaster strikes.
                hazards = hazard_focus(compilation.scenario.hazards, viewer_snapshot, ground=viewer_ground)
                closed, crashes = road_event_sites(compilation.road_events)
                traffic = roads_after(compilation.scenario.hazards, request.hour, closed, crashes)
            else:
                closed, crashes = [], []
            return {
                "compilation": compilation.model_dump(mode="json"), "forecast": forecast, "hazards": hazards,
                "baseline": baseline, "comparison": comparison,
                "traffic": traffic,
                "flood_mode": "legacy_illustrative_water_level",
                # Road indices the typed closures and crashes landed on, for the page's road tools.
                "road_events": {"closed": closed, "crashes": crashes},
            }

    # Disasters dragged onto the map: placed by the page, so no language model
    # is involved and the drop runs straight through the engine. They all
    # strike on the first step, and each drop re-runs the whole set.
    if viewer_snapshot is not None:
        horizon = max((k.year_offset for k in viewer_forecast.kpis), default=6) if viewer_forecast else 6
        located = any(o.centroid_x_m is not None for o in viewer_snapshot.objects)

        @app.post("/v1/viewer/hazard")
        def viewer_hazard(request: ViewerHazardRequest) -> dict:
            if any(drop.kind is HazardKind.FLOOD for drop in request.hazards):
                raise HTTPException(status_code=422, detail="Illustrative flood drops are disabled; use documented hydraulic outputs.")
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
            try:
                compared = compare_forecast(engine, ForecastRequest(
                    snapshot=viewer_snapshot, scenario=scenario, draws=viewer_draws
                ))
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            return {
                "scenario": scenario.model_dump(mode="json"),
                "forecast": forecast_summary(compared["forecast"]),
                "baseline": forecast_summary(compared["baseline"]),
                "comparison": compared["comparison"],
                "hazards": hazard_focus(hazards, viewer_snapshot, ground=viewer_ground),
                "flood_mode": "legacy_illustrative_water_level",
                "traffic": roads_after(hazards, request.hour),
            }

    # Road impact alone, without re-running the forecast: for the hour slider,
    # and for closures and crashes placed on the roads by hand.
    if viewer_snapshot is not None and viewer_traffic is not None:
        from .traffic_sumo import install_routes
        install_routes(app, viewer_traffic, viewer_snapshot, viewer_ground)
        @app.post("/v1/viewer/traffic")
        def viewer_traffic_impact(request: ViewerTrafficRequest) -> dict:
            try:
                return {"traffic": roads_after(request.hazards, request.hour, request.closed, request.crashes)}
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

