"""Evidence gates for the shared SUMO/Unreal street model.

Geometric agreement establishes export consistency, not surveyed accuracy.
Observation checks require dated, sourced, held-out measurements. They do not
claim that an unobserved part of Beirut has been calibrated.
"""
from __future__ import annotations

from datetime import datetime
import math
from pathlib import Path
import xml.etree.ElementTree as ET

from shapely.geometry import LineString


def _finite(value, name):
    if isinstance(value, bool):
        raise ValueError(f'{name} must be finite')
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f'{name} must be finite')
    return result


def _status(checks):
    states = {check['status'] for check in checks}
    return 'fail' if 'fail' in states else 'blocked' if 'blocked' in states or not checks else 'pass'


def validate_sumo_network(twin: dict, net_xml: str | Path, *, tolerance_m=0.1,
                          allow_extra_edges=False) -> dict:
    """Check actual netconvert output against canonical identities and XY geometry.

    SUMO coordinates = projected coordinates + netOffset. Canonical coordinates
    = projected coordinates - origin_utm_m. Internal junction edges are ignored.
    Reference edge shapes are compared because lane shapes are offset and trimmed
    by netconvert at junctions. Elevation and junction lane alignment remain
    separate, explicitly unverified checks.
    """
    tolerance = _finite(tolerance_m, 'tolerance_m')
    if tolerance < 0:
        raise ValueError('tolerance_m must be nonnegative')
    root = ET.parse(net_xml).getroot()
    checks = []
    location = root.find('location')
    if location is None or 'netOffset' not in location.attrib:
        return {'status': 'blocked', 'identity_status': 'blocked', 'reference_geometry_status': 'blocked',
                'drifted_edge_count': 0,
                'checks': [{'status': 'blocked', 'reason': 'SUMO netOffset missing'}]}
    offset = [_finite(x, 'netOffset') for x in location.attrib['netOffset'].split(',')]
    if len(offset) != 2:
        raise ValueError('SUMO netOffset must have two coordinates')
    origin = twin['coordinates']['origin_utm_m']
    edges = {e.attrib['id']: e for e in root.findall('edge')
             if e.attrib.get('function') != 'internal' and not e.attrib['id'].startswith(':')}
    junctions = {j.attrib['id']: j for j in root.findall('junction')}
    expected_ids = set()
    for road in twin['roads']:
        projected = [[_finite(p[0], 'x') + origin[0], _finite(p[1], 'y') + origin[1]]
                     for p in road['path']]
        for expected in road['edges']:
            edge_id = expected['sumo_edge_id']
            expected_ids.add(edge_id)
            entry = {'road_id': road['id'], 'edge_id': edge_id, 'status': 'pass', 'issues': []}
            edge = edges.get(edge_id)
            if edge is None:
                entry.update(status='fail', issues=['missing_edge'])
                checks.append(entry)
                continue
            if any(edge.attrib.get(k) != str(expected[k + '_node']) for k in ('from', 'to')):
                entry['issues'].append('endpoint_identity_mismatch')
            lanes = edge.findall('lane')
            actual_lanes = {lane.attrib['id']: lane for lane in lanes}
            expected_lanes = {lane['sumo_lane_id']: lane for lane in expected['lanes']}
            if set(actual_lanes) != set(expected_lanes):
                entry['issues'].append('lane_identity_mismatch')
            for lane_id in actual_lanes.keys() & expected_lanes.keys():
                lane = actual_lanes[lane_id]
                width = _finite(lane.attrib.get('width', 3.2), 'SUMO lane width')
                if abs(width - expected_lanes[lane_id]['width_m']) > tolerance:
                    entry['issues'].append(f'lane_width_mismatch:{lane_id}')
            try:
                if edge.attrib.get('shape'):
                    shape = [[_finite(v, 'shape') for v in p.split(',')[:2]]
                             for p in edge.attrib['shape'].split()]
                else:
                    shape = [[_finite(junctions[edge.attrib[k]].attrib[a], a) for a in ('x', 'y')]
                             for k in ('from', 'to')]
                shape = [[p[0] - offset[0], p[1] - offset[1]] for p in shape]
                directed = projected if expected['direction'] == 1 else projected[::-1]
                distance = float(LineString(directed).hausdorff_distance(LineString(shape)))
                endpoint_error = max(math.dist(directed[0], shape[0]), math.dist(directed[-1], shape[-1]))
                entry.update(geometry_error_m=distance, endpoint_error_m=endpoint_error)
                if max(distance, endpoint_error) > tolerance:
                    entry['issues'].append('reference_geometry_mismatch')
            except (KeyError, ValueError) as exc:
                entry['issues'].append(f'geometry_unreadable:{exc}')
            if entry['issues']:
                entry['status'] = 'fail'
            checks.append(entry)
    extra = sorted(edges.keys() - expected_ids)
    if extra and not allow_extra_edges:
        checks.append({'status': 'fail', 'reason': 'unexpected_edges', 'edge_ids': extra})
    identity_checks, geometry_checks = [], []
    drifted = 0
    for check in checks:
        issues = check.get('issues', [])
        geometry_issues = [issue for issue in issues if issue == 'reference_geometry_mismatch'
                           or issue.startswith('geometry_unreadable:')]
        drifted += 'reference_geometry_mismatch' in issues
        identity_issues = [issue for issue in issues if issue not in geometry_issues]
        identity_checks.append({'status': 'fail' if identity_issues or check.get('reason') == 'unexpected_edges' else 'pass'})
        geometry_checks.append({'status': 'fail' if geometry_issues else
                                'blocked' if 'missing_edge' in issues or check.get('reason') else 'pass'})
    return {'status': _status(checks), 'identity_status': _status(identity_checks),
            'reference_geometry_status': _status(geometry_checks), 'drifted_edge_count': drifted,
            'checks': checks, 'net_offset_m': offset,
            'tolerance_m': tolerance, 'extra_edge_count': len(extra),
            'scope': 'Export identity and horizontal reference geometry consistency only',
            'unverified': ['surveyed_geometry_accuracy', 'elevation', 'junction_lane_alignment',
                           'observed_traffic_accuracy']}


