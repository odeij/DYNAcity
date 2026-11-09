"""Build the corridor reconstruction GLB from the producer's facade components and the prepared manifest.

python scripts/build_corridor_geometry.py <corridor-manifest.json> <reference-profiles.json> <out_dir>
Only street-facing producer parts are used: the source envelope (roof, rear, sides, other frontages, collision) stays
with the original building, so the producer's body/rear/side/roof parts are dropped. An opaque interior backing sits
behind everything; coverage of the street wall is measured and recorded, never assumed.
"""
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import corridor_reconstruction as cr  # noqa: E402
import reference_facade_geometry as rfg  # noqa: E402

DROP = {'body_core', 'rear_generic', 'side_left', 'side_right', 'side_left_bands', 'side_right_bands', 'roof_slab'}
INTERIOR = {'base_color': [0.035, 0.04, 0.045, 1.0], 'roughness': 0.9, 'metallic': 0.0}


def materials_for(names):
    out = {}
    for n in names:
        if n == 'interior_dark':
            out[n] = INTERIOR
        else:
            r, g, b = (c / 255. for c in rfg._rgb(n))
            out[n] = {'base_color': [round(r, 4), round(g, 4), round(b, 4), 1.0], 'roughness': 0.15 if n in rfg._GLASS else 0.85, 'metallic': 0.0}
    return out


def main(manifest_path, profile_path, out_dir):
    manifest = json.loads(Path(manifest_path).read_text()); profile = json.loads(Path(profile_path).read_text())
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    placed, records = [], []
    for b in manifest['buildings']:
        front = b['frontages'][0]
        assert front['index'] == b['rebuilt_frontage_index']
        W, H, D = front['length_m'], front['top_z'] - front['base_z'], b['plan_depth_m']
        mesh = rfg.mesh_for_profile(b['family'], W, H, depth_m=D, side_facades={'left': 'none', 'right': 'none'}, profile=profile)
        parts = [{'name': f"{b['source_id'][10:18]}:{n}", 'material': p['meta']['material'], 'vertices': p['verts'].astype(float),
                  'triangles': p['faces'].astype(np.int64)} for n, p in mesh.parts.items() if n not in DROP]
        # deepest recess (producer frame: street relief is y<0, recesses go +y) behind which the backing may sit
        recess = max(float(p['vertices'][:, 1].max()) for p in parts)
        parts += [{**bp, 'name': f"{b['source_id'][10:18]}:{bp['name']}"} for bp in cr.backing_parts(W, H, min(recess, D), 'interior_dark')]
        cover = cr.wall_coverage([p for p in parts if not p['name'].endswith(('return_x0', 'return_xw'))], W, H)
        normals_ok = float(np.mean([(cr.triangle_normals(p['vertices'], p['triangles'])[:, 1] < 0).mean() for p in parts if p['name'].endswith(':backing')]))
        placed += cr.place_parts(parts, front)
        prov = mesh.provenance
        records.append({'source_id': b['source_id'], 'family': b['family'], 'evidence_level': b['evidence_level'],
                        'identity': 'candidate (multi-view overlap); NOT confirmed', 'frontage_m': [W, H], 'plan_depth_m': D,
                        'producer_parts_used': sorted(n for n in mesh.parts if n not in DROP), 'producer_parts_dropped': sorted(n for n in mesh.parts if n in DROP),
                        'triangles': int(sum(len(p['triangles']) for p in parts)), 'street_wall_coverage': round(cover, 4),
                        'backing_faces_street': normals_ok, 'floors': prov.get('floors'), 'bays': prov.get('bays'),
                        'parameters': prov.get('parameters'), 'producer_warnings': prov.get('warnings'),
                        'geometry_status': 'estimated from reference photos; not surveyed', 'source_height_unchanged_m': round(H, 2)})
    mats = materials_for(sorted({p['material'] for p in placed}))
    info = cr.write_glb(out / 'corridor-reconstruction.glb', placed, mats)
    (out / 'corridor-materials.json').write_text(json.dumps(mats, indent=1))
    (out / 'corridor-geometry-report.json').write_text(json.dumps({'schema': 'dynacity-corridor-geometry/1', 'glb': info, 'buildings': records,
        'camera_status': 'uncalibrated_estimate', 'note': 'candidate identities and estimated geometry; not an exact reconstruction'}, indent=1, default=str))
    for r in records:
        print(r['source_id'][10:18], r['family'], r['triangles'], 'tris, coverage', r['street_wall_coverage'], 'floors', r['floors'], 'bays', r['bays'])
    print(info)


if __name__ == '__main__':
    main(*sys.argv[1:4])
