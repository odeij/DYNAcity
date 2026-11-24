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
    fetch-terrain   → approximate ground-elevation grid (.npz) over the snapshot extent
    pack-beirut001  → Beirut 001 Rhino model → small private pack (.npz) of outlines, heights, ground
    refine-beirut001-alignment → measure a pack's placement against OSM footprints, write a refined pack + report
    build-traffic   → OSM road graph + Tari'ak hourly speeds (.json) for traffic and road closures
    build-zones     → decision zones (.json) over the roads from OSM boundaries, for adaptation policies
    build-facilities → key buildings (.json): the public services people go to, from OSM and the MEHE school list
    build-access    → walking access to the 15-minute services on a corridor (.npz + report), and what candidate sites would add
    score-access    → one site plan (site → new use) scored on a build-access model (.json)
    bench-access-gpu→ the batch plan scorer checked against AccessModel and timed on CPU/GPU (.json)
    search-access   → exact optimum (integer program) and random/greedy/NSGA-II plans, with gaps (.json, .md)
    propose-links   → candidate crossings and the rail bed as a walkway (.geojson), for a person to review
    build-links     → exact walking-distance blocks for reviewed links on an access model (.npz + report)
    search-joint    → exact optimum and greedy/NSGA-II/Bayesian optimisation for land uses plus links (.json, .md)
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
import math
import shutil
import tempfile
from datetime import date
from pathlib import Path

import numpy as np
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
                    bounds: tuple[float, float, float, float] | None = None, overlays: list[str] | None = None) -> dict | None:
    if not path and not overlays:
        if context_buildings:
            raise SystemExit('--context-buildings requires --landscape')
        return None
    raw = _json(path) if path else {'elements': []}
    # Keep stable OSM identities while adding the newer inventories used by Unreal.
    # Later layers win for duplicate identities; retain each element's provenance.
    elements = {}
    for layer in [raw, *[_json(p) for p in overlays or []]]:
        for element in layer.get('elements', []):
            element = dict(element)
            element.setdefault('source_as_of', layer.get('osm3s', {}).get('timestamp_osm_base'))
            element.setdefault('retrieved_on', layer.get('retrieved_on'))
            identity = (element['type'], element['id'])
            prior = elements.get(identity, {})
            elements[identity] = {**prior, **element,
                                  'tags': {**prior.get('tags', {}), **element.get('tags', {})}}
    raw = {**raw, 'elements': list(elements.values())}
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


def _attach_granite_overlay(payload: dict, args: argparse.Namespace) -> None:
    manifest = getattr(args, 'granite_overlay', None)
    parent = getattr(args, 'granite_parent', None)
    if bool(manifest) != bool(parent):
        raise SystemExit('--granite-overlay and --granite-parent must be supplied together')
    if manifest:
        from .granite_overlay import load_granite_overlay
        payload['granite_overlay'] = load_granite_overlay(manifest, parent)


def _attach_canopy_review(payload: dict, args: argparse.Namespace) -> None:
    """An explicitly loaded evidence overlay; existing mapped trees stay untouched."""
    candidates = getattr(args, 'canopy_review', None)
    screening = getattr(args, 'canopy_screening', None)
    if bool(candidates) != bool(screening):
        raise SystemExit('--canopy-review and --canopy-screening must be supplied together')
    if candidates:
        from .canopy_review import load_canopy_review
        payload['canopy_review'] = load_canopy_review(candidates, screening)