def _interval(row):
    values = [datetime.fromisoformat(str(row[k]).replace('Z', '+00:00'))
              for k in ('interval_start', 'interval_end')]
    if any(t.tzinfo is None or t.utcoffset() is None for t in values) or values[0] >= values[1]:
        raise ValueError('Measurement intervals require timezone offsets and end after start')
    return tuple(values)


def validate_unreal_lane_alignment(twin: dict, manifest: dict, net_xml: str | Path,
                                   *, tolerance_m=0.02) -> dict:
    """Independently check exported lane metadata against actual netconvert lanes.

    Full coverage remains blocked when source elevation is unknown. A separate
    eligible_alignment_status describes only known-height lanes. This checks
    offline exported metadata, not installation, collision or rendering in Unreal.
    """
    tolerance = _finite(tolerance_m, 'tolerance_m')
    if tolerance < 0:
        raise ValueError('tolerance_m must be nonnegative')
    checks, eligible_checks = [], []
    root = ET.parse(net_xml).getroot()
    location = root.find('location')
    if location is None or not location.get('netOffset'):
        return {'status': 'blocked', 'eligible_alignment_status': 'blocked',
                'checks': [{'status': 'blocked', 'reason': 'SUMO netOffset missing'}]}
    offset = [_finite(v, 'netOffset') for v in location.attrib['netOffset'].split(',')]
    if len(offset) != 2:
        raise ValueError('SUMO netOffset must have two coordinates')
    if manifest.get('coordinates') != twin['coordinates']:
        checks.append({'status': 'fail', 'reason': 'manifest_coordinate_frame_mismatch'})
    origin = twin['coordinates']['origin_utm_m']
    actual = {}
    for edge in root.findall('edge'):
        if edge.get('function') == 'internal' or edge.attrib['id'].startswith(':'):
            continue
        for lane in edge.findall('lane'):
            lane_id = lane.attrib['id']
            if lane_id in actual:
                raise ValueError('Duplicate SUMO lane identity')
            actual[lane_id] = lane
    exported = {}
    for tile in manifest.get('tiles', []):
        metadata = tile.get('lanes', [])
        if tile.get('lane_count') != len(metadata):
            checks.append({'status': 'fail', 'reason': 'manifest_tile_lane_count_mismatch',
                           'tile_id': tile.get('tile_id')})
        for lane in metadata:
            lane_id = lane['lane_id']
            if lane_id in exported:
                checks.append({'status': 'fail', 'reason': 'duplicate_exported_lane', 'lane_id': lane_id})
            exported[lane_id] = lane
    expected_ids, known_count, unknown_count = set(), 0, 0
    for road in twin['roads']:
        known_height = bool(road['path']) and all(len(p) == 3 and p[2] is not None for p in road['path'])
        for edge in road['edges']:
            for lane in edge['lanes']:
                lane_id = lane['id']
                expected_ids.add(lane_id)
                entry = {'lane_id': lane_id, 'road_id': road['id'], 'status': 'pass', 'issues': []}
                visual = exported.get(lane_id)
                if not known_height:
                    unknown_count += 1
                    entry.update(status='blocked', reason='unknown_source_elevation')
                    if visual is not None:
                        entry.update(status='fail', issues=['unknown_height_lane_exported'])
                    checks.append(entry)
                    continue
                known_count += 1
                sumo_id = lane['sumo_lane_id']
                simulated = actual.get(sumo_id)
                if visual is None:
                    entry['issues'].append('known_height_lane_missing_from_manifest')
                if simulated is None:
                    entry['issues'].append('canonical_lane_missing_from_sumo')
                if visual is not None and simulated is not None:
                    if (visual.get('sumo_lane_id') != sumo_id or visual.get('edge_id') != edge['id']
                            or visual.get('road_id') != road['id']):
                        entry['issues'].append('lane_identity_mismatch')
                    try:
                        points = visual['lane_path_local_m']
                        if any(len(p) != 3 or p[2] is None for p in points):
                            raise ValueError('visual lane path requires finite XYZ')
                        local = [[_finite(p[0], 'x'), _finite(p[1], 'y')] for p in points]
                        for p in points:
                            _finite(p[2], 'z')
                        shape = [[_finite(v, 'SUMO lane shape') for v in p.split(',')[:2]]
                                 for p in simulated.attrib['shape'].split()]
                        reference = [[p[0] - offset[0] - origin[0], p[1] - offset[1] - origin[1]]
                                     for p in shape]
                        distance = float(LineString(local).hausdorff_distance(LineString(reference)))
                        endpoint = max(math.dist(local[0], reference[0]), math.dist(local[-1], reference[-1]))
                        entry.update(geometry_error_m=distance, endpoint_error_m=endpoint)
                        if max(distance, endpoint) > tolerance:
                            entry['issues'].append('lane_geometry_mismatch')
                        width = _finite(simulated.attrib.get('width', 3.2), 'SUMO lane width')
                        if abs(_finite(visual['width_m'], 'visual lane width') - width) > tolerance:
                            entry['issues'].append('lane_width_mismatch')
                    except (KeyError, ValueError, IndexError, TypeError) as exc:
                        entry['issues'].append(f'lane_geometry_unreadable:{exc}')
                if entry['issues']:
                    entry['status'] = 'fail'
                checks.append(entry)
                eligible_checks.append(entry)
    extra = sorted(exported.keys() - expected_ids)
    if extra:
        checks.append({'status': 'fail', 'reason': 'unexpected_exported_lanes', 'lane_ids': extra})
    # Frame/count/duplicate errors also invalidate the eligible subset gate.
    global_checks = [check for check in checks if 'lane_id' not in check]
    return {'status': _status(checks), 'eligible_alignment_status': _status(eligible_checks + global_checks),
            'checks': checks, 'tolerance_m': tolerance,
            'known_height_lane_count': known_count, 'unknown_height_lane_count': unknown_count,
            'exported_lane_count': len(exported), 'expected_lane_count': len(expected_ids),
            'scope': 'Offline exported lane metadata XY and width against actual SUMO lanes; '
                     'unknown-height lanes block full coverage',
            'unverified': ['Unreal installation', 'rendered mesh alignment', 'collision',
                           'surveyed elevation accuracy', 'junction surfaces']}


