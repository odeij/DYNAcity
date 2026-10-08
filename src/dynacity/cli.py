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
    build-traffic   → OSM road graph + Tari'ak hourly speeds (.json) for traffic and road closures
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
from .google_tiles import KEY_VARIABLE as MAPS_KEY_VARIABLE, GoogleTilesProxy
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
from .snapshot import snapshot_from_bbed
from .terrain import GroundModel, attach_ground_elevation, fetch_ground_model
from .traffic import TrafficModel, build_traffic_model, traffic_payload
from .viewer import build_viewer_payload, render_viewer
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


def command_export_viewer(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    forecast = ForecastResult.model_validate(_json(args.forecast)) if args.forecast else None
    traffic = traffic_payload(TrafficModel.load(args.traffic)) if args.traffic else None
    payload = build_viewer_payload(_json(args.bbed), snapshot, forecast, basemap=args.basemap, traffic=traffic)
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


def _business_as_usual(engine: ForecastEngine, snapshot: UrbanStateSnapshot) -> ForecastResult:
    return engine.forecast(ForecastRequest(
        snapshot=snapshot,
        scenario=ScenarioSpec(scenario_id="business-as-usual", baseline_snapshot_id=snapshot.snapshot_id, horizon_years=6),
        draws=300,
    ))


def command_serve(args: argparse.Namespace) -> None:
    import uvicorn

    engine = ForecastEngine(ModelBundle.load(args.model))
    viewer_html = snapshot = baseline = tiles = None
    if args.basemap == "google":
        tiles = GoogleTilesProxy(sessions=RequestBudget(
            total=args.tiles_session_limit, per_minute=10, what="photorealistic map sessions"
        ))
        if not tiles.api_key:
            raise SystemExit(f"--basemap google needs {MAPS_KEY_VARIABLE} (a Google Maps key with the Map Tiles API)")
    ground = GroundModel.load(args.terrain) if args.terrain else None
    traffic = TrafficModel.load(args.traffic) if args.traffic else None
    if traffic is not None and not args.snapshot:
        raise SystemExit("--traffic needs --snapshot")
    if args.snapshot:
        snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
        if ground is not None:
            # Ground levels only steer floods; the transition model never sees them.
            snapshot = attach_ground_elevation(snapshot, ground)
            print("floods follow terrain from " + args.terrain)
    if args.bbed:
        if snapshot is None:
            raise SystemExit("--bbed needs --snapshot so footprints can be joined to states")
        baseline = (
            ForecastResult.model_validate(_json(args.forecast))
            if args.forecast
            else _business_as_usual(engine, snapshot)
        )
        viewer_html = render_viewer(
            build_viewer_payload(
                _json(args.bbed), snapshot, baseline, api=True, basemap=args.basemap,
                traffic=traffic_payload(traffic) if traffic is not None else None,
            )
        )
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
        tiles_proxy=tiles,
        viewer_traffic=traffic,
    )
    print(f"language-model requests capped at {args.llm_request_limit} per run, "
          f"{args.llm_requests_per_minute} a minute")
    uvicorn.run(app, host=args.host, port=args.port)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="dynacity")
    subparsers = parser.add_subparsers(dest="command", required=True)

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

    traffic = subparsers.add_parser("build-traffic")
    traffic.add_argument("--snapshot", required=True, help="snapshot with building centroids; sets the extent")
    traffic.add_argument("--velocities", required=True, help="Tari'ak velocities.csv")
    traffic.add_argument("--roads", help="saved Overpass JSON of the roads; fetched from Overpass when omitted")
    traffic.add_argument("--bus-routes", help="saved Overpass JSON of bus route relations; fetched when --roads is omitted")
    traffic.add_argument("--output", required=True)
    traffic.add_argument("--margin-m", type=float, default=800.0)
    traffic.add_argument("--velocity-unit", choices=["kmh", "ms"], default="kmh")
    traffic.set_defaults(func=command_build_traffic)

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
    viewer.set_defaults(func=command_export_viewer)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--model", required=True)
    serve.add_argument("--snapshot")
    serve.add_argument("--bbed")
    serve.add_argument("--forecast")
    serve.add_argument("--evidence")
    serve.add_argument("--terrain", help="ground model from fetch-terrain; floods then follow it")
    serve.add_argument("--traffic", help="traffic model from build-traffic; disasters then close roads")
    serve.add_argument("--basemap", choices=["carto", "google", "none"], default="carto")
    serve.add_argument("--provider", choices=["anthropic", "gemini"])
    serve.add_argument("--llm-model")
    # Caps on paid calls, so an exposed server has a known worst-case bill.
    serve.add_argument("--llm-request-limit", type=int, default=300)
    serve.add_argument("--llm-requests-per-minute", type=int, default=12)
    serve.add_argument("--tiles-session-limit", type=int, default=100)
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=command_serve)
    return parser


def main(argv: list[str] | None = None) -> None:
    args = build_parser().parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()

