"""Command-line entrypoints for data preparation, training, and inference.

Each subcommand is one stage of the pipeline, and stages communicate through
files rather than in-process state:

    fetch-bbed      → BBED GeoJSON + provenance manifest
    inspect-las     → header report (no point data read)
    build-copc      → tiled COPC for large clouds
    extract-features→ per-building morphology CSV
    build-panel     → transition panel CSV
    summarize-transitions → descriptive interval JSON (never modelled)
    build-snapshot  → present-state snapshot JSON
    fetch-terrain   → approximate ground-elevation grid (.npz) for terrain-following floods
    pack-beirut001  → Beirut 001 Rhino model → small private pack (.npz) of outlines, heights, ground
    build-traffic   → OSM road graph + Tari'ak hourly speeds (.json) for traffic and road closures
    build-zones     → decision zones (.json) over the roads from OSM boundaries, for adaptation policies
    run-plan        → a street plan in SUMO against a paired baseline (.json), without the viewer
    freeze-evidence → registry JSON → content-hashed EvidenceBundle
    compile-scenario→ planner prose → checked ScenarioSpec + audit trail
    train           → ModelBundle + sibling .metrics.json
    forecast        → ForecastResult JSON
    plan            → backcast + Pareto front over lever combinations
    export-viewer   → self-contained 3D HTML of footprints, states, forecasts
    serve           → the same forecast over HTTP (+ live 3D viewer at /)

Keeping the boundaries on disk means each stage is independently inspectable and
re-runnable — the panel can be examined before training, the metrics before
serving.

Every path here is caller-supplied and expected to sit under the private roots
from `config.Settings`; nothing defaults to a location inside the repository.
"""

from __future__ import annotations

import argparse
import json
import hashlib
import shutil
import tempfile
from datetime import date
from pathlib import Path

import pandas as pd

from .bbed import BBEDClient, write_snapshot
from .coverage import coverage_report
from .contracts import ForecastRequest, ForecastResult, ScenarioSpec, UrbanStateSnapshot
from .engine import ForecastEngine
from .evidence import EvidenceBundle, EvidenceRegistry, freeze_bundle, verify_bundle
from .features import extract_building_features, load_points
from .las import (
    LASHeader,
    classification_sample,
    copc_pipeline,
    run_pdal_pipeline,
)
from .limits import RequestBudget
from .models import ModelBundle, temporal_benchmark
from .panel import PanelBuilder, assert_no_temporal_leakage
from .planning import PlanningRequest, plan
from .providers import resolve_provider
from .scenario_compiler import compile_scenario
from .service import create_app
from .flood_pilot import PilotBundle, require_documented_pilot
from .snapshot import snapshot_from_bbed
from .terrain import GroundModel, attach_ground_elevation, fetch_ground_model
from .traffic import TrafficModel, build_traffic_model, building_coverage, traffic_payload
from .viewer import build_viewer_payload, render_viewer
from .landscape import apply_context_buildings, landscape_payload
from .transitions import summarize_interval


def _json(path: str | Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_json(path: str | Path, payload: dict) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _private_destination(path: str | Path) -> Path:
    destination = Path(path).resolve()
    if any(((parent / ".git" / "HEAD").is_file() or (parent / ".git").is_file()) for parent in (destination, *destination.parents) if parent != Path("/")):
        raise ValueError("Data outputs must be outside Git checkouts")
    return destination


def command_audit_coverage(args: argparse.Namespace) -> None:
    report = coverage_report(
        _json(args.bbed), manifest=_json(args.manifest) if args.manifest else None,
        snapshot=UrbanStateSnapshot.model_validate(_json(args.snapshot)) if args.snapshot else None,
        morphology=pd.read_csv(args.lidar_features) if args.lidar_features else None,
        previous=_json(args.previous) if args.previous else None,
        panel=pd.read_csv(args.panel) if args.panel else None,
    )
    _write_json(_private_destination(args.output), report)
    print(f"coverage -> {args.output}; {len(report['errors'])} errors, {len(report['warnings'])} warnings")
    if not report['passed']:
        raise SystemExit(2)


def command_stage_update(args: argparse.Namespace) -> None:
    """Publish only a checked, immutable candidate; never replace active files."""
    destination = _private_destination(args.output_dir)
    if destination.exists():
        raise ValueError("Candidate directory already exists; choose a new version")
    collection, manifest = _json(args.bbed), _json(args.manifest)
    morphology = pd.read_csv(args.lidar_features) if args.lidar_features else None
    previous = _json(args.previous) if args.previous else None
    report = coverage_report(collection, manifest=manifest, morphology=morphology, previous=previous)
    if report['errors']:
        raise ValueError("Candidate failed checks: " + "; ".join(report['errors']))
    if report.get('update', {}).get('removed') and not args.acknowledge_removals:
        raise ValueError("Candidate removes IDs; inspect audit report and use --acknowledge-removals")
    snapshot = snapshot_from_bbed(collection, snapshot_id=args.snapshot_id,
                                  as_of=date.fromisoformat(args.as_of), data_version=args.data_version,
                                  lidar_features=morphology)
    report = coverage_report(collection, manifest=manifest, snapshot=snapshot,
                             morphology=morphology, previous=previous)
    if not report['passed']:
        raise ValueError("Derived snapshot failed checks: " + "; ".join(report['errors']))
    destination.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix=".dynacity-candidate-", dir=destination.parent))
    try:
        write_snapshot(collection, manifest, stage / "bbed.geojson")
        _write_json(stage / "snapshot.json", snapshot.model_dump(mode="json"))
        _write_json(stage / "coverage.json", report)
        panel = PanelBuilder().build(collection, morphology)
        assert_no_temporal_leakage(panel)
        panel.to_csv(stage / "panel.csv", index=False)
        if args.lidar_features:
            shutil.copyfile(args.lidar_features, stage / "morphology.csv")
        _write_json(stage / "update.json", {
            "schema": "dynacity-update/1", "source_sha256": report['source_sha256'],
            "snapshot_id": args.snapshot_id, "data_version": args.data_version,
            "as_of": args.as_of, "status_periods": [2018, 2022, 2024],
            "morphology_acquisition_year": 2020 if morphology is not None else None,
            "removals_acknowledged": args.acknowledge_removals,
            "previous_sha256": report.get('update', {}).get('previous_sha256'),
            "activation": "manual; retrain and regenerate forecast before serving",
            "artifact_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in stage.iterdir()},
        })
        # An exclusive mkdir reserves the destination against concurrent writers.
        destination.mkdir()
        stage.rename(destination / "data")
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print(f"checked candidate -> {destination}; active data unchanged")


