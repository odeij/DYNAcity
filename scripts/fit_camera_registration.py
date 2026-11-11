"""Fit the shared camera model to annotated static-building landmarks and write the artifact + overlays.

python scripts/fit_camera_registration.py <landmarks.json> <out_dir>
Only numpy/cv2 are needed; the package is not imported.
"""
import glob
import json
import sys
from pathlib import Path

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'dynacity'))
import camera_registration as cr  # noqa: E402
import glb_geometry as g  # noqa: E402
from facade_massing import project_points  # noqa: E402

PHOTOS = Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos')
BATCHES = Path('C:/private/dynacity-data/engine/city-batches')
MATCHED = Path('C:/private/dynacity-data/engine/title-menu/matched.json')
SIZE = (2000, 1500)


def load_buildings(keys, cameras):
    """Resolve landmark keys (uuid prefix, or the 4 hex digits after the first uuid character) to source buildings
    whose centroid lies within 350 m of a camera; ambiguous keys are an error rather than a guess."""
    hits = {k: [] for k in keys}
    for glb in sorted(BATCHES.glob('buildings-*.glb')):
        index = json.loads(glb.with_suffix('.objects.json').read_text())
        meshes = None
        for o in index['objects']:
            uuid = o['source_id'].split(':')[1]
            for k in keys:
                if not (uuid.startswith(k) if len(k) > 4 else uuid[1:5] == k):
                    continue
                meshes = meshes or g.mesh_primitives(glb)
                P = meshes[o['batch']][0][o['first_vertex']:o['first_vertex'] + o['vertex_count']]
                cols = g.vertical_edges(P, None, 2.0)
                if len(cols) and min(np.hypot(*(cols[:, :2].mean(0) - c[:2])) for c in cameras) < 350:
                    hits[k].append({'source_id': o['source_id'], 'columns': cols})
    bad = {k: [h['source_id'] for h in v] for k, v in hits.items() if len(v) != 1}
    if bad:
        raise SystemExit(f'ambiguous or unresolved building keys: {bad}')
    return {k: v[0] for k, v in hits.items()}


def main(landmark_path, out_dir):
    doc = json.loads(Path(landmark_path).read_text())
    out = Path(out_dir); out.mkdir(parents=True, exist_ok=True)
    lms = doc['landmarks']
    origin = json.loads(next(BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m']
    matched = json.loads(MATCHED.read_text())
    views = {}
    for i, pid in enumerate(sorted({lm['view'] for lm in lms})):
        meta = json.loads((PHOTOS / f'{pid}.json').read_text())
        z = matched['m' + pid][0][2] / 100
        views[pid] = {'index': i, 'photo_id': pid, 'heading_deg': meta['heading_grid_deg'],
                      'camera_m': [meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], z]}
    found = load_buildings({lm['building'] for lm in lms if 'building' in lm}, [v['camera_m'] for v in views.values()])
    cols = {k: v['columns'] for k, v in found.items()}
    held = set(doc['held_out'])
    fit_lms = [lm for lm in lms if lm['id'] not in held]
    params, cost = cr.fit(fit_lms, views, cols, SIZE)
    rows, stats = cr.assess(params, lms, views, cols, SIZE, held)
    boot = cr.bootstrap(fit_lms, views, cols, SIZE)
    result = {
        'schema': 'dynacity-camera-registration/1',
        'verdict': cr.verdict(lms, views, cols),
        'fitted': {'hfov_deg': round(float(params[0]), 2), 'pitch_deg': round(float(params[1]), 2),
                   'yaw_offset_deg': {pid: round(float(params[2 + v['index']]), 2) for pid, v in views.items()}},
        'bootstrap_params_mean_std': {'order': ['hfov', 'pitch'] + [f'yaw_{p}' for p in views], **boot},
        'residual_summary': stats,
        'residual_gate': cr.residual_gate(stats),
        'landmarks': rows,
        'building_keys': {k: v['source_id'] for k, v in found.items()},
        'method': 'shared FOV+pitch, per-view yaw offset; Huber loss on silhouette-edge columns; camera XY at recorded position',
        'limits': ['Beirut 001 footprints are registered to UTM by an inferred rigid fit (about 5 m typical), so residuals include real building offsets',
                   'landmarks other than the LGB tower have candidate identity only',
                   'camera translation is not fitted']}
    (out / 'camera-registration.json').write_text(json.dumps(result, indent=1))
    for pid, v in views.items():
        img = cv2.imread(str(PHOTOS / f'{pid}.jpg')); img = cv2.resize(img, SIZE)
        yaw = v['heading_deg'] + params[2 + v['index']]
        for lm in lms:
            if lm['view'] != pid or lm.get('kind') == 'vp':
                continue
            for x, y, z0, z1 in cols[lm['building']]:
                px = project_points([[x, y, z0], [x, y, z1]], v['camera_m'], yaw, params[1], params[0], SIZE)
                if not np.isnan(px).any():
                    cv2.line(img, tuple(px[0].astype(int)), tuple(px[1].astype(int)), (255, 160, 0), 1)
        for lm, r in zip(lms, rows):
            if lm['view'] == pid and lm.get('kind') == 'vp':
                cv2.line(img, (0, int(lm['v'] * SIZE[1])), (SIZE[0], int(lm['v'] * SIZE[1])), (0, 200, 255), 1)
            elif lm['view'] == pid:
                u, vv = int(lm['u'] * SIZE[0]), int(lm['v'] * SIZE[1])
                cv2.drawMarker(img, (u, vv), (0, 0, 255) if r['held_out'] else (0, 255, 0), cv2.MARKER_CROSS, 24, 3)
                cv2.putText(img, f"{lm['id'].split('-',1)[1]} {r['residual_px']:+.0f}px", (u + 8, vv - 8), cv2.FONT_HERSHEY_SIMPLEX, .6, (255, 255, 255), 2)
        cv2.imwrite(str(out / f'overlay-{pid}.jpg'), img)
    print(json.dumps({k: result[k] for k in ('verdict', 'fitted', 'residual_summary', 'residual_gate', 'bootstrap_params_mean_std')}, indent=1))
    for r in rows:
        print(r)


if __name__ == '__main__':
    main(*sys.argv[1:3])
