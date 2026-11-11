"""Source-linked street interchange in local projected metres.

OSM ways in TrafficModel have already lost their original endpoint node IDs.
Identity therefore uses way ID and source endpoints, stable across array reorder,
subsetting, elevation correction and evidence updates, but not source resplitting.
Lane reference lines are schematic until checked against SUMO's converted lanes.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import math

from shapely.geometry import LineString, box

from .traffic import LANE_WIDTH_M, lane_counts


SCHEMA = 'dynacity-street-twin/1'


def content_hash(value):
    """Hash a JSON value independently of mapping insertion order; reject NaN."""
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _finite(value, label):
    value = float(value)
    if not math.isfinite(value):
        raise ValueError(f'{label} must be finite')
    return value


def _evidence(status, source):
    return {'status': status, 'source_ids': [source] if source else []}


def _stable_payload(road):
    """Exclude process-local SUMO array bindings from geometry build hashes."""
    value = deepcopy(road)
    value.pop('source_index', None)
    for edge in value['edges']:
        edge.pop('sumo_edge_id', None)
        # Graph endpoint indices are runtime bindings; geometry remains in path.
        edge.pop('from_node', None)
        edge.pop('to_node', None)
        for lane in edge['lanes']:
            lane.pop('sumo_lane_id', None)
    return value


def build_street_twin(model, *, origin_utm_m=(0, 0, 0), tile_size_m=250,
                      road_indices=None, overrides=None, sources=None):
    """Compile whole roads without clipping or assigning invented elevation.

    Overrides are keyed by source array index (int or decimal string). Supported
    fields: ``path`` (absolute projected XYZ, with nullable Z),
    ``lanes_per_direction``, ``lane_width_m``, and field-level ``evidence``.
    Evidence statuses: unknown, mapped, assumed, modelled, surveyed. Every known
    claim must reference a declared source; unknown claims carry no sources.
    Supplied geometry or dimensions require explicit evidence for changed fields.
    The CRS is deliberately limited to the project's EPSG:32636 metre frame.
    """
    if model.crs != 'EPSG:32636':
        raise ValueError('Street twin requires projected metre CRS EPSG:32636')
    origin = [_finite(x, 'origin') for x in origin_utm_m]
    if len(origin) != 3:
        raise ValueError('origin_utm_m must contain three values')
    size = _finite(tile_size_m, 'tile_size_m')
    if size <= 0:
        raise ValueError('tile_size_m must be positive')
    indices = list(range(len(model))) if road_indices is None else list(road_indices)
    if len(set(indices)) != len(indices) or any(not isinstance(i, int) or not 0 <= i < len(model) for i in indices):
        raise ValueError('road_indices must be unique valid source indices')
    declarations = {
        'traffic_model': {'id': 'traffic_model', 'kind': 'mapped', 'title': str(model.source),
                          'as_of': None, 'limitations': 'Source map date and positional accuracy are undeclared.'},
        'lane_defaults': {'id': 'lane_defaults', 'kind': 'assumption', 'title': 'TrafficModel lane defaults',
                          'as_of': None, 'limitations': 'Class defaults and rounding are not measured lane counts.'},
        'lane_width_default': {'id': 'lane_width_default', 'kind': 'assumption', 'title': 'SUMO export lane width',
                               'as_of': None, 'limitations': 'Uniform 3.2 metre lane width; not surveyed.'},
    }
    for source in sources or []:
        source = deepcopy(source)
        identifier = source.get('id')
        if not isinstance(identifier, str) or not identifier or identifier in declarations:
            raise ValueError('Source IDs must be unique nonempty strings')
        declarations[identifier] = source
    overrides = overrides or {}
    selected_keys = {str(i) for i in indices}
    if any(str(key) not in selected_keys for key in overrides):
        raise ValueError('Override references a road outside the selected source indices')
    counts = lane_counts(model)
    roads, nodes, tile_roads, identifiers = [], {}, {}, set()
    for i in indices:
        override = overrides.get(i, overrides.get(str(i), {}))
        if set(override) - {'path', 'lanes_per_direction', 'lane_width_m', 'evidence'}:
            raise ValueError('Unknown street override field')
        source_path = model.geometry[i]
        if len(source_path) < 2:
            raise ValueError('Road geometry needs at least two points')
        endpoints = sorted([[round(_finite(c, 'source coordinate'), 3) for c in p[:2]]
                            for p in (source_path[0], source_path[-1])])
        identifier = f'osm-way/{int(model.way_id[i])}/segment/{content_hash(endpoints)[:16]}'
        if identifier in identifiers:
            raise ValueError('Ambiguous source road identity: duplicate way and endpoints')
        identifiers.add(identifier)
        path = override.get('path', [[*p[:2], None] for p in source_path])
        if len(path) < 2 or any(len(p) != 3 for p in path):
            raise ValueError('Override path requires at least two XYZ points')
        absolute = [[_finite(p[0], 'path x'), _finite(p[1], 'path y'),
                     None if p[2] is None else _finite(p[2], 'path z')] for p in path]
        line = LineString([p[:2] for p in absolute])
        if line.length <= 0:
            raise ValueError('Road geometry must have positive horizontal length')
        local = [[round(p[0]-origin[0], 6), round(p[1]-origin[1], 6),
                  None if p[2] is None else round(p[2]-origin[2], 6)] for p in absolute]
        count = override.get('lanes_per_direction', int(counts[i]))
        if isinstance(count, bool) or not isinstance(count, int) or count < 1:
            raise ValueError('lanes_per_direction must be a positive integer')
        width = _finite(override.get('lane_width_m', LANE_WIDTH_M), 'lane_width_m')
        if width <= 0:
            raise ValueError('lane_width_m must be positive')
        tagged = float(model.lanes[i]) if model.lanes is not None else 0
        evidence = {'geometry': _evidence('mapped', 'traffic_model'),
                    'elevation': _evidence('unknown', None),
                    'lane_count': _evidence('mapped' if tagged == count else 'assumed',
                                            'traffic_model' if tagged == count else 'lane_defaults'),
                    'lane_width': _evidence('assumed', 'lane_width_default')}
        supplied_evidence = override.get('evidence', {})
        required = ({'geometry', 'elevation'} if 'path' in override else set())
        required |= {'lane_count'} if 'lanes_per_direction' in override else set()
        required |= {'lane_width'} if 'lane_width_m' in override else set()
        if required - set(supplied_evidence):
            raise ValueError('Overrides require evidence for every changed field')
        if set(supplied_evidence) - set(evidence):
            raise ValueError('Unknown evidence field')
        evidence.update(deepcopy(supplied_evidence))
        for field, claim in evidence.items():
            if claim.get('status') not in {'unknown', 'mapped', 'assumed', 'modelled', 'surveyed'}:
                raise ValueError('Invalid evidence status')
            refs = claim.get('source_ids')
            if not isinstance(refs, list) or any(ref not in declarations for ref in refs):
                raise ValueError('Evidence references an undeclared source')
            if (claim['status'] == 'unknown') != (not refs):
                raise ValueError('Known evidence requires sources; unknown evidence has no sources')
            if claim['status'] == 'surveyed' and any(declarations[ref].get('kind') != 'survey' for ref in refs):
                raise ValueError('Surveyed claims require survey sources')
            if field == 'elevation' and any(p[2] is None for p in absolute) and claim['status'] != 'unknown':
                raise ValueError('Incomplete elevation must remain unknown')
        oneway = int(model.oneway[i])
        if oneway not in (-1, 0, 1):
            raise ValueError('oneway must be -1, 0 or 1')
        edges = []
        for direction in (1, -1):
            if oneway == -direction:
                continue
            edge_id = f'{identifier}:{direction}'
            a, b = (int(model.u[i]), int(model.v[i])) if direction == 1 else (int(model.v[i]), int(model.u[i]))
            edges.append({'id': edge_id, 'direction': direction, 'from_node': str(a), 'to_node': str(b),
                          'sumo_edge_id': f'{i}:{direction}', 'lanes': [
                              {'id': f'{edge_id}:{k}', 'index': k, 'width_m': width,
                               'sumo_lane_id': f'{i}:{direction}_{k}'} for k in range(count)]})
        for node, point in ((int(model.u[i]), local[0]), (int(model.v[i]), local[-1])):
            key = str(node)
            if key not in nodes:
                nodes[key] = {'id': key, 'path': point}
            elif nodes[key]['path'][:2] != point[:2]:
                raise ValueError('Shared graph node has inconsistent projected endpoints')
        midpoint = line.interpolate(.5, normalized=True)
        owner = f'{math.floor(midpoint.x/size)}:{math.floor(midpoint.y/size)}'
        minx, miny, maxx, maxy = line.bounds
        touches = [f'{x}:{y}' for x in range(math.floor(minx/size), math.floor(maxx/size)+1)
                   for y in range(math.floor(miny/size), math.floor(maxy/size)+1)
                   if line.intersects(box(x*size, y*size, (x+1)*size, (y+1)*size))]
        road = {'id': identifier, 'source_index': i, 'osm_way_id': int(model.way_id[i]),
                'name': str(model.name[i]) if model.name else '', 'highway': str(model.highway[i]),
                'path': local, 'oneway': oneway, 'lane_count_per_direction': count,
                'lane_width_m': width, 'length_m': round(line.length, 6), 'edges': edges,
                'evidence': evidence, 'owner_tile_id': owner, 'tile_ids': sorted(touches)}
        roads.append(road)
        tile_roads.setdefault(owner, []).append(road)
    roads.sort(key=lambda r: r['id'])
    coordinates = {'crs': model.crs, 'origin_utm_m': origin, 'axes': 'X east, Y north, Z up',
                   'units': 'metres', 'vertical_datum': 'undeclared'}
    tiles = []
    for tile_id, members in sorted(tile_roads.items()):
        members.sort(key=lambda r: r['id'])
        payload = {'coordinates': coordinates, 'tile_size_m': size,
                   'roads': [_stable_payload(r) for r in members]}
        used = {ref for r in members for claim in r['evidence'].values() for ref in claim['source_ids']}
        payload['sources'] = [declarations[ref] for ref in sorted(used)]
        tiles.append({'id': tile_id, 'road_ids': [r['id'] for r in members],
                      'content_hash': content_hash(payload)})
    result = {'schema': SCHEMA, 'coordinates': coordinates, 'tile_size_m': size,
              'sources': [declarations[key] for key in sorted(declarations)], 'roads': roads,
              'nodes': sorted(nodes.values(), key=lambda n: n['id']), 'tiles': tiles,
              'limitations': [
                  'Road IDs use OSM way and source endpoint coordinates; source resplitting changes identity.',
                  'Unknown elevation is null; no flat or invented ground fallback.',
                  'Lane counts may use class defaults; widths default to assumed 3.2 metres.',
                  'Lane offsets are schematic until checked against converted SUMO lane geometry.',
                  'Whole roads belong to midpoint tiles and may extend into neighbouring tiles.',
                  'Geometry compilation does not calibrate demand or validate observed traffic.']}
    result['content_hash'] = content_hash(result)
    return result