def command_export_viewer(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    forecast = ForecastResult.model_validate(_json(args.forecast)) if args.forecast else None
    model = TrafficModel.load(args.traffic) if args.traffic else None
    landscape = _landscape_view(getattr(args, 'landscape', None), model, getattr(args, 'context_buildings', None), overlays=getattr(args, 'context_overlay', None))
    traffic = traffic_payload(model) if model is not None else None
    payload = build_viewer_payload(_json(args.bbed), snapshot, forecast, basemap=args.basemap, traffic=traffic, landscape=landscape,
                                   facilities=_facilities_view(getattr(args, 'facilities', None)))
    _attach_canopy_review(payload, args)
    _attach_granite_overlay(payload, args)
    if getattr(args, "pedestrians", None):
        from .viewer_pedestrians import pedestrian_payload
        payload["pedestrians"] = pedestrian_payload(_json(args.pedestrians), payload.get("traffic"))
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(render_viewer(payload), encoding="utf-8")
    coverage = payload["coverage"]
    print(f"viewer -> {args.output} ({coverage['drawn']} buildings drawn, "
          f"{coverage['missing_geometry']} without geometry)")


def command_fetch_terrain(args: argparse.Namespace) -> None:
    snapshot = UrbanStateSnapshot.model_validate(_json(args.snapshot))
    # Roads reach past the buildings; cover them too so none run off the grid's edge.
    extra = [xy for road in _json(args.traffic)["roads"] for xy in road["xy"]] if args.traffic else []
    ground = fetch_ground_model(snapshot, cell_m=args.cell_m, margin_m=args.margin_m, extra_points=extra)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ground.save(destination)
    rows, cols = ground.ground.shape
    print(f"terrain -> {args.output} ({cols}x{rows} cells of {ground.cell_m:g} m in {ground.crs})")


def command_fetch_relief(args: argparse.Namespace) -> None:
    from .terrain import fetch_relief
    relief = fetch_relief(args.bbox, step=args.step)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    relief.save(destination)
    rows, cols = relief.dm.shape
    print(f"relief -> {args.output} ({cols}x{rows} cells of {relief.cell_deg * 3600:g} arc-seconds, "
          f"{destination.stat().st_size / 1e6:.1f} MB, highest {relief.dm.max() / 10:.0f} m)")


def command_fetch_fabdem(args: argparse.Namespace) -> None:
    from .fabdem import ATTRIBUTION, FABDEM_LICENCE, FABDEM_PAGE, fabdem_ground_model, fetch_tile, lattice_tiles
    from .open_sources import record_source
    like = GroundModel.load(args.like)
    tiles = lattice_tiles(like)
    if len(tiles) != 1:
        raise SystemExit(f"the extent spans {len(tiles)} FABDEM tiles; only a single 1 degree tile is supported")
    root = Path(args.sources_root)
    entry = fetch_tile(*tiles[0], root / "raw" / "fabdem")
    ground = fabdem_ground_model(like, entry["path"])
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ground.save(destination)
    record_source(root, "fabdem-v1-2", files=[entry], licence=FABDEM_LICENCE, attribution=ATTRIBUTION,
                  publisher=f"University of Bristol, {FABDEM_PAGE}")
    rows, cols = ground.ground.shape
    print(f"fabdem terrain -> {args.output} ({cols}x{rows} cells of {ground.cell_m:g} m in {ground.crs}; "
          f"{entry['bytes']} byte tile, sha256 {entry['sha256'][:12]})")


def command_fetch_canopy(args: argparse.Namespace) -> None:
    from .canopy_heights import ATTRIBUTION, LICENCE, METADATA_URL, TILE_URL, tiles_for_bounds
    from .corridor import Corridor
    from .open_sources import download_ranges, record_source
    root = Path(args.sources_root)
    corridor = Corridor.load(args.corridor, args.crs, args.corridor_buffer_m)
    # Roads of the corridor subset reach past the buffer: widen the box by `--margin-deg`.
    west, south, east, north = corridor.bounds()
    tiles = tiles_for_bounds(west - args.margin_deg, south - args.margin_deg, east + args.margin_deg, north + args.margin_deg)
    entries = []
    for tile in tiles:
        raw = root / "raw" / "meta-wri-chm"
        for url, name in [(TILE_URL.format(tile=tile), f"{tile}.tif"), (METADATA_URL.format(tile=tile), f"{tile}.geojson")]:
            entries.append(download_ranges(url, raw / name, max_bytes=args.max_bytes, workers=args.workers))
            print(f"{name}: {entries[-1]['bytes']} bytes sha256 {entries[-1]['sha256'][:12]}")
    record_source(root, "meta-wri-chm-v1", files=entries, licence=LICENCE,
                  attribution=ATTRIBUTION.format(accessed=date.today().isoformat()),
                  publisher="Meta and World Resources Institute (AWS Open Data: dataforgood-fb-data)",
                  notes="Per-footprint acquisition dates are in the metadata geojson; the height raster has no per-pixel date.")
    print("tiles:", ", ".join(tiles))


def command_build_canopy(args: argparse.Namespace) -> None:
    from .canopy_heights import build_canopy_dataset
    raw = Path(args.sources_root) / "raw" / "meta-wri-chm"
    metadata = raw / f"{args.tile}.geojson"
    summary = build_canopy_dataset(
        chm_path=raw / f"{args.tile}.tif", corridor_path=args.corridor, traffic_path=args.traffic, osm_path=args.osm,
        metadata_path=metadata if metadata.exists() else None, output_dir=args.output, tile_id=args.tile,
        crs=args.crs, buffer_m=args.corridor_buffer_m, min_height_m=args.min_height_m, footprints_path=args.footprints,
        min_spacing_m=args.min_spacing_m, road_buffer_m=args.road_buffer_m, unmapped_distance_m=args.unmapped_distance_m)
    print(f"canopy -> {args.output}: {summary['candidates']['count']} candidates, "
          f"{summary['roads']['count']} roads, {summary['osm_trees']['nodes_in_buffer']} OSM trees")


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


def command_refine_beirut001_alignment(args: argparse.Namespace) -> None:
    from .beirut001_alignment import refine_beirut001
    report = refine_beirut001(
        args.pack, args.reference, args.output, args.report, bbed_path=args.bbed, terrain_path=args.terrain,
        structures_path=args.structures_pack, structures_output=args.structures_output,
        display_path=args.display_json, display_output=args.display_output)
    cv, links, chosen = report["cross_validation"], report["bbed_links"], report["selected_model"]
    print(f"Beirut 001 alignment: {chosen} correction; held-out median error vs OSM footprints "
          f"{cv['current']['median_m']} -> {cv[chosen]['median_m']} m (90th percentile "
          f"{cv['current']['p90_m']} -> {cv[chosen]['p90_m']} m); BBED links {links['before']} -> {links['after']}")
    print(f"refined pack -> {args.output}; report -> {args.report}")


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


def _viewer_terrain(ground: GroundModel, pack_path: str | None, relief_path: str | None = None):
    """The page's ground: the Beirut 001 ground where it covers, GLO-30 elsewhere, on one lon/lat grid;
    with `relief_path` the relief tiles carry on over the whole country beyond it."""
    from .terrain import merge_ground
    from .terrain_viewer import ViewerTerrain
    if pack_path:
        from .beirut001_pack import Beirut001Pack
        pack_ground = Beirut001Pack.load(pack_path).ground
        if pack_ground is not None:
            ground = merge_ground(pack_ground, ground, extrapolate_m=400.0)
            print("viewer terrain: Beirut 001 ground where it covers, GLO-30 elsewhere")
    terrain = ViewerTerrain.from_ground(ground)
    if relief_path:
        from dataclasses import replace

        from .terrain_viewer import ReliefGrid
        relief = ReliefGrid.load(relief_path)
        terrain = replace(terrain, backdrop=relief, attribution=terrain.attribution + " Beyond the city: " + relief.source)
        print(f"relief beyond the city: {relief.dm.shape[1]}x{relief.dm.shape[0]} cells over "
              + ", ".join(f"{v:.2f}" for v in relief.bounds))
    print(f"viewer terrain grid {terrain.cols}x{terrain.rows} ({len(terrain.to_bytes()) / 1e6:.2f} MB gzipped)")
    return terrain


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


def command_build_facilities(args: argparse.Namespace) -> None:
    from .facilities import build_facilities, fetch_facilities, read_official_schools
    from .traffic import snapshot_extent

    if args.bbox:
        bbox = tuple(args.bbox)
    elif args.snapshot:
        bbox = snapshot_extent(UrbanStateSnapshot.model_validate(_json(args.snapshot)), args.margin_m)
    else:
        raise SystemExit("build-facilities needs --bbox or --snapshot")
    osm = _json(args.osm) if args.osm else fetch_facilities(bbox)
    official = read_official_schools(args.official_schools) if args.official_schools else None
    collection = build_facilities(osm, official, bbox=bbox)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(collection, ensure_ascii=False), encoding="utf-8")
    counts = ", ".join(f"{n} {kind}" for kind, n in sorted(collection["counts"].items()))
    merged = collection["official_schools"]
    print(f"facilities -> {args.output} ({counts}"
          + (f"; MEHE list: {merged['confirmed']} schools matched in OSM, {merged['added']} added" if merged else "") + ")")


def _size_rules(values: list[str] | None) -> dict[str, float]:
    rules = {}
    for value in values or []:
        use, _, metres = value.partition("=")
        try:
            rules[use.strip()] = float(metres)
        except ValueError:
            raise SystemExit(f"--min-site-m2 takes USE=M2, got {value!r}") from None
    return rules


