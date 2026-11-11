"""Stage finished stop-pole placements at mapped OSM bus_stop nodes (agent 2).

Positions are the mapped node positions; nothing is moved or invented. Nodes
inside OSM building outlines are skipped and recorded. Yaw follows the nearest
source road tangent and is explicitly inferred. Shelters are never placed here.
"""
import argparse, hashlib, json, math, sys
from pathlib import Path
from shapely.geometry import Point, shape
from shapely.strtree import STRtree

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'engine' / 'unreal' / 'Content' / 'Python'))
from library_asset_core import read_prepared

SCALE = [1.5, 1.5, 5.05]  # source pole is 5 x 5 x 47.5 cm; scaled to ~7.5 cm x 2.4 m, inferred


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--street-context', type=Path, required=True)
    p.add_argument('--display-context', type=Path, required=True)
    p.add_argument('--prepared', type=Path, required=True)
    p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.resolve().is_relative_to(root): p.error('Keep outputs outside the repository')
    raw = a.street_context.read_bytes(); ctx = json.loads(raw)
    prepared = read_prepared(a.prepared)
    if prepared['provenance']['id'] != 'kenney_road_sign_empty': raise ValueError('Unexpected asset')
    polys, ids = [], []
    for f in json.loads(a.display_context.read_bytes())['features']:
        pr = f.get('properties', {}); g = f.get('geometry') or {}
        if pr.get('context_group') == 'buildings' and pr.get('kind') == 'osm_building' and g.get('type') in ('Polygon', 'MultiPolygon'):
            try: s = shape(g)
            except Exception: continue
            if s.is_valid and not s.is_empty: polys.append(s); ids.append(pr['id'])
    tree = STRtree(polys); placements, skipped = [], []
    for pt in ctx['points']:
        if pt['kind'] != 'bus_stop': continue
        if not pt['id'].startswith('node/'): skipped.append({'source_id': pt['id'], 'reason': 'not a node'}); continue
        hits = [ids[i] for i in tree.query(Point(pt['source_position_lonlat']), predicate='intersects')]
        if hits: skipped.append({'source_id': pt['id'], 'reason': 'inside OSM building outline', 'building_ids': hits}); continue
        v = pt['position_local_m']
        if len(v) != 3 or not all(math.isfinite(x) for x in v): skipped.append({'source_id': pt['id'], 'reason': 'no terrain position'}); continue
        placements.append({'source_id': pt['id'], 'position_local_m': v, 'source_position_lonlat': pt['source_position_lonlat'],
            'production_area': pt['production_area'], 'as_of': pt['as_of'], 'retrieved_on': pt['retrieved_on'],
            'tags': pt['tags'], 'yaw_degrees_inferred': pt.get('heading_degrees'),
            'yaw_source': pt.get('heading_source'), 'road_id': pt.get('road_id'), 'scale_inferred': SCALE,
            'elevation_source': pt.get('elevation_source'), 'asset_key': 'kenney_road_sign_empty',
            'appearance_source': 'Illustrative generic pole; bare pole, no legend, no shelter; scale inferred'})
    out = {'schema': 'dynacity-agent2-bus-stop-poles/1', 'origin_utm_m': ctx['origin_utm_m'],
        'context_path': str(a.street_context.resolve()), 'context_sha256': hashlib.sha256(raw).hexdigest(),
        'prepared_path': str(a.prepared.resolve()), 'asset_fbx_sha256': prepared['fbx_sha256'],
        'asset_provenance': prepared['provenance'], 'placements': placements, 'skipped_in_source': skipped}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(out, indent=1), encoding='utf8')
    print(json.dumps({'placements': len(placements), 'skipped': len(skipped)}))

if __name__ == '__main__': main()