def command_inspect_las(args: argparse.Namespace) -> None:
    payload = LASHeader.read(args.path).as_dict()
    if args.sample_classes:
        payload["classification_sample"] = classification_sample(
            args.path, args.sample_classes
        )
    print(json.dumps(payload, indent=2))


def command_las_surface(args: argparse.Namespace) -> None:
    from .las_surface import export_las_surface
    report = export_las_surface(args.path, args.output, cell_m=args.cell_m,
                                horizontal_crs=args.crs, crs_evidence=args.crs_evidence,
                                max_points=args.max_points)
    print(json.dumps(report, indent=2))


def command_export_point_cloud(args: argparse.Namespace) -> None:
    from .point_cloud import export_point_cloud
    print(json.dumps(export_point_cloud(args.path, args.output_dir, crs=args.crs,
                      crs_evidence=args.crs_evidence, max_points=args.max_points), indent=2))


def command_fetch_bbed(args: argparse.Namespace) -> None:
    """Download the allowlisted BBED fields and their provenance manifest.

    Only `bbed.BBED_FIELDS` are ever requested. The manifest is written beside
    the data and records source, timestamp, content hash, and the ODbL
    attribution — keep them together.
    """

    client = BBEDClient(layer_url=args.layer_url)
    collection = client.fetch_features(
        where=args.where,
        bbox=tuple(args.bbox) if args.bbox else None,
    )
    output, manifest = write_snapshot(
        collection, {**client.snapshot_manifest(collection),
                     "query": {"where": args.where, "bbox": args.bbox},
                     "status_periods": [2018, 2022, 2024],
                     "completeness": "returned pages; citywide completeness unverified"}, args.output
    )
    print(f"wrote {len(collection['features'])} features to {output}")
    print(f"wrote provenance manifest to {manifest}")


def command_build_copc(args: argparse.Namespace) -> None:
    """Convert LAS to COPC via PDAL, asserting the CRS on the way through.

    `--dry-run` prints the pipeline without executing it — worth using, since
    the CRS is asserted rather than read from the file and a wrong value
    misplaces the entire cloud.
    """

    pipeline = copc_pipeline(args.source, args.output, source_crs=args.crs)
    if args.dry_run:
        print(json.dumps(pipeline, indent=2))
        return
    run_pdal_pipeline(pipeline)
    print(f"wrote {args.output}")


def command_extract_features(args: argparse.Namespace) -> None:
    """Join a point cloud to BBED polygons and write the morphology table.

    Fails rather than truncating when the cloud exceeds `--max-points`
    (default 10M). Most of this project's clouds do; raising the limit also
    raises memory use roughly linearly, so tiling to COPC first is usually the
    better route.
    """

    collection = _json(args.bbed)
    points = load_points(args.las, max_points=args.max_points)
    frame = extract_building_features(points, collection, id_field=args.id_field)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(args.output, index=False)
    print(f"wrote {len(frame)} building feature rows to {args.output}")


def command_build_panel(args: argparse.Namespace) -> None:
    """Build the transition panel, refusing to write it if leakage is present.

    The check runs before the CSV is written, so a leaking panel never reaches
    disk where it could later be trained on by accident.
    """

    lidar = pd.read_csv(args.lidar_features) if args.lidar_features else None
    panel = PanelBuilder(lidar_acquisition_year=args.lidar_year).build(
        _json(args.bbed), lidar
    )
    assert_no_temporal_leakage(panel)
    Path(args.output).parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(args.output, index=False)
    print(f"wrote {len(panel)} transition rows to {args.output}")


def command_build_snapshot(args: argparse.Namespace) -> None:
    lidar = pd.read_csv(args.lidar_features) if args.lidar_features else None
    snapshot = snapshot_from_bbed(
        _json(args.bbed),
        snapshot_id=args.snapshot_id,
        as_of=date.fromisoformat(args.as_of),
        data_version=args.data_version,
        lidar_features=lidar,
    )
    _write_json(args.output, snapshot.model_dump(mode="json"))
    print(f"wrote {len(snapshot.objects)} urban objects to {args.output}")


def command_summarize_transitions(args: argparse.Namespace) -> None:
    """Report observed transitions for one interval — descriptive only.

    Defaults to the full 2018→2024 span, which `temporal_benchmark` explicitly
    excludes. Nothing this writes is ever consumed by training; it exists so the
    six-year change can be reported directly rather than inferred by chaining
    two modelled steps.
    """

    panel = pd.read_csv(args.panel)
    summary = summarize_interval(
        panel,
        start_year=args.start_year,
        target_year=args.target_year,
    )
    _write_json(args.output, summary)
    print(
        f"summarized {summary['total_rows']} "
        f"{args.start_year}->{args.target_year} transition rows -> {args.output}"
    )


