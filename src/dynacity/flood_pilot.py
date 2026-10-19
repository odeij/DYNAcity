"""Bounded flood-output comparison. No hydraulic solver or fragility inference.

Imported values are depths above local ground in metres, timestamps are UTC,
coordinates are projected metres. A frame describes the following interval.
"""
from __future__ import annotations

from datetime import datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class PilotGrid(Record):
    crs: Literal["EPSG:32636"] = "EPSG:32636"
    vertical_datum: str = Field(min_length=1)
    x_min_m: float
    y_min_m: float
    cell_m: float = Field(gt=0)
    rows: int = Field(ge=1, le=100)
    cols: int = Field(ge=1, le=100)
    terrain_id: str = Field(min_length=1)
    source_resolution_m: float = Field(gt=0)
    provenance: str = Field(min_length=1)

    @model_validator(mode="after")
    def bounded(self):
        if self.cell_m * max(self.rows, self.cols) > 2000:
            raise ValueError("pilot extent must be at most 2 km per side")
        return self


class PilotFrame(Record):
    timestamp: datetime
    duration_s: float = Field(ge=0)
    depth_m: list[float | None] = Field(max_length=10000)
    velocity_m_s: list[float | None] | None = Field(default=None, max_length=10000)

    @model_validator(mode="after")
    def valid(self):
        if self.timestamp.utcoffset() is None or self.timestamp.utcoffset().total_seconds() != 0:
            raise ValueError("timestamps must be timezone-aware UTC")
        for values in (self.depth_m, self.velocity_m_s or []):
            if any(v is not None and v < 0 for v in values):
                raise ValueError("depth and velocity magnitude must be nonnegative")
        if self.velocity_m_s is not None and len(self.velocity_m_s) != len(self.depth_m):
            raise ValueError("velocity must align with depth")
        return self


class PilotScenario(Record):
    kind: Literal["existing", "blocked", "improved"]
    label: str = Field(min_length=1)
    capacity_factor: float = Field(ge=0, le=10)
    assumption: str = Field(min_length=1)
    model_identity: str = Field(min_length=1)
    provenance: str = Field(min_length=1)
    frames: list[PilotFrame] = Field(min_length=1, max_length=240)


class PilotAsset(Record):
    asset_id: str = Field(min_length=1)
    kind: Literal["building", "road"]
    # Caller supplies cell intersections, not invented geometry or road links.
    cells: list[int] = Field(min_length=1, max_length=10000)
    provenance: str = Field(min_length=1)


class PilotBundle(Record):
    schema_version: Literal[1] = 1
    label: str = Field(min_length=1)
    evidence: Literal["synthetic", "imported"]
    rainfall: dict
    grid: PilotGrid
    scenarios: list[PilotScenario] = Field(min_length=3, max_length=3)
    assets: list[PilotAsset] = Field(default_factory=list, max_length=20000)
    building_threshold_m: float = Field(default=0.1, gt=0)
    road_closure_threshold_m: float = Field(default=0.3, gt=0)
    warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def comparable(self):
        if {s.kind for s in self.scenarios} != {"existing", "blocked", "improved"}:
            raise ValueError("require existing, blocked and improved outputs")
        factors = {s.kind: s.capacity_factor for s in self.scenarios}
        if factors["existing"] != 1 or factors["blocked"] >= 1 or factors["improved"] <= 1:
            raise ValueError("capacity factors require baseline 1, blockage <1, improvement >1")
        schedule = [(f.timestamp, f.duration_s) for f in self.scenarios[0].frames]
        for i, (start, duration) in enumerate(schedule[:-1]):
            if (schedule[i + 1][0] - start).total_seconds() != duration:
                raise ValueError("output intervals must be contiguous and increasing")
        if not any(duration > 0 for _, duration in schedule):
            raise ValueError("at least one positive output interval required")
        if any(duration <= 0 for _, duration in schedule[:-1]):
            raise ValueError("only terminal output may have zero duration")
        n = self.grid.rows * self.grid.cols
        for scenario in self.scenarios:
            if [(f.timestamp, f.duration_s) for f in scenario.frames] != schedule:
                raise ValueError("scenario output schedules must match")
            if any(len(f.depth_m) != n for f in scenario.frames):
                raise ValueError("depth must match the shared grid")
        if len({a.asset_id for a in self.assets}) != len(self.assets):
            raise ValueError("asset ids must be unique")
        if any(c < 0 or c >= n for a in self.assets for c in a.cells):
            raise ValueError("asset cell outside pilot grid")
        forcing = validate_rainfall(self.rainfall)
        from datetime import timedelta
        if schedule[0][0] < forcing.start or schedule[-1][0] + timedelta(seconds=schedule[-1][1]) > forcing.end:
            raise ValueError("rainfall must cover the full comparison period")
        return self