def _access_inputs(args: argparse.Namespace) -> dict:
    """The walking network, homes, places and candidate sites, as build-access assembles them."""

    from collections import Counter

    from .access import (FIFTEEN_MIN_M, SERVICE_KEYS, destinations, homes_from_pack, limit_by_size,
                         local_size_rules, sites_from_bbed, sites_from_geojson, sites_from_line,
                         sites_from_osm_parking, walk_graph)
    from .beirut001_pack import Beirut001Pack
    from .corridor import Corridor

    crs = "EPSG:32636"
    corridor = Corridor.load(args.corridor, crs, args.corridor_buffer_m)
    reach_m = args.reach_m if args.reach_m is not None else FIFTEEN_MIN_M
    plan_area, reach = corridor.area, corridor.area.buffer(reach_m)
    graph = walk_graph(_json(args.network), reach.buffer(300), crs)
    facilities = _json(args.facilities) if args.facilities else None
    if facilities is not None and facilities.get("schema") != "dynacity-facilities/1":
        raise SystemExit(f"{args.facilities} is not a build-facilities output")
    extracts = [_json(path) for path in args.places or []]
    places = destinations(facilities, extracts, reach, crs)
    homes = homes_from_pack(Beirut001Pack.load(args.beirut001), plan_area)
    sites = sites_from_bbed(_json(args.bbed), plan_area, crs) if args.bbed else []
    if args.add_parking:
        sites += sites_from_osm_parking(extracts, plan_area, crs)
    if args.add_railbed:
        sites += sites_from_line(corridor.lines, crs, args.railbed_piece_m)
    if args.sites:
        sites += sites_from_geojson(_json(args.sites), crs)
    ids = Counter(s["id"] for s in sites)
    if any(n > 1 for n in ids.values()):
        raise SystemExit(f"duplicate site ids: {sorted(k for k, n in ids.items() if n > 1)[:5]}")
    size_rules = local_size_rules(extracts, crs) if args.size_rules == "local" else {}
    given = _size_rules(args.min_site_m2)
    unknown = set(given) - set(SERVICE_KEYS)
    if unknown:
        raise SystemExit(f"--min-site-m2: unknown uses {sorted(unknown)}; use {list(SERVICE_KEYS)}")
    size_rules.update({use: {"m2": m2, "source": "--min-site-m2"} for use, m2 in given.items()})
    rules = {use: rule["m2"] for use, rule in size_rules.items()}
    removed = limit_by_size(sites, rules) if rules else 0
    return {"crs": crs, "corridor": corridor, "reach_m": reach_m, "reach": reach, "graph": graph,
            "facilities": facilities, "extracts": extracts, "places": places, "homes": homes, "sites": sites,
            "size_rules": size_rules, "rules": rules, "removed": removed}


def command_build_access(args: argparse.Namespace) -> None:
    from collections import Counter

    from .access import SCHEMA, SERVICE_KEYS, build_access
    from .viewer import utm_to_lonlat

    inputs = _access_inputs(args)
    corridor, reach_m, reach = inputs["corridor"], inputs["reach_m"], inputs["reach"]
    plan_area = corridor.area
    graph, facilities, places = inputs["graph"], inputs["facilities"], inputs["places"]
    homes, sites, size_rules, rules, removed = (inputs["homes"], inputs["sites"], inputs["size_rules"],
                                                inputs["rules"], inputs["removed"])
    model = build_access(graph, homes, places, sites, score_services=tuple(args.score_service) if args.score_service else None,
                         meta={"corridor": corridor.name, "buffer_m": args.corridor_buffer_m, "reach_m": reach_m})
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    model.save(destination)

    warnings = []
    if facilities is not None and facilities.get("features"):
        lon = [f["geometry"]["coordinates"][0] for f in facilities["features"]]
        lat = [f["geometry"]["coordinates"][1] for f in facilities["features"]]
        x0, y0, x1, y1 = reach.bounds
        (w, s), (e, n) = utm_to_lonlat(x0, y0, 36), utm_to_lonlat(x1, y1, 36)
        beyond = [side for side, out in (("west", w < min(lon) - 0.002), ("south", s < min(lat) - 0.002),
                                         ("east", e > max(lon) + 0.002), ("north", n > max(lat) + 0.002)) if out]
        if beyond:
            warnings.append(f"The reach area extends past the key-buildings layer to the {', '.join(beyond)}; "
                            "schools and clinics there are missing, not absent.")
    scored = model.meta["score_services"]
    if "work" not in scored:
        warnings.append(f"Work is reported but not scored: {model.destination_counts.get('work', 0)} mapped offices "
                        "and government offices in reach, and OSM maps few offices here.")
    if model.meta["home_snap_over_100m"]:
        warnings.append(f"{model.meta['home_snap_over_100m']} homes are over 100 m from the nearest walkable way.")
    if model.meta["homes_off_main_network"]:
        warnings.append(f"{model.meta['homes_off_main_network']} homes sit on a piece of the walking network that "
                        "is not joined to the rest.")
    small = sum(1 for s in sites if s.get("area_m2") is not None and s["area_m2"] < 200)
    if sites and not rules:
        warnings.append(f"No minimum site size applied: {small} of {len(sites)} sites are under 200 m2. "
                        "Pass --min-site-m2 USE=M2 once sizes are known.")
    elif rules:
        unruled = sorted(set(SERVICE_KEYS) - set(rules))
        warnings.append(f"Minimum site sizes are read from Beirut's mapped places, not a Lebanese standard (none was "
                        f"found): {', '.join(f'{u} {m:,.0f} m2' for u, m in rules.items())}. No minimum for "
                        f"{', '.join(unruled)}.")
    if not any(s["allowed"] for s in sites if s.get("area_m2") is not None) and sites:
        warnings.append("No sized site can take any use under the size rules.")
    if any(s["allowed_source"].startswith("not yet known") for s in sites):
        warnings.append("Allowed uses are not known for most sites: every use is allowed until the government "
                        "information gives ownership and rules.")
    report = {
        "schema": SCHEMA + "-report",
        "model": str(destination),
        "corridor": {"name": corridor.name, "buffer_m": args.corridor_buffer_m, "reach_m": reach_m,
                     "planning_area_km2": round(plan_area.area / 1e6, 2), "reach_area_km2": round(reach.area / 1e6, 2)},
        "network": model.meta["network"],
        "homes": {"buildings": int(len(homes.ids)), "residents_est": round(float(homes.residents.sum())),
                  "source": homes.source},
        "places": {k: {"count": model.destination_counts.get(k, 0),
                       "sources": dict(Counter(p.source for p in places if p.service == k))} for k in model.services},
        "sites": {"count": len(sites), "by_state": dict(Counter(s.get("state") for s in sites)),
                  "by_source": dict(Counter(s.get("source") for s in sites)), "size_rules": size_rules,
                  "uses_removed_by_size": removed, "without_any_use": sum(1 for s in sites if not s["allowed"])},
        "baseline": model.score(alpha=args.alpha),
        "greedy": model.greedy(args.steps, alpha=args.alpha),
        "site_gains": model.gains(alpha=args.alpha),
        "warnings": warnings,
        "assumptions": "Walking distances on OSM ways; homes are model buildings with residents from floor area; "
                       "destinations are mapped places with unknown operation and capacity; a site's new use is "
                       "assumed to serve like any mapped place of its kind. Plans are compared with each other, "
                       "not measured outcomes.",
    }
    report_path = Path(args.report) if args.report else destination.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding="utf-8")
    base = report["baseline"]
    print(f"access -> {args.output} ({len(homes.ids)} homes, {len(sites)} sites, {len(graph)} network nodes); "
          f"service score {base['service_score']}; report -> {report_path}")
    for warning in warnings:
        print(f"warning: {warning}")