def command_train(args: argparse.Namespace) -> None:
    """Run the benchmark and persist the winning bundle plus every candidate's scores.

    Leakage is re-checked here even though `build-panel` already did: panels
    round-trip through CSV and may be edited, regenerated by an older build, or
    hand-assembled between the two commands.

    The metrics file holds *all* candidates, not just the winner, and is written
    next to the model. That is what keeps a negative result — the learned model
    failing the gate — visible rather than discarded.
    """

    panel = pd.read_csv(args.panel)
    assert_no_temporal_leakage(panel)
    bundle, metrics = temporal_benchmark(panel)
    bundle.data_version = args.data_version
    bundle.save(args.output)
    report_path = Path(args.output).with_suffix(".metrics.json")
    _write_json(report_path, metrics)
    print(f"selected {bundle.metrics['selected_model']} -> {args.output}")
    print(f"metrics -> {report_path}")


def command_forecast(args: argparse.Namespace) -> None:
    bundle = ModelBundle.load(args.model)
    request = ForecastRequest.model_validate(_json(args.request))
    engine = ForecastEngine(bundle)
    comparison = None
    if args.comparison_output:
        from .comparison import compare_forecast

        comparison = compare_forecast(engine, request)
    result = comparison["forecast"] if comparison else engine.forecast(request)
    _write_json(args.output, result.model_dump(mode="json"))
    print(f"forecast -> {args.output}")
    if comparison is not None:
        comparison["forecast"] = comparison["forecast"].model_dump(mode="json")
        comparison["baseline"] = comparison["baseline"].model_dump(mode="json")
        _write_json(args.comparison_output, comparison)
        print(f"baseline comparison -> {args.comparison_output}")


def command_freeze_evidence(args: argparse.Namespace) -> None:
    registry = EvidenceRegistry.model_validate(_json(args.registry))
    bundle = freeze_bundle(registry, date.fromisoformat(args.as_of))
    _write_json(args.output, bundle.model_dump(mode="json"))
    unverified = [s.source_id for s in bundle.sources if not s.verified]
    print(f"evidence bundle {bundle.bundle_id} ({len(bundle.sources)} sources) -> {args.output}")
    if unverified:
        print(f"  unverified sources cap citing levers at low confidence: {unverified}")


def _load_bundle(path: str | None) -> EvidenceBundle | None:
    if not path:
        return None
    bundle = EvidenceBundle.model_validate(_json(path))
    if not verify_bundle(bundle):
        raise SystemExit(f"evidence bundle {path} does not match its content hash; re-freeze it")
    return bundle


