"""Export canonical street lanes as tiled, identity-preserving Unreal GLBs.

This is offline asset preparation. It does not import, replace city geometry, or
configure World Partition. When supplied, netconvert lane shapes are authoritative
in XY; source-road elevations are interpolated independently and remain labelled.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
import xml.etree.ElementTree as ET

import numpy as np
import shapely
from shapely.geometry import LineString

from .engine_asset import export_glb

VERSION = 'dynacity-street-twin-unreal/1'


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _offset_path(path, distance):
    """Miter a path in its horizontal plane, retaining its source elevations."""
    points = np.array(path, dtype=float, copy=True)
    delta = np.diff(points[:, :2], axis=0)
    lengths = np.linalg.norm(delta, axis=1)
    if len(points) < 2 or (lengths <= 1e-8).any() or not np.isfinite(points).all():
        raise ValueError('Degenerate or non-finite lane path')
    tangent = delta / lengths[:, None]
    normals = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    offset = np.empty_like(points[:, :2])
    offset[0], offset[-1] = normals[0], normals[-1]
    if len(points) > 2:
        bisector = normals[:-1] + normals[1:]
        norm = np.linalg.norm(bisector, axis=1)
        if (norm <= 1e-8).any():
            raise ValueError('Lane path doubles back')
        bisector /= norm[:, None]
        denom = np.sum(bisector * normals[1:], axis=1)
        if (denom < .25).any():
            raise ValueError('Lane corner too sharp for a ribbon')
        offset[1:-1] = bisector / denom[:, None]
    points[:, :2] += distance * offset
    return points


def lane_ribbon(path, width_m, *, reference_xy=None):
    """Use identical sampled sections for centreline and both lane boundaries."""
    if not math.isfinite(width_m) or width_m <= 0:
        raise ValueError('Lane width must be positive and finite')
    if reference_xy is None:
        left = _offset_path(path, width_m / 2)
        right = _offset_path(path, -width_m / 2)
    else:
        # Offset the original SUMO bend vertices once. Adding an elevation
        # sample millimetres before a bend must not add another miter, which can
        # otherwise fold back over that short section. Subdivide the existing
        # boundaries linearly and apply the denser source elevation to them.
        reference = np.array([[*xy, 0.] for xy in reference_xy])
        reference_station = np.r_[0., np.cumsum(np.linalg.norm(np.diff(reference[:, :2], axis=0), axis=1))]
        line = LineString(reference_xy)
        sample_station = shapely.line_locate_point(line, shapely.points(np.asarray(path)[:, :2]))
        sides = []
        for distance in (width_m / 2, -width_m / 2):
            boundary = _offset_path(reference, distance)
            side = np.column_stack([np.interp(sample_station, reference_station, boundary[:, i]) for i in (0, 1)])
            sides.append(np.column_stack([side, np.asarray(path)[:, 2]]))
        left, right = sides
    vertices = np.stack([left, right], axis=1).reshape(-1, 3)
    a = np.arange(len(left) - 1) * 2
    faces = np.stack([np.column_stack([a, a + 1, a + 2]),
                      np.column_stack([a + 1, a + 3, a + 2])], axis=1).reshape(-1, 3)
    triangles = vertices[faces]
    cross = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])[:, 2]
    if (cross <= 1e-10).any():
        raise ValueError('Folded or degenerate lane ribbon')
    return vertices.round(6).tolist(), faces.tolist()


def _elevate(xy, path, *, spacing_m=5.):
    """Retain source slope breaks while sampling the exact SUMO XY polyline.

    SUMO may encode a straight lane with only two points even when source ground
    rises between them. Insert source vertices projected onto that lane, along
    with samples at most five metres apart, before interpolating known source Z.
    Existing lane vertices and endpoints are retained, including junction trims.
    """
    points = np.asarray(path, dtype=float)
    line = LineString(xy)
    if not math.isfinite(spacing_m) or spacing_m <= 0 or line.length <= 0:
        raise ValueError('Invalid elevation sampling spacing or lane length')
    stations = [(0., 2)]
    distance = 0.
    for a, b in zip(xy, xy[1:]):
        length = math.dist(a, b)
        stations.extend((float(s), 0) for s in np.linspace(distance, distance + length,
                                    max(1, math.ceil(length / spacing_m)) + 1)[1:-1])
        distance += length
        stations.append((distance, 2))
    stations.extend((float(s), 1) for s in shapely.line_locate_point(line, shapely.points(points[:, :2])))
    ordered = []
    cluster = []
    for station in sorted(stations):
        if cluster and station[0] - cluster[-1][0] > 1e-4:
            ordered.append(max(cluster, key=lambda item: item[1])[0])
            cluster = []
        cluster.append(station)
    if cluster:
        ordered.append(max(cluster, key=lambda item: item[1])[0])
    samples = shapely.get_coordinates(shapely.line_interpolate_point(line, np.asarray(ordered)))
    starts = points[:-1]
    deltas = np.diff(points, axis=0)
    length2 = np.sum(deltas[:, :2] ** 2, axis=1)
    usable = length2 > 1e-12
    starts, deltas, length2 = starts[usable], deltas[usable], length2[usable]
    if not len(starts):
        raise ValueError('No source elevation segment')
    result = []
    for x, y in samples:
        t = np.clip(np.sum((np.array([x, y]) - starts[:, :2]) * deltas[:, :2], axis=1) / length2, 0, 1)
        projected = starts[:, :2] + t[:, None] * deltas[:, :2]
        best = int(np.argmin(np.sum((projected - [x, y]) ** 2, axis=1)))
        z = float(starts[best, 2] + t[best] * deltas[best, 2])
        result.append([float(x), float(y), z])
    return result


def _net_lanes(net_xml, origin):
    root = ET.parse(net_xml).getroot()
    location = root.find('location')
    if location is None or 'netOffset' not in location.attrib:
        raise ValueError('SUMO network must declare netOffset')
    offset = [float(v) for v in location.attrib['netOffset'].split(',')]
    if len(offset) != 2 or not all(math.isfinite(v) for v in offset):
        raise ValueError('Invalid SUMO netOffset')
    result = {}
    for edge in root.findall('edge'):
        if edge.get('function') == 'internal':
            continue
        for lane in edge.findall('lane'):
            key = edge.get('id'), int(lane.attrib['index'])
            if key in result:
                raise ValueError('Duplicate SUMO lane index')
            shape = [[float(v) for v in p.split(',')] for p in lane.attrib['shape'].split()]
            xy = [[p[0] - offset[0] - origin[0], p[1] - offset[1] - origin[1]] for p in shape]
            if len(xy) < 2 or not np.isfinite(xy).all():
                raise ValueError('Invalid SUMO lane shape')
            result[key] = {'id': lane.attrib['id'], 'xy': xy,
                           'width_m': float(lane.get('width', '3.2'))}
    return result


def build_unreal_scenes(twin, *, net_xml=None):
    """Return per-owner-tile scenes and honest omitted-lane records.

    Whole roads belong to one tile, avoiding seam cuts; tile bounds may therefore
    overlap. Ownership is an asset-build division, not installed runtime streaming.
    """
    coordinates = dict(twin['coordinates'])
    origin = coordinates['origin_utm_m']
    origin = list(origin) + [0.] * (3 - len(origin))
    actual = _net_lanes(net_xml, origin) if net_xml is not None else None
    roads = {r['id']: r for r in twin['roads']}
    scenes, omitted = {}, []
    limitations = [
        'Offline road-lane assets; World Partition and HLODs are not configured.',
        'Internal SUMO junction links and junction surface polygons are not exported.',
        'Source elevation is interpolated across lane width; camber and kerbs are unknown.',
        'Asphalt material is illustrative; no surveyed markings or street furniture are generated.',
        ('Lane XY follows the supplied netconvert network.' if actual is not None else
         'Lane XY is schematic offset geometry; supply net_xml for actual SUMO lane paths.'),
    ]
    for tile in sorted(twin['tiles'], key=lambda t: t['id']):
        scene = {'schema': 'dynacity-engine-scene/1', 'coordinates': coordinates,
                 'meshes': [], 'roads': [], 'trees': [], 'bounds_lonlat': None,
                 'limitations': limitations}
        for road_id in sorted(tile['road_ids']):
            road = roads[road_id]
            path = road['path']
            for edge in road['edges']:
                for lane in edge['lanes']:
                    identifier = lane['id']
                    base = {'road_id': road_id, 'edge_id': edge['id'], 'lane_id': identifier}
                    if not path or any(len(p) != 3 or p[2] is None for p in path):
                        omitted.append({**base, 'reason': 'unknown source elevation'})
                        continue
                    try:
                        width = float(lane['width_m'])
                        sumo_lane_id = None
                        if actual is not None:
                            sumo_edge = edge.get('sumo_edge_id', f"{road['source_index']}:{edge['direction']}")
                            match = actual.get((sumo_edge, int(lane['index'])))
                            if match is None:
                                omitted.append({**base, 'reason': 'lane missing from SUMO network'})
                                continue
                            if not math.isclose(width, match['width_m'], abs_tol=1e-6):
                                omitted.append({**base, 'reason': 'SUMO lane width differs from canonical width'})
                                continue
                            width = match['width_m']
                            centre = _elevate(match['xy'], path)
                            sumo_lane_id = match['id']
                        else:
                            count = int(road['lane_count_per_direction'])
                            distance = (-(count - int(lane['index']) - .5) * width if road['oneway'] == 0 else
                                        (-count / 2 + int(lane['index']) + .5) * width)
                            directed = path if edge['direction'] == 1 else list(reversed(path))
                            centre = _offset_path(directed, distance).tolist()
                        vertices, triangles = lane_ribbon(centre, width,
                            reference_xy=match['xy'] if actual is not None else None)
                    except (ValueError, TypeError) as exc:
                        omitted.append({**base, 'reason': str(exc)})
                        continue
                    metadata = {**base, 'sumo_lane_id': sumo_lane_id,
                                'width_m': width, 'evidence': road.get('evidence', {}),
                                'lane_path_local_m': centre,
                                'xy_source': 'SUMO netconvert' if actual is not None else 'schematic offset'}
                    scene['meshes'].append({'id': identifier, 'kind': 'road',
                                             'vertices': vertices, 'triangles': triangles,
                                             'metadata': metadata})
                    scene['roads'].append({'id': identifier, 'path': centre, **base})
        # The shared GLB exporter combines a road's lanes into one mesh and
        # retains its first metadata record. Carry every lane binding there,
        # including exact ranges in that combined mesh, so no identity is lost.
        grouped = {}
        for mesh in scene['meshes']:
            grouped.setdefault(mesh['metadata']['road_id'], []).append(mesh)
        for members in grouped.values():
            bindings, vertex_offset, triangle_offset = [], 0, 0
            for mesh in members:
                metadata = mesh['metadata']
                bindings.append({'lane_id': metadata['lane_id'], 'edge_id': metadata['edge_id'],
                                 'sumo_lane_id': metadata['sumo_lane_id'],
                                 'first_vertex': vertex_offset, 'vertex_count': len(mesh['vertices']),
                                 'first_triangle': triangle_offset, 'triangle_count': len(mesh['triangles'])})
                vertex_offset += len(mesh['vertices'])
                triangle_offset += len(mesh['triangles'])
            for mesh in members:
                mesh['metadata']['lane_bindings'] = bindings
        scenes[tile['id']] = scene
    return scenes, omitted


def export_street_twin_unreal(twin, output_dir, *, net_xml=None):
    """Write content-addressed GLBs and a deterministic manifest; reuse intact files.

    Existing generated assets are never removed. Only this exporter manifest is
    refreshed. Content verification prevents silently reusing a corrupted asset.
    """
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    scenes, omitted = build_unreal_scenes(twin, net_xml=net_xml)
    entries = []
    for tile_id, scene in sorted(scenes.items()):
        signature = _digest({'version': VERSION, 'scene': scene})
        entry = {'tile_id': tile_id, 'content_hash': signature,
                 'lane_count': len(scene['meshes']), 'asset': None,
                 'lane_ids': [m['id'] for m in scene['meshes']]}
        if scene['meshes']:
            # Content-only filename avoids trusting tile IDs as filesystem paths.
            asset = output / f'street-{signature}.glb'
            checksum = asset.with_suffix('.glb.sha256')
            if asset.exists():
                if not checksum.exists() or checksum.read_text().strip() != hashlib.sha256(asset.read_bytes()).hexdigest():
                    raise ValueError(f'Existing generated asset failed checksum: {asset.name}')
            else:
                export_glb(scene, asset, include_calibration=False)
                checksum.write_text(hashlib.sha256(asset.read_bytes()).hexdigest(), encoding='ascii')
            entry.update(asset=asset.name, sha256=checksum.read_text().strip(),
                         triangle_count=sum(len(m['triangles']) for m in scene['meshes']),
                         lanes=[m['metadata'] for m in scene['meshes']])
        entries.append(entry)
    manifest = {'schema': VERSION, 'coordinates': twin['coordinates'],
                'twin_content_hash': twin.get('content_hash', _digest(twin)),
                'asset_coordinates': {'format': 'glTF 2.0 binary', 'units': 'metres',
                                      'axes': 'X east, Y up, Z south',
                                      'placement': 'All assets share the canonical origin; use identity relative transforms.',
                                      'unreal_axes': 'X east, Y south, Z up; centimetres'},
                'geometry_mode': 'netconvert lanes' if net_xml is not None else 'schematic lanes',
                'tiles': entries, 'omitted': omitted,
                'limitations': next(iter(scenes.values()))['limitations'] if scenes else [],
                'runtime_streaming_configured': False}
    path = output / 'street-twin-unreal.json'
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False) + '\n', encoding='utf-8')
    return manifest
