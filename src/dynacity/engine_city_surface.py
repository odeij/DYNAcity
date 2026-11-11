"""Prepare one common terrain/road layer without cutting roads at district borders.

Private source-derived assets only; this does not import a level or configure
World Partition. Surface styling and unmatched bridge lifts are illustrative.
"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct

import numpy as np
import shapely
from shapely.geometry import LineString, Polygon
from shapely.geometry.polygon import orient
from shapely.ops import substring

from .beirut001_pack import Beirut001Pack, bridge_decks
from .engine_asset import export_glb
from .engine_street_context import TerrainSurface
from .terrain import SRC_PACK, SRC_NONE, merge_ground, GroundModel
from .traffic import LANE_WIDTH_M, TrafficModel, lane_counts, road_widths
from .viewer import utm_to_lonlat
from . import engine_asset, engine_street_context

VERSION = 'shared-city-surface/4'
# Asphalt sits just above the source ground; pavements stand one kerb higher.
# Kerb height and pavement widths are illustrative, not surveyed.
ROAD_LIFT, KERB_M = .12, .15
FAST_ROADS = {'motorway', 'motorway_link', 'trunk', 'trunk_link'}
# Paint sits a hair above the asphalt. Widths and dash lengths are illustrative.
MARK_LIFT, MARK_WIDTH_M, EDGE_INSET_M = .02, .12, .3
DASH_M, GAP_M = 3., 6.
MARKED_ROADS = {'motorway', 'trunk', 'primary', 'secondary', 'tertiary', 'unclassified', 'residential'}


def is_marked(highway):
    """Carriageway classes that carry paint; service, footway and living streets do not."""
    return str(highway).removesuffix('_link') in MARKED_ROADS


def _dashes(line, dash=DASH_M, gap=GAP_M):
    if line.length < dash: return []
    count = int((line.length+gap)//(dash+gap))
    pad = (line.length-(count*dash+(count-1)*gap))/2 if count else 0
    return [substring(line, pad+n*(dash+gap), pad+n*(dash+gap)+dash) for n in range(max(count, 1))]


def lane_marking_shape(line, lanes, oneway, width, clip):
    """Paint polygon for one street-level carriageway, clipped to the asphalt this road owns.

    Two-way roads get a dashed centre line; further lanes in a direction get dashed
    dividers; every carriageway gets solid edge lines. `lanes` counts one direction.
    """
    half = width/2; strips = []
    def offset(distance):
        part = line.offset_curve(distance) if distance else line
        return list(part.geoms) if hasattr(part, 'geoms') else [part]
    def paint(distance, solid):
        for part in offset(distance):
            if part.is_empty or part.length < .5: continue
            for piece in ([part] if solid else _dashes(part)):
                if piece.length > .01: strips.append(piece.buffer(MARK_WIDTH_M/2, cap_style='flat'))
    if oneway:
        for k in range(1, int(lanes)): paint(-half+k*LANE_WIDTH_M, False)
    else:
        paint(0., False)
        for k in range(1, int(lanes)):
            for side in (1, -1): paint(side*k*LANE_WIDTH_M, False)
    for side in (1, -1): paint(side*(half-EDGE_INSET_M), True)
    if not strips: return Polygon()
    return shapely.union_all(strips).intersection(clip)


def _scene(origin):
    lon, lat = utm_to_lonlat(origin[0], origin[1], 36, True)
    return {'schema': 'dynacity-engine-scene/1', 'coordinates': {
        'crs': 'EPSG:32636', 'origin_utm_m': list(origin), 'origin_lonlat': [lon, lat],
        'axes': 'X east, Y north, Z up', 'units': 'metres',
        'unreal_axes': 'X east, Y south, Z up; centimetres',
        'vertical_datum': 'source model datum undeclared'},
        'meshes': [], 'trees': [], 'roads': [], 'bounds_lonlat': None,
        'limitations': ['Private Beirut 001 source; inferred alignment, not surveyed.',
            'Road widths, pavement widths, kerb height, surface materials and unmatched bridge/ramp lifts are illustrative.',
            'Missing ground remains a gap. Underground roads are excluded.',
            'Where the model ground is absent, an offset Copernicus GLO-30 estimate may stand in (road ground_source glo30/blended); lower accuracy, not surveyed.',
            'Shared geometry assets only; no World Partition, vehicle physics or live traffic.']}


def terrain_mesh(ground, origin):
    """Keep original grid elevations and omit any cell touching an unknown sample."""
    rows, cols = ground.ground.shape
    cc, rr = np.meshgrid(np.arange(cols), np.arange(rows))
    z = ground.ground
    vertices = np.stack([ground.x0 + (cc+.5)*ground.cell_m,
                         ground.y0 - (rr+.5)*ground.cell_m,
                         np.nan_to_num(z, nan=0)], axis=-1).reshape(-1, 3)
    good = (np.isfinite(z[:-1, :-1]) & np.isfinite(z[1:, :-1]) &
            np.isfinite(z[:-1, 1:]) & np.isfinite(z[1:, 1:]))
    r, c = np.nonzero(good)
    a = r*cols+c; b = a+1; d = a+cols+1; e = a+cols
    faces = np.stack([np.stack([a,e,b], axis=1), np.stack([b,e,d], axis=1)],axis=1).reshape(-1,3)
    # Unused placeholders above cannot enter the GLB: exporter compacts used indices.
    return {'id': 'source-ground', 'kind': 'terrain',
        'vertices': (vertices-np.asarray(origin)).round(4).tolist(), 'triangles': faces.tolist(),
        'metadata': {'source': ground.source, 'cell_m': ground.cell_m,
                     'valid_cells': len(r), 'missing_cells': int(good.size-len(r))}}


def road_ribbon(line, width, ground, origin, start_lift=0., end_lift=0., spacing=5.):
    """Shared cross-sections avoid cracks between consecutive road segments."""
    if line.is_empty or line.length < .01:
        raise ValueError('degenerate road')
    distances = np.linspace(0, line.length, max(2, math.ceil(line.length/spacing)+1))
    xy = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))[:, :2]
    # Remove any duplicate source/sample points before computing tangents.
    keep = np.r_[True, np.linalg.norm(np.diff(xy, axis=0), axis=1) > 1e-8]
    xy = xy[keep]; distances = distances[keep]
    delta = np.diff(xy, axis=0)
    tangent = delta/np.linalg.norm(delta, axis=1)[:, None]
    normals = np.column_stack([-tangent[:, 1], tangent[:, 0]])
    offsets = np.empty_like(xy); offsets[0] = normals[0]; offsets[-1] = normals[-1]
    if len(xy) > 2:
        bisector = normals[:-1]+normals[1:]
        norm = np.linalg.norm(bisector, axis=1)
        bisector = np.divide(bisector, norm[:, None], out=normals[1:].copy(), where=norm[:,None]>1e-8)
        denom = np.maximum(np.sum(bisector*normals[1:], axis=1), .5)
        offsets[1:-1] = bisector/denom[:,None]
    offsets *= width/2
    edges = np.stack([xy+offsets, xy-offsets], axis=1)
    z = ground.sample(edges[..., 0].ravel(), edges[..., 1].ravel()).reshape(-1, 2)
    centre_z = ground.sample(xy[:,0], xy[:,1])
    if not np.isfinite(z).all() or not np.isfinite(centre_z).all():
        raise ValueError('missing source ground')
    lift = start_lift+(end_lift-start_lift)*distances/line.length+ROAD_LIFT
    vertices = np.concatenate([edges, (z+lift[:,None])[...,None]], axis=2).reshape(-1,3)
    a = np.arange(len(xy)-1)*2
    faces = np.stack([np.stack([a,a+1,a+2],axis=1),
                      np.stack([a+1,a+3,a+2],axis=1)],axis=1).reshape(-1,3)
    # Correct rare sharp-corner orientation while retaining the shared cross-section.
    cross = np.cross(vertices[faces[:,1]]-vertices[faces[:,0]], vertices[faces[:,2]]-vertices[faces[:,0]])
    faces[cross[:,2]<0] = faces[cross[:,2]<0][:,::-1]
    path = np.column_stack([xy, centre_z+lift])-np.asarray(origin)
    return (vertices-np.asarray(origin)).round(4).tolist(), faces.tolist(), path.round(4).tolist()


def ground_source(ground, line, spacing=5.):
    """'pack', 'glo30' or 'blended' (mixed) for the ground a road line sits on."""
    distances = np.linspace(0, line.length, max(2, math.ceil(line.length/spacing)+1))
    xy = shapely.get_coordinates(shapely.line_interpolate_point(line, distances))[:, :2]
    codes = ground.source_at(xy[:, 0], xy[:, 1])
    codes = codes[codes != SRC_NONE]
    if len(codes) == 0 or (codes == SRC_PACK).all(): return 'pack'
    return 'glo30' if not (codes == SRC_PACK).any() else 'blended'


def pavement_width(highway):
    """Same class allowance as the browser street view; not a sidewalk survey."""
    if highway in FAST_ROADS: return 0.
    return 1.6 if highway in ('living_street', 'residential') else 2.4


def kerb_walls(polygon, surface, origin, top, bottom=-.05, spacing=2.):
    """Vertical faces around a raised slab, facing out of it, sampled on the source terrain."""
    vertices, faces = [], []
    parts = list(polygon.geoms) if hasattr(polygon, 'geoms') else [polygon]
    for part in parts:
        if not isinstance(part, Polygon) or part.is_empty: continue
        # Counter-clockwise shells and clockwise holes: the outside of the slab is on the right.
        part = shapely.segmentize(orient(part, 1.), spacing)
        for ring in (part.exterior, *part.interiors):
            points = list(ring.coords)
            heights = [surface.sample(x, y) for x, y in points]
            for (a, b), (za, zb) in zip(zip(points, points[1:]), zip(heights, heights[1:])):
                if za is None or zb is None: continue
                start = len(vertices)
                vertices.extend([[a[0], a[1], za+bottom], [b[0], b[1], zb+bottom],
                                 [b[0], b[1], zb+top], [a[0], a[1], za+top]])
                faces.extend([[start, start+1, start+2], [start, start+2, start+3]])
    if vertices: vertices = (np.asarray(vertices)-np.asarray(origin)).round(4).tolist()
    return vertices, faces


def _low_ends(line, width, start, end, below=.5):
    """Footprint of a ramp's ends that stay close to the ground, where it meets street level."""
    pieces = []
    for lift, other, reverse in ((start, end, False), (end, start, True)):
        if lift >= below: continue
        reach = line.length if other <= below else line.length*(below-lift)/(other-lift)
        part = substring(line, line.length-reach, line.length) if reverse else substring(line, 0, reach)
        if part.length > .01: pieces.append(part.buffer(width/2, cap_style='flat'))
    return pieces