def command_compile_scenario(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    text = args.text if args.text is not None else Path(args.text_file).read_text(encoding="utf-8")
    result = compile_scenario(
        text,
        snapshot,
        provider=resolve_provider(args.provider, args.llm_model),
        max_repairs=args.max_repairs,
        evidence=_load_bundle(args.evidence),
    )
    _write_json(args.output, result.model_dump(mode="json"))
    print(f"compilation report -> {args.output}")
    for attempt in result.attempts:
        for issue in attempt.report.issues:
            print(f"  [{issue.severity}] {issue.code}: {issue.message}")
    if not result.ok:
        raise SystemExit("scenario rejected; see the report for every attempt")
    if args.request_output:
        request = ForecastRequest(snapshot=snapshot, scenario=result.scenario)
        _write_json(args.request_output, request.model_dump(mode="json"))
        print(f"forecast request -> {args.request_output}")


def command_plan(args: argparse.Namespace) -> None:
    engine = ForecastEngine(ModelBundle.load(args.model))
    result = plan(engine, PlanningRequest.model_validate(_json(args.request)))
    _write_json(args.output, result.model_dump(mode="json"))
    print(
        f"plan -> {args.output} ({result.evaluated} of {result.space_size} combinations, "
        f"{result.search}; front {len(result.pareto_front)}, backcast {result.backcast})"
    )


def _landscape_view(path: str | None, traffic: TrafficModel | None = None, context_buildings: str | None = None,
                    bounds: tuple[float, float, float, float] | None = None) -> dict | None:
    if not path:
        if context_buildings:
            raise SystemExit('--context-buildings requires --landscape')
        return None
    raw = _json(path)
    companion = _json(context_buildings) if context_buildings else None
    if bounds is not None:
        from .corridor import crop_context_buildings, crop_osm
        raw = crop_osm(raw, bounds)
        companion = crop_context_buildings(companion, bounds) if companion else None
    if traffic is not None:
        from .traffic import road_crossings
        crossings = road_crossings(raw, traffic.geometry, traffic.crs)
        if crossings is not None:
            traffic.crossings = sorted(set((traffic.crossings or []) + crossings))
    payload = landscape_payload(raw)
    return apply_context_buildings(payload, companion) if companion else payload


def command_export_viewer(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    forecast = ForecastResult.model_validate(_json(args.forecast)) if args.forecast else None
    model = TrafficModel.load(args.traffic) if args.traffic else None
    landscape = _landscape_view(getattr(args, 'landscape', None), model, getattr(args, 'context_buildings', None))
    traffic = traffic_payload(model) if model is not None else None
    payload = build_viewer_payload(_json(args.bbed), snapshot, forecast, basemap=args.basemap, traffic=traffic, landscape=landscape)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_viewer(payload), encoding="utf-8")
    coverage = payload["coverage"]
    print(f"viewer -> {args.output} ({coverage['drawn']} buildings drawn, "
          f"{coverage['missing_geometry']} without geometry)")


def command_fetch_terrain(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    ground = fetch_ground_model(snapshot, cell_m=args.cell_m)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ground.save(destination)
    rows, cols = ground.ground.shape
    print(f"terrain -> {args.output} ({cols}x{rows} cells of {ground.cell_m:g} m in {ground.crs})")


def command_pack_beirut001(args: argparse.Namespace) -> None:
    from .beirut001_pack import build_pack
    pack = build_pack(args.model, args.bbed, cell_m=args.cell_m)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    pack.save(destination)
    counts = pack.meta["counts"]
    print(f"Beirut 001 pack -> {args.output} ({destination.stat().st_size / 1e6:.1f} MB): "
          f"{counts['buildings']} buildings, {counts['linked']} provisionally linked to BBED, "
          f"{counts['bridges']} bridge/tunnel pieces; alignment inferred "
          f"(median {pack.meta['registration']['bbed_to_model_percentiles_m'][2]:.1f} m)")


def _traffic_view(traffic: TrafficModel, pack_path: str | None) -> dict:
    """The viewer's roads, with bridge decks at their Beirut 001 height where the pack has them."""
    view = traffic_payload(traffic)
    if pack_path and traffic.level is not None:
        from .beirut001_pack import Beirut001Pack, bridge_decks
        decks = bridge_decks(Beirut001Pack.load(pack_path), traffic)
        for road, height in decks.items():
            view["roads"][road]["deck"] = height
        print(f"bridge decks: {len(decks)} of {int((traffic.level > 0).sum())} bridge roads at their "
              "Beirut 001 height; the rest at the assumed 6 m per level")
    return view


def _beirut001_payload(pack_path: str | None, bbed_path: str | None,
                       bounds: tuple[float, float, float, float] | None = None) -> dict | None:
    if not pack_path:
        return None
    from .beirut001_pack import Beirut001Pack, viewer_payload
    payload = viewer_payload(Beirut001Pack.load(pack_path), _json(bbed_path) if bbed_path else None)
    if bounds is not None:
        from .corridor import crop_beirut001
        payload = crop_beirut001(payload, bounds)
    print(f"Beirut 001 buildings loaded from {pack_path} ({len(payload['buildings'])} buildings)")
    return payload


def command_build_traffic(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    model = build_traffic_model(
        snapshot, args.velocities, overpass=_json(args.roads) if args.roads else None,
        margin_m=args.margin_m, velocity_unit=args.velocity_unit,
        bus_overpass=_json(args.bus_routes) if args.bus_routes else None,
    )
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    model.save(destination)
    observed = int((~model.estimated).sum())
    print(f"traffic -> {args.output} ({len(model)} roads, {model.length_m.sum() / 1000:.0f} km; "
          f"{observed} with their own speeds, {len(model) - observed} from their road class; "
          f"{len(model.bus_routes)} bus and van routes)")


def command_build_zones(args: argparse.Namespace) -> None:
    from collections import Counter

    from .traffic import TrafficModel
    from .zones import build_zones

    model = TrafficModel.load(args.traffic)
    levels = tuple(args.admin_level or ("8", "7"))
    zones = build_zones(model, _json(args.boundaries) if args.boundaries else None,
                        levels=levels, grid_m=args.grid_m or None)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    zones.save(destination)
    kinds = ", ".join(f"{n} {source}" for source, n in Counter(z.source for z in zones.zones).items())
    unzoned = int((zones.road_zone < 0).sum())
    print(f"zones -> {args.output} ({len(zones)} zones: {kinds}; {len(zones.adjacency)} touching pairs"
          + (f"; {unzoned} roads outside every zone" if unzoned else "") + ")")


def command_run_plan(args: argparse.Namespace) -> None:
    from .plan_runs import RunSettings, StreetPlan, run_plan
    from .traffic import TrafficModel

    model = TrafficModel.load(args.traffic)
    if args.corridor:
        from .corridor import Corridor
        from .mobility_inputs import pilot_network
        corridor = Corridor.load(args.corridor, model.crs, args.corridor_buffer_m)
        model, _ = pilot_network(model, list(corridor.bounds()), note=f'Whole-road subset around {corridor.name}; '
                                 'cars enter and leave at its edge; not a citywide run.')
    plan = StreetPlan.from_json(_json(args.plan), model)
    settings = RunSettings(hour=args.hour, seeds=tuple(args.seed or (42,)), warmup_s=round(args.warmup_min * 60),
                           measure_s=round(args.measure_min * 60))
    result = run_plan(model, plan, settings)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=1), encoding="utf-8")
    hours = result['summary']['vehicle_hours']['change']['median']
    print(f"plan {plan.plan_id} -> {args.output} ({len(model)} roads, {len(settings.seeds)} seed(s); "
          f"median change {hours:+.1f} vehicle-hours over {args.measure_min:g} min)")
    for warning in result['warnings']:
        print(f"warning: {warning}")


def _business_as_usual(engine: ForecastEngine, snapshot: UrbanStateSnapshot) -> ForecastResult:
    return engine.forecast(ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(scenario_id="business-as-usual", baseline_snapshot_id=snapshot.snapshot_id, horizon_years=6),
        draws=300,
    ))


