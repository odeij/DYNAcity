"""Prepare a corridor reconstruction: frontages + exact source-triangle omission patches, privately.

python scripts/prepare_corridor_reconstruction.py <selection.json> <out_dir>
selection.json: {"buildings": [{"source_id", "family", "evidence_level", "views": [...], "note"}], "toward_xy_local": [x, y]}
Original GLBs are never modified; patch copies drop only the listed buildings' triangles.
"""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import glb_geometry as g  # noqa: E402
import corridor_reconstruction as cr  # noqa: E402
from dynacity.capture_building import omit_source_triangles  # noqa: E402

BATCHES = Path('C:/private/dynacity-data/engine/city-batches')


def main(selection_path, out_dir):
    sel = json.loads(Path(selection_path).read_text()); out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    want = {b['source_id']: b for b in sel['buildings']}
    toward = np.array(sel['toward_xy_local'], float)
    records, by_asset, explicit = [], {}, {}
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        index_path = glb.with_suffix('.objects.json'); index = json.loads(index_path.read_text()); meshes = None
        for o in index['objects']:
            if o['source_id'] not in want:
                continue
            meshes = meshes or g.mesh_primitives(glb)
            P, T = meshes[o['batch']]
            # triangles of this object only (index ranges are relative to the batch mesh)
            tri = T[o['first_triangle']:o['first_triangle'] + o['triangle_count']]
            used = np.unique(tri); remap = {int(v): i for i, v in enumerate(used)}
            ring = cr.building_ring(P[used], np.vectorize(remap.get)(tri))
            base, top = float(P[used][:, 2].min()), float(P[used][:, 2].max())
            fronts = cr.street_frontages(ring, toward, base, top)
            front = fronts[0]
            depth = float(np.max((ring - np.asarray(front['start'])) @ -np.asarray(front['normal'])))
            wall = cr.frontage_wall_triangles(P, T[o['first_triangle']:o['first_triangle'] + o['triangle_count']], front)
            if len(wall) < 2:
                raise SystemExit(f"{o['source_id']}: street wall has {len(wall)} triangles; refusing to guess")
            explicit[o['source_id']] = (o['first_triangle'] + wall).tolist()
            records.append({**want[o['source_id']], 'batch': o['batch'], 'asset': str(glb), 'height_m': round(top - base, 2),
                            'base_z_local_m': round(base, 2), 'ring_points': len(ring), 'frontages': fronts[:3], 'rebuilt_frontage_index': front['index'], 'plan_depth_m': round(depth, 2), 'omitted_triangles': int(len(wall)),
                            'envelope_retained': 'roof, rear and side walls and all other frontages keep their original triangles/materials',
                            'source_triangles': int(o['triangle_count'])})
            by_asset.setdefault((str(glb), str(index_path), o['batch']), []).append(o['source_id'])
    missing = set(want) - {r['source_id'] for r in records}
    if missing:
        raise SystemExit(f'unresolved source IDs: {sorted(missing)}')
    patches = []
    for (asset, index, batch), ids in by_asset.items():
        patches.append(omit_source_triangles(asset, index, ids, out / ('omit-' + batch + '.glb'), explicit={k: explicit[k] for k in ids}))
    manifest = {'schema': 'dynacity-corridor-reconstruction/1', 'selection': str(selection_path),
                'origin_utm_m': json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m'],
                'buildings': records, 'patches': patches, 'source_originals_modified': False,
                'identity_policy': 'candidate identities from multi-view overlap; footprint position error about 5 m; none confirmed except as noted per record'}
    (out / 'corridor-manifest.json').write_text(json.dumps(manifest, indent=1))
    for r in records:
        print(r['source_id'][10:18], r['batch'], r['height_m'], r['family'], [(f['length_m'], f['facing']) for f in r['frontages']])
    print('patches', [(Path(p['asset']).name, [(x['source_id'][10:18], x['triangles']) for x in p['removed']]) for p in patches])


if __name__ == '__main__':
    main(*sys.argv[1:3])