def command_score_access(args: argparse.Namespace) -> None:
    from .access import AccessModel

    model = AccessModel.load(args.model)
    plan = _json(args.plan)
    unknown = set(plan) - {"plan_id", "uses"}
    if unknown:
        raise SystemExit(f"unknown plan fields: {sorted(unknown)}")
    try:
        scored = model.score(plan.get("uses", {}), alpha=args.alpha)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    baseline = model.score(alpha=args.alpha)
    change = {"objective": round(scored["objective"] - baseline["objective"], 4),
              "service_score": {k: round(v - baseline["service_score"][k], 4) for k, v in scored["service_score"].items()},
              "residents_within": {s: {k: round(v - baseline["services"][s]["residents_within"][k], 4)
                                       for k, v in row["residents_within"].items()}
                                   for s, row in scored["services"].items()}}
    result = {"plan": {"plan_id": plan.get("plan_id", "plan"), "uses": plan.get("uses", {})},
              "scenario": scored, "baseline": baseline, "change": change}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"scored -> {args.output} (objective {baseline['objective']} -> {scored['objective']})")


def command_bench_access_gpu(args: argparse.Namespace) -> None:
    import time

    from .access import AccessModel
    from .access_gpu import bench, check, run_record

    try:
        import torch
    except ImportError:
        raise SystemExit("bench-access-gpu needs torch (the rl extra; see docs/rl-planning-gpu-plan.md)") from None
    started = time.perf_counter()
    model = AccessModel.load(args.model)
    devices = args.devices or (["cpu", "cuda"] if torch.cuda.is_available() else ["cpu"])
    result = {"check": [], "bench": []}
    for device in devices:
        if args.check:
            result["check"].append(check(model, device, n=args.check, alpha=args.alpha, seed=args.seed))
        result["bench"].extend(bench(model, device, batches=args.batches, budget=args.budget, alpha=args.alpha))
    result["record"] = run_record(args.model, time.perf_counter() - started, seeds=[args.seed])
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(result, indent=1), encoding="utf-8")
    for row in result["check"]:
        print(f"check {row['device']}: max |diff| {row['max_abs_diff']:.2e} over {row['plans']} plans "
              f"({'pass' if row['passed'] else 'FAIL'})")
    for row in result["bench"]:
        print(f"bench {row['device']} B={row['batch']}: {row['plans_per_second']:,} plans/s")
    print(f"bench -> {args.output}")


def command_search_access(args: argparse.Namespace) -> None:
    import time

    from .access import AccessModel
    from .access_gpu import run_record
    from .access_search import add_gaps, run_search, summary_table

    try:
        import torch
    except ImportError:
        raise SystemExit("search-access needs torch (the rl extra; see docs/rl-planning-gpu-plan.md)") from None
    started = time.perf_counter()
    model = AccessModel.load(args.model)
    low, high = args.budget
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    out = run_search(model, args.method, list(range(low, high + 1)), args.alpha, device=device,
                     samples=args.samples, population=args.population, generations=args.generations,
                     pareto_points=args.pareto_points, seed=args.seed)
    if args.optimum:
        add_gaps(out, json.loads(Path(args.optimum).read_text(encoding="utf-8")))
    out["record"] = run_record(args.model, time.perf_counter() - started, seeds=[args.seed], methods=args.method,
                               device=device)
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.summary:
        Path(args.summary).write_text(summary_table(out, args.label or Path(args.model).stem), encoding="utf-8")
    print(f"search -> {args.output} ({len(out['results'])} results, {out['record']['wall_seconds']} s)")


def command_search_joint(args: argparse.Namespace) -> None:
    import time

    from .access import AccessModel
    from .access_gpu import run_record
    from .access_joint import joint_table, run_joint
    from .walk_links import LinkModel

    try:
        import torch
    except ImportError:
        raise SystemExit("search-joint needs torch (the rl extra; see docs/rl-planning-gpu-plan.md)") from None
    started = time.perf_counter()
    model = AccessModel.load(args.model)
    links = LinkModel.load(args.links)
    if links.targets != list(model.services) + list(model.site_ids):
        raise SystemExit(f"{args.links} was not built on {args.model} (targets differ)")
    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    out = run_joint(model, links, args.method, args.budgets, args.link_budgets, args.alpha, bo_budgets=args.bo_budgets,
                    bo_link_budgets=args.bo_link_budgets, bo_reduced=args.bo_reduced,
                    bo_max_iterations=args.bo_max_iterations, population=args.population,
                    generations=args.generations, device=device, seed=args.seed)
    out["record"] = run_record(args.model, time.perf_counter() - started, seeds=[args.seed], methods=args.method,
                               device=device, links=str(args.links),
                               links_sha256=hashlib.sha256(Path(args.links).read_bytes()).hexdigest())
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(out, indent=1, ensure_ascii=False), encoding="utf-8")
    if args.summary:
        Path(args.summary).write_text(joint_table(out, args.label or Path(args.model).stem), encoding="utf-8")
    print(f"joint -> {args.output} ({len(out['results'])} results, {out['record']['wall_seconds']} s)")


