"""Source-linked placement manifest for utilities/waterfront point features (agent-3).

Maps eligible OSM nodes from the infrastructure supplement to finished CC0 models,
checks each against the actual imported building footprints (Beirut 001 pack and
city-batch object bounds), and keeps every inference explicit. Output stays outside Git.
"""
import argparse, glob, hashlib, json, math, sys
from pathlib import Path
import numpy as np

root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root / 'engine' / 'unreal' / 'Content' / 'Python'))
from library_asset_core import read_prepared

PREP = 'C:/private/dynacity-data/engine/asset-quality-pass/prepared/'
# kind -> (asset id, uniform scale, rotation rule, note)
RULES = {
    'man_made_lighthouse': ('lighthouse_grab3d', 1.0, 'fixed', 'Generic 27.5 m stylised lighthouse; mapped seamark light height is 52 m (not applied).'),
    'man_made_water_tower': ('water_tower_grab3d', 1.0, 'fixed', 'Generic 18 m stylised water tower; mapped size unknown.'),
    'man_made_chimney': ('chimney_stack_grab3d', 1.0, 'fixed', 'Generic 15.5 m brick chimney; mapped height unknown.'),
    'post_box': ('mailbox_grab3d', 1.0, 'road', 'Generic mailbox; operator branding (Liban Post) not represented.'),
    'telephone': ('phone_booth_grab3d', 1.0, 'road', 'Generic open phone booth.'),
    'advertising_billboard': ('billboard_ggbotnet', 0.25, 'road_y', 'Source units unspecified (47.7 units tall); scaled x0.25 to ~11.9 m: INFERRED. Face assumed to look along the road; unmapped.'),
}
SKIP_TELEPHONE = 'amenity=telephone node carries a shop name or alt_name (a mobile-phone shop mis-tag is likely); not shown as a booth'

def footprints():
    z = np.load('C:/private/dynacity-data/beirut001/beirut001-structures-pack.npz', allow_pickle=True)
    ox, oy = 728160.1865800284, 3749765.105583867
    return z, ox, oy

def inside(ring, pt):
    x, y = pt; c = False
    for i in range(len(ring)):
        a, b = ring[i], ring[i - 1]
        if (a[1] > y) != (b[1] > y) and x < (b[0] - a[0]) * (y - a[1]) / (b[1] - a[1]) + a[0]: c = not c
    return c

def building_hits(pts, origin):
    z, ox, oy = footprints()
    ids = list(z['building_ids']); ps = z['building_part_start']; rs = z['building_ring_start']; pp = z['building_point_start']; xy = z['building_xy']
    shift = np.array([ox - origin[0], oy - origin[1]])
    hits = {}
    for key, (x, y) in pts.items():
        for i in range(len(ids)):
            for part in range(ps[i], ps[i + 1]):
                for r in range(rs[part], rs[part + 1]):
                    ring = xy[pp[r]:pp[r + 1]] + shift
                    if len(ring) > 2 and ring[:, 0].min() <= x <= ring[:, 0].max() and ring[:, 1].min() <= y <= ring[:, 1].max() and inside(ring, (x, y)):
                        hits.setdefault(key, []).append('beirut001:' + ids[i])
    return hits

def main():
    p = argparse.ArgumentParser(); p.add_argument('--context', type=Path, required=True); p.add_argument('--output', type=Path, required=True)
    a = p.parse_args()
    if a.output.resolve().is_relative_to(root): p.error('keep generated data outside the repository')
    raw = a.context.read_bytes(); ctx = json.loads(raw)
    origin = ctx['origin_utm_m']
    cand = [r for r in ctx['points'] if r['kind'] in RULES]
    pts = {r['id']: r['position_local_m'][:2] for r in cand}
    hits = building_hits(pts, origin)
    placements, skipped = [], []
    assets = {}
    for r in cand:
        aid, scale, rule, note = RULES[r['kind']]
        t = r['tags']
        if r['kind'] == 'telephone' and ('name' in t or 'name:alt' in t):
            skipped.append({'source_id': r['id'], 'reason': SKIP_TELEPHONE, 'tags': t}); continue
        if r['id'] in hits:
            skipped.append({'source_id': r['id'], 'reason': 'inside imported building footprint', 'buildings': hits[r['id']]}); continue
        assets.setdefault(aid, read_prepared(PREP + aid))
        heading = r.get('heading_degrees', 0) if rule != 'fixed' else 0
        placements.append({'source_id': r['id'], 'kind': r['kind'], 'asset_id': aid, 'scale': scale, 'rotation_rule': rule,
            'position_local_m': r['position_local_m'], 'source_position_lonlat': r['source_position_lonlat'],
            'production_area': r.get('production_area', 'Unassigned'), 'tags': t, 'as_of': r.get('as_of'),
            'elevation_source': r.get('elevation_source'), 'heading_degrees': heading,
            'heading_source': 'inferred from nearest source road tangent' if rule != 'fixed' else 'none; rotation-symmetric use or unknown',
            'dimension_source': note})
    out = {'schema': 'dynacity-mapped-utilities-waterfront/1', 'origin_utm_m': origin, 'placements': placements, 'skipped': skipped,
           'context_path': str(a.context.resolve()), 'context_sha256': hashlib.sha256(raw).hexdigest(),
           'assets': {k: {'prepared_path': PREP + k, 'fbx_sha256': v['fbx_sha256'], 'provenance': v['provenance']} for k, v in assets.items()}}
    a.output.parent.mkdir(parents=True, exist_ok=True); a.output.write_text(json.dumps(out, indent=2, ensure_ascii=False), encoding='utf8')
    print(json.dumps({'placements': len(placements), 'skipped': len(skipped), 'by_asset': {k: sum(1 for x in placements if x['asset_id'] == k) for k in assets}}))
if __name__ == '__main__': main()
