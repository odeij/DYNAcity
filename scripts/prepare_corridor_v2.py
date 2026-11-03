"""Prepare iteration-2 corridor reconstruction: chosen visible walls -> exact triangle omission patches (private).

python scripts/prepare_corridor_v2.py <selection2.json> <out_dir>

selection2.json:
{"buildings": [{"key", "source_id" (uuid prefix), "family", "walls": [{"edge", "kind": "producer|plain|netwrap", "params": {...}}],
                "height_override": {"est","lo","hi","rationale"} | null,
                "split_group_source_id": uuid prefix of a building whose triangles keep their own material slot (batch 9 / LGB)}]}
Walls are ring edges (the same indexing as visible_walls.py). Only the selected walls' triangles are omitted; the roof, rear,
sides and every unselected wall keep their original triangles and materials. Originals are never modified.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import corridor_reconstruction as cr  # noqa: E402
import glb_geometry as g  # noqa: E402
from dynacity.batch_patch import patch_batch  # noqa: E402

BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
CAMERAS_LOCAL = None


def main(selection_path, out_dir):
    sel = json.loads(Path(selection_path).read_text()); out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    want = {b['source_id']: b for b in sel['buildings']}
    split_ids = {b['split_group_source_id'] for b in sel['buildings'] if b.get('split_group_source_id')}
    cam_xy = np.array(sel.get('toward_xy_local', [2430., 1491.]), float)
    records, omit, groups = [], {}, {}
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        index_path = glb.with_suffix('.objects.json'); index = json.loads(index_path.read_text()); meshes = None
        for o in index['objects']:
            uuid = o['source_id'].split(':')[1]
            key = next((k for k in want if uuid.startswith(k)), None)
            gk = next((k for k in split_ids if uuid.startswith(k)), None)
            if not key and not gk:
                continue
            b = o['local_bounds_m']
            if np.hypot(*((np.array([(b[0][0] + b[1][0]) / 2, (b[0][1] + b[1][1]) / 2])) - cam_xy)) > 400:
                continue
            meshes = meshes or g.mesh_primitives(glb)
            P, T = meshes[o['batch']]
            first, count = o['first_triangle'], o['triangle_count']
            if gk:
                groups.setdefault((str(glb), o['batch']), {}).setdefault('LGB_group', []).extend(range(first, first + count))
            if not key:
                continue
            tri = T[first:first + count]
            used = np.unique(tri); remap = {int(v): i for i, v in enumerate(used)}
            local_tri = np.vectorize(remap.get)(tri)
            ring = cr.building_ring(P[used], local_tri)
            base, top = float(P[used][:, 2].min()), float(P[used][:, 2].max())
            rec = {**{k: v for k, v in want[key].items() if k != 'walls'}, 'source_id_full': o['source_id'], 'batch': o['batch'], 'asset': str(glb),
                   'source_height_m': round(top - base, 2), 'base_z_local_m': round(base, 2), 'ring_local': ring.round(4).tolist(), 'walls': []}
            tris_to_omit = []
            for w in want[key]['walls']:
                i = w['edge']
                a, bb = ring[i], ring[(i + 1) % len(ring)]
                tvec = (bb - a) / np.linalg.norm(bb - a)
                n = np.array([tvec[1], -tvec[0]])
                front = {'index': i, 'start': a.tolist(), 'tangent': tvec.tolist(), 'normal': n.tolist(), 'length_m': round(float(np.linalg.norm(bb - a)), 3),
                         'base_z': base, 'top_z': top}
                wall = cr.frontage_wall_triangles(P[used], local_tri, front)
                if len(wall) < 2:
                    raise SystemExit(f"{o['source_id']} edge {i}: {len(wall)} wall triangles; refusing to guess")
                depth = float(np.max((ring - a) @ -n))
                tris_to_omit += (first + wall).tolist()
                rec['walls'].append({**w, 'frontage': front, 'plan_depth_m': round(depth, 2), 'omitted_triangles': int(len(wall))})
            rec['omitted_triangles'] = len(tris_to_omit)
            omit.setdefault((str(glb), o['batch']), []).extend(tris_to_omit)
            records.append(rec)
    missing = set(want) - {r['source_id'] for r in records}
    if missing:
        raise SystemExit(f'unresolved building keys: {sorted(missing)}')
    patches = []
    for (asset, batch), tris in omit.items():
        grp = groups.get((asset, batch), {})
        # the LGB group keeps its own slot only in batches where a split building was found
        names = ('SourceExisting',)
        gdict = {'ReferenceFacade': grp['LGB_group']} if grp else None
        p = patch_batch(asset, batch, tris, gdict, out / ('omit2-' + batch + '.glb'), names)
        p['buildings'] = [r['source_id'] for r in records if r['batch'] == batch]
        patches.append(p)
    manifest = {'schema': 'dynacity-corridor-reconstruction/2', 'selection': str(selection_path),
                'origin_utm_m': json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m'],
                'buildings': records, 'patches': patches, 'source_originals_modified': False,
                'identity_policy': 'candidates from multi-view wall visibility + family polygon overlap; not confirmed'}
    (out / 'corridor-manifest-v2.json').write_text(json.dumps(manifest, indent=1))
    for r in records:
        print(r['key'], r['source_id'][10:18], r['batch'][-10:], r['source_height_m'], [(w['edge'], w['frontage']['length_m'], w['omitted_triangles'], w['kind']) for w in r['walls']])
    for p in patches:
        print(Path(p['asset']).name, 'omitted', p['omitted_triangles'], 'of', p['source_triangles'], {k: v['triangles'] for k, v in p['primitives'].items()})


if __name__ == '__main__':
    main(*sys.argv[1:3])