def require_documented_pilot(bundle: PilotBundle) -> PilotBundle:
    """Product boundary: mathematical fixtures are never city flood inputs."""
    if bundle.evidence != "imported":
        raise ValueError("Synthetic flood data is disabled. Supply documented hydraulic outputs from real inputs.")
    if validate_rainfall(bundle.rainfall).provenance.kind == "hypothetical":
        raise ValueError("Hypothetical rainfall is disabled; supply documented observed or derived rainfall.")
    if bundle.grid.vertical_datum.strip().lower() in {"unknown", "missing", "unspecified"}:
        raise ValueError("Documented vertical datum required; missing data is unavailable.")
    return bundle


def summarize_pilot(bundle: PilotBundle) -> dict:
    """Compute conservative intersection exposure; partial nodata stays unknown.

    Closure duration is threshold exceedance summed over supplied intervals,
    with no temporal interpolation or extrapolation. No economic/population loss.
    """
    results = []
    for scenario in bundle.scenarios:
        exposures = []
        for asset in bundle.assets:
            threshold = bundle.road_closure_threshold_m if asset.kind == "road" else bundle.building_threshold_m
            wet_s = known_s = 0.0
            peak = None
            any_exposure = False
            all_known = True
            for frame in scenario.frames:
                values = [frame.depth_m[c] for c in asset.cells]
                known = [v for v in values if v is not None]
                if known:
                    peak = max(known) if peak is None else max(peak, *known)
                # Any observed exceedance proves exposure; absence requires all cells.
                if known and max(known) >= threshold:
                    any_exposure = True
                    wet_s += frame.duration_s
                    known_s += frame.duration_s
                elif len(known) == len(values):
                    known_s += frame.duration_s
                else:
                    all_known = False
            total_s = sum(f.duration_s for f in scenario.frames)
            exposures.append({"asset_id": asset.asset_id, "kind": asset.kind, "peak_depth_m": peak,
                              "threshold_m": threshold, "exceedance_duration_s": wet_s,
                              "unknown_duration_s": total_s - known_s,
                              "exposed": True if any_exposure else (False if all_known else None)})
        depths = [v for f in scenario.frames for v in f.depth_m if v is not None]
        results.append({"kind": scenario.kind, "label": scenario.label,
                        "peak_depth_m": max(depths) if depths else None,
                        "assets": exposures,
                        "exposed_buildings": sum(e["exposed"] is True for e in exposures if e["kind"] == "building"),
                        "closed_roads": sum(e["exposed"] is True for e in exposures if e["kind"] == "road")})
    forcing_hash = hashlib.sha256(json.dumps(bundle.rainfall, sort_keys=True).encode()).hexdigest()
    return {"mode": "model_output_pilot", "label": bundle.label, "evidence": bundle.evidence,
            "rainfall_sha256": forcing_hash, "results": results,
            "thresholds": {"building_depth_m": bundle.building_threshold_m, "road_depth_m": bundle.road_closure_threshold_m},
            "coverage": {"buildings": sum(a.kind == "building" for a in bundle.assets),
                         "roads": sum(a.kind == "road" for a in bundle.assets),
                         "method": "maximum over caller-supplied intersecting cells; no population inference"},
            "critical_facility_accessibility": {"status": "unavailable", "reason": "No verified facility-to-road linkage supplied."},
            "validation": {"status": "not_validated", "reason": "No independent observed event supplied."},
            "warnings": bundle.warnings}