def _claim(polygons, order, extra=None):
    """Give each overlap to the earliest road in `order`, so the parts never stack."""
    tree = shapely.STRtree(polygons); rank = {k: n for n, k in enumerate(order)}
    blockers = [p for p in (extra or []) if not p.is_empty]
    block_tree = shapely.STRtree(blockers) if blockers else None
    pieces = {}
    for k in order:
        shape = polygons[k]
        earlier = [polygons[j] for j in tree.query(shape, predicate='intersects') if rank.get(j, len(order)) < rank[k]]
        if block_tree is not None:
            earlier += [blockers[j] for j in block_tree.query(shape, predicate='intersects')]
        if earlier: shape = shape.difference(shapely.union_all(earlier))
        pieces[k] = shapely.make_valid(shape)
    return pieces


def _drape(surface, shape, origin, lift):
    vertices, faces = [], []
    for part in (list(shape.geoms) if hasattr(shape, 'geoms') else [shape]):
        if not isinstance(part, Polygon) or part.area < .01: continue
        v, f, _ = surface.drape(part, origin, lift)
        faces.extend([[i+len(vertices) for i in face] for face in f]); vertices.extend(v)
    return vertices, faces


def build_surface_scenes(pack, traffic, origin, fallback_ground=None, extrapolate_m=400.):
    """`fallback_ground` (a GLO-30 GroundModel) fills where the pack ground has no samples."""
    if pack.meta.get('crs') != 'EPSG:32636' or traffic.crs != 'EPSG:32636':
        raise ValueError('Source coordinates must match EPSG:32636')
    if pack.ground is None:
        raise ValueError('Source ground is required')
    if len(origin) != 3 or not np.isfinite(origin).all():
        raise ValueError('A finite common origin is required')
    terrain, roads, structures = _scene(origin), _scene(origin), _scene(origin)
    ground = pack.ground
    if fallback_ground is not None:
        ground = merge_ground(pack.ground, fallback_ground, extrapolate_m=extrapolate_m)
    terrain['meshes'].append(terrain_mesh(ground, origin))
    decks = bridge_decks(pack, traffic); node_lift = {}; skipped = []
    widths = road_widths(traffic); lane_each = lane_counts(traffic)
    oneway = np.asarray(traffic.oneway)
    levels = traffic.level if traffic.level is not None else np.zeros(len(traffic.geometry), dtype=int)
    for i, level in enumerate(levels):
        if level <= 0: continue
        lift = decks.get(i, 5.5*int(level))
        for node in (int(traffic.u[i]), int(traffic.v[i])):
            node_lift[node] = max(node_lift.get(node, 0.), lift)
    street, raised_low = [], []
    for i, coords in enumerate(traffic.geometry):
        if levels[i] < 0:
            skipped.append({'road_id': i, 'reason': 'underground road'}); continue
        start, end = node_lift.get(int(traffic.u[i]), 0.), node_lift.get(int(traffic.v[i]), 0.)
        line = LineString(coords)
        try:
            vertices, faces, path = road_ribbon(line, float(widths[i]), ground, origin, start, end)
        except ValueError as exc:
            skipped.append({'road_id': i, 'reason': str(exc)}); continue
        height_source = ('matched model deck' if i in decks else
                         'illustrative ramp/level offset' if start or end else 'source ground')
        metadata = {'road_id': i, 'source_way': int(traffic.way_id[i]), 'height_source': height_source,
                    'ground_source': ground_source(ground, line), 'level': int(levels[i]), 'width_m': float(widths[i]),
                    'name': traffic.name[i] if traffic.name else ''}
        roads['roads'].append({'id': i, 'path': path, **metadata})
        if start or end:
            # Bridges and their ramps keep their own graded ribbon.
            roads['meshes'].append({'id': f'road:{i}', 'kind': 'road',
                                   'vertices': vertices, 'triangles': faces, 'metadata': metadata})
            raised_low += _low_ends(line, float(widths[i]), start, end)
        else:
            street.append((i, line, float(widths[i]), pavement_width(traffic.highway[i]), metadata))
    # Street-level roads share one carriageway: round ends fill junction corners,
    # and every overlap belongs to one road, so no two surfaces stack.
    surface = TerrainSurface(ground)
    carriage = [line.buffer(width/2, quad_segs=4) for _, line, width, _, _ in street]
    # Wider roads keep the junction box; ties go to the lower road ID.
    order = sorted(range(len(street)), key=lambda k: (-street[k][2], street[k][0]))
    asphalt = _claim(carriage, order, raised_low)
    walks = [line.buffer(width/2+walk, quad_segs=4) if walk else Polygon() for _, line, width, walk, _ in street]
    walked = [k for k in order if walks[k].area]
    pavement = _claim(walks, walked, carriage+raised_low) if walked else {}
    for k, (i, _, _, _, metadata) in enumerate(street):
        vertices, faces = _drape(surface, asphalt[k], origin, ROAD_LIFT)
        if faces:
            roads['meshes'].append({'id': f'road:{i}', 'kind': 'road',
                                   'vertices': vertices, 'triangles': faces, 'metadata': metadata})
            if is_marked(traffic.highway[i]) and not asphalt[k].is_empty:
                paint = lane_marking_shape(street[k][1], lane_each[i], oneway[i] != 0, street[k][2], asphalt[k])
                m_vertices, m_faces = _drape(surface, paint, origin, ROAD_LIFT+MARK_LIFT)
                if m_faces:
                    roads['meshes'].append({'id': f'marking:{i}', 'kind': 'marking', 'vertices': m_vertices,
                        'triangles': m_faces, 'metadata': {**metadata, 'paint': 'centre dashed, edge solid; illustrative'}})
        if k not in pavement or pavement[k].is_empty: continue
        vertices, faces = _drape(surface, pavement[k], origin, ROAD_LIFT+KERB_M)
        walls, wall_faces = kerb_walls(pavement[k], surface, origin, ROAD_LIFT+KERB_M)
        faces += [[n+len(vertices) for n in face] for face in wall_faces]; vertices += walls
        if faces:
            roads['meshes'].append({'id': f'pavement:{i}', 'kind': 'pavement', 'vertices': vertices,
                                   'triangles': faces, 'metadata': {**metadata, 'kerb_m': KERB_M}})
    raw = np.asarray(getattr(pack, 'bridge_triangles', np.zeros((0,3,3))), dtype=float)
    valid = raw[np.isfinite(raw).all(axis=(1,2))]
    if len(valid):
        structures['meshes'].append({'id': 'beirut001:transport-structures', 'kind': 'concrete',
            'vertices': (valid.reshape(-1,3)-np.asarray(origin)).round(4).tolist(),
            'triangles': np.arange(len(valid)*3).reshape(-1,3).tolist(),
            'metadata': {'source': 'Beirut 001 transport structures, January 2020',
                         'geometry': 'original source triangles; no invented piers',
                         'triangle_count': len(valid)}})
    return {'terrain': terrain, 'roads': roads, 'structures': structures}, skipped