def command_serve(args: argparse.Namespace) -> None:
    import uvicorn

    if getattr(args, 'prewarm_traffic', False) and not args.traffic:
        raise SystemExit('--prewarm-traffic requires --traffic')
    flood_context = None
    if args.flood_context:
        from .flood_context import load_flood_context
        if args.host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit("The survey package has unverified reuse terms; --flood-context requires a loopback host for local research.")
        flood_context = load_flood_context(args.flood_context)
    flood_jobs = None
    if args.flood_runs:
        from .flood_jobs import FloodJobs
        if args.host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit("--flood-runs requires a loopback host for local execution.")
        flood_jobs = FloodJobs(args.flood_runs)
    engine = ForecastEngine(ModelBundle.load(args.model))
    point_cloud = None
    if args.point_cloud_dir:
        if args.host not in {"127.0.0.1", "::1", "localhost"}:
            raise SystemExit("--point-cloud-dir requires a loopback host for private local viewing")
        from .point_cloud import PointCloudView
        point_cloud = PointCloudView.load(args.point_cloud_dir)
    if args.beirut001 and args.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("--beirut001 requires a loopback host: the model names no licence for web serving")
    viewer_html = snapshot = baseline = corridor = None
    if getattr(args, 'corridor', None) and not (args.snapshot and args.bbed):
        raise SystemExit('--corridor needs --snapshot and --bbed')
    if args.snapshot:
        snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    if getattr(args, 'corridor', None):
        from .corridor import Corridor, crop_snapshot
        corridor = Corridor.load(args.corridor, snapshot.crs, args.corridor_buffer_m)
        snapshot = crop_snapshot(snapshot, corridor)
    focus = corridor.bounds() if corridor is not None else None
    ground = GroundModel.load(args.terrain) if args.terrain else None
    traffic = TrafficModel.load(args.traffic) if args.traffic else None
    if traffic is not None and corridor is not None:
        from .mobility_inputs import pilot_network
        traffic, _ = pilot_network(traffic, list(focus), note=f'Whole-road subset around {corridor.name}; '
                                   'cars enter and leave at its edge; not a citywide run.')
    landscape = _landscape_view(getattr(args, 'landscape', None), traffic, getattr(args, 'context_buildings', None), focus)
    if args.mobility_inputs:
        if traffic is None:
            raise SystemExit('--mobility-inputs requires --traffic')
        from .mobility_inputs import MobilityInputs
        traffic.mobility = MobilityInputs.load(args.mobility_inputs)
    if traffic is not None and not args.snapshot:
        raise SystemExit("--traffic needs --snapshot")
    if snapshot is not None:
        if ground is not None:
            # Ground levels only steer floods; the transition model never sees them.
            snapshot = attach_ground_elevation(snapshot, ground)
            print("viewer terrain loaded from " + args.terrain + "; hydraulic runs require their own documented mesh")
    if traffic is not None and snapshot is not None and corridor is None:
        covered = building_coverage(traffic, snapshot)
        if covered is not None and covered < 0.5:
            print(f"warning: the traffic model covers {covered:.0%} of the snapshot's buildings; "
                  "simulated roads and cars appear only in that area. Use the citywide road model for the whole city.")
    if args.bbed:
        if snapshot is None:
            raise SystemExit("--bbed needs --snapshot so footprints can be joined to states")
        collection = _json(args.bbed)
        if corridor is not None:
            from .corridor import crop_bbed
            collection = crop_bbed(collection, snapshot)
            if args.forecast:
                print("--forecast is citywide; the corridor's baseline is recomputed for its own buildings")
        baseline = (
            ForecastResult.model_validate(_json(args.forecast))
            if args.forecast and corridor is None
            else _business_as_usual(engine, snapshot)
        )
        payload = build_viewer_payload(
            collection, snapshot, baseline, api=True, basemap=args.basemap,
            traffic=_traffic_view(traffic, args.beirut001) if traffic is not None else None,
            landscape=landscape,
        )
        if corridor is not None:
            payload["corridor"] = corridor.payload({
                "buildings": len(snapshot.objects), "roads": len(traffic) if traffic is not None else 0})
            print(f"corridor {corridor.name}: {len(snapshot.objects)} buildings, "
                  f"{len(traffic) if traffic is not None else 0} roads within {corridor.buffer_m:g} m")
        viewer_html = render_viewer(payload)
        print(f"3D viewer at http://{args.host}:{args.port}/")
    provider = None
    if snapshot is not None:
        try:
            provider = resolve_provider(args.provider, args.llm_model)
            print(f"scenario prompts use {provider.name}:{provider.model}")
        except Exception as exc:  # missing SDK or key; the rest of the server still runs
            print(f"scenario prompts disabled until a key is set: {exc}")
    app = create_app(
        engine,
        scenario_provider=provider,
        viewer_html=viewer_html,
        viewer_snapshot=snapshot,
        evidence=_load_bundle(args.evidence),
        viewer_forecast=baseline,
        viewer_ground=ground,
        llm_budget=RequestBudget(
            total=args.llm_request_limit, per_minute=args.llm_requests_per_minute, what="language-model requests"
        ),
        viewer_traffic=traffic,
        flood_pilot=PilotBundle.model_validate(_json(args.flood_pilot)) if args.flood_pilot else None,
        flood_context=flood_context,
        flood_jobs=flood_jobs,
        point_cloud=point_cloud,
        beirut001=_beirut001_payload(args.beirut001, args.bbed, focus),
    )
    print(f"language-model requests capped at {args.llm_request_limit} per run, "
          f"{args.llm_requests_per_minute} a minute")
    if getattr(args, 'prewarm_traffic', False):
        import subprocess
        from .traffic_sumo import prewarm_network
        try:
            retained = prewarm_network(traffic)
        except (RuntimeError, OSError, subprocess.SubprocessError) as exc:
            raise SystemExit('Traffic network prewarm failed: ' + str(exc)) from exc
        print('Traffic network ready in memory' if retained else
              'Traffic network exceeds the in-memory cache; first connection will convert it again')
    uvicorn.run(app, host=args.host, port=args.port)