def synthetic_pilot() -> PilotBundle:
    """Deterministic imported-output fixture, never presented as hydraulic results."""
    from datetime import timedelta, timezone
    start = datetime(2026, 10, 16, tzinfo=timezone.utc)
    scenarios = []
    # These arrays are a UI/ingestion fixture, not simulated intervention effects.
    for kind, factor, peak in [("existing", 1., .35), ("blocked", .5, .5), ("improved", 1.5, .2)]:
        frames = [PilotFrame(timestamp=start + timedelta(seconds=i * 600), duration_s=600,
                             depth_m=[round(peak * t * v, 4) for v in [0, .3, .6, .2, .8, 1, 0, .4, .5]])
                  for i, t in enumerate([0, .5, 1, .6, .2, 0])]
        scenarios.append(PilotScenario(kind=kind, label={"existing": "Existing drainage assumption", "blocked": "50% capacity blockage", "improved": "50% capacity increase"}[kind],
                         capacity_factor=factor, assumption="Synthetic capacity assumption; no surveyed drains.",
                         model_identity="synthetic-output-fixture-v1 (not a solver)",
                         provenance="Hand-defined demonstration arrays; differences are not causal predictions.", frames=frames))
    return PilotBundle(label="Beirut neighborhood · synthetic 300 m demonstration extent", evidence="synthetic",
                       rainfall={"provenance": {"kind": "hypothetical", "source": "Hypothetical 30 mm/h constant storm, no return period"}, "intervals": [{"start_utc": start.isoformat(), "duration_s": 3600, "intensity_mm_h": 30}]},
                       grid=PilotGrid(vertical_datum="synthetic local ground reference; not surveyed", x_min_m=732800, y_min_m=3752000,
                                      cell_m=100, rows=3, cols=3, terrain_id="synthetic-demo-grid", source_resolution_m=100,
                                      provenance="Synthetic schematic cells in a Beirut neighborhood extent; no terrain measurements"),
                       scenarios=scenarios,
                       warnings=["SYNTHETIC OUTPUT DEMONSTRATION: arrays are not a hydraulic simulation or Beirut calibration.",
                                 "No surveyed drainage, building/road intersections, critical facilities, population or observed flood events are loaded.",
                                 "Current app terrain is coarse Copernicus surface data; resampling adds no source detail. Running building snapshot has no point-cloud features."])


def rainfall_record(forcing) -> dict:
    """Serialize the ready rainfall component without changing its time semantics."""
    from dataclasses import asdict
    return {"provenance": asdict(forcing.provenance),
            "intervals": [{"start_utc": i.start.isoformat(), "duration_s": i.duration_seconds,
                           "intensity_mm_h": i.intensity_mm_h} for i in forcing.intervals]}


def validate_rainfall(record: dict):
    from datetime import timedelta
    from .flood_rainfall import RainfallForcing, RainfallInterval, RainfallProvenance
    try:
        json.dumps(record, allow_nan=False)
        provenance = RainfallProvenance(**record["provenance"])
        intervals = []
        for i in record["intervals"]:
            start = datetime.fromisoformat(i["start_utc"].replace("Z", "+00:00"))
            intervals.append(RainfallInterval(start, start + timedelta(seconds=i["duration_s"]), i["intensity_mm_h"]))
        return RainfallForcing(tuple(intervals), provenance)
    except (KeyError, TypeError, OverflowError) as exc:
        raise ValueError("invalid rainfall intervals/provenance") from exc


