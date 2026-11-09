"""Compile an evidence-labelled street twin from existing private Beirut inputs.

The SUMO network stays citywide even when only a corridor is exported for Unreal.
Outputs must stay outside the repository. This command does not change a level.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
import os
from pathlib import Path
import time

from dynacity.street_twin import build_street_twin
from dynacity.street_twin_pipeline import elevation_overrides, file_hash, select_roads
from dynacity.street_twin_unreal import export_street_twin_unreal
from dynacity.street_twin_validation import (validate_observations, validate_sumo_network,
                                             validate_unreal_lane_alignment)
from dynacity.traffic import TrafficModel
from dynacity.traffic_sumo import export_network, runtime


def _read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def _write(path, value):
    path = Path(path)
    pending = path.with_suffix(path.suffix + '.pending')
    pending.write_text(json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    os.replace(pending, path)


def _prepare(traffic_path, output, *, elevation_metadata=None, bounds_utm=None,
            origin_utm_m=None, tile_size_m=250, observation_package=None):
    started = time.perf_counter()
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        raise ValueError('Source-derived output must live outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    model = TrafficModel.load(traffic_path)
    indices = select_roads(model, bounds_utm)
    if not indices:
        raise ValueError('No traffic roads intersect the requested bounds')
    sources, overrides = [], {}
    elevation_report = {'matched': 0, 'selected': len(indices), 'reason': 'No elevation metadata supplied'}
    if elevation_metadata:
        metadata = _read(elevation_metadata)
        overrides, elevation_report = elevation_overrides(model, metadata, indices)
        origin_utm_m = origin_utm_m or metadata['coordinates']['origin_utm_m']
        sources.append({'id': 'city_elevations', 'kind': 'model',
                        'title': 'Existing shared-city road elevation metadata',
                        'sha256': file_hash(elevation_metadata),
                        'limitations': metadata.get('limitations', [])})
    if origin_utm_m is None:
        points = [p for i in indices for p in model.geometry[i]]
        origin_utm_m = [min(p[0] for p in points), min(p[1] for p in points), 0]
    twin = build_street_twin(model, origin_utm_m=origin_utm_m,
                             tile_size_m=tile_size_m, road_indices=indices,
                             overrides=overrides, sources=sources)
    _write(output / 'street-twin.json', twin)
    timings = {'compile_s': time.perf_counter() - started}
    network_dir = output / 'sumo'
    network_dir.mkdir(exist_ok=True)
    network_started = time.perf_counter()
    _, _, converter = runtime()
    net = export_network(model, network_dir, converter)
    timings['network_s'] = time.perf_counter() - network_started
    consistency = validate_sumo_network(twin, net, allow_extra_edges=bounds_utm is not None)
    _write(output / 'sumo-consistency.json', consistency)
    # netconvert may deliberately shift source reference geometry at junctions.
    # Keep that drift visible, but require identities/dimensions before using its
    # actual lanes. The independent visual alignment check runs after export.
    unsafe = [check for check in consistency['checks']
              if any(issue != 'missing_edge' and issue != 'reference_geometry_mismatch'
                     and not issue.startswith('geometry_unreadable:')
                     for issue in check.get('issues', [])) or check.get('reason') == 'unexpected_edges']
    if unsafe or consistency.get('identity_status', consistency['status']) == 'blocked':
        raise ValueError(f"SUMO identity consistency {consistency['status']}; inspect {output / 'sumo-consistency.json'}")
    geometry_started = time.perf_counter()
    manifest = export_street_twin_unreal(twin, output / 'unreal', net_xml=net)
    alignment = validate_unreal_lane_alignment(twin, manifest, net)
    _write(output / 'lane-alignment.json', alignment)
    timings['unreal_export_s'] = time.perf_counter() - geometry_started
    package = _read(observation_package) if observation_package else {}
    observed = validate_observations(package.get('observations', []),
                                     package.get('simulations', []), package.get('tolerances', {}))
    _write(output / 'observation-validation.json', observed)
    timings['total_s'] = time.perf_counter() - started
    summary = {
        'schema': 'dynacity-street-twin-build/1',
        'build_status': 'complete',
        'traffic_sha256': file_hash(traffic_path), 'twin_content_hash': twin['content_hash'],
        'scope': 'whole city' if bounds_utm is None else 'whole-road corridor export; SUMO network remains citywide',
        'bounds_utm': bounds_utm, 'city_roads': len(model), 'selected_roads': len(twin['roads']),
        'directed_edges': sum(len(r['edges']) for r in twin['roads']),
        'expected_lanes': sum(len(e['lanes']) for r in twin['roads'] for e in r['edges']),
        'elevation_binding': elevation_report,
        'owner_tiles': len(twin['tiles']),
        'generated_tiles': sum(bool(t['asset']) for t in manifest['tiles']),
        'exported_lanes': sum(t['lane_count'] for t in manifest['tiles']),
        'triangles': sum(t.get('triangle_count', 0) for t in manifest['tiles']),
        'omitted_lane_reasons': dict(Counter(r['reason'] for r in manifest['omitted'])),
        'sumo_consistency': consistency['status'], 'observed_accuracy': observed['status'],
        'sumo_identity': consistency['identity_status'],
        'sumo_source_geometry': consistency['reference_geometry_status'],
        'sumo_drifted_edges': consistency['drifted_edge_count'],
        'sumo_missing_edges': sum('missing_edge' in c.get('issues', []) for c in consistency['checks']),
        'lane_alignment': alignment['status'],
        'known_height_lane_alignment': alignment['eligible_alignment_status'],
        'runtime_streaming_configured': False, 'live_unreal_verified': False,
        'timings_s': timings,
        'next_requirements': ['Dated geometry and traffic observations with predefined tolerances',
                              'Junction internal-link surfaces and pedestrian/curb geometry',
                              'Isolated Unreal import, save/reload and sustained runtime performance verification'],
    }
    _write(output / 'build-report.json', summary)
    return summary


def prepare(traffic_path, output, **kwargs):
    """Invalidate a prior success before updating any artifacts in that directory."""
    output = Path(output).resolve()
    repository = Path(__file__).resolve().parents[1]
    if output == repository or repository in output.parents:
        raise ValueError('Source-derived output must live outside the repository')
    output.mkdir(parents=True, exist_ok=True)
    report = output / 'build-report.json'
    _write(report, {'schema': 'dynacity-street-twin-build/1', 'build_status': 'building',
                    'note': 'Artifacts in this directory are not current until this build completes.'})
    try:
        return _prepare(traffic_path, output, **kwargs)
    except Exception as exc:
        _write(report, {'schema': 'dynacity-street-twin-build/1', 'build_status': 'failed',
                        'error': str(exc), 'note': 'Existing artifacts may belong to an earlier build.'})
        raise


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--traffic', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--elevation-metadata')
    parser.add_argument('--bounds-utm', nargs=4, type=float, metavar=('WEST', 'SOUTH', 'EAST', 'NORTH'))
    parser.add_argument('--origin-utm-m', nargs=3, type=float)
    parser.add_argument('--tile-size-m', type=float, default=250)
    parser.add_argument('--observation-package')
    args = parser.parse_args()
    result = prepare(args.traffic, args.output, elevation_metadata=args.elevation_metadata,
                     bounds_utm=args.bounds_utm, origin_utm_m=args.origin_utm_m,
                     tile_size_m=args.tile_size_m, observation_package=args.observation_package)
    print(json.dumps({k: v for k, v in result.items() if k != 'elevation_binding'}, indent=2))


if __name__ == '__main__':
    main()
