"""Roof height implied by a hand-read roof corner in several photos, through the fitted camera.

python scripts/estimate_roof_heights.py <camera-registration.json> <roof-observations.json> <out.json>

Observation: {"view", "building" (uuid prefix), "u", "v" (normalised to the photo), "sigma_px" (on a 1200-high frame)}.
The footprint corner whose projected column is nearest ``u`` is taken as the observed corner; the ray through (u, v) is
intersected with that corner's vertical line. Disagreement between views is reported, never averaged away: it points to
camera, identity or temporal error. Heights are relative to the model ground at the corner, not surveyed.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import visible_walls as vw  # noqa: E402

S = vw.S


def ray(u, v, yaw, pitch, hfov):
    f = (S[0] / 2) / math.tan(math.radians(hfov) / 2)
    x, y = u * S[0] - S[0] / 2, S[1] / 2 - v * S[1]
    yw, q = math.radians(yaw), math.radians(pitch)
    fwd = np.array([math.sin(yw) * math.cos(q), math.cos(yw) * math.cos(q), math.sin(q)])
    right = np.array([math.cos(yw), -math.sin(yw), 0.])
    up = np.cross(right, fwd)
    d = fwd * f + right * x + up * y
    return d / np.linalg.norm(d), f


def main(reg_path, obs_path, out_path):
    reg = json.loads(Path(reg_path).read_text()); obs = json.loads(Path(obs_path).read_text())
    origin = json.loads(next(vw.BATCHES.glob('buildings-remeil-*.objects.json')).read_text())['coordinates']['origin_utm_m']
    matched = json.loads(vw.MATCHED.read_text())
    cams = {}
    for pid, off in reg['fitted']['yaw_offset_deg'].items():
        meta = json.loads((vw.PHOTOS / f'{pid}.json').read_text())
        cams[pid] = (np.array([meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], matched['m' + pid][0][2] / 100]), meta['heading_grid_deg'] + off)
    walls = vw.load_walls([c[0] for c in cams.values()], 260.)
    rows = []
    for o in obs['observations']:
        cam, yaw = cams[o['view']]
        corners = {}
        for w in walls:
            if w['sid'].split(':')[1].startswith(o['building']):
                for p in (w['a'], w['b']):
                    corners[tuple(np.round(p, 2))] = (np.asarray(p), w['z0'], w['z1'])
        best = None
        for p, z0, z1 in corners.values():
            d, f = ray(o['u'], 0.5, yaw, reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg'])
            cs = vw.camera_space([[*p, z0]], cam, yaw, reg['fitted']['pitch_deg'])[0]
            if cs[2] <= 0:
                continue
            u_pred = (S[0] / 2 + f * cs[0] / cs[2]) / S[0]
            if best is None or abs(u_pred - o['u']) < best[0]:
                best = (abs(u_pred - o['u']), p, z0, z1)
        _, p, z0, z1 = best
        d, f = ray(o['u'], o['v'], yaw, reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg'])
        h = np.hypot(*(p - cam[:2])) / np.hypot(d[0], d[1])
        z = cam[2] + d[2] * h
        sig = o.get('sigma_px', 15.) / f * np.hypot(*(p - cam[:2]))
        rows.append({**o, 'corner_xy_local_m': np.round(p, 2).tolist(), 'distance_m': round(float(np.hypot(*(p - cam[:2]))), 1),
                     'column_miss_px': round(best[0] * S[0], 1), 'implied_roof_z_m': round(float(z), 2),
                     'implied_height_above_model_ground_m': round(float(z - z0), 1), 'source_height_m': round(float(z1 - z0), 1),
                     'height_sigma_m_from_pixel_error': round(float(sig), 1)})
    by = {}
    for r in rows:
        by.setdefault(r['building'], []).append(r)
    summary = {b: {'views': [r['view'] for r in v], 'implied_heights_m': [r['implied_height_above_model_ground_m'] for r in v],
                   'source_height_m': v[0]['source_height_m'],
                   'spread_m': round(max(r['implied_height_above_model_ground_m'] for r in v) - min(r['implied_height_above_model_ground_m'] for r in v), 1)} for b, v in by.items()}
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-roof-height-estimates/1', 'camera_status': reg['verdict']['status'],
        'summary': summary, 'rows': rows, 'limits': ['hand-read roof corners (+-15 px)', 'uncalibrated camera', 'footprint position error ~5 m']}, indent=1))
    print(json.dumps(summary, indent=1))


if __name__ == '__main__':
    main(*sys.argv[1:4])