def from_hydraulic_outputs(*, outputs: dict, rainfall, rainfall_source: str, label: str,
                           source_resolution_m: float, capacity_assumptions: dict,
                           assets: list[PilotAsset] = ()) -> PilotBundle:
    """Adapt ready HydraulicOutput objects, verifying common immutable input refs.

    Input grids are north-first; pilot cells are south-first. Only N-1 output
    intervals have duration, left-holding the starting sample. Last time is an
    endpoint with zero duration, retained for peak exposure and never extrapolated. Exact exceedance timing is not recoverable.
    """
    import numpy as np
    from datetime import timezone
    if set(outputs) != {"existing", "blocked", "improved"}:
        raise ValueError("three output cases required")
    baseline = outputs["existing"]
    meta = baseline.metadata
    if meta["crs"] != "EPSG:32636":
        raise ValueError("Beirut pilot requires EPSG:32636; no implicit reprojection")
    x, ymax, dx, dy = meta["grid"]
    if dx != dy:
        raise ValueError("pilot requires square cells; no implicit resampling")
    if len(baseline.depth_m) < 2:
        raise ValueError("at least two timestamps required for duration metrics")
    rows, cols = baseline.depth_m.shape[1:]
    scenarios, warnings = [], []
    for kind, output in outputs.items():
        m = output.metadata
        for key in ("crs", "vertical_datum", "terrain_id", "grid", "timestamps"):
            if m[key] != meta[key]:
                raise ValueError(f"comparison mismatch: {key}")
        if output.depth_m.shape != baseline.depth_m.shape:
            raise ValueError("comparison grids have different shapes")
        if m["forcing"]["rainfall"].get("source") != rainfall_source or m["forcing"]["rainfall"]["status"] != "supplied":
            raise ValueError("external rainfall reference must match supplied forcing")
        if m["forcing"]["boundary"] != meta["forcing"]["boundary"]:
            raise ValueError("boundary conditions must match")
        if m["model"]["name"] != meta["model"]["name"] or m["model"]["version"] != meta["model"]["version"]:
            raise ValueError("comparison engine/version must match")
        times = [datetime.fromisoformat(t.replace("Z", "+00:00")).astimezone(timezone.utc) for t in m["timestamps"]]
        if times[0] < rainfall.start or times[-1] > rainfall.end:
            raise ValueError("rainfall must cover output intervals")
        frames = []
        for i, a in enumerate(times):
            b = times[i+1] if i+1 < len(times) else a
            depth = output.depth_m[i, ::-1].reshape(-1)
            speed = None
            if output.velocity_m_s is not None:
                speed = np.linalg.norm(output.velocity_m_s[i, ::-1], axis=-1).reshape(-1)
            frames.append(PilotFrame(timestamp=a, duration_s=(b-a).total_seconds(),
                                     depth_m=[float(v) if np.isfinite(v) else None for v in depth],
                                     velocity_m_s=None if speed is None else [float(v) if np.isfinite(v) else None for v in speed]))
        assumption = capacity_assumptions[kind]
        scenarios.append(PilotScenario(kind=kind, label=assumption["label"], capacity_factor=assumption["capacity_factor"],
                                       assumption=assumption["assumption"], model_identity=f'{m["model"]["name"]} {m["model"]["version"]}',
                                       provenance=json.dumps(m, sort_keys=True), frames=frames))
        warnings.extend(m["warnings"])
    warnings.extend(rainfall.warnings)
    warnings.append("Sampled depth is left-held between outputs; threshold duration is an approximation. Final timestamp is an endpoint; no extrapolation.")
    return PilotBundle(label=label, evidence="synthetic" if any(o.metadata["model"]["evidence"] == "synthetic" for o in outputs.values()) else "imported",
                       rainfall={**rainfall_record(rainfall), "source": rainfall_source},
                       grid=PilotGrid(vertical_datum=meta["vertical_datum"], x_min_m=x, y_min_m=ymax-rows*dy,
                                      cell_m=dx, rows=rows, cols=cols, terrain_id=meta["terrain_id"],
                                      source_resolution_m=source_resolution_m, provenance=meta["terrain_provenance"]),
                       scenarios=scenarios, assets=list(assets), warnings=list(dict.fromkeys(warnings)))


