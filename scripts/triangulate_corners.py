"""Joint multi-view triangulation of hand-read static corners through the fitted cameras, with uncertainty.

python scripts/triangulate_corners.py <camera-registration.json> <corner-observations.json> <out.json>

observations: {"corners": {name: {"view": [x, y px on 1600x1200], ...}}, "held_out_views": [...], "sigma_px": 12}
Each corner is the point nearest to its rays (least squares). Uncertainty comes from a bootstrap that perturbs the pixel by
``sigma_px``, each view's heading by the fitted yaw bootstrap std, and the camera XY by ``cam_xy_sigma_m`` (GPS). Reprojection
residuals are reported per view. The camera is an uncalibrated estimate: positions are estimates, not survey.
"""
import json
import math
import sys
from pathlib import Path

import numpy as np

S = (1600, 1200)
PHOTOS = Path('C:/private/dynacity-data/backups/beirut-evidence/engine/appearance-evidence-20261030/street-photos')
MATCHED = Path('C:/private/dynacity-data/engine/title-menu/matched.json')
BATCH_REMEIL = next(Path('C:/private/dynacity-data/engine/city-batches').glob('buildings-remeil-*.objects.json'))


def load_views(reg):
    origin = json.loads(BATCH_REMEIL.read_text())['coordinates']['origin_utm_m']
    matched = json.loads(MATCHED.read_text())
    views = {}
    for pid, off in reg['fitted']['yaw_offset_deg'].items():
        meta = json.loads((PHOTOS / f'{pid}.json').read_text())
        views[pid] = {'cam': np.array([meta['camera_xy_m'][0] - origin[0], meta['camera_xy_m'][1] - origin[1], matched['m' + pid][0][2] / 100]),
                      'yaw': meta['heading_grid_deg'] + off}
    return views


def ray(px, view, pitch, hfov):
    f = (S[0] / 2) / math.tan(math.radians(hfov) / 2)
    x, y = px[0] - S[0] / 2, S[1] / 2 - px[1]
    yw, q = math.radians(view['yaw']), math.radians(pitch)
    fwd = np.array([math.sin(yw) * math.cos(q), math.cos(yw) * math.cos(q), math.sin(q)])
    right = np.array([math.cos(yw), -math.sin(yw), 0.])
    up = np.cross(right, fwd)
    d = fwd * f + right * x + up * y
    return d / np.linalg.norm(d)


def project(X, view, pitch, hfov):
    f = (S[0] / 2) / math.tan(math.radians(hfov) / 2)
    yw, q = math.radians(view['yaw']), math.radians(pitch)
    fwd = np.array([math.sin(yw) * math.cos(q), math.cos(yw) * math.cos(q), math.sin(q)])
    right = np.array([math.cos(yw), -math.sin(yw), 0.])
    up = np.cross(right, fwd)
    p = X - view['cam']
    z = p @ fwd
    if z <= 0:
        return None
    return np.array([S[0] / 2 + f * (p @ right) / z, S[1] / 2 - f * (p @ up) / z])


def nearest_point(origins, dirs):
    A = np.zeros((3, 3)); b = np.zeros(3)
    for o, d in zip(origins, dirs):
        M = np.eye(3) - np.outer(d, d)
        A += M; b += M @ o
    return np.linalg.solve(A, b)


def main(reg_path, obs_path, out_path, yaw_sigma_deg=None, rounds=300, cam_xy_sigma=3., seed=3):
    reg = json.loads(Path(reg_path).read_text()); obs = json.loads(Path(obs_path).read_text())
    views = load_views(reg); pitch, hfov = reg['fitted']['pitch_deg'], reg['fitted']['hfov_deg']
    sig_px = obs.get('sigma_px', 12.)
    yaw_std = dict(zip([k.replace('yaw_', '') for k in reg['bootstrap_params_mean_std']['order'][2:]], reg['bootstrap_params_mean_std']['std'][2:]))
    if yaw_sigma_deg is not None:  # override: e.g. the fit-residual scale (landmark residuals ~20 px = 0.6 deg) instead of the resampled std
        yaw_std = {k: float(yaw_sigma_deg) for k in yaw_std}
    rng = np.random.default_rng(seed)
    result = {}
    for name, per in obs['corners'].items():
        use = {v: p for v, p in per.items() if v not in obs.get('held_out_views', [])}
        origins = [views[v]['cam'] for v in use]; dirs = [ray(p, views[v], pitch, hfov) for v, p in use.items()]
        X = nearest_point(origins, dirs)
        reproj = {v: (None if project(X, views[v], pitch, hfov) is None else (project(X, views[v], pitch, hfov) - np.array(p)).round(1).tolist()) for v, p in per.items()}
        boots = []
        for _ in range(rounds):
            o2, d2 = [], []
            for v, p in use.items():
                vw = {'cam': views[v]['cam'] + np.array([*rng.normal(0, cam_xy_sigma, 2), 0.]), 'yaw': views[v]['yaw'] + rng.normal(0, yaw_std.get(v, 1.2))}
                o2.append(vw['cam']); d2.append(ray(np.array(p) + rng.normal(0, sig_px, 2), vw, pitch, hfov))
            boots.append(nearest_point(o2, d2))
        B = np.array(boots)
        result[name] = {'views_used': list(use), 'xyz_local_m': X.round(2).tolist(), 'bootstrap_std_m': B.std(0).round(2).tolist(),
                        'bootstrap_p5_p95_m': [np.percentile(B, 5, axis=0).round(2).tolist(), np.percentile(B, 95, axis=0).round(2).tolist()],
                        'ray_miss_m': [round(float(np.linalg.norm((np.eye(3) - np.outer(d, d)) @ (X - o))), 2) for o, d in zip(origins, dirs)],
                        'reprojection_px_by_view(model-photo)': reproj, 'ground_z_m_at_camera_ref': round(float(views[list(use)[0]]['cam'][2] - 1.4), 2)}
    Path(out_path).write_text(json.dumps({'schema': 'dynacity-corner-triangulation/1', 'camera_status': reg['verdict']['status'], 'sigma_px': sig_px,
        'cam_xy_sigma_m': cam_xy_sigma, 'yaw_sigma_deg_used': yaw_sigma_deg if yaw_sigma_deg is not None else yaw_std, 'held_out_views': obs.get('held_out_views', []), 'corners': result,
        'limits': ['hand-read corners', 'uncalibrated camera (pitch/FOV shared, yaw per view)', 'camera XY from GPS, not fitted']}, indent=1))
    for k, v in result.items():
        print(k, v['xyz_local_m'], '+-', v['bootstrap_std_m'], 'miss', v['ray_miss_m'], v['reprojection_px_by_view(model-photo)'])


if __name__ == '__main__':
    main(*sys.argv[1:4], yaw_sigma_deg=float(sys.argv[4]) if len(sys.argv) > 4 else None)