def prepare_surface(pack_path, traffic_path, origin, output, fallback_path=None):
    output = Path(output); output.mkdir(parents=True, exist_ok=True)
    digest = hashlib.sha256(VERSION.encode()+Path(__file__).read_bytes()+Path(engine_asset.__file__).read_bytes()
                            +Path(engine_street_context.__file__).read_bytes())
    for path in (pack_path, traffic_path, *([fallback_path] if fallback_path else [])):
        with Path(path).open('rb') as stream:
            while chunk := stream.read(1024*1024): digest.update(chunk)
    digest.update(json.dumps(list(origin)).encode())
    digest.update(b'fallback:'+(b'none' if not fallback_path else b'glo30')); signature = digest.hexdigest()
    manifest_path = output/(signature+'.json')
    if manifest_path.is_file():
        result = json.loads(manifest_path.read_text())
        valid = Path(result['roads_metadata']).is_file()
        for layer in result['layers']:
            asset = Path(layer['asset'])
            if not asset.is_file(): valid=False; break
            with asset.open('rb') as stream: header = stream.read(12)
            if len(header)!=12 or struct.unpack('<4sII',header)!=(b'glTF',2,asset.stat().st_size):
                valid=False; break
        if valid:
            result['cache_hit'] = True
            (output/'shared-world.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
            return result
    scenes, skipped = build_surface_scenes(Beirut001Pack.load(pack_path),TrafficModel.load(traffic_path),origin,
        GroundModel.load(fallback_path) if fallback_path else None)
    layers = []
    for name, scene in scenes.items():
        if not scene['meshes']: continue
        asset = output/(name+'-'+signature+'.glb'); temporary=asset.with_suffix('.glb.partial')
        if temporary.exists(): temporary.unlink()
        stats = export_glb(scene,temporary,include_calibration=name=='terrain'); temporary.replace(asset)
        layers.append({'name': name, 'asset': str(asset), 'stats': stats,
                       'meshes': len(scene['meshes']), 'calibration_nodes': name=='terrain'})
    road_metadata = output/('roads-'+signature+'.json')
    road_metadata.write_text(json.dumps({'coordinates': scenes['roads']['coordinates'],
        'roads': scenes['roads']['roads'], 'limitations': scenes['roads']['limitations']},separators=(',',':')),encoding='utf-8')
    result = {'schema': 'dynacity-shared-city-surface/1', 'signature': signature, 'cache_hit': False,
        'origin_utm_m': list(origin), 'layers': layers, 'roads_metadata': str(road_metadata),
        'source_roads': len(scenes['roads']['roads'])+len(skipped),
        'exported_roads': len(scenes['roads']['roads']), 'skipped_roads': skipped,
        'skipped_counts': dict(Counter(s['reason'] for s in skipped)),
        'ground_source_counts': dict(Counter(r['ground_source'] for r in scenes['roads']['roads'])),
        'fallback_ground': str(fallback_path) if fallback_path else None,
        'terrain': scenes['terrain']['meshes'][0]['metadata'],
        'limitations': scenes['terrain']['limitations']}
    manifest_path.write_text(json.dumps(result,indent=2),encoding='utf-8')
    (output/'shared-world.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--pack',required=True); parser.add_argument('--traffic',required=True)
    parser.add_argument('--origin-scene',required=True); parser.add_argument('--output',required=True)
    parser.add_argument('--fallback-terrain',help='GLO-30 .npz from `dynacity fetch-terrain`; fills where the pack ground is missing')
    args=parser.parse_args()
    origin=json.loads(Path(args.origin_scene).read_text())['coordinates']['origin_utm_m']
    result=prepare_surface(args.pack,args.traffic,origin,args.output,args.fallback_terrain)
    print(json.dumps({k:v for k,v in result.items() if k not in ('skipped_roads','limitations')},indent=2))


if __name__=='__main__': main()