def intersect_assets(grid: PilotGrid, *, buildings: dict, roads: dict, crs: str,
                     provenance: str) -> list[PilotAsset]:
    """Intersect supplied projected Shapely footprints/road lines; no invented assets.

    Partly outside assets are excluded: declaring them dry would hide uncovered
    portions. Cell-edge intersections count, giving conservative raster exposure.
    """
    from shapely.geometry import box
    if crs != grid.crs:
        raise ValueError("asset CRS must match grid; no implicit reprojection")
    extent = box(grid.x_min_m, grid.y_min_m, grid.x_min_m+grid.cols*grid.cell_m, grid.y_min_m+grid.rows*grid.cell_m)
    cells = [box(grid.x_min_m+c*grid.cell_m, grid.y_min_m+r*grid.cell_m,
                 grid.x_min_m+(c+1)*grid.cell_m, grid.y_min_m+(r+1)*grid.cell_m)
             for r in range(grid.rows) for c in range(grid.cols)]
    assets = []
    for kind, geometries in (("building", buildings), ("road", roads)):
        for identifier, geom in geometries.items():
            if geom.is_empty or not geom.is_valid:
                raise ValueError("asset geometry must be valid and nonempty")
            if extent.covers(geom):
                indexes = [i for i, cell in enumerate(cells) if cell.intersects(geom)]
                assets.append(PilotAsset(asset_id=f"{kind}:{identifier}", kind=kind, cells=indexes, provenance=provenance))
    return assets


# Ready planning component: preserve original producer fingerprints on import.
from .flood_planning import (Budget, Evaluation, ExperimentInputs, Metric, Plan,
                             PlanningChallenge)


class PlanningRunBundle(Record):
    inputs: ExperimentInputs
    plans: tuple[Plan, ...] = Field(max_length=100)
    budget: Budget
    evaluator_id: str = Field(min_length=1)
    evaluations: dict[str, Evaluation]
    hypothesis: str = Field(min_length=1, max_length=4000)
    objectives: tuple[Metric, ...] = tuple(Metric)
    challenge: PlanningChallenge | None = None


def evaluate_planning_bundle(bundle: PlanningRunBundle) -> dict:
    class ImportedEvaluator:
        evaluator_id = bundle.evaluator_id

        def evaluate(self, inputs, plan):
            if plan.fingerprint not in bundle.evaluations:
                raise ValueError(f"No original model evaluation for plan {plan.plan_id}")
            return bundle.evaluations[plan.fingerprint]

    challenge = bundle.challenge or PlanningChallenge(challenge_id="neighborhood-pilot", inputs=bundle.inputs,
                                  budget=bundle.budget, objectives=bundle.objectives)
    if (challenge.inputs.fingerprint != bundle.inputs.fingerprint or challenge.budget != bundle.budget
            or challenge.objectives != bundle.objectives):
        raise ValueError("experiment or budget changed; start a new challenge journal")
    challenge = challenge.iterate(hypothesis=bundle.hypothesis, plans=bundle.plans, evaluator=ImportedEvaluator())
    iteration = challenge.iterations[-1]
    return {"iteration": iteration.model_dump(mode="json"), "challenge": challenge.model_dump(mode="json"),
            "report_sha256": iteration.report.fingerprint,
            "notice": "Costs are supplied assumptions. Outcomes retain original model evidence; no causal benefit or safety claim."}


def drainage_rainfall(forcing):
    """Rebin validated gross rain for SWMM's whole-minute regular intervals."""
    from .flood_drainage import Rainfall
    if forcing.start.second or forcing.start.microsecond or forcing.duration_seconds % 60:
        raise ValueError("SWMM rainfall requires whole-minute start and coverage")
    return Rainfall(start=forcing.start, interval_s=60,
                    intensity_mm_hr=tuple(s.intensity_mm_h for s in forcing.steps(60)),
                    provenance=json.dumps(rainfall_record(forcing), sort_keys=True))