def command_propose_links(args: argparse.Namespace) -> None:
    from .access import FIFTEEN_MIN_M, walk_graph
    from .corridor import Corridor
    from .walk_links import LINKS_SCHEMA, barriers, link_feature, propose_crossings, railbed_links
    from .viewer import lonlat_to_utm, _utm_zone

    crs = "EPSG:32636"
    corridor = Corridor.load(args.corridor, crs, args.corridor_buffer_m)
    reach_m = args.reach_m if args.reach_m is not None else FIFTEEN_MIN_M
    reach = corridor.area.buffer(reach_m)
    osm = _json(args.network)
    graph = walk_graph(osm, reach.buffer(300), crs)
    today = date.today().isoformat()
    features = []
    crossings = propose_crossings(graph, barriers(osm, reach, crs), reach, max_gap_m=args.max_gap_m,
                                  ratio=args.ratio, spacing_m=args.spacing_m)
    for i, c in enumerate(crossings):
        features.append(link_feature(
            f"crossing/{i}", "crossing", [tuple(graph.xy[c["a"]]), tuple(graph.xy[c["b"]])], crs,
            source=f"proposed by dynacity propose-links on {today} from OSM walkable ways ({Path(args.network).name})",
            barrier=c["barrier"], barrier_name=c["barrier_name"], straight_m=round(c["gap_m"], 1),
            network_m=None if c["network_m"] is None else round(c["network_m"], 1),
            saved_at_least_m=round(c["saved_at_least_m"], 1), review_note=""))
    zone, north = _utm_zone(crs)
    lines = [np.array([lonlat_to_utm(lon, lat, zone, north) for lon, lat in line]) for line in corridor.lines]
    pieces, free = railbed_links(lines, graph, args.railbed_piece_m)
    for i, piece in enumerate(pieces):
        joins = ["free" if any(np.hypot(*(np.asarray(end) - np.asarray(f))) <= 0.5 for f in free) else "network"
                 for end in (piece["xy"][0], piece["xy"][-1])]
        features.append(link_feature(
            f"railbed-walk/{i}", "railbed_walkway", [tuple(p) for p in piece["xy"]], crs,
            source=f"corridor line ({Path(args.corridor).name}), cut where it crosses walkable ways; {today}",
            site=piece["site"], ends_join=joins, review_note="width not mapped; walkable only if the bed is opened"))
    collection = {"type": "FeatureCollection", "schema": LINKS_SCHEMA, "status": "proposals: not reviewed",
                  "rules": {"crossings": f"walkable nodes {8}-{args.max_gap_m:g} m apart across a motorway, trunk road "
                            f"or river, whose walk along the network is over {args.ratio:g} times as long; one per "
                            f"{args.spacing_m:g} m, biggest saving first",
                            "railbed_walkway": "the corridor line cut where it crosses walkable ways (the access "
                            "points) and at the rail-bed site boundaries"},
                  "review": "set reviewed_by on each link kept, or rejected: true; only reviewed links enter plans",
                  "attribution": "© OpenStreetMap contributors, ODbL", "features": features}
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(collection, indent=1, ensure_ascii=False), encoding="utf-8")
    kinds = {}
    for c in crossings:
        kinds[c["barrier"]] = kinds.get(c["barrier"], 0) + 1
    print(f"links -> {args.output}: {len(crossings)} crossings ({kinds}), {len(pieces)} rail-bed walkway pieces "
          f"({len(free)} cut points on no way)")


def command_build_links(args: argparse.Namespace) -> None:
    import time

    from .access import AccessModel
    from .access_gpu import run_record
    from .walk_links import build_links, full_dijkstra, read_links

    started = time.perf_counter()
    inputs = _access_inputs(args)
    model = AccessModel.load(args.access)
    homes, sites, places, graph = inputs["homes"], inputs["sites"], inputs["places"], inputs["graph"]
    if list(map(str, homes.ids)) != list(map(str, model.home_ids)) or [s["id"] for s in sites] != model.site_ids:
        raise SystemExit(f"{args.access} was not built from these inputs (homes or sites differ)")
    try:
        links = read_links(_json(args.links), inputs["crs"], require_review=not args.allow_unreviewed)
    except ValueError as error:
        raise SystemExit(str(error)) from None
    free = set()
    for link in links:
        for end, join in zip((link.xy[0], link.xy[-1]), link.props.get("ends_join") or ["network", "network"]):
            if join == "free":
                free.add((float(end[0]), float(end[1])))
    blocks, ends = build_links(graph, homes, places, sites, model.services, links, free_points=free,
                               meta={"access_model": str(args.access), "links_file": str(args.links),
                                     "unreviewed_allowed": bool(args.allow_unreviewed)})
    base = np.vstack([model.base, model.site_dist])
    check = {}
    if args.check:
        rng = np.random.default_rng(args.seed)
        worst_np = worst_gpu = 0.0
        changed = 0
        batch_sets = []
        for i in range(args.check):
            size = int(rng.integers(1, min(args.check_max_links, len(links)) + 1))
            active = sorted(rng.choice(len(links), size, replace=False).tolist())
            fast = blocks.distances(model, active)
            slow = full_dijkstra(ends, homes, places, sites, model.services, active, graph)
            both = np.isfinite(fast) | np.isfinite(slow)
            if not np.array_equal(np.isfinite(fast), np.isfinite(slow)):
                worst_np = math.inf
            else:
                worst_np = max(worst_np, float(np.abs(fast[both] - slow[both]).max(initial=0)))
            changed += bool((fast < base - 1e-6).any())
            batch_sets.append((active, fast))
        check = {"link_sets": args.check, "max_links": args.check_max_links, "seed": args.seed,
                 "max_abs_diff_m_vs_dijkstra": worst_np, "sets_that_change_a_walk": changed}
        try:
            import torch

            from .walk_links import BatchLinks

            device = "cuda" if torch.cuda.is_available() else "cpu"
            gpu = BatchLinks(blocks, model, device)
            active = np.zeros((len(batch_sets), len(links)), dtype=bool)
            for row, (sel, _) in enumerate(batch_sets):
                active[row, sel] = True
            for start in range(0, len(batch_sets), 64):
                out = gpu.distances(active[start:start + 64]).cpu().numpy()
                for row, (_, fast) in enumerate(batch_sets[start:start + 64]):
                    ok = np.isfinite(fast)
                    if not np.array_equal(ok, np.isfinite(out[row])):
                        worst_gpu = math.inf
                    else:
                        rel = np.abs(out[row][ok] - fast[ok]).max(initial=0)
                        worst_gpu = max(worst_gpu, float(rel))
            check["batched_device"] = device
            check["max_abs_diff_m_batched_vs_reference"] = worst_gpu
        except ImportError:
            check["batched_device"] = None
    destination = Path(args.output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    blocks.save(destination)
    report = {"model": str(destination), "access_model": str(args.access), "links": len(links), **blocks.meta,
              "check": check, "record": run_record(args.access, time.perf_counter() - started, seeds=[args.seed])}
    report_path = destination.with_suffix(".report.json")
    report_path.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"links -> {args.output} ({len(links)} links, {blocks.meta['end_points']} end points, "
          f"{blocks.meta['split_nodes']} split ways); check {check}")


