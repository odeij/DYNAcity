"""Prepare supplementary Overture scenery and a source-identifiable Unreal catalog.

Existing OSM features and ground cover win. Road topology is reference data only.
Downloads stay outside Git; this script never modifies an Unreal map.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path
import re
import math

import numpy as np
from pyproj import Transformer
import shapely
from shapely.geometry import box, shape, mapping
from shapely.ops import transform

from dynacity.engine_asset import export_glb
from dynacity.engine_street_assets import COLOURS, _colour_glb
from dynacity.engine_street_context import TerrainSurface
from dynacity.landscape import landscape_payload
from dynacity.terrain import GroundModel
import unicodedata


def normalize(text):
    return ' '.join(''.join(c if c.isalnum() else ' ' for c in
        unicodedata.normalize('NFKD', text).casefold() if not unicodedata.combining(c)).split())

KINDS = {'grass': 'grass', 'park': 'park', 'garden': 'garden', 'forest': 'wood',
         'wood': 'wood', 'scrub': 'scrub', 'shrubland': 'scrub', 'grassland': 'grass',
         'trees': 'wood', 'tree_cover': 'wood', 'herbaceous_vegetation': 'grass',
         'mangroves': 'wood', 'sand': 'sand', 'beach': 'sand', 'wetland': 'wetland',
         'pitch': 'pitch', 'playground': 'playground', 'cemetery': 'cemetery',
         'orchard': 'orchard', 'farmland': 'farmland', 'cropland': 'farmland',
         'village_green': 'village_green', 'water': 'water', 'river': 'water',
         'pond': 'water', 'lake': 'water', 'sea': 'water', 'ocean': 'water'}
KINDS['shrub'] = 'scrub'


def drape_fast(surface, polygon, origin, lift=.03):
    """Same terrain triangles as TerrainSurface.drape; vectorize complete triangles."""
    g = surface.ground
    xmin, ymin, xmax, ymax = polygon.bounds
    rows, cols = g.ground.shape
    c0 = max(0, math.floor((xmin-g.x0)/g.cell_m-.5))
    c1 = min(cols-2, math.floor((xmax-g.x0)/g.cell_m-.5))
    r0 = max(0, math.floor((g.y0-ymax)/g.cell_m-.5))
    r1 = min(rows-2, math.floor((g.y0-ymin)/g.cell_m-.5))
    vertices, faces, missing = [], [], 0
    shapely.prepare(polygon)
    for start_row in range(r0, r1+1, 24):
        rr, cc = np.meshgrid(np.arange(start_row, min(r1+1, start_row+24)), np.arange(c0, c1+1), indexing='ij')
        rr, cc = rr.ravel(), cc.ravel()
        z = np.stack([g.ground[rr, cc], g.ground[rr, cc+1], g.ground[rr+1, cc], g.ground[rr+1, cc+1]], axis=1)
        finite = np.isfinite(z).all(axis=1)
        missing += int((~finite).sum())
        rr, cc, z = rr[finite], cc[finite], z[finite]
        if not len(rr): continue
        x, y, s = g.x0+(cc+.5)*g.cell_m, g.y0-(rr+.5)*g.cell_m, g.cell_m
        xy = np.stack([np.column_stack([x, y]), np.column_stack([x+s, y]),
                       np.column_stack([x, y-s]), np.column_stack([x+s, y-s])], axis=1)
        for indices in ((0, 2, 1), (1, 2, 3)):
            coordinates, heights = xy[:, indices, :], z[:, indices]
            triangles = shapely.polygons(coordinates)
            complete = shapely.covers(polygon, triangles)
            selected = np.concatenate([coordinates[complete], heights[complete, :, None]], axis=2)
            selected -= np.asarray(origin)[None, None, :]
            selected[:, :, 2] += lift
            offset = len(vertices)
            vertices.extend(np.round(selected.reshape(-1, 3), 4).tolist())
            faces.extend((np.arange(selected.shape[0]*3).reshape(-1, 3)+offset).tolist())
            boundary = np.flatnonzero(~complete & shapely.intersects(polygon, triangles))
            for i in boundary:
                clipped = triangles[i].intersection(polygon)
                plane = np.linalg.solve(np.column_stack([coordinates[i], np.ones(3)]), heights[i])
                for piece in shapely.get_parts(clipped):
                    if piece.geom_type != 'Polygon' or piece.area < 1e-6: continue
                    for tri in shapely.get_parts(shapely.constrained_delaunay_triangles(piece)):
                        points = np.asarray(tri.exterior.coords)[:3]
                        zz = points@plane[:2]+plane[2]+lift
                        offset = len(vertices)
                        vertices.extend(np.round(np.column_stack([points, zz])-np.asarray(origin), 4).tolist())
                        a, b = points[1]-points[0], points[2]-points[0]
                        cross = a[0]*b[1]-a[1]*b[0]
                        faces.append([offset, offset+1, offset+2] if cross > 0 else [offset, offset+2, offset+1])
    return vertices, faces, missing


def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def source_ids(properties):
    result = []
    for source in properties.get('sources') or []:
        if 'openstreetmap' not in str(source.get('dataset', '')).lower(): continue
        match = re.match(r'([nwr])(\d+)', str(source.get('record_id') or ''))
        if match: result.append({'n': 'node', 'w': 'way', 'r': 'relation'}[match[1]] + '/' + match[2])
    return result


def label(properties):
    return (properties.get('names') or {}).get('primary') or properties.get('class') or properties.get('subtype') or properties['id']


def prepare(root, output, private):
    output.mkdir(parents=True, exist_ok=True)
    origin = read(private/'engine/city-batches/city-batches.json')['origin_utm_m']
    project = Transformer.from_crs(4326, 32636, always_xy=True).transform
    bbox = box(35.4693, 33.8288, 35.572, 33.9076)
    extent = transform(project, bbox)
    districts = read(private/'engine/coverage/beirut-districts.json')['groups']['features']
    # Match the existing municipal city production extent; keep surrounding records in the catalog.
    extent = extent.intersection(shapely.union_all([
        shapely.make_valid(transform(project, shape(f['geometry']))) for f in districts]))
    osm = landscape_payload(read(private/'traffic/osm-beirut-context-infrastructure-20261027.json'))
    existing_ids = {f['properties']['id'] for f in osm['features']}
    covers = []
    for f in osm['features']:
        geom = shape(f['geometry'])
        if geom.geom_type in ('Polygon', 'MultiPolygon'):
            covers.append(shapely.make_valid(transform(project, geom)))
    tree = shapely.STRtree(covers)
    surface = TerrainSurface(GroundModel.load(private/'engine/greenery-refresh/terrain/extended-visual-ground.npz'))
    # Existing rendered road surfaces and their lane widths also take precedence.
    from dynacity.traffic import TrafficModel, road_widths
    from shapely.geometry import LineString
    traffic = TrafficModel.load(private/'traffic/beirut-traffic-20261024.json')
    roads = [LineString(g).buffer(float(w)/2+.3, cap_style=2)
             for g, w, level in zip(traffic.geometry, road_widths(traffic), traffic.level) if int(level) == 0]
    road_tree = shapely.STRtree(roads)
    accepted = []
    jobs = []
    tables = []
    summary = {}
    order = {'land_use': 0, 'water': 1, 'land_cover': 2}
    for path in sorted(root.glob('*.geojson'), key=lambda p: (order.get(p.stem, 3), p.stem)):
        if not path.stat().st_size: continue
        data = read(path)
        kind = path.stem
        stats = Counter(downloaded=len(data['features']))
        csv_path = output/(kind+'.csv')
        with csv_path.open('w', encoding='utf-8', newline='') as handle:
            writer = csv.writer(handle)
            writer.writerow(['Key', 'SourceString', 'OvertureType', 'Geometry', 'Properties', 'Release'])
            for feature in data['features']:
                props = feature['properties']
                geom = shapely.make_valid(shape(feature['geometry'])).intersection(bbox)
                if geom.is_empty:
                    stats['bbox_only_false_positive'] += 1
                    continue
                writer.writerow([props['id'], label(props), kind, json.dumps(mapping(geom), separators=(',', ':')),
                                 json.dumps(props, ensure_ascii=False, separators=(',', ':')), data['release']])
                stats['catalog_entries'] += 1
                stats['already_mapped_sources' if any(s in existing_ids for s in source_ids(props)) else 'additional_source_records'] += 1
                if kind not in ('land_use', 'land_cover', 'water'): continue
                mapped_kind = KINDS.get(props.get('class') or props.get('subtype'))
                if (props.get('class') or props.get('subtype')) in ('sea', 'ocean'):
                    stats['existing_sea_reference_only'] += 1
                    continue
                if not mapped_kind or geom.geom_type not in ('Polygon', 'MultiPolygon'):
                    stats['reference_only_class_or_geometry'] += 1
                    continue
                if any(s in existing_ids for s in source_ids(props)):
                    stats['existing_osm_source'] += 1
                    continue
                projected = transform(project, geom).intersection(extent)
                if projected.is_empty:
                    stats['outside_municipal_city'] += 1
                    continue
                neighbors = [covers[int(i)] for i in tree.query(projected, predicate='intersects')]
                neighbors += [roads[int(i)] for i in road_tree.query(projected, predicate='intersects')]
                neighbors += [g for g in accepted if g.intersects(projected)]
                if neighbors: projected = projected.difference(shapely.union_all(neighbors))
                if projected.is_empty or projected.area < 2:
                    stats['covered_by_existing_geometry'] += 1
                    continue
                vertices, faces, missing = drape_fast(surface, projected, origin)
                if not faces:
                    stats['no_terrain_for_drape'] += 1
                    continue
                signature = hashlib.sha256((props['id'] + json.dumps(vertices)).encode()).hexdigest()[:12]
                name = 'StreetCover_Overture_'+mapped_kind+'_'+signature
                asset = output/(name+'.glb')
                if not asset.exists():
                    scene = {'coordinates': {'origin_utm_m': origin, 'frame': 'local east/north/up metres'},
                             'limitations': ['Overture ground context; illustrative material, terrain drape; existing geometry takes precedence.'],
                             'bounds_lonlat': list(bbox.bounds), 'trees': [], 'roads': [],
                             'meshes': [{'id': name, 'kind': 'terrain', 'vertices': vertices, 'triangles': faces,
                                         'metadata': {'overture_id': props['id'], 'overture_type': kind}}]}
                    export_glb(scene, asset, include_calibration=False)
                    _colour_glb(asset, COLOURS.get(mapped_kind, (.2, .25, .12)))
                jobs.append({'name': name, 'asset': str(asset), 'kind': 'overture_ground',
                             'overture_id': props['id'], 'source_objects': 1, 'triangles': len(faces),
                             'missing_terrain_cells': missing})
                accepted.append(projected)
                stats['visual_features'] += 1
        tables.append({'type': kind, 'asset_name': 'OT_'+kind, 'csv': str(csv_path), 'entries': stats['catalog_entries']})
        summary[kind] = dict(stats)
        print(json.dumps({'prepared': kind, **dict(stats)}), flush=True)
    manifest = {'schema': 'dynacity-overture-context/1', 'origin_utm_m': origin, 'jobs': jobs,
                'tables': tables, 'counts': summary, 'release': '2026-10-21.0',
                'visual_extent': 'Municipal Beirut production areas; surrounding records retained as reference data',
                'attribution': 'Overture Maps Foundation and per-feature sources; licenses retained in Properties metadata.',
                'limitations': ['Roads and connectors are reference data; existing simulation routing is unchanged.',
                                'Catalog entries are geographic records, not finished 3D objects.',
                                'Source bbox matches are clipped to the city extent; no water elevations are invented.']}
    (output/'overture-context.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    return manifest


def merge_locations(index_path, places_path):
    import shutil
    data = read(index_path)
    backup = index_path.with_name('location-index.before-overture.json')
    if not backup.exists(): shutil.copy2(index_path, backup)
    existing = [r for r in data['places'] if not r['id'].startswith('overture/')]
    cells = {}
    for row in existing:
        cells.setdefault(normalize(row['name']), []).append(row)
    added = skipped = 0
    for f in read(places_path)['features']:
        props = f['properties']
        if props.get('operating_status') == 'permanently_closed' or props.get('confidence') == 0:
            continue
        name = (props.get('names') or {}).get('primary')
        if not name: continue
        point = shape(f['geometry']).representative_point()
        if not box(35.4693, 33.8288, 35.572, 33.9076).covers(point): continue
        if any((r['lon']-point.x)**2+(r['lat']-point.y)**2 < .00045**2 for r in cells.get(normalize(name), [])):
            skipped += 1
            continue
        common = (props.get('names') or {}).get('common') or []
        aliases = [str(pair[1]) for pair in common if isinstance(pair, list) and len(pair) == 2]
        row = {'id': 'overture/'+props['id'], 'name': name, 'aliases': aliases,
               'lat': point.y, 'lon': point.x, 'kind': 'poi', 'source': 'Overture Maps',
               'category': props.get('basic_category'),
               'source_properties': props}
        existing.append(row)
        cells.setdefault(normalize(name), []).append(row)
        added += 1
    data['places'] = existing
    data['overture'] = {'added_places': added, 'duplicate_names_nearby_skipped': skipped,
                        'release': '2026-10-21.0', 'note': 'Source positions are not surveyed entrances; businesses may be outdated.'}
    target = index_path.with_suffix('.partial')
    target.write_text(json.dumps(data, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    target.replace(index_path)
    return data['overture']


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--private', default='C:/private/dynacity-data')
    args = parser.parse_args()
    prepare(Path(args.root), Path(args.output), Path(args.private))


if __name__ == '__main__': main()