def command_flood_validate(args: argparse.Namespace) -> None:
    from .flood_validation import load_validation_inputs, evaluate_flood
    cube, observations = load_validation_inputs(args.input)
    _write_json(_private_destination(args.output), evaluate_flood(cube, observations))
    print(f"flood observation report -> {args.output}; no calibration performed")


def command_flood_import(args: argparse.Namespace) -> None:
    from .flood_pilot import from_hydraulic_outputs, validate_rainfall, attach_terrain_evidence
    from .flood_surface import load_hydraulic_output
    manifest_path = Path(args.manifest).resolve()
    manifest = _json(manifest_path)
    outputs = {kind: load_hydraulic_output(manifest_path.parent / path)
               for kind, path in manifest["outputs"].items()}
    bundle = from_hydraulic_outputs(outputs=outputs, rainfall=validate_rainfall(manifest["rainfall"]),
        rainfall_source=manifest["rainfall_source"], label=manifest["label"],
        source_resolution_m=manifest["source_resolution_m"],
        capacity_assumptions=manifest["capacity_assumptions"],
        assets=manifest.get("assets", []))
    if manifest.get("terrain"):
        from .flood_terrain import TerrainGrid
        bundle = attach_terrain_evidence(bundle, TerrainGrid.load(manifest_path.parent / manifest["terrain"]))
    require_documented_pilot(bundle)
    _write_json(_private_destination(args.output), bundle.model_dump(mode="json"))
    print(f"validated flood outputs -> {args.output} ({bundle.evidence})")


def command_flood_drainage_demo(args: argparse.Namespace) -> None:
    raise ValueError("Invented drainage demos are disabled; documented real network inputs are required.")