def attach_terrain_evidence(bundle: PilotBundle, terrain) -> PilotBundle:
    """Require exact terrain/output alignment and retain quality limitations."""
    from dataclasses import asdict
    g = bundle.grid
    expected = (g.x_min_m, g.y_min_m, g.x_min_m+g.cols*g.cell_m, g.y_min_m+g.rows*g.cell_m)
    if terrain.crs != g.crs or tuple(terrain.bounds) != expected or terrain.cell_m != g.cell_m:
        raise ValueError("terrain and pilot grid must match exactly; no implicit resampling")
    if terrain.provenance.vertical_reference != g.vertical_datum:
        raise ValueError("terrain and output vertical references differ")
    if terrain.source_sha256 != g.terrain_id:
        raise ValueError("output terrain_id must match supplied terrain source SHA256")
    quality = terrain.quality()
    data = bundle.model_dump(mode="json")
    data["grid"]["source_resolution_m"] = quality.effective_resolution_m
    data["grid"]["provenance"] = json.dumps({"terrain":asdict(terrain.provenance),"quality":asdict(quality),
                                                "source_sha256":terrain.source_sha256}, sort_keys=True)
    data["warnings"] = list(dict.fromkeys([*bundle.warnings, *quality.warnings]))
    return PilotBundle.model_validate(data)


def evaluate_observation_payload(payload: dict) -> dict:
    """HTTP JSON adapter for the ready observation validator, no file access."""
    import numpy as np
    from .flood_validation import (DepthCube, ValidationGrid, DepthObservation,
                                   ExtentObservation, TimingObservation, evaluate_flood)
    model = dict(payload["model"])
    model["grid"] = ValidationGrid(**model["grid"])
    if model["grid"].rows * model["grid"].cols > 10000 or len(model["depth_m"]) > 240:
        raise ValueError("validation exceeds bounded pilot limits")
    model["depth_m"] = np.asarray(model["depth_m"], dtype=float)
    model["valid"] = np.asarray(model["valid"])
    records = []
    classes = {"depth": DepthObservation, "extent": ExtentObservation, "timing": TimingObservation}
    for raw in payload["observations"]:
        item = dict(raw)
        kind = item.pop("kind")
        if kind not in classes:
            raise ValueError("unsupported observation kind")
        if kind == "extent":
            item["grid"] = ValidationGrid(**item["grid"])
            for key in ("flooded", "valid", "uncertain"):
                item[key] = np.asarray(item[key])
        records.append(classes[kind](**item))
    return evaluate_flood(DepthCube(**model), records)


def pilot_map_geometry(bundle: PilotBundle) -> dict:
    """Georeference model cells for the schematized, local-ground city viewer.

    No terrain elevations or water-surface stages are invented. The 3D viewer
    places its buildings at a local zero baseline; depth extrusion uses that
    same display reference and is not an absolute-elevation hydraulic surface.
    """
    from .viewer import utm_to_lonlat
    g = bundle.grid
    polygons = []
    for row in range(g.rows):
        for col in range(g.cols):
            x, y = g.x_min_m + col*g.cell_m, g.y_min_m + row*g.cell_m
            polygons.append([utm_to_lonlat(xx, yy, 36) for xx, yy in
                             ((x,y),(x+g.cell_m,y),(x+g.cell_m,y+g.cell_m),(x,y+g.cell_m),(x,y))])
    return {"polygons": polygons,
            "center": utm_to_lonlat(g.x_min_m + g.cols*g.cell_m/2, g.y_min_m + g.rows*g.cell_m/2, 36),
            "render_reference": "Depth above schematized viewer ground; no absolute water-surface elevation or new flow simulation."}