def _key(row):
    for field in ('location_id', 'metric', 'unit'):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f'Measurements require {field}')
    return (row['location_id'], row['metric'], row['unit'], *_interval(row))


def validate_observations(observations: list[dict], simulations: list[dict],
                          tolerances: dict) -> dict:
    """Compare matched held-out intervals, retaining calibration rows separately.

    Rows use location_id, metric, unit, interval_start/end (ISO with offset), value.
    Observations additionally require split ('calibration' or 'validation') and
    source_id. Tolerances map metric -> {unit, absolute, relative}; allowed error
    is absolute + relative * abs(observed). At least one nonnegative tolerance
    must be supplied. Overlapping calibration/validation intervals at the same
    location and metric are rejected to prevent accidental evidence reuse.
    """
    observed, simulated = {}, {}
    for row in observations:
        key = _key(row)
        if key in observed:
            raise ValueError('Duplicate observation interval')
        if row.get('split') not in ('calibration', 'validation'):
            raise ValueError('Observations require explicit calibration/validation split')
        if not isinstance(row.get('source_id'), str) or not row['source_id'].strip():
            raise ValueError('Observations require source_id')
        _finite(row['value'], 'observed value')
        observed[key] = row
    for row in simulations:
        key = _key(row)
        if key in simulated:
            raise ValueError('Duplicate simulated interval')
        _finite(row['value'], 'simulated value')
        simulated[key] = row
    calibration = [(k, r) for k, r in observed.items() if r['split'] == 'calibration']
    validation = [(k, r) for k, r in observed.items() if r['split'] == 'validation']
    for key, _ in validation:
        for other, _ in calibration:
            if key[:2] == other[:2] and key[3] < other[4] and other[3] < key[4]:
                raise ValueError('Calibration and validation intervals overlap for the same location and metric')
    checks = []
    for key, row in validation:
        entry = {k: row[k] for k in ('location_id', 'metric', 'unit', 'interval_start', 'interval_end', 'source_id')}
        entry['status'] = 'blocked'
        tolerance = tolerances.get(row['metric'])
        result = simulated.get(key)
        if result is None:
            entry['reason'] = 'missing_matching_simulation'
        elif tolerance is None:
            entry['reason'] = 'missing_tolerance'
        elif tolerance.get('unit') != row['unit']:
            entry['reason'] = 'tolerance_unit_mismatch'
        else:
            if not {'absolute', 'relative'} & tolerance.keys():
                raise ValueError('Tolerance requires absolute or relative')
            absolute = _finite(tolerance.get('absolute', 0), 'absolute tolerance')
            relative = _finite(tolerance.get('relative', 0), 'relative tolerance')
            if min(absolute, relative) < 0:
                raise ValueError('Tolerances must be nonnegative')
            error = abs(float(result['value']) - float(row['value']))
            allowed = absolute + relative * abs(float(row['value']))
            entry.update(status='pass' if error <= allowed else 'fail',
                         observed=float(row['value']), simulated=float(result['value']),
                         absolute_error=error, allowed_error=allowed)
        checks.append(entry)
    if not validation:
        checks.append({'status': 'blocked', 'reason': 'no_held_out_observations'})
    return {'status': _status(checks), 'checks': checks,
            'calibration_observation_count': len(calibration),
            'validation_observation_count': len(validation),
            'scope': 'Only the supplied observed locations, intervals, metrics and classes'}