def command_flood_pilot(args: argparse.Namespace) -> None:
    bundle = require_documented_pilot(PilotBundle.model_validate(_json(args.input)))
    destination = _private_destination(args.output)
    _write_json(destination, bundle.model_dump(mode="json"))
    print(f"flood pilot -> {destination} ({bundle.evidence}; no Beirut calibration)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dynacity")
    subparsers = parser.add_subparsers(dest="command", required=True)

    surface = subparsers.add_parser("flood-run", help="run ANUGA on explicit documented mesh/forcing inputs")
    surface.add_argument("--manifest", required=True)
    surface.add_argument("--output-dir", required=True, help="new private directory outside Git")
    def execute_surface(args):
        from .flood_solver import run_surface
        print(run_surface(args.manifest, args.output_dir))
    surface.set_defaults(func=execute_surface)

    package = subparsers.add_parser("flood-package", help="package existing documented solver inputs for the city UI")
    package.add_argument("--manifest", required=True)
    package.add_argument("--output", required=True, help="new ZIP outside Git")
    def execute_flood_package(args):
        from .flood_jobs import package_inputs
        print(package_inputs(args.manifest, args.output))
    package.set_defaults(func=execute_flood_package)

    flood_validate = subparsers.add_parser("flood-validate")
    flood_validate.add_argument("--input", required=True, help="private event depth/observation JSON")
    flood_validate.add_argument("--output", required=True, help="private report path outside Git")
    flood_validate.set_defaults(func=command_flood_validate)

    flood_import = subparsers.add_parser("flood-import")
    flood_import.add_argument("--manifest", required=True, help="private JSON manifest with three hydraulic NPZ output paths")
    flood_import.add_argument("--output", required=True, help="private pilot bundle JSON outside Git")
    flood_import.set_defaults(func=command_flood_import)

    drainage_demo = subparsers.add_parser("flood-drainage-demo")
    drainage_demo.add_argument("--output-dir", required=True, help="new private directory outside Git")
    drainage_demo.add_argument("--swmm", help="EPA SWMM executable path")
    drainage_demo.set_defaults(func=command_flood_drainage_demo)

    pilot = subparsers.add_parser("flood-pilot")
    pilot.add_argument("--input", required=True, help="documented private hydraulic-output bundle JSON")
    pilot.add_argument("--output", required=True, help="output JSON outside Git")
    pilot.set_defaults(func=command_flood_pilot)

    audit = subparsers.add_parser("audit-coverage")
    audit.add_argument("--bbed", required=True)
    audit.add_argument("--manifest")
    audit.add_argument("--snapshot")
    audit.add_argument("--lidar-features")
    audit.add_argument("--previous")
    audit.add_argument("--panel")
    audit.add_argument("--output", required=True)
    audit.set_defaults(func=command_audit_coverage)

    update = subparsers.add_parser("stage-update")
    update.add_argument("--bbed", required=True)
    update.add_argument("--manifest", required=True)
    update.add_argument("--previous")
    update.add_argument("--lidar-features")
    update.add_argument("--output-dir", required=True)
    update.add_argument("--snapshot-id", required=True)
    update.add_argument("--data-version", required=True)
    update.add_argument("--as-of", required=True)
    update.add_argument("--acknowledge-removals", action="store_true")
    update.set_defaults(func=command_stage_update)

    inspect_las = subparsers.add_parser("inspect-las")
    inspect_las.add_argument("path")
    inspect_las.add_argument("--sample-classes", type=int, default=0)
    inspect_las.set_defaults(func=command_inspect_las)

    surface = subparsers.add_parser("las-surface", help="observed LAS surface for review; not verified flood terrain")
    surface.add_argument("path")
    surface.add_argument("--output", required=True)
    surface.add_argument("--cell-m", type=float, required=True)
    surface.add_argument("--crs", required=True)
    surface.add_argument("--crs-evidence", required=True)
    surface.add_argument("--max-points", type=int, default=10_000_000)
    surface.set_defaults(func=command_las_surface)

    ground = subparsers.add_parser("las-ground-review", help="extract supported ground candidates from explicit review footprints")
    ground.add_argument("path")
    ground.add_argument("--footprints", required=True)
    ground.add_argument("--output", required=True)
    ground.add_argument("--cell-m", type=float, required=True)
    ground.add_argument("--crs", required=True)
    ground.add_argument("--crs-evidence", required=True)
    ground.add_argument("--exclusion-buffer-m", type=float, required=True)
    ground.add_argument("--min-points", type=int, required=True)
    ground.add_argument("--max-spread-m", type=float, required=True)
    ground.add_argument("--max-points", type=int, default=10_000_000)
    def execute_ground_review(args):
        from .las_ground_review import review_ground
        result = review_ground(args.path, args.footprints, args.output, cell_m=args.cell_m,
            horizontal_crs=args.crs, crs_evidence=args.crs_evidence,
            exclusion_buffer_m=args.exclusion_buffer_m, min_points=args.min_points,
            max_spread_m=args.max_spread_m, max_points=args.max_points)
        print(json.dumps(result, indent=2))
    ground.set_defaults(func=execute_ground_review)

    cloud = subparsers.add_parser("export-point-cloud", help="all LAS positions and source RGB for the local map")
    cloud.add_argument("path")
    cloud.add_argument("--output-dir", required=True)
    cloud.add_argument("--crs", required=True)
    cloud.add_argument("--crs-evidence", required=True)
    cloud.add_argument("--max-points", type=int, default=10_000_000)
    cloud.set_defaults(func=command_export_point_cloud)

    fetch = subparsers.add_parser("fetch-bbed")
    fetch.add_argument("--output", required=True)
    fetch.add_argument("--where", default="1=1")
    fetch.add_argument("--bbox", nargs=4, type=float)
    fetch.add_argument("--layer-url", default=BBEDClient.layer_url)
    fetch.set_defaults(func=command_fetch_bbed)

    copc = subparsers.add_parser("build-copc")
    copc.add_argument("source")
    copc.add_argument("output")
    copc.add_argument("--crs", default="EPSG:32636")
    copc.add_argument("--dry-run", action="store_true")
    copc.set_defaults(func=command_build_copc)

    features = subparsers.add_parser("extract-features")
    features.add_argument("--las", required=True)
    features.add_argument("--bbed", required=True)
    features.add_argument("--output", required=True)
    features.add_argument("--id-field", default="BULBuildingID")
    features.add_argument("--max-points", type=int, default=10_000_000)
    features.set_defaults(func=command_extract_features)

    panel = subparsers.add_parser("build-panel")
    panel.add_argument("--bbed", required=True)
    panel.add_argument("--output", required=True)
    panel.add_argument("--lidar-features")
    panel.add_argument("--lidar-year", type=int, default=2020)
    panel.set_defaults(func=command_build_panel)

    summary = subparsers.add_parser("summarize-transitions")
    summary.add_argument("--panel", required=True)
    summary.add_argument("--output", required=True)
    summary.add_argument("--start-year", type=int, default=2018)
    summary.add_argument("--target-year", type=int, default=2024)
    summary.set_defaults(func=command_summarize_transitions)

    snapshot = subparsers.add_parser("build-snapshot")
    snapshot.add_argument("--bbed", required=True)
    snapshot.add_argument("--output", required=True)
    snapshot.add_argument("--snapshot-id", required=True)
    snapshot.add_argument("--as-of", default="2024-04-30")
    snapshot.add_argument("--data-version", required=True)
    snapshot.add_argument("--lidar-features")
    snapshot.set_defaults(func=command_build_snapshot)

    terrain = subparsers.add_parser("fetch-terrain")
    terrain.add_argument("--snapshot", required=True)
    terrain.add_argument("--output", required=True)
    terrain.add_argument("--cell-m", type=float, default=10.0)
    terrain.set_defaults(func=command_fetch_terrain)

    beirut001 = subparsers.add_parser("pack-beirut001")
    beirut001.add_argument("--model", required=True, help="Beirut 001 Rhino .3dm (private; read once)")
    beirut001.add_argument("--bbed", required=True, help="BBED GeoJSON in EPSG:32636, for alignment and links")
    beirut001.add_argument("--output", required=True)
    beirut001.add_argument("--cell-m", type=float, default=10.0, help="ground grid cell size")
    beirut001.set_defaults(func=command_pack_beirut001)

    traffic = subparsers.add_parser("build-traffic")
    traffic.add_argument("--snapshot", required=True, help="snapshot with building centroids; sets the extent")
    traffic.add_argument("--velocities", required=True, help="Tari'ak velocities.csv")
    traffic.add_argument("--roads", help="saved Overpass JSON of the roads; fetched from Overpass when omitted")
    traffic.add_argument("--bus-routes", help="saved Overpass JSON of bus route relations; fetched when --roads is omitted")
    traffic.add_argument("--output", required=True)
    traffic.add_argument("--margin-m", type=float, default=800.0)
    traffic.add_argument("--velocity-unit", choices=["kmh", "ms"], default="kmh")
    traffic.set_defaults(func=command_build_traffic)

    zones = subparsers.add_parser("build-zones", help="decision zones over a traffic model from OSM boundaries")
    zones.add_argument("--traffic", required=True, help="traffic model from build-traffic")
    zones.add_argument("--boundaries", help="saved Overpass JSON of boundary relations (out geom); fetched when omitted")
    zones.add_argument("--admin-level", action="append",
                       help="OSM admin_level to use, finest first; repeat for more (default: 8 then 7)")
    zones.add_argument("--grid-m", type=float, default=500.0, help="grid cell for roads outside every boundary; 0 for none")
    zones.add_argument("--output", required=True)
    zones.set_defaults(func=command_build_zones)

    run = subparsers.add_parser("run-plan", help="a street plan in SUMO against a paired baseline, without the viewer")
    run.add_argument("--traffic", required=True, help="traffic model from build-traffic")
    run.add_argument("--plan", required=True, help="JSON: plan_id, close_roads, close_streets, add_lanes")
    run.add_argument("--corridor", help="GeoJSON line; the roads are cut to its buffer, as `serve --corridor` does")
    run.add_argument("--corridor-buffer-m", type=float, default=250.0)
    run.add_argument("--hour", type=int, default=8)
    run.add_argument("--seed", type=int, action="append", help="repeat for more seeds (default 42)")
    run.add_argument("--warmup-min", type=float, default=10.0)
    run.add_argument("--measure-min", type=float, default=30.0)
    run.add_argument("--output", required=True)
    run.set_defaults(func=command_run_plan)

    train = subparsers.add_parser("train")
    train.add_argument("--panel", required=True)
    train.add_argument("--output", required=True)
    train.add_argument("--data-version", default="BBED-2018-2024")
    train.set_defaults(func=command_train)

    freeze = subparsers.add_parser("freeze-evidence")
    freeze.add_argument("--registry", required=True)
    freeze.add_argument("--as-of", required=True)
    freeze.add_argument("--output", required=True)
    freeze.set_defaults(func=command_freeze_evidence)

    compile_parser = subparsers.add_parser("compile-scenario")
    compile_parser.add_argument("--snapshot", required=True)
    source = compile_parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--text")
    source.add_argument("--text-file")
    compile_parser.add_argument("--output", required=True)
    compile_parser.add_argument("--request-output")
    compile_parser.add_argument("--evidence")
    compile_parser.add_argument("--provider", choices=["anthropic", "gemini"])
    compile_parser.add_argument("--llm-model")
    compile_parser.add_argument("--max-repairs", type=int, default=1)
    compile_parser.set_defaults(func=command_compile_scenario)

    forecast = subparsers.add_parser("forecast")
    forecast.add_argument("--model", required=True)
    forecast.add_argument("--request", required=True)
    forecast.add_argument("--output", required=True)
    forecast.add_argument("--comparison-output", help="matching business-as-usual forecast and median changes")
    forecast.set_defaults(func=command_forecast)

    plan_parser = subparsers.add_parser("plan")
    plan_parser.add_argument("--model", required=True)
    plan_parser.add_argument("--request", required=True)
    plan_parser.add_argument("--output", required=True)
    plan_parser.set_defaults(func=command_plan)

    viewer = subparsers.add_parser("export-viewer")
    viewer.add_argument("--bbed", required=True)
    viewer.add_argument("--snapshot", required=True)
    viewer.add_argument("--forecast")
    viewer.add_argument("--output", required=True)
    viewer.add_argument("--basemap", choices=["carto", "none"], default="carto")
    viewer.add_argument("--traffic", help="traffic model from build-traffic; draws roads and moving cars")
    viewer.add_argument("--landscape", help="private OSM context JSON: surfaces, trees, paths, places and structures")
    viewer.add_argument("--context-buildings", help="Overture companion from engine_context_buildings --web-output: estimated heights and extra footprints")
    viewer.set_defaults(func=command_export_viewer)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--model", required=True)
    serve.add_argument("--snapshot")
    serve.add_argument("--bbed")
    serve.add_argument("--forecast")
    serve.add_argument("--evidence")
    serve.add_argument("--terrain", help="ground model from fetch-terrain; floods then follow it")
    serve.add_argument("--beirut001", help="private pack from pack-beirut001; the map then draws its buildings")
    serve.add_argument("--point-cloud-dir", help="private export-point-cloud directory for local RGB point viewing")
    serve.add_argument("--flood-pilot", help="private neighborhood model-output bundle JSON; separate from illustrative hazards")
    serve.add_argument("--flood-context", help="verified private real-input-handoff.json for local survey mapping")
    serve.add_argument("--flood-runs", help="private directory outside Git for local ANUGA input packages and runs")
    serve.add_argument("--traffic", help="traffic model from build-traffic; disasters then close roads")
    serve.add_argument("--prewarm-traffic", action="store_true",
                       help="build the default SUMO network before serving to reduce first-connect work")
    serve.add_argument("--mobility-inputs", help="private mobility evidence JSON for the traffic pilot")
    serve.add_argument("--landscape", help="private OSM context JSON: surfaces, trees, paths, places and structures")
    serve.add_argument("--context-buildings", help="Overture companion from engine_context_buildings --web-output: estimated heights and extra footprints")
    serve.add_argument("--corridor", help="GeoJSON line to focus on (e.g. Al Masar al Akhdar); every input is cut to its buffer")
    serve.add_argument("--corridor-buffer-m", type=float, default=250.0, help="buffer around --corridor, metres")
    serve.add_argument("--basemap", choices=["carto", "none"], default="carto")
    serve.add_argument("--provider", choices=["anthropic", "gemini"])
    serve.add_argument("--llm-model")
    # Caps on paid calls, so an exposed server has a known worst-case bill.
    serve.add_argument("--llm-request-limit", type=int, default=300)
    serve.add_argument("--llm-requests-per-minute", type=int, default=12)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=command_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