def _facilities_view(path: str | None, bounds: tuple[float, float, float, float] | None = None) -> dict | None:
    if not path:
        return None
    collection = _json(path)
    if collection.get("schema") != "dynacity-facilities/1":
        raise SystemExit(f"{path} is not a build-facilities output")
    if bounds is None:
        return collection
    from .facilities import crop_facilities
    # The page loads basemap buildings this far around the corridor too (corridorExtent).
    pad = 0.006
    return crop_facilities(collection, (bounds[0] - pad, bounds[1] - pad, bounds[2] + pad, bounds[3] + pad))


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
    landscape = _landscape_view(getattr(args, 'landscape', None), traffic, getattr(args, 'context_buildings', None), focus, overlays=getattr(args, 'context_overlay', None))
    if args.mobility_inputs:
        if traffic is None:
            raise SystemExit('--mobility-inputs requires --traffic')
        from .mobility_inputs import MobilityInputs
        traffic.mobility = MobilityInputs.load(args.mobility_inputs)
    if traffic is not None and not args.snapshot:
        raise SystemExit("--traffic needs --snapshot")
    if snapshot is not None:
        if ground is not None:
            # Ground levels are for the viewer and traffic; the transition model never sees them.
            snapshot = attach_ground_elevation(snapshot, ground)
            print("viewer terrain loaded from " + args.terrain)
    if getattr(args, "relief", None) and ground is None:
        raise SystemExit("--relief needs --terrain (the city ground it extends)")
    viewer_terrain = _viewer_terrain(ground, args.beirut001, getattr(args, "relief", None)) if ground is not None else None
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
            landscape=landscape, facilities=_facilities_view(args.facilities, focus),
        )
        if viewer_terrain is not None:
            payload["terrain"] = viewer_terrain.meta("/v1/viewer/terrain.bin", "/v1/viewer/terrain/{z}/{x}/{y}.png")
        _attach_canopy_review(payload, args)
        _attach_granite_overlay(payload, args)
        if getattr(args, "pedestrians", None):
            from .viewer_pedestrians import pedestrian_payload
            payload["pedestrians"] = pedestrian_payload(_json(args.pedestrians), payload.get("traffic"))
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
    planner_context = None
    if getattr(args, 'population', None) or getattr(args, 'places', None):
        if traffic is None or snapshot is None:
            raise SystemExit('--population and --places belong to the /planner page: they need --traffic, --snapshot and --bbed')
        from .planner_context import PlannerContext
        planner_context = PlannerContext.load(traffic, args.population, args.places)
        print('planner context layers: ' + ', '.join(
            n for n, f in (('WorldPop residents', args.population), ('Overture places', args.places)) if f))
    app = create_app(
        engine,
        scenario_provider=provider,
        viewer_html=viewer_html,
        viewer_snapshot=snapshot,
        evidence=_load_bundle(args.evidence),
        viewer_forecast=baseline,
        viewer_ground=ground,
        viewer_terrain=viewer_terrain,
        llm_budget=RequestBudget(
            total=args.llm_request_limit, per_minute=args.llm_requests_per_minute, what="language-model requests"
        ),
        viewer_traffic=traffic,
        point_cloud=point_cloud,
        beirut001=_beirut001_payload(args.beirut001, args.bbed, focus),
        planner_context=planner_context,
        planner_trees=_planner_trees(landscape),
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



def _add_access_inputs(access: argparse.ArgumentParser) -> None:
    access.add_argument("--corridor", required=True, help="GeoJSON line, as `serve --corridor`")
    access.add_argument("--corridor-buffer-m", type=float, default=250.0, help="planning area: homes and sites inside it")
    access.add_argument("--reach-m", type=float, help="services count out to this far beyond the planning area "
                        "(default a 15-minute walk, 1,170 m)")
    access.add_argument("--network", required=True, help="saved Overpass JSON with highway ways (out geom, with nodes)")
    access.add_argument("--places", action="append", help="saved Overpass JSON with shops, leisure, parks, offices (repeat)")
    access.add_argument("--facilities", help="build-facilities output: education, medical care, government offices")
    access.add_argument("--beirut001", required=True, help="pack-beirut001 output: homes")
    access.add_argument("--bbed", help="BBED GeoJSON: empty, parking, stalled and vacant sites become candidates")
    access.add_argument("--sites", help="extra candidate sites (GeoJSON; properties id, allowed, name, owner, source)")
    access.add_argument("--add-parking", action="store_true", help="surface parking lots mapped in the --places extracts "
                        "become candidate sites")
    access.add_argument("--add-railbed", action="store_true", help="the corridor line, cut into pieces, becomes candidate "
                        "sites for parks and play")
    access.add_argument("--railbed-piece-m", type=float, default=100.0)
    access.add_argument("--size-rules", choices=("local", "none"), default="local",
                        help="local: smallest site per use from Beirut's mapped schools, parks and playgrounds "
                        "in the --places extracts")
    access.add_argument("--min-site-m2", action="append", metavar="USE=M2", help="smallest site a use may take "
                        "(repeat; overrides --size-rules)")


def _planner_trees(landscape: dict | None) -> dict | None:
    if not landscape:
        return None
    from .planner import planner_trees
    return planner_trees(landscape)


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

    surface = subparsers.add_parser("las-surface", help="observed LAS surface for review; not verified terrain")
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
    terrain.add_argument("--traffic", help="traffic model from build-traffic; the grid then also covers its roads")
    terrain.add_argument("--margin-m", type=float, default=1500.0, help="ground beyond the outermost building or road")
    terrain.set_defaults(func=command_fetch_terrain)

    relief = subparsers.add_parser("fetch-relief", help="country-wide GLO-30 relief for the viewer's basemap (default: Lebanon)")
    relief.add_argument("--output", required=True)
    relief.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"),
                        default=[35.0, 33.0, 36.75, 34.75])
    relief.add_argument("--step", type=int, default=1, help="average step x step 1-arc-second cells (2 ~ 60 m)")
    relief.set_defaults(func=command_fetch_relief)

    fabdem = subparsers.add_parser("fetch-fabdem", help="FABDEM V1-2 bare-earth ground on the lattice of an existing terrain .npz")
    fabdem.add_argument("--like", required=True, help="terrain .npz from fetch-terrain; FABDEM takes its CRS, extent and cell size")
    fabdem.add_argument("--output", required=True)
    fabdem.add_argument("--sources-root", required=True, help="open-sources folder: raw tile goes to raw/fabdem, ledger to sources.json")
    fabdem.set_defaults(func=command_fetch_fabdem)

    canopy_fetch = subparsers.add_parser("fetch-canopy", help="Meta/WRI canopy height tile(s) and acquisition-date footprints covering a corridor")
    canopy_fetch.add_argument("--corridor", required=True, help="corridor line GeoJSON")
    canopy_fetch.add_argument("--sources-root", required=True, help="open-sources folder: files go to raw/meta-wri-chm, ledger to sources.json")
    canopy_fetch.add_argument("--crs", default="EPSG:32636")
    canopy_fetch.add_argument("--corridor-buffer-m", type=float, default=250.0)
    canopy_fetch.add_argument("--margin-deg", type=float, default=0.02)
    canopy_fetch.add_argument("--max-bytes", type=int, default=1_000_000_000, help="refuse any single file larger than this")
    canopy_fetch.add_argument("--workers", type=int, default=16, help="parallel range requests")
    canopy_fetch.set_defaults(func=command_fetch_canopy)

    canopy_build = subparsers.add_parser("build-canopy", help="tree candidates, OSM tree heights and per-road canopy cover for a corridor")
    canopy_build.add_argument("--corridor", required=True)
    canopy_build.add_argument("--sources-root", required=True)
    canopy_build.add_argument("--tile", required=True, help="tile id (quadkey) from fetch-canopy")
    canopy_build.add_argument("--traffic", required=True, help="traffic model from build-traffic")
    canopy_build.add_argument("--osm", required=True, help="OSM context extract holding natural=tree nodes")
    canopy_build.add_argument("--output", required=True)
    canopy_build.add_argument("--footprints", help="lon/lat building footprints GeoJSON (e.g. Overture); flags candidates lying on a footprint")
    canopy_build.add_argument("--crs", default="EPSG:32636")
    canopy_build.add_argument("--corridor-buffer-m", type=float, default=250.0)
    canopy_build.add_argument("--min-height-m", type=float, default=3.0)
    canopy_build.add_argument("--min-spacing-m", type=float, default=4.0)
    canopy_build.add_argument("--road-buffer-m", type=float, default=8.0)
    canopy_build.add_argument("--unmapped-distance-m", type=float, default=5.0)
    canopy_build.set_defaults(func=command_build_canopy)

    beirut001 = subparsers.add_parser("pack-beirut001")
    beirut001.add_argument("--model", required=True, help="Beirut 001 Rhino .3dm (private; read once)")
    beirut001.add_argument("--bbed", required=True, help="BBED GeoJSON in EPSG:32636, for alignment and links")
    beirut001.add_argument("--output", required=True)
    beirut001.add_argument("--cell-m", type=float, default=10.0, help="ground grid cell size")
    beirut001.set_defaults(func=command_pack_beirut001)

    refine = subparsers.add_parser("refine-beirut001-alignment",
                                   help="measure and refine a Beirut 001 pack's placement (no Rhino source needed)")
    refine.add_argument("--pack", required=True, help="original pack from pack-beirut001 (never overwritten)")
    refine.add_argument("--reference", required=True,
                        help="Overture/OSM building footprints GeoJSON (WGS84), independent of the model and BBED")
    refine.add_argument("--output", required=True, help="refined pack (.npz)")
    refine.add_argument("--report", required=True, help="alignment-report.json")
    refine.add_argument("--bbed", help="BBED GeoJSON in EPSG:32636: links are re-matched and BBED residuals reported")
    refine.add_argument("--terrain", help="GLO-30 ground model (.npz) for the vertical check")
    refine.add_argument("--structures-pack", help="structures pack from the same build; gets the same correction")
    refine.add_argument("--structures-output")
    refine.add_argument("--display-json", help="beirut-model-display.json; regenerated from the refined structures pack")
    refine.add_argument("--display-output")
    refine.set_defaults(func=command_refine_beirut001_alignment)

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

    facilities = subparsers.add_parser("build-facilities", help="key buildings, the public services people go to, from OSM and the MEHE school list")
    facilities.add_argument("--output", required=True)
    facilities.add_argument("--bbox", type=float, nargs=4, metavar=("WEST", "SOUTH", "EAST", "NORTH"))
    facilities.add_argument("--snapshot", help="use the snapshot's extent plus --margin-m instead of --bbox")
    facilities.add_argument("--margin-m", type=float, default=800.0)
    facilities.add_argument("--osm", help="saved Overpass JSON (out center); fetched from Overpass when omitted")
    facilities.add_argument("--official-schools", help="MEHE public and UNRWA schools list (.xlsx needs openpyxl, or .csv)")
    facilities.set_defaults(func=command_build_facilities)

    access = subparsers.add_parser("build-access", help="walking access to the 15-minute services on a corridor, "
                                   "and what candidate sites would add (after Zheng et al. 2023)")
    _add_access_inputs(access)
    access.add_argument("--score-service", action="append", help="services in the score (repeat; default education, "
                        "medical, shopping, entertainment)")
    access.add_argument("--alpha", type=float, default=1.0, help="weight of services against parks, as the paper's alpha")
    access.add_argument("--steps", type=int, default=10, help="greedy search steps in the report")
    access.add_argument("--output", required=True, help="model (.npz)")
    access.add_argument("--report", help="report JSON (default: next to the model)")
    access.set_defaults(func=command_build_access)

    score_access = subparsers.add_parser("score-access", help="score one site plan on a build-access model")
    score_access.add_argument("--model", required=True)
    score_access.add_argument("--plan", required=True, help='JSON: {"plan_id": ..., "uses": {"bbed/123": "parks"}}')
    score_access.add_argument("--alpha", type=float, default=1.0)
    score_access.add_argument("--output", required=True)
    score_access.set_defaults(func=command_score_access)

    bench_gpu = subparsers.add_parser("bench-access-gpu", help="check and time the batch plan scorer on CPU and GPU")
    bench_gpu.add_argument("--model", required=True)
    bench_gpu.add_argument("--output", required=True)
    bench_gpu.add_argument("--devices", nargs="+", help="default: cpu, and cuda when available")
    bench_gpu.add_argument("--batches", type=int, nargs="+", default=[1_000, 16_000, 256_000])
    bench_gpu.add_argument("--budget", type=int, default=10, help="sites changed per timed plan")
    bench_gpu.add_argument("--alpha", type=float, default=1.0)
    bench_gpu.add_argument("--check", type=int, default=10_000, help="random plans compared with AccessModel (0: skip)")
    bench_gpu.add_argument("--seed", type=int, default=0)
    bench_gpu.set_defaults(func=command_bench_access_gpu)

    search = subparsers.add_parser("search-access", help="exact optimum and search baselines for land-use plans")
    search.add_argument("--model", required=True)
    search.add_argument("--output", required=True)
    search.add_argument("--method", nargs="+", choices=["milp", "greedy", "random", "nsga2"],
                        default=["milp", "greedy", "random", "nsga2"])
    search.add_argument("--budget", type=int, nargs=2, default=[1, 30], metavar=("LOW", "HIGH"),
                        help="sites changed, every budget from LOW to HIGH")
    search.add_argument("--alpha", type=float, nargs="+", default=[0.5, 1.0, 2.0, 4.0])
    search.add_argument("--device", help="default: cuda when available")
    search.add_argument("--samples", type=int, default=1_000_000, help="random plans per budget and alpha")
    search.add_argument("--population", type=int, default=1024)
    search.add_argument("--generations", type=int, default=200)
    search.add_argument("--pareto-points", type=int, default=11, help="epsilon-constraint points per budget")
    search.add_argument("--optimum", help="a milp run's JSON, for gaps when milp is not among the methods")
    search.add_argument("--summary", help="Markdown table for Gate A1")
    search.add_argument("--label")
    search.add_argument("--seed", type=int, default=0)
    search.set_defaults(func=command_search_access)

    propose = subparsers.add_parser("propose-links", help="candidate crossings and the rail bed as a walkway, for review")
    propose.add_argument("--corridor", required=True)
    propose.add_argument("--corridor-buffer-m", type=float, default=250.0)
    propose.add_argument("--reach-m", type=float)
    propose.add_argument("--network", required=True, help="saved Overpass JSON with highway and waterway ways")
    propose.add_argument("--max-gap-m", type=float, default=60.0, help="longest straight crossing proposed")
    propose.add_argument("--ratio", type=float, default=3.0, help="network walk over straight line, at least")
    propose.add_argument("--spacing-m", type=float, default=100.0, help="one crossing per this distance")
    propose.add_argument("--railbed-piece-m", type=float, default=100.0)
    propose.add_argument("--output", required=True, help="candidate-links GeoJSON (unreviewed)")
    propose.set_defaults(func=command_propose_links)

    links = subparsers.add_parser("build-links", help="exact distance blocks for reviewed candidate links")
    _add_access_inputs(links)
    links.add_argument("--score-service", action="append")
    links.add_argument("--access", required=True, help="the build-access model made from the same inputs")
    links.add_argument("--links", required=True, help="reviewed candidate-links GeoJSON")
    links.add_argument("--allow-unreviewed", action="store_true", help="accept unreviewed links (for testing only)")
    links.add_argument("--check", type=int, default=0, help="random link sets checked against full Dijkstra")
    links.add_argument("--check-max-links", type=int, default=8)
    links.add_argument("--seed", type=int, default=0)
    links.add_argument("--output", required=True, help="distance blocks (.npz); report next to it")
    links.set_defaults(func=command_build_links)

    joint = subparsers.add_parser("search-joint", help="exact optimum and search baselines for land uses plus links")
    joint.add_argument("--model", required=True, help="build-access model")
    joint.add_argument("--links", required=True, help="build-links blocks made on that model")
    joint.add_argument("--output", required=True)
    joint.add_argument("--method", nargs="+", choices=["exact", "greedy", "nsga2", "bo"],
                       default=["exact", "greedy", "nsga2", "bo"])
    joint.add_argument("--budgets", type=int, nargs="+", default=[5, 10, 15, 20, 25, 30], help="sites changed, N")
    joint.add_argument("--link-budgets", type=int, nargs="+", default=[1, 2, 3], help="link groups opened, K")
    joint.add_argument("--alpha", type=float, nargs="+", default=[0.5, 1.0, 2.0, 4.0])
    joint.add_argument("--bo-budgets", type=int, nargs="+", help="N for Bayesian optimisation (default: --budgets)")
    joint.add_argument("--bo-link-budgets", type=int, nargs="+", help="K for Bayesian optimisation")
    joint.add_argument("--bo-reduced", type=int, default=40, help="(site, use) pairs kept in the reduced problem")
    joint.add_argument("--bo-max-iterations", type=int, default=300)
    joint.add_argument("--population", type=int, default=1024)
    joint.add_argument("--generations", type=int, default=200)
    joint.add_argument("--device")
    joint.add_argument("--summary", help="Markdown table")
    joint.add_argument("--label")
    joint.add_argument("--seed", type=int, default=0)
    joint.set_defaults(func=command_search_joint)

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
    viewer.add_argument("--pedestrians", help="private pedestrian-walkers.json shared with Unreal (modelled PM flows)")
    viewer.add_argument("--context-overlay", action="append", help="additional private OSM inventory used by Unreal; repeatable, merged by source ID")
    viewer.add_argument("--landscape", help="private OSM context JSON: surfaces, trees, paths, places and structures")
    viewer.add_argument("--context-buildings", help="Overture companion from engine_context_buildings --web-output: estimated heights and extra footprints")
    viewer.add_argument("--canopy-review", help="private canopy candidate GeoJSON; optional review markers, not confirmed trees")
    viewer.add_argument("--canopy-screening", help="matching canopy screening JSON; required with --canopy-review")
    viewer.add_argument("--granite-overlay", help="private corridor bundle manifest; estimated panels, off by default")
    viewer.add_argument("--granite-parent", help="matching parent snapshot; required with --granite-overlay")
    viewer.add_argument("--facilities", help="build-facilities output: the Key buildings view (public services) on the map")
    viewer.set_defaults(func=command_export_viewer)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--model", required=True)
    serve.add_argument("--snapshot")
    serve.add_argument("--bbed")
    serve.add_argument("--forecast")
    serve.add_argument("--evidence")
    serve.add_argument("--terrain", help="ground model from fetch-terrain")
    serve.add_argument("--relief", help="country-wide relief from fetch-relief; the basemap's hills continue beyond the city")
    serve.add_argument("--beirut001", help="private pack from pack-beirut001; the map then draws its buildings")
    serve.add_argument("--point-cloud-dir", help="private export-point-cloud directory for local RGB point viewing")
    serve.add_argument("--traffic", help="traffic model from build-traffic; closures and crashes then reroute traffic")
    serve.add_argument("--prewarm-traffic", action="store_true",
                       help="build the default SUMO network before serving to reduce first-connect work")
    serve.add_argument("--mobility-inputs", help="private mobility evidence JSON for the traffic pilot")
    serve.add_argument("--pedestrians", help="private pedestrian-walkers.json shared with Unreal (modelled PM flows)")
    serve.add_argument("--context-overlay", action="append", help="additional private OSM inventory used by Unreal; repeatable, merged by source ID")
    serve.add_argument("--landscape", help="private OSM context JSON: surfaces, trees, paths, places and structures")
    serve.add_argument("--context-buildings", help="Overture companion from engine_context_buildings --web-output: estimated heights and extra footprints")
    serve.add_argument("--canopy-review", help="private canopy candidate GeoJSON; optional review markers, not confirmed trees")
    serve.add_argument("--canopy-screening", help="matching canopy screening JSON; required with --canopy-review")
    serve.add_argument("--granite-overlay", help="private corridor bundle manifest; estimated panels, off by default")
    serve.add_argument("--granite-parent", help="matching parent snapshot; required with --granite-overlay")
    serve.add_argument("--facilities", help="build-facilities output: the Key buildings view (public services) on the map")
    serve.add_argument("--corridor", help="GeoJSON line to focus on (e.g. Al Masar al Akhdar); every input is cut to its buffer")
    serve.add_argument("--population", help="WorldPop residents file from scripts/prepare_planner_context.py; the /planner page then shows residents near streets")
    serve.add_argument("--places", help="Overture places file from scripts/prepare_planner_context.py; the /planner page then shows shops and services on streets")
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

